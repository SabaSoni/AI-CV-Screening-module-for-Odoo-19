# -*- coding: utf-8 -*-
"""Visual AI report shown on the applicant form.

One computed HTML field renders the whole read-only part of the "AI" tab (score
header, summary, strengths / gaps, requirement checklist) with the module's own
stylesheet (static/src/scss). Everything user-facing goes through _() so it follows
the user's language; every piece of data is escaped through Markup formatting.
"""
from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.tools import format_datetime

VERDICT_STYLE = {
    "match": ("match", "#1f9d63", "fa-check-circle"),
    "partial": ("partial", "#d98a1f", "fa-adjust"),
    "no_match": ("nomatch", "#d64545", "fa-times-circle"),
}
STATUS_ICON = {"met": "fa-check", "partial": "fa-adjust", "not_met": "fa-times"}
BULLET = chr(0x2022)


class HrApplicant(models.Model):
    _inherit = "hr.applicant"

    ai_report_html = fields.Html(
        string="AI report", compute="_compute_ai_report_html", sanitize=False,
    )

    ai_banner_html = fields.Html(
        string="AI result banner", compute="_compute_ai_report_html", sanitize=False,
    )

    @api.depends(
        "ai_state", "ai_score", "ai_llm_score", "ai_verdict", "ai_threshold",
        "ai_mandatory_missing", "ai_summary", "ai_strengths", "ai_gaps", "ai_last_run",
        "partner_name", "job_id",
        "ai_requirement_result_ids.status", "ai_requirement_result_ids.name",
        "ai_requirement_result_ids.keywords_found", "ai_requirement_result_ids.ai_evidence",
    )
    @api.depends_context("lang", "tz")
    def _compute_ai_report_html(self):
        for applicant in self:
            applicant.ai_report_html = applicant._ai_render_report()
            applicant.ai_banner_html = applicant._ai_render_banner()

    # ------------------------------------------------------------------ #
    #  Banner (top of the applicant form)
    # ------------------------------------------------------------------ #
    @api.model
    def _ai_first_sentence(self, text, limit=190):
        text = " ".join((text or "").split())
        if not text:
            return ""
        for mark in (". ", "! ", "? "):
            pos = text.find(mark)
            if 40 <= pos <= limit:
                return text[:pos + 1]
        return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."

    def _ai_render_banner(self):
        """Slim strip shown above the applicant's fields: the AI result at a glance."""
        self.ensure_one()
        state = self.ai_state or "none"
        if state != "done":
            kind, icon, title, text = {
                "none": ("none", "fa-magic", _("No AI analysis yet"),
                         _("Attach a CV (PDF or DOCX), or use the button on the right.")),
                "pending": ("pending", "fa-hourglass-half", _("Analysis queued"),
                            _("The result appears here within two minutes.")),
                "error": ("error", "fa-exclamation-triangle", _("The last analysis failed"),
                          _("Details are on the AI tab.")),
            }[state]
            return Markup(
                '<div class="o_hrai_banner o_hrai_banner--%s"><div class="o_hrai_banner_icon">'
                '<i class="fa %s"></i></div><div class="o_hrai_banner_main">'
                '<div class="o_hrai_banner_title">%s</div><div class="o_hrai_banner_text">%s</div>'
                '</div></div>') % (kind, icon, title, text)

        verdict_labels = dict(self._fields["ai_verdict"]._description_selection(self.env))
        css, color, icon = VERDICT_STYLE.get(self.ai_verdict, ("none", "#7b8496", "fa-circle-o"))
        score = max(0, min(100, self.ai_score or 0)) if self.ai_verdict else 0
        ring_value = Markup("%s<small>%%</small>") % score if self.ai_verdict else Markup("&ndash;")
        ring = Markup(
            '<div class="o_hrai_ring o_hrai_ring--sm" style="background: conic-gradient(%s 0 %s%%, #e3e7ee %s%% 100%%);">'
            '<div class="o_hrai_ring_inner"><span class="o_hrai_ring_value" style="color: %s;">%s</span></div></div>'
        ) % (color, score, score, color, ring_value)
        meta = []
        results = self.ai_requirement_result_ids
        if results:
            met = len(results.filtered(lambda r: r.status == "met"))
            meta.append(_("%(met)s of %(total)s requirements met", met=met, total=len(results)))
        meta.append(_("threshold %s%%", self.ai_threshold or 0))
        warning = Markup("")
        if self.ai_mandatory_missing:
            warning = Markup('<span class="o_hrai_chip o_hrai_chip--must"><i class="fa fa-exclamation-triangle"></i> %s</span>') % _(
                "must-have missing")
        return Markup(
            '<div class="o_hrai_banner o_hrai_banner--%s">%s<div class="o_hrai_banner_main">'
            '<div class="o_hrai_banner_top"><span class="o_hrai_pill o_hrai_pill--%s"><i class="fa %s"></i>%s</span>'
            '<span class="o_hrai_banner_meta">%s</span>%s</div>'
            '<div class="o_hrai_banner_text">%s</div></div></div>'
        ) % (css, ring, css, icon, verdict_labels.get(self.ai_verdict) or _("Not scored"),
             " · ".join(meta), warning, self._ai_first_sentence(self.ai_summary))

    # ------------------------------------------------------------------ #
    #  Rendering
    # ------------------------------------------------------------------ #
    def _ai_render_report(self):
        self.ensure_one()
        if self.ai_state == "none" or not self.ai_state:
            body = self._ai_render_empty(
                "", "fa-magic", _("No AI analysis yet"),
                _("Attach a CV (PDF or DOCX) and it is analysed automatically, or use the "
                  "\"Analyze CV (AI)\" button."))
        elif self.ai_state == "pending":
            body = self._ai_render_empty(
                "pending", "fa-hourglass-half", _("Analysis queued"),
                _("This CV is queued. The analysis starts automatically within two minutes."))
        elif self.ai_state == "error":
            body = self._ai_render_empty(
                "error", "fa-exclamation-triangle", _("The last analysis failed"),
                _("The reason is in the technical details at the bottom of this tab."))
        else:
            body = Markup("").join([
                self._ai_render_hero(),
                self._ai_render_warning(),
                self._ai_render_summary(),
                self._ai_render_strengths_gaps(),
                self._ai_render_requirements(),
            ])
        return Markup('<div class="o_hrai_report">%s</div>') % body

    @api.model
    def _ai_render_empty(self, kind, icon, title, text):
        return Markup(
            '<div class="o_hrai_empty %s"><div class="o_hrai_empty_icon"><i class="fa %s"></i></div>'
            '<div class="o_hrai_empty_title">%s</div><div class="o_hrai_empty_text">%s</div></div>'
        ) % ("o_hrai_empty--%s" % kind if kind else "", icon, title, text)

    def _ai_render_hero(self):
        verdict_labels = dict(self._fields["ai_verdict"]._description_selection(self.env))
        css, color, icon = VERDICT_STYLE.get(self.ai_verdict, ("none", "#7b8496", "fa-circle-o"))
        score = max(0, min(100, self.ai_score or 0))
        if self.ai_verdict:
            ring_value = Markup("%s<small>%%</small>") % score
            pill_label = verdict_labels.get(self.ai_verdict)
        else:
            ring_value = Markup("&ndash;")
            pill_label = _("Not scored")
            score = 0
        ring = Markup(
            '<div class="o_hrai_ring" style="background: conic-gradient(%s 0 %s%%, #e3e7ee %s%% 100%%);">'
            '<div class="o_hrai_ring_inner"><span class="o_hrai_ring_value" style="color: %s;">%s</span>'
            '<span class="o_hrai_ring_label">%s</span></div></div>'
        ) % (color, score, score, color, ring_value, _("match"))

        results = self.ai_requirement_result_ids
        if not self.job_id:
            subtitle = _("No job selected - the score is the AI's overall estimate.")
        elif not results:
            subtitle = _("Job: %s - it has no criteria yet, so the score is the AI's overall estimate.",
                         self.job_id.name)
        else:
            subtitle = _("Job: %s", self.job_id.name)
        # One percentage only. The model's own rough guess is deliberately not shown next to
        # it (two different numbers confused users); it is kept in the technical log.
        stats = [("fa-bullseye", _("Threshold"), "%s%%" % (self.ai_threshold or 0))]
        if results:
            met = len(results.filtered(lambda r: r.status == "met"))
            stats.append(("fa-check-square-o", _("Requirements met"), "%s / %s" % (met, len(results))))
        if self.ai_last_run:
            stats.append(("fa-clock-o", _("Last analysis"),
                          format_datetime(self.env, self.ai_last_run, dt_format="short")))
        stats_html = Markup("").join(
            Markup('<div class="o_hrai_stat"><i class="fa %s"></i><div>'
                   '<span class="o_hrai_stat_label">%s</span><span class="o_hrai_stat_value">%s</span>'
                   '</div></div>') % stat for stat in stats)
        return Markup(
            '<div class="o_hrai_hero o_hrai_hero--%s">%s<div class="o_hrai_hero_main">'
            '<span class="o_hrai_pill o_hrai_pill--%s"><i class="fa %s"></i>%s</span>'
            '<div class="o_hrai_hero_title">%s</div><div class="o_hrai_hero_sub">%s</div>'
            '<div class="o_hrai_stats">%s</div></div></div>'
        ) % (css, ring, css, icon, pill_label, self.partner_name or "", subtitle, stats_html)

    def _ai_render_warning(self):
        if not self.ai_mandatory_missing:
            return Markup("")
        return Markup('<div class="o_hrai_notice"><i class="fa fa-exclamation-triangle"></i><span>%s</span></div>') % _(
            "A must-have requirement is missing, so this candidate can only be a partial match.")

    def _ai_render_summary(self):
        if not self.ai_summary:
            return Markup("")
        paragraphs = Markup("").join(
            Markup("<p>%s</p>") % line.strip() for line in self.ai_summary.splitlines() if line.strip())
        return Markup(
            '<div class="o_hrai_card o_hrai_card--summary"><div class="o_hrai_card_title">'
            '<i class="fa fa-quote-left"></i>%s</div>%s</div>') % (_("Summary"), paragraphs)

    @api.model
    def _ai_items(self, text):
        items = [line.strip().lstrip(BULLET + "-*").strip() for line in (text or "").splitlines()]
        return [item for item in items if item]

    def _ai_render_list_card(self, css, icon, item_icon, title, text):
        items = self._ai_items(text)
        if items:
            content = Markup('<ul class="o_hrai_list">%s</ul>') % Markup("").join(
                Markup('<li><i class="fa %s"></i><span>%s</span></li>') % (item_icon, item) for item in items)
        else:
            content = Markup('<span class="o_hrai_muted">%s</span>') % _("Nothing noted.")
        return Markup(
            '<div class="o_hrai_card %s"><div class="o_hrai_card_title"><i class="fa %s"></i>%s</div>%s</div>'
        ) % (css, icon, title, content)

    def _ai_render_strengths_gaps(self):
        if not self.ai_strengths and not self.ai_gaps:
            return Markup("")
        return Markup('<div class="o_hrai_grid">%s%s</div>') % (
            self._ai_render_list_card("o_hrai_card--good", "fa-thumbs-up", "fa-check",
                                      _("Strengths"), self.ai_strengths),
            self._ai_render_list_card("o_hrai_card--bad", "fa-flag", "fa-exclamation",
                                      _("Gaps"), self.ai_gaps),
        )

    def _ai_render_requirements(self):
        results = self.ai_requirement_result_ids
        if not results:
            return Markup("")
        status_labels = dict(results._fields["status"]._description_selection(self.env))
        met = len(results.filtered(lambda r: r.status == "met"))
        segments = Markup("").join(
            Markup('<span class="o_hrai_seg o_hrai_seg--%s" style="flex: %s;" title="%s"></span>')
            % (r.status, max(r.weight, 1), r.name) for r in results)
        rows = Markup("").join(self._ai_render_requirement(r, status_labels) for r in results)
        # Make the percentage traceable: it is nothing but these points added up.
        total = sum(max(r.weight, 0) for r in results)
        gained = sum(max(r.weight, 0) * r.score for r in results)
        formula = Markup('<div class="o_hrai_formula"><i class="fa fa-calculator"></i><span>%s</span></div>') % _(
            "How the %(score)s%% is calculated: %(gained)s of %(total)s points. A met requirement "
            "gives its full weight, a partly met one gives half.",
            score=self.ai_score, gained="%g" % gained, total=total)
        return Markup(
            '<div class="o_hrai_card"><div class="o_hrai_card_title"><i class="fa fa-list-ul"></i>%s'
            '<span class="o_hrai_card_meta">%s</span></div>'
            '<div class="o_hrai_segments">%s</div>%s%s</div>'
        ) % (_("Requirement checks"),
             _("%(met)s of %(total)s requirements met", met=met, total=len(results)),
             segments, formula, rows)

    @api.model
    def _ai_render_requirement(self, result, status_labels):
        chips = Markup("")
        if result.mandatory:
            chips += Markup('<span class="o_hrai_chip o_hrai_chip--must">%s</span>') % _("Must have")
        chips += Markup('<span class="o_hrai_chip">%s</span>') % _("Weight %s", result.weight)

        evidence = Markup("")
        keywords = [k.strip() for k in (result.keywords_found or "").split(",") if k.strip()]
        if keywords:
            evidence += Markup('<div><i class="fa fa-key"></i><span>%s %s</span></div>') % (
                _("Found in the CV:"),
                Markup("").join(Markup('<span class="o_hrai_chip o_hrai_chip--kw">%s</span>') % k for k in keywords))
        if result.similarity_counts:
            evidence += Markup('<div><i class="fa fa-random"></i><span>%s</span></div>') % _(
                "Similar wording found in the CV (semantic match %s%%).", int(round(result.similarity * 100)))
        # The final status is the strongest evidence of the three checks. When the model's own
        # opinion differs from it (e.g. the keyword is plainly in the CV but the model said
        # "partly"), its sentence would contradict the badge next to it, so it is left out.
        agrees = not result.ai_status or result.ai_status == result.status
        if result.ai_evidence and agrees and result.ai_policy != "unverified":
            evidence += Markup('<div><i class="fa fa-magic"></i><span>%s</span></div>') % result.ai_evidence
        if result.ai_quote and agrees and result.status != "not_met" and not keywords:
            # the exact words of the CV the AI relied on (checked to really be in the CV)
            evidence += Markup('<div><i class="fa fa-quote-left"></i><span><em>%s</em></span></div>') % result.ai_quote
        if result.ai_policy == "keywords_missing":
            evidence += Markup('<div><i class="fa fa-info-circle"></i><span>%s</span></div>') % _(
                "Half credit at most: none of the keywords is in the CV, the AI only found it worded "
                "differently. Add that wording to the keywords if it should count fully.")
        elif result.ai_policy == "unverified":
            evidence += Markup('<div><i class="fa fa-info-circle"></i><span>%s</span></div>') % _(
                "The AI claimed this, but could not point to it in the CV, so it does not count.")
        if not evidence:
            evidence = Markup('<div><i class="fa fa-search"></i><span>%s</span></div>') % _(
                "Nothing about this was found in the CV.")
        points = _("%(gained)s / %(total)s points",
                   gained="%g" % (max(result.weight, 0) * result.score), total=max(result.weight, 0))
        return Markup(
            '<div class="o_hrai_req o_hrai_req--%s"><div class="o_hrai_req_icon"><i class="fa %s"></i></div>'
            '<div class="o_hrai_req_body"><div><span class="o_hrai_req_name">%s</span>%s</div>'
            '<div class="o_hrai_req_evidence">%s</div></div>'
            '<div class="o_hrai_req_status"><span class="o_hrai_pill o_hrai_pill--sm o_hrai_pill--%s">%s</span>'
            '<div class="o_hrai_req_points">%s</div></div></div>'
        ) % (result.status, STATUS_ICON.get(result.status, "fa-circle-o"), result.name, chips,
             evidence, result.status, status_labels.get(result.status, result.status), points)
