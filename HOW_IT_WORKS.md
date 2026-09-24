# How the module works, top to bottom

## In one paragraph

You describe a job as a list of requirements. Every CV that lands on an applicant of that
job is read by the module, checked requirement by requirement, and turned into **one match
percentage** with the evidence for each requirement. Applicants at or above the job's
threshold are flagged as a match: their contacts are filled in from the CV, they get a tag
and a "contact candidate" activity, and an invitation e-mail is drafted. The AI runs on your
own computer through Ollama: no API key, no cost per CV, no CV ever leaves the machine.
The module never rejects anybody; it sorts and explains, a person decides.

## 1. What you set up: the job's criteria

On the job (the quick "create job" dialog or the job's **AI** tab):

- **Description** and **free-text requirements** - context the AI reads.
- **Minimum experience (years)** - relevant experience; counts as a must-have.
- **Requirements**, each with
  - *keywords / synonyms*: words that prove it when found in the CV (`python, django, flask`);
  - *weight* 1-10: how much it matters;
  - *must have*: if it is missing the applicant can only be a partial match.
- **Threshold** (default 80 %) and what happens to matches (tag, activity, optional stage).

## 2. How a CV gets in

- **E-mail**: Odoo's own mail gateway turns every mail of the connected mailbox (or of a
  job's alias address) into an applicant and attaches the files. The AI never reads a mailbox.
- **By hand**: *Recruitment > AI analysis > Upload CVs* (many files, one applicant each), or
  the *Upload a CV* control on an applicant.
- Any other way a file is attached to an applicant (chatter, API).

A hook on attachments notices a CV-like file (PDF, DOCX, TXT) on an applicant and puts the
applicant in the **queue** (`AI status = Queued`).

## 3. Who does the work: the background worker

Analyses never run inside a web request or a scheduled job, because Odoo restarts its
server when either runs longer than two minutes and a local model can need longer on a
slow computer. Instead the module has its own **background worker thread**: it takes the
queued applicants one after another until the queue is empty. It is started whenever
something is queued; a scheduled job checks every two minutes that it is running while CVs
wait. The **Analyze CV** button hands the CV to the same worker and waits up to 90 seconds
to show the result. A database lock guarantees that one applicant is never analysed twice
at the same time, and the result is written in a short, fresh transaction, so saving the
applicant meanwhile cannot break the analysis.

## 4. What happens to one CV (the pipeline)

1. **Text** is extracted from the file (pypdf / python-docx). Scanned images have no text;
   the log says so.
2. **Contacts** - e-mail, phone (Georgian `+995` aware), LinkedIn, name - are found with
   plain pattern matching. No AI here, so nothing can be invented.
3. **Every requirement is checked three ways**
   - *keywords*: is one of your words in the CV? (exact, instant, always the same result);
   - *meaning*: a multilingual embedding model (`bge-m3`) compares the requirement with each
     part of the CV; only a clearly strong similarity counts;
   - *the language model* (`gemma3:4b`) reads the CV against the job and answers, for each
     requirement, met / partly met / not met, **with the exact words of the CV that prove it**
     and a one-sentence explanation in Georgian. It also writes the summary, strengths and gaps.
4. **The evidence policy** decides what the model's opinion is worth, because a language
   model can be wrong or invent things:
   - keyword found or strong similarity: the requirement is proven, the model only explains;
   - otherwise the model's quote must really be in the CV; a claim it cannot point to counts
     for nothing;
   - if you typed keywords and none of them is in the CV, a verified claim is worth half at
     most ("the CV says it in other words - add that wording to your keywords").
   - *minimum experience*: the model reads the years of experience relevant to the job; the
     date ranges in the CV cap that number.
5. **The score** is simple arithmetic you can check on the report:

       met = full weight,  partly met = half,  not met = 0
       match % = points gained / all points          e.g. 16 of 18 points = 89 %

   There is one percentage. (The model also gives its own rough overall guess; it is only
   kept in the technical log, and only used as the score when a job has no requirements.)
6. **The verdict**: *Match* = at or above the threshold and no must-have missing;
   *Partial* = within 25 points below it, or above it with a must-have missing; else *No match*.
7. **Text clean-up**, all deterministic: the candidate's name is restored to the CV's
   spelling, words from other alphabets are removed, repeated gaps are merged, list items
   with garbled words are dropped, and text the model wrote in the wrong language is
   translated by a second short call.

## 5. What the module writes

- Score, verdict, threshold used, summary, strengths, gaps, one result line per requirement
  (status, points, keywords found, the quote, the AI's explanation).
- Name / e-mail / phone / LinkedIn - only into empty fields (a setting allows overwriting).
- For matches (if the job says so): the **AI Match** tag, a higher priority, one
  **Contact candidate** activity, an optional stage move.
- An **invitation e-mail draft** from a fixed Georgian template (a setting lets the model
  write it). *Contact candidate* opens Odoo's composer pre-filled; you press send.
- A short note in the chatter, only when the result changed.

## 6. Where you see it

- **Job positions**: cards with counters, AI matches, criteria count, average score, queue.
- **Applicant**: a banner under the name (score ring, verdict, first sentence of the
  summary, the AI buttons) and the **AI** tab with the full report, the e-mail draft and,
  collapsed, the technical log and the extracted CV text.
- **Applicants list / kanban**: AI % and verdict, filters and grouping by verdict.
- **Recruitment > AI analysis**: totals, progress, engine status, bulk upload,
  "analyse new" / "re-analyse everything", shortcuts to the lists, latest results.
- **Settings > AI CV Screening**: Ollama URL, models, language, default threshold, switches.

## 7. If something is missing

| Situation | What happens |
|---|---|
| Ollama is off / model not downloaded | keywords and contacts still work; the page says so |
| No GPU (or a laptop on battery) | same results, minutes instead of seconds per CV |
| Scanned PDF, old `.doc` | the analysis fails with a clear message in the log |
| Job without requirements | the score is the model's overall estimate |
| Applicant without a job | contacts and summary are filled, the score is the overall estimate |

## 8. What is in the folder

| Path | Purpose |
|---|---|
| `tools/` | the engine, plain Python with no Odoo: text extraction, contacts, matcher + evidence policy, experience from dates, Ollama client, prompts, text clean-up, `selftest.py` |
| `models/` | Odoo side: job criteria, applicant fields + worker + pipeline, the visual report, the AI analysis page, settings, attachment hook, language hook |
| `views/`, `static/src/scss/` | screens and the module's stylesheet (`o_hrai_*`) |
| `data/` | default settings, the scheduled watchdog, the tag, core-screen translations |
| `i18n/` | Georgian translation of the module |
| `scripts/`, `requirements.txt`, `INSTALL.md` | everything needed to set it up elsewhere |
| `dev/i18n_check.py` | quality gate for the Georgian texts |
