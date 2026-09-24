# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.tools import html2plaintext, str2bool

from ..tools import contacts as contacts_tool
from ..tools import experience, matcher, prompts, textfix
from ..tools.ollama_client import OllamaClient

_logger = logging.getLogger(__name__)

PARAM_PREFIX = "hr_recruitment_ai."
EXPERIENCE_REQ_ID = 0      # synthetic requirement id for the minimum-experience rule
EXPERIENCE_WEIGHT = 3

# Order matters for type detection: bool is checked before int.
DEFAULTS = {
    "ollama_url": "http://127.0.0.1:11434",
    "chat_model": "gemma3:4b",
    "embed_model": "bge-m3",
    "use_llm": True,
    "use_embeddings": True,
    "embed_on_cpu": True,
    "auto_run": True,
    # Measured with gemma3:4b: the second pass fixed no spelling and re-mangled names,
    # so it is off by default. Worth enabling only with a larger chat model.
    "proofread": False,
    "threshold": 80,
    "language": "ka",
    "overwrite_contacts": False,
    "disable_thinking": False,
    "llm_email": False,
    "num_ctx": 8192,
    "max_chars": 12000,
    "embed_hi": 0.68,
    "embed_lo": 0.55,
    "timeout": 600,
    "partial_margin": 25,
}


def _clamp_int(value, lo=0, hi=100):
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return lo


def _strip_delimiters(text):
    """Models sometimes echo the prompt's <<< >>> markers or wrap the answer in code fences."""
    text = (text or "").replace("<<<", "").replace(">>>", "")
    return text.strip().strip("`").strip()


def _as_float(value):
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _as_lines(value, limit=3):
    """Bullet list from the model's array: trimmed, near-duplicates removed, capped.
    Small models like to say the same gap three different ways."""
    if isinstance(value, (list, tuple)):
        items = [" ".join(str(v).split()) for v in value]
        items = textfix.dedupe_similar([i for i in items if i])
        return "\n".join("• %s" % item for item in items[:limit])
    return (value or "").strip() if isinstance(value, str) else ""


