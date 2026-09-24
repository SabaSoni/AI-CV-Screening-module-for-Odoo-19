# -*- coding: utf-8 -*-
"""Deterministic contact extraction (email, phone, LinkedIn, GitHub, name).

Regex beats any language model here: it is instant, free and never invents
data. Tuned for Georgian (+995) numbers but works for international ones too.
"""
import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
LINKEDIN_RE = re.compile(
    r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|pub)/[A-Za-z0-9_%\-\.]+/?", re.I
)
GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_\-]+/?", re.I)
# A run of digits with optional separators, not glued to letters or slashes. Separators are
# spaces, tabs and no-break spaces only: a phone number never continues on the next line
# (that glued "577 44 55 66" to the "2016" starting the following line of a real CV).
PHONE_CANDIDATE_RE = re.compile(
    r"(?<![\w/.])(?:\+|00)?\(?\d(?:[\d \t().\-" + chr(0xA0) + r"]{6,18})\d(?![\w])")
DATE_LIKE_RE = re.compile(
    r"^\D*(?:\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}|(?:19|20)\d{2}\D+(?:19|20)\d{2}|\d{4}[./\-]\d{2}[./\-]\d{2})\D*$"
)
PHONE_HINT_RE = re.compile(r"(tel|phone|mob|cell|whatsapp|viber|ტელ|მობ|тел|моб)", re.I)

NAME_STOPWORDS = {
    "curriculum", "vitae", "cv", "resume", "résumé", "profile", "contact", "contacts", "summary",
    "objective", "personal", "information", "details", "about", "me", "education", "experience",
    "skills", "work", "languages", "references",
    "რეზიუმე", "ავტობიოგრაფია", "პროფილი", "კონტაქტი", "პირადი", "ინფორმაცია", "განათლება",
    "გამოცდილება", "უნარები", "ენები",
    "резюме", "профиль", "контакты", "образование", "опыт",
    # common job-title words that also look like "Firstname Lastname"
    "developer", "engineer", "manager", "senior", "junior", "lead", "specialist", "analyst",
    "designer", "accountant", "assistant", "director", "consultant", "intern", "software",
    "frontend", "backend", "fullstack", "data", "project", "product", "sales", "marketing",
    "მენეჯერი", "სპეციალისტი", "ინჟინერი", "დეველოპერი", "ბუღალტერი", "დიზაინერი",
}
WORD_RE = re.compile(
    r"^[A-Za-zÀ-ÖØ-öø-ÿႠ-ჿЀ-ӿ][A-Za-zÀ-ÖØ-öø-ÿႠ-ჿЀ-ӿ'’\-\.]*$"
)
# "Name : Enrique Jones", "სახელი და გვარი: ნინო ბერიძე", "ФИО: ..."
NAME_LABEL_RE = re.compile(
    r"^(?:full\s+name|name|სახელი(?:\s+და\s+გვარი)?|სახელი,?\s*გვარი|фио|имя)\s*[:：\-–]\s*(.+)$",
    re.I,
)


def extract_email(text):
    m = EMAIL_RE.search(text or "")
    return m.group(0).strip(".") if m else ""


def extract_linkedin(text):
    m = LINKEDIN_RE.search(text or "")
    if not m:
        return ""
    url = m.group(0).rstrip("/.,;")
    if not url.lower().startswith("http"):
        url = "https://" + url
    return url


def extract_github(text):
    m = GITHUB_RE.search(text or "")
    if not m:
        return ""
    url = m.group(0).rstrip("/.,;")
    if not url.lower().startswith("http"):
        url = "https://" + url
    return url


def extract_phone(text):
    """Return (display, e164_guess). Picks the most phone-like number in the text."""
    text = text or ""
    best, best_score = "", -1
    for m in PHONE_CANDIDATE_RE.finditer(text):
        raw = m.group(0)
        digits = re.sub(r"\D", "", raw)
        if not 9 <= len(digits) <= 15:
            continue
        if DATE_LIKE_RE.match(raw):
            continue
        score = 0
        if raw.strip().startswith("+"):
            score += 3
        if digits.startswith("995"):
            score += 2
        if len(digits) == 9 and digits[0] == "5":  # Georgian mobile
            score += 2
        if digits[0] == "0":
            score += 1
        context = text[max(0, m.start() - 25):m.start()]
        if PHONE_HINT_RE.search(context):
            score += 3
        if score > best_score:
            best, best_score = raw, score
    if not best:
        return "", ""
    display = re.sub(r"\s+", " ", best.strip())
    digits = re.sub(r"\D", "", best)
    if best.strip().startswith("+"):
        e164 = "+" + digits
    elif digits.startswith("00"):
        e164 = "+" + digits[2:]
    elif digits.startswith("995") and len(digits) == 12:
        e164 = "+" + digits
    elif len(digits) == 9 and digits[0] == "5":
        e164 = "+995" + digits
    else:
        e164 = display
    return display, e164


def _looks_like_name(line):
    words = [w for w in re.split(r"\s+", line) if w]
    if not 2 <= len(words) <= 4:
        return False
    if any(w.lower().strip(".,") in NAME_STOPWORDS for w in words):
        return False
    if not all(WORD_RE.match(w) for w in words):
        return False
    # Latin/Cyrillic words must be capitalised; Georgian script has no case.
    return not any(not ("Ⴀ" <= w[0] <= "ჿ") and not w[0].isupper() for w in words)


def guess_name(text):
    """Best-effort full name from the top of the CV. Empty string when unsure."""
    lines = [line.strip(" \t•|-–—:*_") for line in (text or "").splitlines()]
    candidates = [line for line in lines if line][:25]
    # 1. An explicit "Name: ..." label anywhere near the top wins.
    for line in candidates:
        m = NAME_LABEL_RE.match(line)
        if m:
            value = m.group(1).strip(" .,;")
            if _looks_like_name(value) and not any(ch.isdigit() for ch in value):
                return value.title() if value.isupper() else value
    # 2. Otherwise the first capitalised 2-4 word line without digits/links.
    for line in candidates[:12]:
        low = line.lower()
        if "@" in line or "http" in low or "www." in low:
            continue
        if any(ch.isdigit() for ch in line):
            continue
        if _looks_like_name(line):
            return line.title() if line.isupper() else line
    return ""


def extract_contacts(text):
    phone_display, phone_e164 = extract_phone(text)
    return {
        "email": extract_email(text),
        "phone": phone_e164 or phone_display,
        "phone_display": phone_display,
        "linkedin": extract_linkedin(text),
        "github": extract_github(text),
        "name": guess_name(text),
    }
