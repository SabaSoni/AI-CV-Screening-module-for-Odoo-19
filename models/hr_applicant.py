# -*- coding: utf-8 -*-
import logging
import threading
import time

import psycopg2
from markupsafe import Markup

from odoo import SUPERUSER_ID, api, fields, models, Command, _
from odoo.exceptions import UserError
from odoo.modules import module as odoo_module
from odoo.tools import config, email_normalize, html2plaintext, plaintext2html

try:  # Odoo 19
    from odoo.orm.registry import Registry
except ImportError:  # pragma: no cover - older layouts
    from odoo.modules.registry import Registry

from ..tools import prompts
from ..tools.cv_text import extract_text, looks_like_cv_file

_logger = logging.getLogger(__name__)

AI_STATES = [
    ("none", "Not analyzed"),
    ("pending", "Queued"),
    ("done", "Analyzed"),
    ("error", "Error"),
]
AI_VERDICTS = [
    ("match", "Match"),
    ("partial", "Partial match"),
    ("no_match", "No match"),
]
JUNK_VALUES = {"", "null", "none", "n/a", "na", "unknown", "-", "not provided", "not available"}
# PostgreSQL advisory-lock namespace of this module: one analysis per applicant at a time.
AI_LOCK_NAMESPACE = 87421
# The "Analyze" button waits this long for the background worker, then tells the user to
# refresh. It must stay well below Odoo's limit_time_real (120 s by default): a request or a
# scheduled job that runs longer makes the threaded server RESTART ITSELF.
BUTTON_WAIT_SECONDS = 90

# ---------------------------------------------------------------------------
#  Background worker
# ---------------------------------------------------------------------------
# A local model needs 15 s per CV on a GPU, minutes on a CPU. Odoo kills anything that runs
# longer than limit_time_real inside a request or a scheduled job, so the analyses run in
# the module's own daemon thread (exempt from that limit), one CV after another. The
# scheduled job is only a watchdog that (re)starts this worker when CVs are waiting.
_WORKERS = {}
_WORKERS_GUARD = threading.Lock()


def _ai_worker_loop(dbname):
    threading.current_thread().dbname = dbname
    _logger.info("hr_recruitment_ai: background worker started (%s)", dbname)
    skipped = set()  # applicants another process is analysing right now
    try:
        while True:
            # Pick up what other processes changed (settings, translations, module updates):
            # outside a web request or a scheduled job nobody else refreshes the caches.
            registry = Registry(dbname).check_signaling()
            with registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                applicant = env["hr.applicant"].search(
                    [("ai_state", "=", "pending"), ("id", "not in", list(skipped))],
                    limit=1, order="write_date asc, id asc")
                if not applicant:
                    break
                if not applicant._ai_run(raise_on_error=False):
                    skipped.add(applicant.id)
    except Exception:
        _logger.exception("hr_recruitment_ai: background worker crashed (%s)", dbname)
    finally:
        _logger.info("hr_recruitment_ai: background worker finished (%s)", dbname)


def _ai_start_worker(dbname):
    with _WORKERS_GUARD:
        worker = _WORKERS.get(dbname)
        if worker and worker.is_alive():
            return False
        worker = threading.Thread(target=_ai_worker_loop, args=(dbname,),
                                  name="hr_recruitment_ai.worker.%s" % dbname, daemon=True)
        _WORKERS[dbname] = worker
        worker.start()
        return True


def _clean(value):
    if value is None:
        return ""
    value = str(value).strip()
    return "" if value.lower() in JUNK_VALUES else value


