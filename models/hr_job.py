# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

from ..tools.cv_text import looks_like_cv_file


class HrJob(models.Model):
    _inherit = "hr.job"

    ai_requirement_ids = fields.One2many(
        "hr.job.requirement", "job_id", string="AI Requirements", copy=True,
    )
    ai_requirement_count = fields.Integer(compute="_compute_ai_counts")
    ai_matched_applicant_count = fields.Integer(compute="_compute_ai_counts")
    ai_analysed_count = fields.Integer(string="CVs analysed", compute="_compute_ai_counts")
    ai_pending_count = fields.Integer(string="CVs queued", compute="_compute_ai_counts")
    ai_avg_score = fields.Integer(string="Average AI match %", compute="_compute_ai_counts")
    ai_match_threshold = fields.Integer(
        string="Match threshold (%)",
        default=lambda self: self.env["hr.recruitment.ai.engine"]._settings()["threshold"],
        help="Applicants scoring at least this percentage (and missing no 'must have') "
             "are flagged as a match, auto-filled and get a 'Contact candidate' activity.",
    )
    ai_min_experience_years = fields.Integer(
        string="Minimum experience (years)",
        help="Required years of relevant professional experience. Treated as a "
             "must-have requirement judged from the CV; 0 = not required.",
    )
    ai_auto_fill = fields.Boolean(
        string="Auto-flag matches", default=True,
        help="When a CV matches: add the 'AI Match' tag, raise priority, schedule a "
             "'Contact candidate' activity and optionally move the stage.",
    )
    ai_match_stage_id = fields.Many2one(
        "hr.recruitment.stage", string="Move matches to stage",
        domain="['|', ('job_ids', '=', False), ('job_ids', '=', id)]",
        help="Optional. Only applicants still in the first stage are moved.",
    )

    @api.constrains("ai_match_threshold")
    def _check_ai_match_threshold(self):
        for job in self:
            if not 0 <= job.ai_match_threshold <= 100:
                raise ValidationError(_("The match threshold must be between 0 and 100."))

    @api.depends("ai_requirement_ids", "ai_min_experience_years")
    def _compute_ai_counts(self):
        Applicant = self.env["hr.applicant"]
        matched = {job.id: count for job, count in Applicant._read_group(
            [("job_id", "in", self.ids), ("ai_verdict", "=", "match")], ["job_id"], ["__count"])}
        pending = {job.id: count for job, count in Applicant._read_group(
            [("job_id", "in", self.ids), ("ai_state", "=", "pending")], ["job_id"], ["__count"])}
        analysed = {job.id: (count, avg) for job, count, avg in Applicant._read_group(
            [("job_id", "in", self.ids), ("ai_state", "=", "done"), ("ai_verdict", "!=", False)],
            ["job_id"], ["__count", "ai_score:avg"])}
        for job in self:
            count, avg = analysed.get(job.id, (0, 0))
            job.ai_requirement_count = len(job.ai_requirement_ids) + (1 if job.ai_min_experience_years else 0)
            job.ai_matched_applicant_count = matched.get(job.id, 0)
            job.ai_pending_count = pending.get(job.id, 0)
            job.ai_analysed_count = count
            job.ai_avg_score = int(round(avg or 0))

    def action_ai_reevaluate_applicants(self):
        """Queue every active applicant of these jobs that has a CV file."""
        applicants = self.env["hr.applicant"].search([("job_id", "in", self.ids)])
        attachments = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "hr.applicant"), ("res_id", "in", applicants.ids),
        ])
        with_cv = {a.res_id for a in attachments if looks_like_cv_file(a.name, a.mimetype)}
        queued = applicants.filtered(lambda a: a.id in with_cv)
        queued._ai_mark_pending(force=True)   # also starts the background worker
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("AI screening queued"),
                "message": _(
                    "%(n)s applicant(s) queued. The scheduler processes them within a "
                    "couple of minutes; open an applicant and click 'Analyze CV' to run one now.",
                    n=len(queued),
                ),
                "type": "success" if queued else "warning",
                "sticky": False,
            },
        }

    def action_ai_open_matches(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("AI matches - %s", self.name),
            "res_model": "hr.applicant",
            "view_mode": "list,kanban,form",
            "domain": [("job_id", "=", self.id), ("ai_verdict", "=", "match")],
            "context": {"default_job_id": self.id, "search_default_job_id": self.id},
        }
