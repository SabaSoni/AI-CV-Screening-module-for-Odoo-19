# -*- coding: utf-8 -*-
{
    "name": "AI CV Screening (local, free)",
    "summary": "Match applicant CVs against job requirements with a free local AI engine "
               "(regex + embeddings + Ollama LLM); auto-fill contacts and draft the invitation.",
    "description": """
Upload or e-mail a CV to an applicant. The module extracts the text, pulls out
email / phone / LinkedIn with regex, scores the CV against the job's requirement
list (keywords + multilingual embeddings + a local Ollama model), and when the
match is above the job's threshold it fills the application, tags it "AI Match",
schedules a "Contact candidate" activity and drafts the invitation e-mail.
No API keys, no credits: everything runs on your own machine.
    """,
    "author": "FMG Soft",
    "website": "https://fmgsoft.ge",
    "category": "Human Resources/Recruitment",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": ["hr_recruitment"],
    "data": [
        "security/ir.model.access.csv",
        "data/config_parameters.xml",
        "data/hr_applicant_category_data.xml",
        "data/ir_cron.xml",
        "views/hr_job_views.xml",
        "views/hr_applicant_views.xml",
        "views/hr_recruitment_ai_center_views.xml",
        "views/res_config_settings_views.xml",
        "data/load_core_translations.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "hr_recruitment_ai/static/src/scss/hr_recruitment_ai.scss",
        ],
    },
    "external_dependencies": {
        "python": ["pypdf", "python-docx"],
    },
    "installable": True,
    "application": False,
}
