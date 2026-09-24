# -*- coding: utf-8 -*-
"""Requirement matching: keyword layer, embedding layer, LLM layer and the
weighted combination that produces the final match percentage.

A requirement is a dict: {id, name, keywords: [str], weight: int, mandatory: bool,
description: str}. Each layer returns per-requirement evidence; `combine`
merges them with a simple, explainable rule: the strongest evidence wins.
"""
import math
import re
import unicodedata

STATUS_SCORE = {"met": 1.0, "partial": 0.5, "not_met": 0.0}
GEORGIAN_RE = re.compile(r"[Ⴀ-ჿ]")


def normalize(text):
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", text).lower().strip()


def split_keywords(value):
    return [k.strip() for k in re.split(r"[,;\n]", value or "") if k.strip()]


def _keyword_regex(keyword):
    kw = normalize(keyword)
    # Georgian words carry case suffixes (პითონი / პითონის); allow a suffix.
    tail = "" if GEORGIAN_RE.search(kw) else r"(?![\w])"
    return re.compile(r"(?<![\w])" + re.escape(kw) + tail)


def keyword_hits(normalized_text, keywords):
    """Keywords found in the (already normalised) CV text."""
    hits = []
    for kw in keywords or []:
        if kw and _keyword_regex(kw).search(normalized_text):
            hits.append(kw)
    return hits


def requirement_text(req):
    """Text used to embed a requirement."""
    parts = [req.get("name") or ""]
    if req.get("keywords"):
        parts.append(", ".join(req["keywords"]))
    if req.get("description"):
        parts.append(req["description"])
    return ". ".join(p for p in parts if p)


def chunk_text(text, size=700, overlap=150):
    """Split a CV into overlapping chunks so short requirements can be compared
    against the relevant paragraph instead of the whole document."""
    text = text or ""
    if not text.strip():
        return []
    if len(text) <= size:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = max(text.rfind("\n", start, end), text.rfind(" ", start, end))
            if cut > start + size // 2:
                end = cut
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def best_similarity(req_vec, chunk_vecs):
    return max((cosine(req_vec, c) for c in chunk_vecs), default=0.0)


def embed_score(similarity, hi, lo):
    if similarity >= hi:
        return 1.0
    if similarity >= lo:
        return 0.5
    return 0.0


def quote_in_text(quote, normalized_text):
    """Is the snippet the model quoted really in the CV? Tolerant to punctuation and line
    breaks: either the normalised quote is a substring, or nearly all of its words are there."""
    q = normalize(quote).strip(" \"'.,;:-")
    if len(q) < 3:
        return False
    if q in normalized_text:
        return True
    tokens = [t for t in re.findall(r"\w+", q) if len(t) >= 3]
    if len(tokens) < 2:
        return False
    found = sum(1 for t in tokens if t in normalized_text)
    return found / float(len(tokens)) >= 0.85


def apply_evidence_policy(requirements, keyword_layer, embed_layer, llm_layer, normalized_cv,
                          embed_hi=0.68):
    """A language model may be wrong or simply invent things, so its opinion only counts when
    it is backed by the CV:

    * keywords found / a strong semantic match: the requirement is proven anyway, the model
      only adds its explanation;
    * otherwise the model must QUOTE the CV, and the quote must really be in the CV; an
      unverifiable claim counts for nothing ("unverified");
    * when the recruiter typed keywords for the requirement and none of them is in the CV, a
      verified "met" is worth half, a "partial" nothing ("keywords_missing"): the recruiter's
      own words are decisive, the model can only point at a differently worded CV.
    """
    adjusted = {}
    for req in requirements:
        rid = req["id"]
        llm = dict((llm_layer or {}).get(rid) or {})
        status = llm.get("status")
        proven = bool((keyword_layer or {}).get(rid)) or ((embed_layer or {}).get(rid) or 0.0) >= embed_hi
        if status in ("met", "partial") and not proven and not req.get("synthetic"):
            if not quote_in_text(llm.get("quote"), normalized_cv):
                llm["status"], llm["policy"] = "not_met", "unverified"
            elif req.get("explicit_keywords"):
                llm["status"] = "partial" if status == "met" else "not_met"
                llm["policy"] = "keywords_missing"
        adjusted[rid] = llm
    return adjusted


def combine(requirements, keyword_layer=None, embed_layer=None, llm_layer=None,
            embed_hi=0.68, embed_lo=0.55):
    """Merge the layers into per-requirement scores and a weighted percentage.

    keyword_layer: {req_id: [matched keywords]}
    embed_layer:   {req_id: cosine similarity}
    llm_layer:     {req_id: {"status": met|partial|not_met, "evidence": str}}
    """
    keyword_layer = keyword_layer or {}
    embed_layer = embed_layer or {}
    llm_layer = llm_layer or {}
    results, total_weight, gained = [], 0.0, 0.0
    mandatory_missing = False
    for req in requirements:
        rid = req["id"]
        weight = max(float(req.get("weight") or 1), 0.0)
        hits = keyword_layer.get(rid) or []
        sim = embed_layer.get(rid)
        llm = llm_layer.get(rid) or {}
        parts = {}
        if hits:
            parts["keyword"] = 1.0
        if sim is not None:
            parts["embedding"] = embed_score(sim, embed_hi, embed_lo)
        if llm.get("status") in STATUS_SCORE:
            parts["llm"] = STATUS_SCORE[llm["status"]]
        score = max(parts.values()) if parts else 0.0
        status = "met" if score >= 1.0 else ("partial" if score >= 0.5 else "not_met")
        sources = [k for k, v in parts.items() if v == score and score > 0]
        # Structured evidence only: the caller words it in the user's language.
        results.append({
            "id": rid,
            "name": req.get("name"),
            "sequence": req.get("sequence", 10),
            "weight": int(weight),
            "mandatory": bool(req.get("mandatory")),
            "score": score,
            "status": status,
            "sources": sources,
            "keywords_found": list(hits),
            "similarity": sim,
            "similarity_counts": bool(parts.get("embedding")),
            "ai_status": llm.get("status") if llm.get("status") in STATUS_SCORE else False,
            "ai_evidence": llm.get("evidence") or "",
            "ai_quote": llm.get("quote") or "",
            "ai_policy": llm.get("policy") or "",
        })
        total_weight += weight
        gained += weight * score
        if req.get("mandatory") and score < 0.5:
            mandatory_missing = True
    percent = int(round(100.0 * gained / total_weight)) if total_weight else 0
    return {"results": results, "percent": percent, "mandatory_missing": mandatory_missing}


def verdict(percent, threshold, mandatory_missing, partial_margin=25):
    if percent >= threshold and not mandatory_missing:
        return "match"
    if percent >= max(threshold - partial_margin, 0):
        return "partial"
    return "no_match"
