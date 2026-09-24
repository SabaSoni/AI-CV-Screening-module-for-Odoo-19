# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.tools import str2bool


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    hr_recruitment_ai_ollama_url = fields.Char(
        string="Ollama URL", config_parameter="hr_recruitment_ai.ollama_url",
        default="http://127.0.0.1:11434",
    )
    hr_recruitment_ai_chat_model = fields.Char(
        string="Chat model", config_parameter="hr_recruitment_ai.chat_model",
        default="gemma3:4b",
        help="Any model from ollama.com/library, e.g. gemma3:4b, gemma3:12b, qwen3:8b.",
    )
    hr_recruitment_ai_embed_model = fields.Char(
        string="Embedding model", config_parameter="hr_recruitment_ai.embed_model",
        default="bge-m3",
        help="Multilingual embedding model used for semantic requirement matching.",
    )
    hr_recruitment_ai_default_threshold = fields.Integer(
        string="Default match threshold (%)", config_parameter="hr_recruitment_ai.threshold",
        default=80, help="Used by jobs that do not set their own threshold.",
    )
    hr_recruitment_ai_language = fields.Selection(
        [("ka", "Georgian"), ("en", "English"), ("ru", "Russian")],
        string="Language of AI texts", config_parameter="hr_recruitment_ai.language",
        default="ka", help="Language of the summary and the drafted e-mail.",
    )
    hr_recruitment_ai_num_ctx = fields.Integer(
        string="Context window (tokens)", config_parameter="hr_recruitment_ai.num_ctx",
        default=8192, help="Raise if long CVs get cut; lower if the GPU runs out of memory.",
    )
    hr_recruitment_ai_max_chars = fields.Integer(
        string="Max CV characters sent to the model", config_parameter="hr_recruitment_ai.max_chars",
        default=12000,
    )
    hr_recruitment_ai_llm_email = fields.Boolean(
        string="Let the AI write the invitation e-mail",
        config_parameter="hr_recruitment_ai.llm_email",
        help="Off (recommended for Georgian): a fixed, personalised template in the chosen "
             "language is used. On: the local model writes the e-mail itself; small models "
             "produce poor Georgian.",
    )
    hr_recruitment_ai_overwrite_contacts = fields.Boolean(
        string="Overwrite existing contact details",
        config_parameter="hr_recruitment_ai.overwrite_contacts",
        help="By default the AI only fills empty name / email / phone fields.",
    )
    hr_recruitment_ai_disable_thinking = fields.Boolean(
        string="Disable 'thinking' mode", config_parameter="hr_recruitment_ai.disable_thinking",
        help="Enable for reasoning models such as qwen3 or deepseek-r1 to answer faster.",
    )
    # True-by-default switches. Odoo deletes a config parameter when a boolean
    # setting is unticked, which would silently restore the default; so these
    # are stored explicitly as '1' / '0'.
    hr_recruitment_ai_use_llm = fields.Boolean(string="Use local LLM (Ollama chat model)", default=True)
    hr_recruitment_ai_use_embeddings = fields.Boolean(string="Use semantic matching (embeddings)", default=True)
    hr_recruitment_ai_auto_run = fields.Boolean(string="Screen new CVs automatically", default=True)
    hr_recruitment_ai_embed_on_cpu = fields.Boolean(
        string="Run the embedding model on the CPU", default=True,
        help="Keeps the chat model resident on the GPU. On cards with 8 GB VRAM or less, "
             "loading both models on the GPU makes Ollama reload a model on every CV.",
    )
    hr_recruitment_ai_proofread = fields.Boolean(
        string="Proofread the AI summary (second pass)", default=False,
        help="Runs a second, short model pass that fixes spelling in the chosen language. "
             "Adds a few seconds per CV. Words from other alphabets are always removed.",
    )

    _AI_SWITCHES = {
        "hr_recruitment_ai_use_llm": "hr_recruitment_ai.use_llm",
        "hr_recruitment_ai_use_embeddings": "hr_recruitment_ai.use_embeddings",
        "hr_recruitment_ai_auto_run": "hr_recruitment_ai.auto_run",
        "hr_recruitment_ai_embed_on_cpu": "hr_recruitment_ai.embed_on_cpu",
        "hr_recruitment_ai_proofread": "hr_recruitment_ai.proofread",
    }

    @api.model
    def get_values(self):
        res = super().get_values()
        ICP = self.env["ir.config_parameter"].sudo()
        for fname, key in self._AI_SWITCHES.items():
            default = bool(self._fields[fname].default(self)) if self._fields[fname].default else False
            res[fname] = str2bool(ICP.get_param(key, "1" if default else "0"), default)
        return res

    def set_values(self):
        super().set_values()
        ICP = self.env["ir.config_parameter"].sudo()
        for fname, key in self._AI_SWITCHES.items():
            ICP.set_param(key, "1" if self[fname] else "0")

    def action_hr_recruitment_ai_test_connection(self):
        ok, message = self.env["hr.recruitment.ai.engine"].test_connection(
            ollama_url=self.hr_recruitment_ai_ollama_url,
            chat_model=self.hr_recruitment_ai_chat_model,
            embed_model=self.hr_recruitment_ai_embed_model,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Ollama connection"),
                "message": message,
                "type": "success" if ok else "danger",
                "sticky": not ok,
            },
        }
