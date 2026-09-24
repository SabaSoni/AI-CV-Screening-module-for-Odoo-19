# -*- coding: utf-8 -*-
from odoo import api, models

from ..tools.cv_text import looks_like_cv_file


class IrAttachment(models.Model):
    """Queue an applicant for AI screening as soon as a CV file lands on it,
    whether it came from the e-mail alias, the chatter upload or the API."""
    _inherit = "ir.attachment"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._ai_queue_applicants()
        return records

    def write(self, vals):
        res = super().write(vals)
        if "res_id" in vals or "res_model" in vals:
            self._ai_queue_applicants()
        return res

    def _ai_queue_applicants(self):
        if "hr.applicant" not in self.env:
            return
        ids = {
            att.res_id for att in self
            if att.res_model == "hr.applicant" and att.res_id
            and looks_like_cv_file(att.name, att.mimetype)
        }
        if ids:
            self.env["hr.applicant"].sudo().browse(list(ids)).exists()._ai_mark_pending()
