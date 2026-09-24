# -*- coding: utf-8 -*-
"""Prompt and JSON-schema definitions for the LLM layer, plus a model-free
fallback e-mail so the workflow works even when no LLM is running."""

LANG_LABELS = {"ka": "Georgian", "en": "English", "ru": "Russian"}
# The model follows a language instruction better when it also sees it in that language.
LANG_NATIVE = {"ka": "ქართულ ენაზე (Georgian language)", "en": "in English", "ru": "на русском языке"}


def language_label(language_code):
    return LANG_LABELS.get(language_code, language_code or "English")


def system_prompt(language_code, company, ai_email=False):
    language = language_label(language_code)
    company = company or "the HR team"
    rules = [
        "You are a meticulous recruiting assistant. You read one candidate's CV and "
        "compare it with a job's requirements.",
        "Rules:",
        "- Judge ONLY from the CV text and the recruiter notes. Never invent experience, "
        "contact details or dates. If a contact detail is not in the CV, return an empty string.",
        "- For every requirement id you were given, answer \"met\" when the CV clearly shows it, "
        "\"partial\" when it is only partly or indirectly shown, \"not_met\" otherwise. In "
        "\"quote\" copy the exact words from the CV that prove it: at most 15 words, unchanged, "
        "in the CV's own language. If nothing in the CV proves it, \"quote\" is an empty string "
        "and the status must be \"not_met\"; never give credit for something the CV does not say. "
        "In \"evidence\" explain in one short sentence, written in %s, what in the CV shows it "
        "(technology names may stay as written in the CV)." % language,
        "- overall_score is your holistic 0-100 estimate of how well the candidate fits this job.",
        "- candidate.years_experience is the number of years of professional work experience "
        "that is RELEVANT TO THIS JOB, counted from the dates in the CV (0 if none; work in an "
        "unrelated profession does not count).",
        "- Write summary, strengths and gaps in %s, in correct spelling. The summary is 3-4 "
        "sentences. Give at most 3 strengths and at most 3 gaps, one short sentence each; every "
        "item must be about a DIFFERENT topic - never repeat or rephrase the same point. If "
        "there is only one real gap, return only one. "
        "Keep personal names, company names and technology names exactly as "
        "they are spelled in the CV (Latin letters stay Latin; never transliterate them)."
        % language,
    ]
    if ai_email:
        rules.append(
            "- Write email_subject and email_body in %s: a warm, professional invitation to a "
            "first conversation about the job, addressed to the candidate by name and signed "
            "\"%s\". No placeholders like [Name]." % (language, company)
        )
    else:
        rules.append("- Set email_subject and email_body to empty strings.")
    rules.append("- Reply with JSON only, matching the schema you were given, as compact JSON on a "
                 "single line: no indentation, no line breaks, no extra spaces.")
    return "\n".join(rules)


def build_user_prompt(job_name, job_description, job_requirements_text, requirements,
                      recruiter_notes, cv_text, language_code="en", candidate_name=""):
    lines = ["JOB TITLE: %s" % (job_name or "Spontaneous application (no specific job)")]
    if candidate_name:
        # Small models mangle names when they transliterate them; pin the spelling.
        lines.append("CANDIDATE NAME (whenever you mention the candidate, copy this spelling "
                     "exactly, letter by letter): %s" % candidate_name)
    if job_description:
        lines += ["", "JOB DESCRIPTION:", job_description.strip()]
    if job_requirements_text:
        lines += ["", "ADDITIONAL JOB REQUIREMENTS (free text):", job_requirements_text.strip()]
    if requirements:
        lines += ["", "STRUCTURED REQUIREMENTS (evaluate each id):"]
        for r in requirements:
            flag = " [MANDATORY]" if r.get("mandatory") else ""
            kws = ", ".join(r.get("keywords") or [])
            line = "- id %s: %s%s" % (r["id"], r["name"], flag)
            if kws and kws != r["name"]:
                line += " (keywords: %s)" % kws
            if r.get("description"):
                line += " - %s" % r["description"].strip()
            lines.append(line)
    if recruiter_notes and recruiter_notes.strip():
        lines += ["", "RECRUITER NOTES:", recruiter_notes.strip()]
    lines += ["", "CANDIDATE CV TEXT:", "<<<", cv_text or "", ">>>"]
    lines += ["", "OUTPUT LANGUAGE for summary, strengths and gaps: %s - %s. Every single item of "
              "strengths and gaps must be in that language too; never switch to English "
              "except for names and technology terms copied from the CV."
              % (language_label(language_code), LANG_NATIVE.get(language_code, language_label(language_code)))]
    return "\n".join(lines)


