# -*- coding: utf-8 -*-
"""The "AI analysis" page of the Recruitment app.

A full-page form over a transient record: live numbers about the CVs in the database,
the state of the local AI engine, a hand upload (one applicant per dropped file) and
the "analyse everything" buttons. The analysis itself always runs in the background
job, so the page never blocks while a model is working.
"""
import os
import re

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import format_datetime

from ..tools.cv_text import looks_like_cv_file
from ..tools.ollama_client import OllamaClient

SECONDS_PER_CV = 20  # rough estimate shown to the user; a GPU run takes about 15 s


class HrRecruitmentAiCenter(models.TransientModel):
    _name = "hr.recruitment.ai.center"
    _description = "AI CV analysis page"

    job_id = fields.Many2one(
        "hr.job", string="Job position",
        help="The uploaded CVs are scored against this job's criteria. Leave empty for "
             "spontaneous applications.",
    )
    attachment_ids = fields.Many2many("ir.attachment", string="CV files")
    stats_html = fields.Html(compute="_compute_pages", sanitize=False)
    recent_html = fields.Html(compute="_compute_pages", sanitize=False)
    pending_count = fields.Integer(compute="_compute_pages")
    todo_count = fields.Integer(compute="_compute_pages")

    def _compute_display_name(self):
        for record in self:
            record.display_name = _("AI CV analysis")

    # ------------------------------------------------------------------ #
    #  Numbers
    # ------------------------------------------------------------------ #
    @api.model
    def _cv_applicants(self):
        """Active applicants that have at least one CV-like file."""
        attachments = self.env["ir.attachment"].sudo().search_read(
            [("res_model", "=", "hr.applicant"), ("res_id", "!=", False)],
            ["res_id", "name", "mimetype"])
        ids = {a["res_id"] for a in attachments if looks_like_cv_file(a["name"], a["mimetype"])}
        return self.env["hr.applicant"].search([("id", "in", list(ids))])

    @api.model
    def _engine_status(self):
        """(css, icon, text) about the local AI engine; never slow, never raises."""
        settings = self.env["hr.recruitment.ai.engine"]._settings()
        if not settings["use_llm"]:
            return "partial", "fa-power-off", _("The AI model is switched off in the settings: keywords only.")
        client = OllamaClient(settings["ollama_url"], timeout=3)
        try:
            names = client.list_models(timeout=2)
        except Exception:
            return "nomatch", "fa-plug", _(
                "The AI engine (Ollama) is not reachable: only keywords will be checked.")
        if not client.has_model(settings["chat_model"], names):
            return "partial", "fa-download", _(
                "Ollama is running, but the model %s is not downloaded yet.", settings["chat_model"])
        return "match", "fa-check-circle", _("AI engine ready: %s", settings["chat_model"])

    @api.depends("job_id")
    @api.depends_context("lang", "tz")
    def _compute_pages(self):
        applicants = self._cv_applicants()
        by_state = {}
        for applicant in applicants:
            by_state.setdefault(applicant.ai_state or "none", self.env["hr.applicant"])
            by_state[applicant.ai_state or "none"] |= applicant
        total = len(applicants)
        done = len(by_state.get("done", []))
        pending = len(by_state.get("pending", []))
        failed = len(by_state.get("error", []))
        fresh = len(by_state.get("none", []))
        matches = len(applicants.filtered(lambda a: a.ai_verdict == "match"))
        stats = self._render_stats(total, done, pending, failed, fresh, matches)
        recent = self._render_recent(applicants)
        for record in self:
            record.stats_html = stats
            record.recent_html = recent
            record.pending_count = pending
            record.todo_count = fresh + failed

    @api.model
    def _render_stats(self, total, done, pending, failed, fresh, matches):
        css, icon, status = self._engine_status()
        percent = int(round(100.0 * done / total)) if total else 0
        tiles = Markup("").join(
            Markup('<div class="o_hrai_tile %s"><span class="o_hrai_tile_num">%s</span>'
                   '<span class="o_hrai_tile_label">%s</span></div>') % tile
            for tile in (
                ("", total, _("CVs in total")),
                ("o_hrai_tile--green", done, _("Analysed")),
                ("o_hrai_tile--amber", pending, _("In the queue")),
                ("o_hrai_tile--blue", fresh, _("Not analysed yet")),
                ("o_hrai_tile--red", failed, _("Failed")),
                ("o_hrai_tile--green", matches, _("Matches")),
            ))
        if pending:
            minutes = max(1, int(round(pending * SECONDS_PER_CV / 60.0)))
            progress_text = _("%(done)s of %(total)s CVs analysed - %(pending)s in the queue, about %(minutes)s min left. "
                              "Press Refresh to update.", done=done, total=total, pending=pending, minutes=minutes)
        elif total:
            progress_text = _("%(done)s of %(total)s CVs analysed.", done=done, total=total)
        else:
            progress_text = _("No CVs yet. Upload some below, or let them arrive by e-mail.")
        return Markup(
            '<div class="o_hrai_report"><div class="o_hrai_center">'
            '<div class="o_hrai_center_head"><div><div class="o_hrai_hero_title">%s</div>'
            '<div class="o_hrai_hero_sub">%s</div></div>'
            '<span class="o_hrai_pill o_hrai_pill--%s"><i class="fa %s"></i>%s</span></div>'
            '<div class="o_hrai_tiles o_hrai_tiles--6">%s</div>'
            '<div class="o_hrai_progress"><div class="o_hrai_progress_bar" style="width: %s%%;"></div></div>'
            '<div class="o_hrai_hero_sub">%s</div></div></div>'
        ) % (_("AI CV analysis"),
             _("Every CV in Recruitment, scored against the criteria of its job."),
             css, icon, status, tiles, percent, progress_text)

    @api.model
    def _render_recent(self, applicants):
        recent = applicants.filtered(lambda a: a.ai_state == "done" and a.ai_last_run).sorted(
            "ai_last_run", reverse=True)[:8]
        if not recent:
            return Markup("")
        verdict_labels = dict(self.env["hr.applicant"]._fields["ai_verdict"]._description_selection(self.env))
        rows = Markup("")
        for applicant in recent:
            css = {"match": "match", "partial": "partial", "no_match": "nomatch"}.get(applicant.ai_verdict, "none")
            score = ("%s%%" % applicant.ai_score) if applicant.ai_verdict else "-"
            rows += Markup(
                '<div class="o_hrai_row"><span class="o_hrai_row_name">%s</span>'
                '<span class="o_hrai_row_job">%s</span>'
                '<span class="o_hrai_pill o_hrai_pill--sm o_hrai_pill--%s">%s</span>'
                '<span class="o_hrai_row_verdict">%s</span><span class="o_hrai_row_time">%s</span></div>'
            ) % (applicant.partner_name or "", applicant.job_id.name or _("No job"), css, score,
                 verdict_labels.get(applicant.ai_verdict) or _("Not scored"),
                 format_datetime(self.env, applicant.ai_last_run, dt_format="short"))
        return Markup(
            '<div class="o_hrai_report"><div class="o_hrai_card"><div class="o_hrai_card_title">'
            '<i class="fa fa-history"></i>%s</div>%s</div></div>') % (_("Latest results"), rows)

    # ------------------------------------------------------------------ #
    #  Actions
    # ------------------------------------------------------------------ #
    def _reopen(self, title=None, message=None, kind="success"):
        action = {
            "type": "ir.actions.act_window",
            "name": _("AI CV analysis"),
            "res_model": self._name,
            "view_mode": "form",
            "target": "main",
        }
        if not message:
            return action
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": title or _("AI CV analysis"), "message": message,
                       "type": kind, "sticky": False, "next": action},
        }

    def action_refresh(self):
        return self._reopen()

    @api.model
    def _name_from_filename(self, filename):
        """ "giorgi_kapanadze_CV.docx" -> "Giorgi Kapanadze" (a placeholder until the AI reads the CV)."""
        base = os.path.splitext(filename or "")[0]
        base = re.sub(r"[_\-.()\[\]]+", " ", base)          # separators first, so "_CV" becomes a word
        base = re.sub(r"(?i)\b(cv|resume|curriculum|vitae)\b", " ", base)
        base = re.sub(r"\s+", " ", base).strip()
        if not base:
            return _("New applicant")
        return base.title() if base.islower() or base.isupper() else base

    def action_upload(self):
        """One applicant per uploaded file; the AI replaces the file-name placeholder with
        the name it reads in the CV."""
        self.ensure_one()
        files = self.attachment_ids
        if not files:
            raise UserError(_("Add at least one CV file (PDF, DOCX or TXT) first."))
        bad = files.filtered(lambda a: not looks_like_cv_file(a.name, a.mimetype))
        if bad:
            raise UserError(_("These files are not CVs the AI can read (use PDF, DOCX or TXT): %s",
                              ", ".join(bad.mapped("name"))))
        Applicant = self.env["hr.applicant"]
        created = Applicant
        for attachment in files:
            applicant = Applicant.create({
                "partner_name": self._name_from_filename(attachment.name),
                "job_id": self.job_id.id or False,
                "ai_name_is_placeholder": True,
            })
            attachment.sudo().write({"res_model": "hr.applicant", "res_id": applicant.id, "public": False})
            created |= applicant
        created._ai_mark_pending(force=True)
        Applicant._ai_trigger_queue()
        self.attachment_ids = [(5, 0, 0)]
        return self._reopen(
            _("CVs uploaded"),
            _("%s applicant(s) created and queued. The analysis runs in the background; press Refresh "
              "to follow it.", len(created)))

    def _queue(self, applicants, empty_message):
        if not applicants:
            return self._reopen(_("Nothing to do"), empty_message, "info")
        applicants._ai_mark_pending(force=True)
        self.env["hr.applicant"]._ai_trigger_queue()
        minutes = max(1, int(round(len(applicants) * SECONDS_PER_CV / 60.0)))
        return self._reopen(
            _("Analysis started"),
            _("%(n)s CV(s) queued, about %(minutes)s min. It runs in the background; press Refresh "
              "to follow it.", n=len(applicants), minutes=minutes))

    def action_analyze_new(self):
        todo = self._cv_applicants().filtered(lambda a: a.ai_state in ("none", "error", False))
        return self._queue(todo, _("Every CV is already analysed."))

    def action_analyze_all(self):
        return self._queue(self._cv_applicants(), _("There are no CVs yet."))

    def _open_applicants(self, name, domain):
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": "hr.applicant",
            "view_mode": "list,kanban,form",
            "domain": [("id", "in", self._cv_applicants().ids)] + domain,
        }

    def action_open_matches(self):
        return self._open_applicants(_("AI matches"), [("ai_verdict", "=", "match")])

    def action_open_analysed(self):
        return self._open_applicants(_("Analysed CVs"), [("ai_state", "=", "done")])

    def action_open_queue(self):
        return self._open_applicants(_("CVs in the queue"), [("ai_state", "=", "pending")])

    def action_open_failed(self):
        return self._open_applicants(_("Failed analyses"), [("ai_state", "=", "error")])
