# -*- coding: utf-8 -*-
from odoo import api, models


class ResLang(models.Model):
    """The module ships its own translation of the core Recruitment screens. It is imported
    on install/update, but a language can also be activated later: import it then as well,
    so the module behaves the same on every database it is moved to."""
    _inherit = "res.lang"

    @api.model_create_multi
    def create(self, vals_list):
        langs = super().create(vals_list)
        if any(lang.active for lang in langs):
            self.env["hr.recruitment.ai.engine"].sudo()._load_core_translations()
        return langs

    def write(self, vals):
        res = super().write(vals)
        if vals.get("active"):
            self.env["hr.recruitment.ai.engine"].sudo()._load_core_translations()
        return res