def response_schema():
    return {
        "type": "object",
        "properties": {
            "candidate": {
                "type": "object",
                "properties": {
                    "full_name": {"type": "string"},
                    "email": {"type": "string"},
                    "phone": {"type": "string"},
                    "linkedin": {"type": "string"},
                    "location": {"type": "string"},
                    "current_title": {"type": "string"},
                    "years_experience": {"type": "number"},
                },
                "required": ["full_name", "email", "phone"],
            },
            "requirements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "status": {"type": "string", "enum": ["met", "partial", "not_met"]},
                        "quote": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["id", "status", "quote", "evidence"],
                },
            },
            "overall_score": {"type": "integer", "minimum": 0, "maximum": 100},
            "summary": {"type": "string"},
            "strengths": {"type": "array", "items": {"type": "string"}},
            "gaps": {"type": "array", "items": {"type": "string"}},
            "email_subject": {"type": "string"},
            "email_body": {"type": "string"},
        },
        "required": ["candidate", "requirements", "overall_score", "summary",
                     "strengths", "gaps", "email_subject", "email_body"],
    }


def translate_prompts(language_code, text):
    """System/user prompts to translate a text the model wrote in the wrong language."""
    language = language_label(language_code)
    native = LANG_NATIVE.get(language_code, language)
    system = "You are a professional translator into %s." % language
    user = (
        "Translate the text below into %s (%s). Keep personal names, company names and "
        "technology names exactly as written (Latin letters stay Latin). Keep the same "
        "line breaks and bullet marks. Output only the translation, nothing else.\n\n"
        "TEXT:\n<<<\n%s\n>>>" % (language, native, text)
    )
    return system, user


def proofread_prompts(language_code, text):
    """System/user prompts for the proofreading pass (plain-text answer)."""
    language = language_label(language_code)
    native = LANG_NATIVE.get(language_code, language)
    system = (
        "You are a careful proofreader of %s text. You only fix language; you never add, "
        "remove or reorder information." % language
    )
    user = (
        "Rewrite the text below in correct, natural %s (%s). Fix spelling mistakes, broken "
        "words and words from other alphabets. Keep personal names, company names and "
        "technology names exactly in their original Latin spelling. Keep the same "
        "sentences and the same facts. Output only the corrected text, nothing else.\n\n"
        "TEXT:\n<<<\n%s\n>>>" % (language, native, text)
    )
    return system, user


def fallback_email(language_code, candidate_name, job_name, company):
    """Deterministic invitation e-mail used when the LLM is unavailable."""
    name = candidate_name or ""
    company = company or ""
    if language_code == "ka":
        job = job_name or "ჩვენი ვაკანსია"
        subject = "%s - %s" % (job, company) if company else job
        body = (
            "გამარჯობა, %s!\n\n"
            "გმადლობთ, რომ დაინტერესდით ვაკანსიით „%s“. თქვენს რეზიუმეს გავეცანით და გვსურს "
            "პირველადი გასაუბრება, რათა უკეთ გავიცნოთ ერთმანეთი და უფრო დეტალურად მოგიყვეთ "
            "პოზიციის შესახებ.\n\n"
            "გთხოვთ, მოგვწეროთ, რომელი დღე და საათი იქნება თქვენთვის მოსახერხებელი მოკლე "
            "სატელეფონო ან ონლაინ საუბრისთვის.\n\n"
            "პატივისცემით,\n%s"
        ) % (name, job, company)
    elif language_code == "ru":
        job = job_name or "нашей вакансией"
        subject = "Ваш отклик на вакансию %s" % (job_name or "") if job_name else "Ваш отклик"
        body = (
            "Здравствуйте, %s!\n\n"
            "Спасибо за интерес к вакансии «%s». Мы ознакомились с вашим резюме и хотели бы "
            "пригласить вас на короткий вводный разговор, чтобы познакомиться и рассказать "
            "подробнее о позиции.\n\n"
            "Пожалуйста, напишите, какой день и время вам удобны для короткого звонка.\n\n"
            "С уважением,\n%s"
        ) % (name, job, company)
    else:
        job = job_name or "the position"
        subject = "Your application for %s%s" % (job, (" at " + company) if company else "")
        body = (
            "Hello %s,\n\n"
            "Thank you for applying for %s. We have reviewed your CV and would like to invite "
            "you to a short introductory call to get to know each other and tell you more "
            "about the role.\n\n"
            "Please let us know which day and time would suit you for a brief phone or online "
            "conversation.\n\n"
            "Best regards,\n%s"
        ) % (name, job, company)
    return subject, body
