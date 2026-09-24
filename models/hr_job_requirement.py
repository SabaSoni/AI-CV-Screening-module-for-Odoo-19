# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class HrJobRequirement(models.Model):
    _name = "hr.job.requirement"
    _description = "Job Requirement (AI CV matching)"
    _order = "sequence, id"

    job_id = fields.Many2one("hr.job", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(
        string="Requirement", required=True,
        help="What the candidate must show, e.g. 'Python backend development'.",
    )
    keywords = fields.Char(
        string="Keywords / synonyms",
        help="Comma-separated words that prove this requirement when found in the CV, "
             "e.g. 'python, django, flask, fastapi'. Leave empty to search for the "
             "requirement name itself.",
    )
    description = fields.Text(
        string="Details for the AI",
        help="Optional clarification the AI reads when judging, e.g. "
             "'3+ years of commercial experience, not only courses'.",
    )
    weight = fields.Integer(
        default=1, required=True,
        help="Importance from 1 to 10. The match percentage is a weighted average.",
    )
    mandatory = fields.Boolean(
        string="Must have",
        help="If this is missing the applicant can only be a partial match, "
             "whatever the percentage.",
    )

    @api.constrains("weight")
    def _check_weight(self):
        for rec in self:
            if not 1 <= rec.weight <= 10:
                raise ValidationError(_("Requirement weight must be between 1 and 10."))
