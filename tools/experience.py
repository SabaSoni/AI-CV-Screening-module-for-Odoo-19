# -*- coding: utf-8 -*-
"""Deterministic estimate of the years of work experience from the date ranges in a CV.

"2019 - 2024", "03/2019 - 08/2021", "Jan 2019 - present" ... Ranges on education lines, or
under an "Education" heading, are skipped; overlapping jobs are not counted twice. The
number is a floor the language model cannot push down: the same CV always gives the same
value, so the "minimum experience" rule does not flip between two analyses.
"""
import re

_MONTH = r"(?:[^\W\d_]{3,12}\.?\s+|\d{1,2}\s*[./]\s*)?"          # "Jan ", "March ", "03/" before a year
_NOW_WORDS = ["present", "now", "current", "currently", "today", "ongoing"]
# Georgian / Russian "until now" words, built from code points (no typing slips in this file).
_NOW_WORDS += ["".join(chr(c) for c in codes) for codes in (
    (0x10D3, 0x10E6, 0x10D4, 0x10DB, 0x10D3, 0x10D4),                    # dghemde
    (0x10D0, 0x10DB, 0x10DF, 0x10D0, 0x10DB, 0x10D0, 0x10D3),            # amzhamad
    (0x043D, 0x0430, 0x0441, 0x0442),                                    # nast(oyashchee)
    (0x0441, 0x0435, 0x0439, 0x0447, 0x0430, 0x0441),                    # seychas
)]
RANGE_RE = re.compile(
    r"(?<!\d)" + _MONTH + r"((?:19|20)\d{2})\s*(?:-|–|—|to|until|till)\s*" + _MONTH +
    r"((?:19|20)\d{2}(?!\d)|" + "|".join(re.escape(w) for w in _NOW_WORDS) + r")",
    re.I,
)
_EDU_LATIN = ["universit", "college", "school", "bachelor", "master", "bsc", "msc", "phd", "degree",
              "diploma", "faculty", "student", "education", "academy", "institute", "course"]
_EDU_OTHER = ["".join(chr(c) for c in codes) for codes in (
    (0x10E3, 0x10DC, 0x10D8, 0x10D5, 0x10D4, 0x10E0, 0x10E1, 0x10D8, 0x10E2),   # universit
    (0x10D1, 0x10D0, 0x10D9, 0x10D0, 0x10DA, 0x10D0, 0x10D5),                   # bakalav
    (0x10DB, 0x10D0, 0x10D2, 0x10D8, 0x10E1, 0x10E2),                           # magist
    (0x10E1, 0x10D9, 0x10DD, 0x10DA),                                           # skol
    (0x10D2, 0x10D0, 0x10DC, 0x10D0, 0x10D7, 0x10DA, 0x10D4, 0x10D1),           # ganatleb
    (0x10E1, 0x10E2, 0x10E3, 0x10D3, 0x10D4, 0x10DC, 0x10E2),                   # student
    (0x10D0, 0x10D9, 0x10D0, 0x10D3, 0x10D4, 0x10DB),                           # akadem
    (0x10D9, 0x10E3, 0x10E0, 0x10E1),                                           # kurs
    (0x0443, 0x043D, 0x0438, 0x0432, 0x0435, 0x0440),                           # univer
    (0x043E, 0x0431, 0x0440, 0x0430, 0x0437, 0x043E, 0x0432),                   # obrazov
)]
EDUCATION_WORDS = _EDU_LATIN + _EDU_OTHER
_WORK_HEADINGS = ["experience", "employment", "work history", "career"] + ["".join(chr(c) for c in codes) for codes in (
    (0x10D2, 0x10D0, 0x10DB, 0x10DD, 0x10EA, 0x10D3, 0x10D8, 0x10DA),           # gamotsdil
    (0x10E1, 0x10D0, 0x10DB, 0x10E3, 0x10E8, 0x10D0, 0x10DD),                   # samushao
    (0x043E, 0x043F, 0x044B, 0x0442),                                           # opyt
)]


def _is_heading(line):
    return len(line.split()) <= 4 and not any(ch.isdigit() for ch in line)


def estimate_years(text, current_year):
    """Return (years, spans): whole years of work experience and the merged (start, end) spans."""
    spans, in_education = [], False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if _is_heading(line):
            if any(w in low for w in EDUCATION_WORDS):
                in_education = True
                continue
            if any(w in low for w in _WORK_HEADINGS):
                in_education = False
                continue
        if in_education or any(w in low for w in EDUCATION_WORDS):
            continue
        for m in RANGE_RE.finditer(line):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2)[:2] in ("19", "20") and m.group(2).isdigit() else current_year
            if 1970 <= start <= end <= current_year + 1 and end - start <= 50:
                spans.append((start, min(end, current_year)))
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return sum(end - start for start, end in merged), merged
