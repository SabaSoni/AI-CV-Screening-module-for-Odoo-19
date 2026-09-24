# -*- coding: utf-8 -*-
from odoo import fields, models

STATUSES = [("met", "Met"), ("partial", "Partial"), ("not_met", "Not met")]


class HrApplicantRequirementResult(models.Model):
    _name = "hr.applicant.requirement.result"
    _description = "AI requirement check result"
    _order = "sequence, id"

    applicant_id = fields.Many2one(
        "hr.applicant", required=True, ondelete="cascade", index=True,
    )
    requirement_id = fields.Many2one(
        "hr.job.requirement", string="Job requirement", ondelete="set null",
    )
    name = fields.Char(string="Requirement", required=True)
    sequence = fields.Integer(default=10)
    weight = fields.Integer(default=1)
    mandatory = fields.Boolean(string="Must have")
    status = fields.Selection(STATUSES, required=True, default="not_met")
    score = fields.Float(
        string="Score", digits=(3, 2),
        help="0 = not found, 0.5 = partial evidence, 1 = clearly met.",
    )
    sources = fields.Char(
        string="Evidence from",
        help="Which layers produced the score: keyword, embedding, llm.",
    )
    evidence = fields.Text(help="All evidence as plain text, in the language of the analysis.")
    # Structured evidence, used by the visual report.
    keywords_found = fields.Char(string="Keywords found")
    similarity = fields.Float(string="Semantic similarity", digits=(3, 2))
    similarity_counts = fields.Boolean(string="Semantic match counted")
    ai_status = fields.Selection(STATUSES, string="AI assessment")
    ai_evidence = fields.Text(string="AI explanation")
    ai_quote = fields.Text(string="Quote from the CV",
                           help="The words of the CV the AI pointed at; verified to be in the CV.")
    ai_policy = fields.Char(
        string="Evidence rule applied",
        help="unverified: the AI's claim was not found in the CV and does not count. "
             "keywords_missing: none of the requirement's keywords is in the CV, so the AI "
             "could give half credit at most.",
    )
