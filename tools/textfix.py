# -*- coding: utf-8 -*-
"""Clean-up for model-written text.

Small local models sometimes drop a word from another script or language
(Greek, Tamil, Vietnamese, Cyrillic, Portuguese...) into their output. The rule
is language-aware and deterministic: a word containing letters outside
"basic Latin + the target language's script" is removed, unless that exact word
occurs in the CV (then it is a real name or term the model copied).
"""
import re

# Code-point ranges are built with chr() so this file contains no exotic literals.
_COMMON_RANGES = [
    (0x0009, 0x000D),   # tab, newlines
    (0x0020, 0x007E),   # basic Latin (letters, digits, punctuation)
    (0x00A0, 0x00BF),   # Latin-1 punctuation and symbols
    (0x2000, 0x206F),   # general punctuation (quotes, dashes, bullets)
    (0x20A0, 0x20CF),   # currency symbols
    (0x2100, 0x22FF),   # letterlike symbols, arrows, math
    (0x25A0, 0x25FF),   # geometric shapes (bullets)
]
_SCRIPT_RANGES = {
    "ka": [(0x10A0, 0x10FF), (0x1C90, 0x1CBF), (0x2D00, 0x2D2F)],   # Georgian
    "ru": [(0x0400, 0x04FF)],                                        # Cyrillic
    "en": [],
}
_TRAILING_PUNCT = ",.;:!?"
_EDGE_CHARS = " \t\r\n,.;:!?()[]{}\"'" + "".join(chr(c) for c in (0x2018, 0x2019, 0x201C, 0x201D, 0x201E, 0x00AB, 0x00BB))


def _char_class(ranges, negate=False):
    body = "".join("%s-%s" % (re.escape(chr(a)), re.escape(chr(b))) for a, b in ranges)
    return "[%s%s]" % ("^" if negate else "", body)


_UNEXPECTED = {
    lang: re.compile(_char_class(_COMMON_RANGES + ranges, negate=True))
    for lang, ranges in _SCRIPT_RANGES.items()
}


def _unexpected_re(language):
    return _UNEXPECTED.get(language) or _UNEXPECTED["en"]


_BASIC_LATIN_RE = re.compile("[A-Za-z]")
_GEORGIAN_RE = re.compile(_char_class([(0x10A0, 0x10FF)]))


def _is_mashup(core):
    """Georgian and Latin letters glued together in one word ("moXYZ" with a Georgian
    prefix). Legit mixed forms always put a hyphen between the scripts: "Python-" + suffix."""
    return any(_GEORGIAN_RE.search(part) and _BASIC_LATIN_RE.search(part) for part in core.split("-"))


def _is_invented_stem(core):
    """A lowercase English stem with a Georgian ending ("demonstr-" + suffix). Real mixed
    forms use a proper noun or acronym (Python-, API-, Odoo-) or a word from the CV; the
    caller checks the CV, here we only look at the shape."""
    parts = core.split("-")
    if len(parts) < 2 or not _GEORGIAN_RE.search(parts[-1]):
        return False
    stem = parts[0]
    return bool(stem) and stem.isascii() and stem.isalpha() and stem.islower()


def _is_glitch(token, language, source_lower):
    core = token.strip(_EDGE_CHARS)
    suspicious = bool(_unexpected_re(language).search(token))
    if language == "ka" and not suspicious:
        if _is_mashup(core):
            suspicious = True
        elif _is_invented_stem(core):
            # Legit when the stem itself is a word of the CV ("e-mail-" + suffix, "backend-" ...).
            return core.split("-")[0].lower() not in source_lower
    if not suspicious:
        return False
    # A word the model copied from the CV (a real name or term) is legitimate.
    return not (core and core.lower() in source_lower)


def script_ratio(text, language):
    """Share of the text's letters that belong to the target language's script (1.0 when the
    language has no script of its own, e.g. English)."""
    ranges = _SCRIPT_RANGES.get(language) or []
    if not ranges:
        return 1.0
    letters = [ch for ch in (text or "") if ch.isalpha()]
    if not letters:
        return 1.0
    own = sum(1 for ch in letters if any(a <= ord(ch) <= b for a, b in ranges))
    return own / float(len(letters))


def foreign_words(text, language="ka", source_text=""):
    source_lower = (source_text or "").lower()
    return [w for w in re.split(r"\s+", text or "") if w and _is_glitch(w, language, source_lower)]


def has_foreign_script(text, language="ka", source_text=""):
    return bool(foreign_words(text, language, source_text))


# --------------------------------------------------------------------------- #
#  Near-duplicate list items ("no Kubernetes experience" said three ways)
# --------------------------------------------------------------------------- #
def _stems(text):
    """Crude language-neutral stems: lowercase words cut to 5 letters, short words dropped.
    Good enough to see that two sentences share most of their content."""
    words = re.findall(r"\w+", (text or "").lower())
    return {w[:5] for w in words if len(w) >= 3}


def _latin_terms(text, ignore=()):
    """Technology-like Latin terms of an item ("Kubernetes", "Docker"), lowercased."""
    skip = {w.lower() for w in ignore}
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.]{3,}", text or "")} - skip


