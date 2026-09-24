# -*- coding: utf-8 -*-
"""Quality gate for the Georgian translations. Run it after changing any text.

    <odoo python> dev/i18n_check.py                 check i18n/ka.po and data/i18n_core/ka.po
    <odoo python> dev/i18n_check.py --merge POT     first merge a freshly exported template
                                                    (new strings are added, old ones kept)
    <odoo python> dev/i18n_check.py --dict DIR      also spell-check with a Hunspell dictionary
                                                    (DIR contains ka_GE.aff and ka_GE.dic)

Export a fresh template with:
    odoo-bin i18n export -c odoo.conf -d <db> -l pot -o hr_recruitment_ai.pot hr_recruitment_ai

What it checks, because these mistakes really happened while translating:
  * untranslated entries;
  * letters that are neither Georgian nor basic Latin inside a translation (Cyrillic,
    Armenian or Greek look-alikes are invisible to the eye but wrong);
  * placeholders (%s, %(name)s, {{0}}) and HTML tags that differ from the source text;
  * optionally, words unknown to a Hunspell Georgian dictionary. Most unknown words are
    valid inflected forms; read the list, a few are real mistakes.

Needs `polib` (ships with Odoo). The spell-check needs `pip install spylls` and the free
dictionary from https://github.com/wooorm/dictionaries/tree/main/dictionaries/ka
(save index.aff / index.dic as ka_GE.aff / ka_GE.dic).
"""
import argparse
import os
import re
import sys

import polib

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PO_FILES = [
    os.path.join(MODULE_DIR, "i18n", "ka.po"),
    os.path.join(MODULE_DIR, "data", "i18n_core", "ka.po"),
]
GEORGIAN = (0x10D0, 0x10FF)
ALLOWED_EXTRA = {0x201E, 0x201C, 0x201D, 0x2013, 0x2014, 0x2026, 0x2022, 0x2116}
TOKEN_RE = re.compile(r"%\([a-z_]+\)[sd]|%\([a-z_]+\)\.\d+f|%\.\d+f|%[sd]|%%|\{\{\d+\}\}|<[^>]+>")
WORD_RE = re.compile("[%s-%s]+" % (chr(GEORGIAN[0]), chr(GEORGIAN[1])))


def bad_characters(text):
    out = []
    for ch in text:
        code = ord(ch)
        if code < 0x80 or GEORGIAN[0] <= code <= GEORGIAN[1] or code in ALLOWED_EXTRA:
            continue
        out.append("%s (U+%04X)" % (ch, code))
    return out


def check_file(path, dictionary=None):
    po = polib.pofile(path)
    problems, unknown = [], {}
    for entry in po:
        if entry.obsolete or not entry.msgid:
            continue
        if not entry.msgstr:
            if re.sub(r"<[^>]+>", "", entry.msgid).strip():
                problems.append("UNTRANSLATED: %s" % " ".join(entry.msgid.split())[:110])
            continue
        bad = bad_characters(entry.msgstr)
        if bad:
            problems.append("FOREIGN LETTERS %s in: %s" % (", ".join(bad), entry.msgstr[:90]))
        if sorted(TOKEN_RE.findall(entry.msgid)) != sorted(TOKEN_RE.findall(entry.msgstr)):
            problems.append("PLACEHOLDER/TAG MISMATCH: %s" % " ".join(entry.msgid.split())[:110])
        if dictionary:
            for word in WORD_RE.findall(entry.msgstr):
                if len(word) > 1 and not dictionary.lookup(word):
                    unknown[word] = unknown.get(word, 0) + 1
    print("%s: %d entries, %d problem(s)" % (os.path.relpath(path, MODULE_DIR), len(po), len(problems)))
    for line in problems:
        print("   " + line)
    if dictionary:
        print("   words not in the dictionary (review, most are valid inflections): %d" % len(unknown))
        for word in sorted(unknown):
            print("      %s  ->  %s" % (word, ", ".join(list(dictionary.suggest(word))[:3]) or "-"))
    return len(problems)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--merge", metavar="POT", help="merge this exported template into i18n/ka.po first")
    parser.add_argument("--dict", metavar="DIR", help="folder with ka_GE.aff / ka_GE.dic for spell-checking")
    args = parser.parse_args()

    if args.merge:
        po = polib.pofile(PO_FILES[0])
        po.merge(polib.pofile(args.merge))
        po.save(PO_FILES[0])
        print("merged %s into i18n/ka.po" % args.merge)

    dictionary = None
    if args.dict:
        from spylls.hunspell import Dictionary
        dictionary = Dictionary.from_files(os.path.join(args.dict, "ka_GE"))

    total = sum(check_file(path, dictionary) for path in PO_FILES if os.path.exists(path))
    print("OK" if not total else "%d problem(s) to fix" % total)
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