class HrApplicant(models.Model):
    _inherit = "hr.applicant"

    ai_state = fields.Selection(
        AI_STATES, string="AI Status", default="none", index=True, copy=False,
    )
    ai_score = fields.Integer(string="AI Match %", copy=False, aggregator="avg")
    ai_llm_score = fields.Integer(
        string="Model's own estimate %", copy=False,
        help="The language model's rough overall guess. Information only: it is not part of "
             "the match %, which is calculated from the job's requirements. It is only used "
             "as the score when the job has no requirements at all.",
    )
    ai_verdict = fields.Selection(AI_VERDICTS, string="AI Verdict", copy=False, index=True)
    ai_threshold = fields.Integer(string="Threshold used (%)", copy=False)
    ai_mandatory_missing = fields.Boolean(string="Missing a must-have", copy=False)
    ai_summary = fields.Text(string="AI Summary", copy=False)
    ai_strengths = fields.Text(string="Strengths", copy=False)
    ai_gaps = fields.Text(string="Gaps", copy=False)
    ai_requirement_result_ids = fields.One2many(
        "hr.applicant.requirement.result", "applicant_id",
        string="Requirement checks", copy=False,
    )
    ai_email_subject = fields.Char(string="Email subject", copy=False)
    ai_email_body = fields.Text(string="Email draft", copy=False)
    ai_cv_text = fields.Text(string="Extracted CV text", copy=False)
    ai_engine_log = fields.Text(string="Engine log", copy=False)
    ai_last_run = fields.Datetime(string="Last AI run", copy=False)
    ai_name_is_placeholder = fields.Boolean(
        string="Name is a placeholder", copy=False,
        help="Set for applicants created from an uploaded file: the name is only the file "
             "name, so the AI replaces it with the name found in the CV.",
    )
    # Hand upload straight from the form. Never stored: the file becomes a normal
    # attachment of the applicant (see create/write), exactly like a CV that came by e-mail.
    ai_cv_upload = fields.Binary(string="Upload a CV", store=False)
    ai_cv_upload_name = fields.Char(string="CV file name", store=False)
    # The visual report (ai_report_html) lives in hr_applicant_report.py.

    # ------------------------------------------------------------------ #
    #  Buttons
    # ------------------------------------------------------------------ #
    def _ai_notify(self, title, message, kind="info", sticky=False):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": title, "message": message, "type": kind, "sticky": sticky,
                       "next": {"type": "ir.actions.act_window_close"}},
        }

    def action_ai_analyze(self):
        """Hand the CV(s) to the background worker and wait a little for the result. The
        analysis itself never runs inside this request (see BUTTON_WAIT_SECONDS)."""
        if not self:
            return True
        for applicant in self:
            if not applicant._ai_cv_attachments():
                raise UserError(_(
                    "No readable CV found on this applicant. Attach a PDF or DOCX; "
                    "scanned images need OCR first."
                ))
        started = fields.Datetime.now()
        self._ai_mark_pending(force=True)
        self.env.cr.commit()              # the worker uses its own cursor: it must see the queue
        _ai_start_worker(self.env.cr.dbname)
        if len(self) > 1:
            return self._ai_notify(_("Analysis started"), _(
                "%s CV(s) queued. The analysis runs in the background.", len(self)))

        deadline = time.time() + BUTTON_WAIT_SECONDS
        while time.time() < deadline:
            time.sleep(2)
            self.env.cr.commit()          # nothing pending here: only takes a fresh snapshot
            self.env.invalidate_all()
            if self.ai_state in ("done", "error") and self.ai_last_run and self.ai_last_run >= started:
                break
        if self.ai_state == "done":
            labels = dict(self._fields["ai_verdict"]._description_selection(self.env))
            return self._ai_notify(
                _("AI screening finished"),
                _("%(name)s: %(score)s%% match (%(verdict)s).",
                  name=self.partner_name or self.display_name, score=self.ai_score,
                  verdict=labels.get(self.ai_verdict) or _("not scored")),
                "success" if self.ai_verdict == "match" else "info")
        if self.ai_state == "error":
            return self._ai_notify(_("The last analysis failed"),
                                   _("Details are on the AI tab."), "danger", sticky=True)
        return self._ai_notify(_("Analysis started"), _(
            "The analysis is still running in the background (a slow computer or a big model needs "
            "a few minutes). Refresh this page in a moment to see the result."))

    def action_ai_open_contact_email(self):
        """Open the mail composer pre-filled with the AI-drafted invitation."""
        self.ensure_one()
        if not self.email_from:
            raise UserError(_("This applicant has no email address yet."))
        subject = self.ai_email_subject or _("Your application - %s",
                                             self.job_id.name or self.company_id.name)
        ctx = dict(
            self.env.context,
            default_model="hr.applicant",
            default_res_ids=self.ids,
            default_composition_mode="comment",
            default_subject=subject,
            default_body=plaintext2html(self.ai_email_body or ""),
            force_email=True,
            mail_post_autofollow=True,
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Contact candidate"),
            "res_model": "mail.compose.message",
            "view_mode": "form",
            "target": "new",
            "context": ctx,
        }

    # ------------------------------------------------------------------ #
    #  Queue / cron
    # ------------------------------------------------------------------ #
    def _ai_mark_pending(self, force=False):
        """Queue applicants for the scheduler (respects the 'auto run' setting)."""
        if not self:
            return
        if not force and not self.env["hr.recruitment.ai.engine"]._settings()["auto_run"]:
            return
        todo = self.sudo().filtered(lambda a: a.ai_state != "pending")
        if todo:
            todo.write({"ai_state": "pending"})
        self._ai_trigger_queue()

    @api.model
    def _cron_ai_process_pending(self, limit=5):
        """Scheduled watchdog (every two minutes): it does NOT analyse anything itself, it only
        makes sure the background worker is running while CVs are waiting. `limit` is kept
        for databases whose scheduled action still passes it."""
        if self.search_count([("ai_state", "=", "pending")]):
            self._ai_trigger_queue()
        return True

    @api.model
    def _ai_trigger_queue(self):
        """Start the background worker once the current transaction is committed (the worker
        has its own cursor and must be able to see what was just queued)."""
        dbname = self.env.cr.dbname
        if config["test_enable"] or getattr(odoo_module, "current_test", False):
            return  # automated tests call _ai_run directly (the same flag core mail code checks)
        self.env.cr.postcommit.add(lambda: _ai_start_worker(dbname))

    # ------------------------------------------------------------------ #
    #  Hand upload
    # ------------------------------------------------------------------ #
    @api.model_create_multi
    def create(self, vals_list):
        uploads = [(vals.pop("ai_cv_upload", None), vals.pop("ai_cv_upload_name", None)) for vals in vals_list]
        applicants = super().create(vals_list)
        for applicant, (data, name) in zip(applicants, uploads):
            if data:
                applicant._ai_attach_cv(data, name)
        return applicants

    def write(self, vals):
        data, name = vals.pop("ai_cv_upload", None), vals.pop("ai_cv_upload_name", None)
        res = super().write(vals) if vals else True
        if data:
            for applicant in self:
                applicant._ai_attach_cv(data, name)
        return res

    def _ai_attach_cv(self, data, name):
        """Store an uploaded CV as an attachment of the applicant and queue the analysis."""
        self.ensure_one()
        self.env["ir.attachment"].create({
            "name": name or _("CV"),
            "datas": data,
            "res_model": "hr.applicant",
            "res_id": self.id,
        })
        self._ai_mark_pending(force=True)
        self._ai_trigger_queue()

    # ------------------------------------------------------------------ #
    #  Pipeline
    # ------------------------------------------------------------------ #
    def _ai_cv_attachments(self):
        self.ensure_one()
        attachments = self.env["ir.attachment"].sudo().search(
            [("res_model", "=", "hr.applicant"), ("res_id", "=", self.id)],
            order="create_date desc",
        )
        cv_like = attachments.filtered(lambda a: looks_like_cv_file(a.name, a.mimetype))
        main = self.message_main_attachment_id
        if main and main in cv_like:
            cv_like = main | (cv_like - main)
        return cv_like

    def _ai_collect_cv_text(self, log):
        texts = []
        for att in self._ai_cv_attachments()[:3]:
            note = ""
            try:
                text, note = extract_text(att.name, att.raw or b"", att.mimetype)
            except Exception as err:
                text, note = "", str(err)
            if not text and att.index_content and len(att.index_content) > 80:
                text = att.index_content
                note = (note + " " if note else "") + _("Used Odoo's indexed text instead.")
            log.append("%s: %s" % (att.name, _("%s characters", len(text)) + (" - " + note if note else "")))
            if text:
                texts.append("### %s\n%s" % (att.name, text))
        return "\n\n".join(texts)

    def _ai_run(self, raise_on_error=False, wait_seconds=0):
        """Run the screening with texts rendered in the configured AI language, so
        notes, activities and logs are the same whether a user clicked the button or
        the scheduler (system user, English) processed the queue.

        Returns True when this call (or a run it waited for) produced a result, False when
        the applicant is being analysed by someone else and we did not wait."""
        self.ensure_one()
        language = self.env["hr.recruitment.ai.engine"]._settings()["language"]
        lang_code = {"ka": "ka_GE", "en": "en_US", "ru": "ru_RU"}.get(language)
        record = self
        if lang_code and lang_code != self.env.lang and \
                self.env["res.lang"].sudo().search_count([("code", "=", lang_code), ("active", "=", True)]):
            record = self.with_context(lang=lang_code)
        return record._ai_run_inner(raise_on_error=raise_on_error, wait_seconds=wait_seconds)

    # -- one analysis per applicant at a time ---------------------------------
    def _ai_try_lock(self, wait_seconds=0):
        """Session-level advisory lock: it survives the commits made during a run and,
        unlike a row lock, never blocks a user who edits the applicant meanwhile."""
        deadline = time.time() + wait_seconds
        while True:
            self.env.cr.execute("SELECT pg_try_advisory_lock(%s, %s)", (AI_LOCK_NAMESPACE, self.id))
            if self.env.cr.fetchone()[0]:
                return True
            if time.time() >= deadline:
                return False
            time.sleep(1.0)

    def _ai_unlock(self):
        try:
            self.env.cr.execute("SELECT pg_advisory_unlock(%s, %s)", (AI_LOCK_NAMESPACE, self.id))
        except psycopg2.Error:  # aborted transaction: clear it first, then release
            self.env.cr.rollback()
            self.env.cr.execute("SELECT pg_advisory_unlock(%s, %s)", (AI_LOCK_NAMESPACE, self.id))

    def _ai_run_inner(self, raise_on_error=False, wait_seconds=0):
        self.ensure_one()
        started = fields.Datetime.now()
        if not self._ai_try_lock(wait_seconds):
            if raise_on_error:
                raise UserError(_("This CV is still being analysed in the background. "
                                  "Refresh the page in a moment to see the result."))
            return False
        try:
            if wait_seconds:
                # We may have waited for another run of this applicant: look at fresh data and
                # do not repeat an analysis that has just finished.
                self.env.cr.commit()
                self.env.invalidate_all()
                if self.ai_state == "done" and self.ai_last_run and self.ai_last_run >= started:
                    return True
            return self._ai_run_locked(raise_on_error)
        finally:
            self._ai_unlock()

    def _ai_run_locked(self, raise_on_error):
        cr = self.env.cr
        log = []
        try:
            cv_files = self._ai_cv_attachments()
            cv_text = self._ai_collect_cv_text(log)
            if not cv_text.strip():
                raise UserError(_(
                    "No readable CV found on this applicant. Attach a PDF or DOCX; "
                    "scanned images need OCR first."
                ))
            result = self.env["hr.recruitment.ai.engine"].analyze(
                cv_text,
                job=self.job_id,
                recruiter_notes=html2plaintext(self.applicant_notes or ""),
                company_name=self.company_id.name or self.env.company.name,
            )
            log.extend(result["log"])
        except Exception as err:
            self._ai_fail(err, log)
            if raise_on_error:
                raise UserError(_("The analysis failed: %s", err)) from err
            return True

        # The model call can take minutes. The result is written in a fresh, short
        # transaction, so that anything saved on this applicant meanwhile (a user's edit, the
        # upload that queued it) cannot make our write fail with a serialization error.
        for attempt in (1, 2, 3):
            try:
                cr.commit()                      # nothing pending: only ends the old snapshot
                self.env.invalidate_all()
                self._ai_apply_results(result, cv_text, log)
                if self._ai_cv_attachments() != cv_files:
                    # a CV was added while we were analysing: look at it in the next round
                    self.sudo().write({"ai_state": "pending"})
                    self._ai_trigger_queue()
                cr.commit()
                return True
            except psycopg2.errors.SerializationFailure as err:
                cr.rollback()
                self.env.invalidate_all()
                if attempt == 3:
                    self._ai_fail(err, log)
                    if raise_on_error:
                        raise UserError(_("The analysis failed: %s", err)) from err
                    return True
                time.sleep(0.5 * attempt)
            except Exception as err:
                self._ai_fail(err, log)
                if raise_on_error:
                    raise UserError(_("The analysis failed: %s", err)) from err
                return True

    def _ai_fail(self, err, log):
        """Record a failed analysis; must be called from inside the except block."""
        _logger.exception("hr_recruitment_ai: screening failed for applicant %s", self.id)
        cr = self.env.cr
        cr.rollback()
        self.env.invalidate_all()
        try:
            self.sudo().write({
                "ai_state": "error",
                "ai_engine_log": "\n".join(log + [_("ERROR: %s", err)]),
                "ai_last_run": fields.Datetime.now(),
            })
            cr.commit()
        except psycopg2.Error:
            cr.rollback()
            self.env.invalidate_all()

    def _ai_apply_results(self, res, cv_text, log):
        self.ensure_one()
        previous = (self.ai_state, self.ai_score, self.ai_verdict)
        settings = res["settings"]
        overwrite = settings["overwrite_contacts"]
        contact = res["contacts"]
        candidate = res["candidate"] or {}
        job = self.job_id
        verdict = res["verdict"]

        vals = {
            "ai_state": "done",
            "ai_last_run": fields.Datetime.now(),
            "ai_score": res["percent"],
            "ai_llm_score": res["llm_score"],
            "ai_verdict": verdict,
            "ai_threshold": res["threshold"],
            "ai_mandatory_missing": res["mandatory_missing"],
            "ai_summary": res["summary"],
            "ai_strengths": res["strengths"],
            "ai_gaps": res["gaps"],
            "ai_cv_text": cv_text,
        }
        if not res["llm_used"] and self.ai_summary:
            # The AI layer failed this time (Ollama down, broken answer): the scores above are
            # keyword-based and fresh, but do not wipe the texts of the previous, complete run.
            vals.pop("ai_summary"), vals.pop("ai_strengths"), vals.pop("ai_gaps")
            log.append(_("The AI text of the previous analysis was kept."))

        # --- Contact details: regex first (never hallucinates), LLM as backup.
        filled = []
        email = contact.get("email") or _clean(candidate.get("email"))
        if email and email_normalize(email) and (overwrite or not self.email_from):
            vals["email_from"] = email
            filled.append(_("email"))
        phone = contact.get("phone") or _clean(candidate.get("phone"))
        if phone and (overwrite or not self.partner_phone):
            vals["partner_phone"] = phone[:32]
            filled.append(_("phone"))
        linkedin = contact.get("linkedin") or _clean(candidate.get("linkedin"))
        if linkedin and (overwrite or not self.linkedin_profile):
            if not linkedin.lower().startswith("http"):
                linkedin = "https://" + linkedin
            vals["linkedin_profile"] = linkedin
            filled.append("LinkedIn")
        name = _clean(candidate.get("full_name")) or contact.get("name")
        current_name = (self.partner_name or "").strip()
        placeholder = (self.ai_name_is_placeholder or not current_name or "@" in current_name
                       or current_name.lower() in ("new", "applicant"))
        if name and (overwrite or placeholder):
            vals["partner_name"] = name
            vals["ai_name_is_placeholder"] = False
            filled.append(_("name"))
        if "email_from" in vals and not (vals.get("partner_name") or current_name):
            # Odoo needs a contact name before it can link the email to a partner.
            vals["partner_name"] = vals["email_from"].split("@")[0]

        # --- Contact e-mail draft (LLM text, or a deterministic template).
        subject, body = res["email_subject"], res["email_body"]
        if not body:
            subject, body = prompts.fallback_email(
                settings["language"],
                vals.get("partner_name") or current_name,
                job.name if job else "",
                self.company_id.name or self.env.company.name,
            )
        vals["ai_email_subject"], vals["ai_email_body"] = subject, body

        # --- Flagging: tag, priority, stage.
        auto = (not job) or job.ai_auto_fill
        tag = self.env.ref("hr_recruitment_ai.applicant_category_ai_match", raise_if_not_found=False)
        if tag:
            vals["categ_ids"] = [Command.link(tag.id)] if verdict == "match" else [Command.unlink(tag.id)]
        if auto and self.priority == "0":
            if verdict == "match":
                vals["priority"] = "2"
            elif verdict == "partial":
                vals["priority"] = "1"
        if auto and verdict == "match" and job and job.ai_match_stage_id \
                and job.ai_match_stage_id != self.stage_id:
            if not self.stage_id or self.stage_id == job._get_first_stage():
                vals["stage_id"] = job.ai_match_stage_id.id

        log.append(_("Auto-filled: %s", ", ".join(filled) or _("nothing (fields were already set)")))
        self.write(vals)

        # --- Per-requirement lines (technical records, written with sudo).
        Result = self.env["hr.applicant.requirement.result"].sudo()
        Result.search([("applicant_id", "=", self.id)]).unlink()
        if res["requirements"]:
            Result.create([{
                "applicant_id": self.id,
                # Synthetic lines (e.g. minimum experience) have no job requirement record.
                "requirement_id": r["id"] if r["id"] > 0 else False,
                "name": r["name"],
                "sequence": r.get("sequence", 10),
                "weight": r["weight"],
                "mandatory": r["mandatory"],
                "status": r["status"],
                "score": r["score"],
                "sources": r.get("sources_label") or "",
                "evidence": r.get("evidence") or "",
                "keywords_found": ", ".join(r.get("keywords_found") or []),
                "similarity": r.get("similarity") or 0.0,
                "similarity_counts": bool(r.get("similarity_counts")),
                "ai_status": r.get("ai_status") or False,
                "ai_evidence": r.get("ai_evidence") or "",
                "ai_quote": r.get("ai_quote") or "",
                "ai_policy": r.get("ai_policy") or "",
            } for r in res["requirements"]])

        if auto and verdict == "match":
            self._ai_schedule_contact_activity(res)
        if previous != ("done", res["percent"], res["verdict"]) or filled:
            self._ai_post_note(res, filled)
        self.ai_engine_log = "\n".join(log)

    def _ai_schedule_contact_activity(self, res):
        """One open "contact" activity per applicant, refreshed on every analysis. The note
        stays short: the full report is on the AI tab, not in the activity panel."""
        markers = (_("Contact candidate"), "Contact candidate")  # any language it was created in
        summary = _("Contact candidate - AI match %s%%", res["percent"])
        note = Markup("<p>%s</p>") % _("The full AI report is on the applicant's AI tab.")
        existing = self.activity_ids.filtered(lambda a: (a.summary or "").startswith(markers))
        if existing:
            existing[:1].write({"summary": summary, "note": note})
            existing[1:].unlink()
            return
        user = self.user_id or self.job_id.user_id or self.create_uid
        self.activity_schedule(
            "mail.mail_activity_data_todo", summary=summary, note=note, user_id=user.id,
        )

    def _ai_post_note(self, res, filled):
        """Short log note in the chatter (the summary itself lives on the AI tab)."""
        verdict_labels = dict(self._fields["ai_verdict"]._description_selection(self.env))
        lines = [
            (_("Match"), _("%(score)s%% (threshold %(threshold)s%%)",
                           score=res["percent"], threshold=res["threshold"])),
            (_("Verdict"), verdict_labels.get(res["verdict"]) or _("Not scored")),
        ]
        if res["mandatory_missing"]:
            lines.append((_("Warning"), _("a must-have requirement is missing")))
        lines.append((_("Auto-filled"), ", ".join(filled) or _("nothing")))
        engine = _("LLM + embeddings") if res["llm_used"] else _("keywords only (Ollama not used)")
        lines.append((_("Engine"), engine))
        body = Markup("<p><b>%s</b></p><ul>%s</ul>") % (
            _("AI CV screening"),
            Markup("").join(Markup("<li><b>%s:</b> %s</li>") % (k, v) for k, v in lines),
        )
        self.message_post(body=body, subtype_xmlid="mail.mt_note", message_type="comment")