def dedupe_similar(items, threshold=0.5, same_topic=False, ignore=()):
    """Keep the first of several items that say the same thing.

    Two items are duplicates when their word stems overlap (Jaccard >= threshold) or,
    with same_topic=True (used for gaps), when they name the same Latin term: "no
    Kubernetes experience" and "the profile does not mention Kubernetes" are one gap.
    `ignore` lists words that do not define a topic, e.g. the candidate's name.
    """
    kept, kept_stems, kept_terms = [], [], []
    for item in items:
        stems = _stems(item)
        if not stems:
            continue
        terms = _latin_terms(item, ignore) if same_topic else set()
        duplicate = False
        for other_stems, other_terms in zip(kept_stems, kept_terms):
            overlap = len(stems & other_stems) / float(len(stems | other_stems))
            if overlap >= threshold or (terms and terms & other_terms):
                duplicate = True
                break
        if not duplicate:
            kept.append(item)
            kept_stems.append(stems)
            kept_terms.append(terms)
    return kept


# --------------------------------------------------------------------------- #
#  Candidate-name repair (Georgian output, Latin name in the CV)
# --------------------------------------------------------------------------- #
# Latin skeleton of the 33 Mkhedruli letters U+10D0..U+10F0, in code-point order.
# Aspirated/ejective pairs collapse to one Latin letter so both spellings match.
_GEO_ROMAN = ["a", "b", "g", "d", "e", "v", "z", "t", "i", "k", "l", "m", "n", "o", "p", "zh",
              "r", "s", "t", "u", "p", "k", "gh", "k", "sh", "ch", "ts", "dz", "ts", "ch", "kh",
              "j", "h"]
_GEO_FIRST, _GEO_LAST = 0x10D0, 0x10F0


# Case endings / postpositions that may legitimately follow a name ("Nino-ს", "Beridze-სთან").
# Written in the Latin skeleton and converted by code point, so this file has no typing slips.
_SUFFIX_LETTERS = {"a": 0x10D0, "d": 0x10D3, "e": 0x10D4, "v": 0x10D5, "z": 0x10D6, "t": 0x10D7,
                   "i": 0x10D8, "m": 0x10DB, "n": 0x10DC, "s": 0x10E1, "sh": 0x10E8}


def _geo_suffix(spec):
    out, i = "", 0
    while i < len(spec):
        key = spec[i:i + 2] if spec[i:i + 2] in _SUFFIX_LETTERS else spec[i]
        out += chr(_SUFFIX_LETTERS[key])
        i += len(key)
    return out


_NAME_SUFFIXES = {_geo_suffix(s) for s in (
    "s", "is", "m", "i", "im", "it", "ad", "tan", "stan", "ze", "shi", "dan", "idan",
    "tvis", "istvis", "sa", "isa", "n", "an",
)} | {""}


def _allowed_distance(skeleton):
    n = len(skeleton)
    return 0 if n <= 4 else 1 if n == 5 else 2 if n <= 7 else 3


def _is_georgian_word(word):
    return bool(word) and all(_GEO_FIRST <= ord(ch) <= _GEO_LAST for ch in word)


def _romanize_georgian(word):
    return "".join(_GEO_ROMAN[ord(ch) - _GEO_FIRST] for ch in word)


def _latin_skeleton(name):
    s = name.lower().replace("'", "")
    for a, b in (("ph", "p"), ("th", "t"), ("q", "k"), ("w", "v"), ("x", "kh"), ("y", "i")):
        s = s.replace(a, b)
    return re.sub(r"c(?!h)", "k", s)


def _edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def restore_names(text, candidate_name):
    """Replace mangled Georgian transliterations of the candidate's (Latin) name with the
    exact spelling from the CV. "ბერძიძეს" -> "Beridze-ს". Deterministic, no model."""
    if not text or not candidate_name:
        return text
    parts = [p for p in re.split(r"[\s\-]+", candidate_name) if len(p) >= 3 and p.isascii() and p.isalpha()]
    if not parts:
        return text
    skeletons = [(p, _latin_skeleton(p)) for p in parts]
    out = []
    for token in re.split(r"(\s+)", text):
        core = token.strip(_EDGE_CHARS)
        if len(core) < 3 or not _is_georgian_word(core):
            out.append(token)
            continue
        best = None  # (distance, suffix length, part, suffix): smallest wins
        for part, skel in skeletons:
            allowed = _allowed_distance(skel)
            for g_len in range(2, len(core) + 1):
                rom = _romanize_georgian(core[:g_len])
                if not rom or rom[0] != skel[0]:
                    break
                suffix = core[g_len:]
                if suffix not in _NAME_SUFFIXES:
                    continue
                dist = _edit_distance(rom, skel)
                if dist <= allowed and (best is None or (dist, len(suffix)) < best[:2]):
                    best = (dist, len(suffix), part, suffix)
        if best is None:
            out.append(token)
            continue
        part, suffix = best[2], best[3]
        out.append(token.replace(core, part + ("-" + suffix if suffix else ""), 1))
    return "".join(out)


def strip_foreign_script(text, language="ka", source_text=""):
    """Remove glitch words; keep their trailing punctuation so sentences stay readable."""
    if not text:
        return text
    source_lower = (source_text or "").lower()
    out = []
    for token in re.split(r"(\s+)", text):
        if not token or token.isspace() or not _is_glitch(token, language, source_lower):
            out.append(token)
            continue
        tail = ""
        while token and token[-1] in _TRAILING_PUNCT:
            tail = token[-1] + tail
            token = token[:-1]
        out.append(tail)
    result = "".join(out)
    result = re.sub(r"[ \t]{2,}", " ", result)
    result = re.sub(r"[ \t]+([,.;:!?])", r"\1", result)
    # A removed first word must not leave its punctuation at the start of a line.
    result = re.sub(r"(?m)^[ \t]*[,;:]+[ \t]*", "", result)
    return result.strip()