class HrRecruitmentAiEngine(models.AbstractModel):
    _name = "hr.recruitment.ai.engine"
    _description = "Local AI engine for CV screening (regex + embeddings + Ollama)"

    # ------------------------------------------------------------------ #
    #  Settings
    # ------------------------------------------------------------------ #
    @api.model
    def _settings(self):
        # Read straight from the table, not through the ORM's per-process cache: the
        # background worker runs for minutes outside any request, and a setting changed
        # meanwhile (from the UI, another worker process or a script) must apply to the
        # next CV. It is a dozen rows once per analysis.
        self.env.cr.execute(
            "SELECT key, value FROM ir_config_parameter WHERE key LIKE %s", (PARAM_PREFIX + "%",))
        stored = {key[len(PARAM_PREFIX):]: value for key, value in self.env.cr.fetchall()}
        out = {}
        for key, default in DEFAULTS.items():
            raw = stored.get(key)
            if raw in (None, False, ""):
                out[key] = default
                continue
            try:
                if isinstance(default, bool):
                    out[key] = str2bool(raw, default)
                elif isinstance(default, int):
                    out[key] = int(float(raw))
                elif isinstance(default, float):
                    out[key] = float(raw)
                else:
                    out[key] = str(raw).strip()
            except (ValueError, TypeError):
                out[key] = default
        return out

    @api.model
    def _client(self, settings=None):
        settings = settings or self._settings()
        return OllamaClient(settings["ollama_url"], timeout=settings["timeout"])

    @api.model
    def test_connection(self, ollama_url=None, chat_model=None, embed_model=None):
        """Return (ok, human message). Used by the Settings button."""
        s = self._settings()
        url = (ollama_url or s["ollama_url"]).strip()
        chat_model = (chat_model or s["chat_model"]).strip()
        embed_model = (embed_model or s["embed_model"]).strip()
        client = OllamaClient(url, timeout=15)
        try:
            names = client.list_models()
        except Exception as err:
            return False, _(
                "Cannot reach Ollama at %(url)s (%(err)s). Is Ollama installed and running?",
                url=url, err=err,
            )
        missing = [m for m in (chat_model, embed_model) if m and not client.has_model(m, names)]
        if missing:
            return False, _(
                "Ollama is running, but these models are not downloaded yet: %(models)s. "
                "Run in a terminal: %(cmd)s",
                models=", ".join(missing),
                cmd="  ;  ".join("ollama pull %s" % m for m in missing),
            )
        return True, _(
            "Ollama is reachable at %(url)s. Models ready: %(models)s.",
            url=url, models=", ".join(m for m in (chat_model, embed_model) if m),
        )

    # ------------------------------------------------------------------ #
    #  Core translations
    # ------------------------------------------------------------------ #
    @api.model
    def _load_core_translations(self):
        """Odoo's own Georgian translation of Recruitment is almost empty, and a module's
        i18n/ folder may only translate its own terms. This imports our translation of
        the core Recruitment screens (job dialog, applicant form, menus, stages) for every
        active language we ship a file for. Called from data on install and update."""
        from odoo.tools import file_path
        from odoo.tools.translate import TranslationImporter

        for lang_code, filename in (("ka_GE", "ka.po"),):
            active = self.env["res.lang"].sudo().search_count(
                [("code", "=", lang_code), ("active", "=", True)])
            if not active:
                continue
            try:
                path = file_path("hr_recruitment_ai/data/i18n_core/%s" % filename)
            except FileNotFoundError:
                continue
            importer = TranslationImporter(self.env.cr, verbose=False)
            importer.load_file(path, lang_code)
            importer.save(overwrite=True)
            _logger.info("hr_recruitment_ai: core %s translations imported", lang_code)
        self._localize_match_tag()
        return True

    @api.model
    def _localize_match_tag(self):
        """Applicant tags are not translatable in Odoo, so the "AI Match" tag is renamed once,
        to the language the AI texts are written in. A name the user changed is left alone."""
        tag = self.env.ref("hr_recruitment_ai.applicant_category_ai_match", raise_if_not_found=False)
        if not tag or tag.name != "AI Match":
            return
        language = self._settings()["language"]
        lang_code = {"ka": "ka_GE", "ru": "ru_RU"}.get(language)
        if not lang_code or not self.env["res.lang"].sudo().search_count(
                [("code", "=", lang_code), ("active", "=", True)]):
            return
        name = self.with_context(lang=lang_code)._ai_match_tag_name()
        if name and name != tag.name:
            tag.sudo().name = name

    @api.model
    def _ai_match_tag_name(self):
        return _("AI Match")

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #
    @api.model
    def _job_requirements(self, job):
        reqs = []
        if not job:
            return reqs
        for r in job.ai_requirement_ids:
            reqs.append({
                "id": r.id,
                "name": r.name,
                "keywords": matcher.split_keywords(r.keywords) or [r.name],
                # did the recruiter type keywords? then they are decisive (see the evidence policy)
                "explicit_keywords": bool(matcher.split_keywords(r.keywords)),
                "weight": r.weight or 1,
                "mandatory": bool(r.mandatory),
                "description": r.description or "",
                "sequence": r.sequence,
            })
        if job.ai_min_experience_years > 0:
            years = job.ai_min_experience_years
            reqs.append({
                "id": EXPERIENCE_REQ_ID,
                "name": _("Experience: at least %s years", years),
                "keywords": [],
                "weight": EXPERIENCE_WEIGHT,
                "mandatory": True,
                "description": _("Total relevant professional experience of %s years or more.", years),
                "sequence": 0,
                "synthetic": True,
            })
        return reqs

    @api.model
    def _word_evidence(self, results, language, cv_text):
        """Turn the matcher's structured evidence into text in the user's language."""
        status_labels = {
            "met": _("met"),
            "partial": _("partially met"),
            "not_met": _("not met"),
        }
        source_labels = {
            "keyword": _("keywords"),
            "embedding": _("semantic match"),
            "llm": _("AI assessment"),
        }
        for r in results:
            # A one-sentence explanation with a garbled word removed would read as nonsense:
            # drop it and let the report fall back to its neutral wording.
            if textfix.foreign_words(r.get("ai_evidence") or "", language, cv_text):
                r["ai_evidence"] = ""
            lines = []
            if r.get("keywords_found"):
                lines.append(_("Keywords found: %s", ", ".join(r["keywords_found"])))
            if r.get("similarity_counts") and r.get("similarity") is not None:
                lines.append(_("Semantic match: %s%%", int(round(r["similarity"] * 100))))
            if r.get("ai_status"):
                line = _("AI assessment: %s", status_labels[r["ai_status"]])
                if r.get("ai_evidence"):
                    line += " - " + r["ai_evidence"]
                lines.append(line)
            r["evidence"] = "\n".join(lines)
            r["sources_label"] = ", ".join(source_labels[s] for s in r.get("sources") or [])
        return results

    @api.model
    def _proofread(self, client, s, text, log):
        """Second model pass that fixes spelling in the chosen language."""
        if not text:
            return text
        try:
            system, user = prompts.proofread_prompts(s["language"], text)
            fixed, raw = client.chat_text(
                s["chat_model"], system, user, num_ctx=4096, num_predict=700,
                think=False if s["disable_thinking"] else None,
            )
            fixed = _strip_delimiters(fixed)
            if fixed and len(fixed) <= 3 * len(text):
                secs = (raw.get("total_duration") or 0) / 1e9
                log.append(_("Proofreading pass: done in %.1f s.", secs))
                return fixed
            log.append(_("Proofreading pass: answer discarded (empty or too long)."))
        except Exception as err:
            log.append(_("Proofreading pass skipped: %s", err))
        return text

    @api.model
    def _ensure_language(self, client, s, text, log, label):
        """Small models sometimes answer in English although another language was asked.
        Detect it from the alphabet and have the model translate its own text: a much
        easier task than the full analysis, so it succeeds reliably."""
        language = s["language"]
        if not text or not client or textfix.script_ratio(text, language) >= 0.35:
            return text
        try:
            system, user = prompts.translate_prompts(language, text)
            translated, raw = client.chat_text(
                s["chat_model"], system, user, num_ctx=4096, num_predict=900,
                think=False if s["disable_thinking"] else None,
            )
            translated = _strip_delimiters(translated)
            if translated and textfix.script_ratio(translated, language) >= 0.35:
                secs = (raw.get("total_duration") or 0) / 1e9
                log.append(_("%(label)s: written in the wrong language, translated in %(secs).1f s.",
                             label=label, secs=secs))
                return translated
        except Exception as err:
            log.append(_("%(label)s: translation skipped: %(err)s", label=label, err=err))
        return text

    @api.model
    def _clean_items(self, text, log, label, language, source_text, candidate_name, same_topic, limit=3):
        """Clean a bullet list item by item and return it as bullet text again."""
        bullet = chr(0x2022)
        items = [line.strip().lstrip(bullet + "-*").strip() for line in (text or "").splitlines()]
        items = [item for item in items if item]
        if not items:
            return ""
        intact = [i for i in items if not textfix.foreign_words(i, language, source_text)]
        dropped = len(items) - len(intact)
        if intact:
            items = intact
            if dropped:
                log.append(_("%(label)s: dropped %(n)s item(s) containing garbled words.",
                             label=label, n=dropped))
        else:  # everything was damaged: keep the items but strip the garbled words
            items = [textfix.strip_foreign_script(i, language, source_text) for i in items]
        ignore = [p for p in candidate_name.split() if p]
        before = len(items)
        items = textfix.dedupe_similar(items, same_topic=same_topic, ignore=ignore)
        if len(items) < before:
            log.append(_("%(label)s: merged %(n)s repeated item(s).", label=label, n=before - len(items)))
        if language == "ka" and candidate_name:
            items = [textfix.restore_names(i, candidate_name) for i in items]
        return "\n".join("%s %s" % (bullet, item) for item in items[:limit])

    @api.model
    def _clean_text(self, text, log, label, language="ka", source_text=""):
        """Deterministic removal of words from other alphabets/languages, except
        words that literally occur in the CV (real names and terms)."""
        bad = textfix.foreign_words(text, language, source_text) if text else []
        if not bad:
            return text
        cleaned = textfix.strip_foreign_script(text, language, source_text)
        log.append(_("%(label)s: removed %(n)s garbled word(s) from another alphabet.",
                     label=label, n=len(bad)))
        return cleaned

    # ------------------------------------------------------------------ #
    #  Analysis pipeline
    # ------------------------------------------------------------------ #
    @api.model
    def analyze(self, cv_text, job=None, recruiter_notes="", company_name=""):
        """Run the full pipeline on one CV. Returns a plain dict (no writes)."""
        s = self._settings()
        log = []
        cv_text = cv_text or ""
        cv_for_model = cv_text[: s["max_chars"]]
        if len(cv_text) > s["max_chars"]:
            log.append(_("CV text truncated to %s characters for the models.", s["max_chars"]))

        # 1. Deterministic contacts (always).
        contact = contacts_tool.extract_contacts(cv_text)
        found = [k for k in ("email", "phone", "linkedin", "name") if contact.get(k)]
        log.append(_("Regex contacts: %s", ", ".join(found) or _("none found")))

        # 2. Requirements + keyword layer.
        job = job.sudo() if job else None
        reqs = self._job_requirements(job)
        min_years = job.ai_min_experience_years if job else 0
        normalized = matcher.normalize(cv_text)
        keyword_layer = {}
        for r in reqs:
            hits = matcher.keyword_hits(normalized, r["keywords"])
            if hits:
                keyword_layer[r["id"]] = hits
        if reqs:
            log.append(_("Keyword layer: %(hit)s of %(total)s requirements found by keywords.",
                         hit=len(keyword_layer), total=len(reqs)))

        # 3. Ollama availability.
        want_ollama = s["use_llm"] or (s["use_embeddings"] and reqs)
        client = self._client(s) if want_ollama else None
        available = bool(client) and client.is_available()
        if want_ollama and not available:
            log.append(_("Ollama not reachable at %s - using keyword matching only.", s["ollama_url"]))

        # 4. Embedding layer (real requirements only, not the experience rule).
        embed_layer = {}
        embed_reqs = [r for r in reqs if not r.get("synthetic")]
        if embed_reqs and s["use_embeddings"] and available:
            try:
                chunks = matcher.chunk_text(cv_for_model)
                req_texts = [matcher.requirement_text(r) for r in embed_reqs]
                # On a small GPU the embedding model would evict the chat model
                # (and vice versa) on every CV; keeping it on the CPU avoids that.
                embed_options = {"num_gpu": 0} if s["embed_on_cpu"] else None
                vectors = client.embed(s["embed_model"], req_texts + chunks, options=embed_options)
                req_vecs, chunk_vecs = vectors[: len(req_texts)], vectors[len(req_texts):]
                if chunk_vecs:
                    for r, vec in zip(embed_reqs, req_vecs):
                        embed_layer[r["id"]] = matcher.best_similarity(vec, chunk_vecs)
                log.append(_("Embedding layer (%(model)s): compared against %(n)s CV chunks.",
                             model=s["embed_model"], n=len(chunks)))
            except Exception as err:
                log.append(_("Embedding layer skipped: %s", err))

        # 5. LLM layer.
        llm_layer, llm_out = {}, None
        if s["use_llm"] and available:
            try:
                system = prompts.system_prompt(s["language"], company_name, ai_email=s["llm_email"])
                user = prompts.build_user_prompt(
                    job_name=job.name if job else "",
                    job_description=html2plaintext(job.description) if job and job.description else "",
                    job_requirements_text=(job.requirements or "") if job else "",
                    requirements=reqs,
                    recruiter_notes=recruiter_notes or "",
                    cv_text=cv_for_model,
                    language_code=s["language"],
                    candidate_name=contact.get("name") or "",
                )
                # A broken answer (cut off, invalid JSON) is retried once with another seed
                # and a larger token budget; the second answer is as repeatable as the first.
                attempts = ((7, 2048), (13, 3072))
                for attempt, (seed, num_predict) in enumerate(attempts, 1):
                    try:
                        llm_out, raw = client.chat_json(
                            s["chat_model"], system, user,
                            schema=prompts.response_schema(),
                            num_ctx=s["num_ctx"], num_predict=num_predict, seed=seed,
                            think=False if s["disable_thinking"] else None,
                        )
                        break
                    except Exception as err:
                        if attempt == len(attempts):
                            raise
                        log.append(_("LLM layer: broken answer (%s), retrying once.", err))
                for item in llm_out.get("requirements") or []:
                    try:
                        rid = int(item.get("id"))
                    except (TypeError, ValueError):
                        continue
                    llm_layer[rid] = {
                        "status": item.get("status"),
                        "evidence": (item.get("evidence") or "").strip(),
                        "quote": (item.get("quote") or "").strip(),
                    }
                secs = (raw.get("total_duration") or 0) / 1e9
                log.append(_("LLM layer (%(model)s): answered in %(secs).1f s.",
                             model=s["chat_model"], secs=secs))
                if reqs:
                    log.append(_("The model's own rough estimate was %s%%. Information only: the match %% "
                                 "is calculated from the requirements.",
                                 _clamp_int(llm_out.get("overall_score"))))
            except Exception as err:
                _logger.warning("hr_recruitment_ai: LLM layer failed: %s", err)
                log.append(_("LLM layer skipped: %s", err))

        # 5a. Evidence policy: the model's opinion only counts when the CV backs it.
        if llm_layer:
            llm_layer = matcher.apply_evidence_policy(
                reqs, keyword_layer, embed_layer, llm_layer, normalized, s["embed_hi"])
            rejected = sum(1 for v in llm_layer.values() if v.get("policy") == "unverified")
            halved = sum(1 for v in llm_layer.values() if v.get("policy") == "keywords_missing")
            if rejected:
                log.append(_("Evidence check: %s claim(s) of the AI were not found in the CV and do not count.",
                             rejected))
            if halved:
                log.append(_("Evidence check: %s requirement(s) got half credit at most, because none of "
                             "their keywords is in the CV.", halved))

        # 5b. Minimum experience means experience RELEVANT to the job, which only the model can
        # judge (eight years of accounting are not three years of programming). The date
        # ranges of the CV are a sanity cap: the model cannot claim more years than the CV
        # covers. Without the model, all work years count (best effort).
        candidate = (llm_out or {}).get("candidate") or {}
        if min_years > 0:
            counted_years, spans = experience.estimate_years(cv_text, fields.Date.today().year)
            if counted_years:
                log.append(_("Experience from the dates in the CV: %(years)s years (%(spans)s).",
                             years=counted_years,
                             spans=", ".join("%s-%s" % span for span in spans)))
            if llm_out is not None or counted_years:
                if llm_out is not None:
                    years = _as_float(candidate.get("years_experience"))
                    if counted_years and years > counted_years + 1:
                        log.append(_("The AI read %(ai)s years of experience, but the dates in the CV only "
                                     "cover %(dates)s: the lower number is used.",
                                     ai="%g" % years, dates=counted_years))
                        years = float(counted_years)
                else:
                    years = float(counted_years)
                if years > 0:
                    status = "met" if years >= min_years else (
                        "partial" if years >= 0.7 * min_years else "not_met")
                    llm_layer[EXPERIENCE_REQ_ID] = {
                        "status": status,
                        "evidence": _("About %(y)s years of relevant experience found (required %(n)s).",
                                      y=("%g" % years), n=min_years),
                    }
                else:
                    llm_layer[EXPERIENCE_REQ_ID] = {
                        "status": "not_met",
                        "evidence": _("No experience duration could be read from the CV."),
                    }
            else:
                # Cannot judge without the model: do not penalise the applicant.
                reqs = [r for r in reqs if not r.get("synthetic")]
                log.append(_("Minimum experience not checked (LLM unavailable)."))

        # 6. Combine.
        if reqs:
            combined = matcher.combine(reqs, keyword_layer, embed_layer, llm_layer,
                                       s["embed_hi"], s["embed_lo"])
            percent = combined["percent"]
            mandatory_missing = combined["mandatory_missing"]
            results = self._word_evidence(combined["results"], s["language"], cv_text)
            scored = True
        elif llm_out is not None:
            percent = _clamp_int(llm_out.get("overall_score"))
            mandatory_missing, results, scored = False, [], True
            log.append(_("No structured requirements on the job - using the AI overall fit score."))
        else:
            percent, mandatory_missing, results, scored = 0, False, [], False
            log.append(_("Nothing to score: add requirements to the job or start Ollama."))

        threshold = job.ai_match_threshold if job and job.ai_match_threshold else s["threshold"]
        verdict = matcher.verdict(percent, threshold, mandatory_missing, s["partial_margin"]) if scored else False

        # 7. Text quality: proofreading pass + deterministic clean-up.
        llm_out = llm_out or {}
        lang = s["language"]
        name = contact.get("name") or ""
        summary = (llm_out.get("summary") or "").strip()
        strengths = _as_lines(llm_out.get("strengths"), limit=6)
        gaps = _as_lines(llm_out.get("gaps"), limit=6)
        if available:
            summary = self._ensure_language(client, s, summary, log, _("Summary"))
            strengths = self._ensure_language(client, s, strengths, log, _("Strengths"))
            gaps = self._ensure_language(client, s, gaps, log, _("Gaps"))
        if summary and s["proofread"] and lang != "en" and available:
            summary = self._proofread(client, s, summary, log)
        summary = self._clean_text(summary, log, _("Summary"), lang, cv_text)
        if lang == "ka" and name:
            # Small models mangle Latin names when writing Georgian; restore the CV spelling.
            summary = textfix.restore_names(summary, name)
        # Lists: an item damaged by a glitch word is dropped whole; several gaps about the
        # same technology ("no Kubernetes" said three ways) collapse into the first one.
        strengths = self._clean_items(strengths, log, _("Strengths"), lang, cv_text, name, same_topic=False)
        gaps = self._clean_items(gaps, log, _("Gaps"), lang, cv_text, name, same_topic=True)
        email_subject = (llm_out.get("email_subject") or "").strip()
        email_body = (llm_out.get("email_body") or "").strip()
        if not s["llm_email"]:
            # The deterministic, personalised template is used instead (see hr_applicant).
            email_subject, email_body = "", ""
        else:
            email_body = self._clean_text(email_body, log, _("E-mail"), lang, cv_text)

        return {
            "settings": s,
            "log": log,
            "contacts": contact,
            "candidate": candidate,
            "requirements": results,
            "percent": percent,
            "mandatory_missing": mandatory_missing,
            "threshold": threshold,
            "verdict": verdict,
            "scored": scored,
            "llm_used": bool(llm_out),
            "llm_score": _clamp_int(llm_out.get("overall_score")) if llm_out else 0,
            "summary": summary,
            "strengths": strengths,
            "gaps": gaps,
            "email_subject": email_subject,
            "email_body": email_body,
        }
