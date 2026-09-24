# Installing the module on another computer or on your real Odoo 19

Everything the module needs travels inside this folder (or `hr_recruitment_ai.zip`):
code, screens, the stylesheet, the Georgian translations (including the translation of
Odoo's own Recruitment screens), setup scripts and developer tools.

Three things live outside the folder and must exist on every computer:

| What | Why | How it gets there |
|---|---|---|
| Odoo 19, self-hosted | the module is an Odoo add-on | already on your server |
| Python packages `pypdf`, `python-docx` | reading PDF / DOCX files | `pip install -r requirements.txt` |
| Ollama + two models (about 5 GB) | the free local AI | `scripts/setup_ollama.*` |

Your **data** (jobs, criteria, applicants, CV files, settings, mail servers) lives in the
Odoo database, not in the module. It moves with a database backup.

Odoo Online (the SaaS at `*.odoo.com`) cannot install custom modules. Odoo.sh can, but
Ollama must then run on a machine of yours (see "One AI server for several computers").

---

## A. Putting it on your real (production) database - step by step

Do the steps in this order. Steps 1-2 protect you; do not skip them.

1. **Back up the real database.** Open `https://<your-odoo>/web/database/manager`,
   *Backup*, format *zip (includes filestore)*. Keep the file.
2. **Rehearse on a copy.** Same page: *Duplicate* the real database (e.g. `mycompany-test`),
   and do steps 6-10 on the copy first. When everything works there, repeat on the real one.
3. **Copy the module.** Unzip `hr_recruitment_ai.zip` into the server's custom addons
   directory. Check that this directory is listed in `addons_path` in `odoo.conf`, e.g.

       addons_path = C:\Program Files\Odoo 19.0\server\odoo\addons,C:\odoo\custom_addons

4. **Install the Python packages** with the Python that runs Odoo:

       Windows:  "C:\Program Files\Odoo 19.0.<build>\python\python.exe" -m pip install -r requirements.txt
       Linux:    pip install -r requirements.txt      (same virtualenv / user as odoo-bin)
       Docker:   add the two packages to your image, or pip install inside the container

5. **Install Ollama and the models on the server** (or on another machine, see below):

       Windows:  powershell -ExecutionPolicy Bypass -File scripts\setup_ollama.ps1
       Linux:    ./scripts/setup_ollama.sh

   *Windows servers:* the Ollama app starts when a user logs in, not at boot. On a server
   nobody logs in to, register it to start with Windows: Task Scheduler > Create Task >
   trigger "At startup", action `"%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve`,
   "Run whether user is logged on or not". On Linux the installer creates a systemd
   service, nothing to do.
6. **Restart the Odoo service** so it sees the new folder.
7. **Install the module.** Turn on developer mode (*Settings > Developer Tools > Activate*),
   then *Apps > Update Apps List*, search **AI CV Screening**, *Install*. Recruitment is
   installed automatically if it is not there yet. From a terminal instead:

       odoo-bin -c odoo.conf -d <database> -i hr_recruitment_ai --stop-after-init

8. **Check the AI engine.** *Recruitment > Configuration > Settings > AI CV Screening >
   Test connection* must say the two models are ready.
9. **Georgian.** *Settings > Translations > Languages*: activate Georgian and choose it in
   your user preferences. The module imports its translation of the core Recruitment
   screens by itself, whether Georgian was active before or is activated later.
10. **Try it.** Create a job with criteria, open *Recruitment > AI analysis*, upload two or
    three real CVs by hand and read the reports. Adjust keywords and weights until the
    scores match your own judgment. Only then:
11. **Connect the mailbox** ("Receiving CVs by e-mail" in `README.md`) so CVs arrive alone.

**Hardware.** A GPU with 6 GB+ memory analyses a CV in about 15 seconds. Without a GPU it
takes one to three minutes per CV. That is fine: the analysis runs in the module's own
background worker, never inside a web request, so a slow server stays responsive and
Odoo's time limits do not apply to it.

**Updating the module later.** Replace the folder, restart Odoo, *Apps > AI CV Screening >
Upgrade* (or `odoo-bin -u hr_recruitment_ai -d <database> --stop-after-init`). Your data stays.

**Removing it.** *Apps > AI CV Screening > Uninstall* removes the module's own fields and
results (scores, reports, criteria). Applicants, their CV files and everything else stay.

---

## B. Moving to a different computer (developer / second office)

1. Copy `hr_recruitment_ai.zip`, unzip it into that Odoo's addons directory (step A3).
2. `pip install -r requirements.txt` (step A4).
3. `scripts/setup_ollama.ps1` or `.sh` (step A5).
4. Restart Odoo, install the module (steps A6-A8).
5. To bring your data too: on the old computer *Backup* the database
   (`/web/database/manager`), on the new one *Restore Database*. Without a restore you start
   empty and only re-enter the job criteria.

E-mail servers are part of the database, so they move with a backup. On a fresh database
follow "Receiving CVs by e-mail" in `README.md`.

## One AI server for several computers

Ollama does not have to run on the same machine as Odoo. Run it on one strong PC or server
(`OLLAMA_HOST=0.0.0.0`), and on every Odoo set *Settings > AI CV Screening > Ollama URL* to
`http://<that-machine>:11434`. Keep that port inside your own network: Ollama has no password.

## Better Georgian text

The default model (`gemma3:4b`) scores well but writes clumsy Georgian. `gemma3:12b` writes
clearly better Georgian; it needs about 9 GB, so on a 6 GB GPU it runs partly on the CPU
(about two minutes per CV, in the background). `ollama pull gemma3:12b`, then put that name
into *Settings > AI CV Screening > LLM model*. Scores do not depend on the text quality.

## Checking an installation

    <odoo python> tools/selftest.py        engine checks + a live model test if Ollama runs
    <odoo python> dev/i18n_check.py        translation quality gate (see the file header)
