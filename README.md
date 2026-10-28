# tri-offres

A daily job alert for people looking for work in France. You create an account, answer a short questionnaire, and
every weekday morning you get a mail with the offers that fit you, each scored and explained by Claude. A sort page
lets you swipe through them; your answers refine the next scores. Nothing is ever applied for on your behalf.

The interface and the mails are in French only. The code, comments and docs are in English.

**Status: pre-beta.** The sign-in, the questionnaire, the collection, the scoring, the sort page, the morning mail,
the data-protection pages, the OpenRouter key of each person, a scheduler and its monitoring are coded and tested. One hosted
instance is run by the authors; it is not open to the public yet. This README describes what the code does today.

## What it does

For the person looking for work:

1. **Account.** Sign in with a code or a link sent by mail, no password.
2. **Questionnaire**, open to every sector. It starts with "Which job are you looking for?": one to three job
   titles typed freely, with suggestions from the French ROME job register (France Travail); the job domain is
   deduced from them, or picked among the 22 ROME domains if no title fits. Then hard criteria (place and distance,
   remote work, contract types, salary or daily rate floor, part time, optional keywords that the offer's title must
   contain), then weighted preferences (including the employer's sector) and free text ("what I look for, what I
   refuse"). A CV (PDF or .docx) is optional. A live counter shows how many recent offers pass the criteria.
3. **Summary.** Claude writes "what I understood of your search", which the person can edit.
4. **First sort, right away.** Recent offers are scored on the spot and sorted on the final screen.
5. **Morning mail**, on weekdays: the offers scored 5 out of 10 and above, with a short reason each, a link to the
   offer's page at its source, and a single button to the sort page. No yes/no links in the mail (corporate
   antivirus software clicks every link).
6. **Sort page** (`/tri/`): swipe right (interested), left (not for me), "maybe" and undo; keyboard and plain buttons
   work too, and the page works without JavaScript.
7. **What I learned.** Once a few answers are in, Claude writes a short summary of what the answers say. The person
   can correct it or delete it.
8. **Own data.** "Ma recherche" and "Mes données" show and edit everything stored. Unsubscribe in one click from any
   mail (link in the footer and the RFC 8058 one-click header), resubscribe from "Mes données", delete the account
   for good from the same page. Privacy page and legal notice at `/confidentialite/` and `/mentions-legales/`.

Every score is labelled "Notes générées par IA, la décision vous appartient" (AI-generated scores, the decision is
yours).

## How it works

```
manage.py collecte      runs the collector (a separate program, see below) and imports new offers
manage.py notation      phase "resumes": one short summary and a ROME code per offer (Claude Haiku 5.5)
                        phase "personnes": scores each person's candidate offers in batches (Claude Sonnet 5.5)
manage.py envoi         sends the morning mail (weekdays)
manage.py synthese_apprise          writes the "what I learned" summary of each person
manage.py relance_onboarding        reminds people who left the questionnaire unfinished
manage.py purge_offres              deletes full texts after 7 days and offers after 45 days
manage.py purge_login_codes         deletes old sign-in codes
manage.py nuit nuit|matin|soir      runs the commands of one time slot, with monitoring (see below)
manage.py planificateur             the scheduler: starts `nuit <slot>` when a slot is due
```

- Django 5.2, Python 3.12, SQLite in WAL mode, gunicorn, whitenoise. Pages are rendered on the server.
- Hard criteria are applied by code, without Claude. Only the offers that pass them reach Claude.
- One call to Claude never mixes two people. Output is validated against a JSON schema; any text Claude writes and
  the site shows again goes through an allowlist that refuses links, domains, mail addresses and phone numbers.
- The commands above have a `--dry-run`, except `planificateur`. `manage.py <command> --help` lists the options.

## Sources and safeguards

Offers come from 22 French sources: generalist job boards, public sector sites, culture and book trade sites, and
the career pages that employers host on some recruiting software. One sheet per source, with what is kept and how to
cut it: [docs/sources/](docs/sources/README.md).

What the service keeps, and what it never does:

- **Kept for each offer:** title, employer, place, contract, dates, salary when given, the link to the offer's page
  at its source, and a short summary written by Claude. The full text is only used for the night's scoring and is
  deleted 7 days after collection; the offer itself is deleted after 45 days or once it expires (an offer that
  carries a person's answer keeps its fields, never its text).
- **Traffic goes back to the source:** every offer card links to the offer's page on the source site, never to a
  direct application link, and shows the source's name, plus its attribution text when the source requires one.
- **A source can be cut in one action** from the admin: "Couper et supprimer ses offres" disables it and deletes its
  offers (superusers only). The procedure for a site asking to be removed is in
  [docs/retrait-source.md](docs/retrait-source.md) (in French).
- **No recruiter contact field** is imported. A name or a contact written inside an offer's text leaves with that
  text, after 7 days at most.
- **Data from third-party sites is checked field by field** at import; an absurd item is dropped, never trusted.

Each site has its own terms of use. If you run your own instance, checking them and answering the sites' requests
is your job, not the authors'.

Personal data:

- **CV:** read in memory, never written to disk; its text is extracted in an isolated subprocess, contact details are
  removed before Claude sees it, and only the extracted profile (positions, skills, education) is stored.
- **Mails:** only to people who signed up; unsubscribed or deleted accounts are checked again right before each
  send.
- **Account deletion** erases the person and everything linked to them at once, including their sessions.
- Logs carry account keys, never mail addresses, tokens or profile content.

## Self-hosting with Docker

This is possible but not supported: the authors run one hosted instance and answer no support requests for others.

### What you need

- Docker with Compose.
- **An OpenRouter account** (`CLAUDE_ENGINE=api`, `OPENROUTER_API_KEY`) for the calls that serve no one in
  particular (the offers' pre-pass), at your cost. Every call made for a person (CV extraction, summary, scoring)
  goes out on that person's own OpenRouter key, which they connect by OAuth and which is sealed with
  `OPENROUTER_KEYS_FERNET`. The site never falls back to your key for them. The other engine, `oauth`, runs `claude -p` on a Claude subscription:
  it exists for local development only, and the code refuses to use it for any account outside `DEV_EMAILS`. A
  personal Claude subscription must never serve other people.
- **A Resend account** to send mail (`RESEND_API_KEY`), and a sender address on a domain you control. Without the
  key, mails are printed to the container log.
- **A collector.** This is the main limit today: see the next section.

### The collector is not included

`manage.py collecte` runs an external program, *Francophone Jobs Scraper*, as a subprocess (`jobs/scraper.py`) and
reads the offers it writes in a local folder. That program is proprietary, by the same author, and is not part of
this repository. The `Dockerfile` copies it from `vendor/actor/` at the commit written in `ACTOR_REF`, which
`scripts/fetch_actor.sh` fills from a private clone.

As a consequence, **the image does not build without `vendor/actor/`**, and without a collector no offers come in:
accounts, the questionnaire, scoring and mails work, but on an empty offer table. Making the collector optional is
on the list of things to do before the beta.

### Configuration

Settings are read from environment variables (`config/settings.py`). There is no `.env` loader. With Docker, a
variable reaches the container only if the `environment:` block of `docker-compose.yml` lists it; the block reads
its values from your shell, or from a `.env` file next to it, which Compose uses for interpolation only. Never add
`env_file:` pointing at a file that holds other secrets.

| Variable | Default | What it does |
|---|---|---|
| `DJANGO_SECRET_KEY` | none | Required unless `DJANGO_DEBUG=1`. Signs sessions and unsubscribe links |
| `DJANGO_SECRET_KEY_FALLBACKS` | empty | Old keys, comma-separated, so that old unsubscribe links keep working after a rotation |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated host names |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | empty | Needed behind a proxy that serves HTTPS |
| `SITE_URL` | `http://localhost:8000` | Public address used in mails. The one-click unsubscribe header is only sent with `https://` |
| `DATABASE_PATH` | `/data/tri-offres.sqlite3` in the image | SQLite file |
| `CLAUDE_ENGINE` | empty (no Claude) | `api` for self-hosting; `fake` is used by the tests |
| `OPENROUTER_API_KEY` | none | The site's own OpenRouter key, for the shared calls of the `api` engine (never a person's) |
| `OPENROUTER_KEYS_FERNET` | none | Fernet keys, comma-separated: the first seals the people's OpenRouter keys, all of them open (rotation). **Required** with `CLAUDE_ENGINE=api`. Generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`; lose it and everyone reconnects |
| `RESEND_API_KEY` | none | Without it, mails go to the log |
| `DEFAULT_FROM_EMAIL` | `tri-offres <noreply@example.org>` | Sender of every mail. **Required** when `RESEND_API_KEY` is set and `DJANGO_DEBUG` is off: the site refuses to start without it |
| `MAIL_REPLY_TO` | `contact@example.org` | Reply-to of every mail, and the contact address of the legal pages. Required in the same case |
| `LEGAL_NAME`, `LEGAL_ADDRESS`, `LEGAL_SIRET` | `Nom de l’éditeur`, empty, empty | The publisher on the legal notice; an empty address or SIRET hides its line. `LEGAL_NAME` is required in the same case as the two above |
| `ALERT_EMAIL` | `MAIL_REPLY_TO` | Where the scheduler sends its alerts |
| `HEALTHCHECKS_PING_URL` | empty | healthchecks.io ping URL (a secret); empty means no ping |
| `BETA` | `1` | While `1`, the site says « bêta gratuite » (wording only) |
| `ADMIN_ACCESS_TEAM_DOMAIN`, `ADMIN_ACCESS_AUD` | empty | With an `https://` `SITE_URL`, `/admin` answers 404 unless the request carries a valid Cloudflare Access token for this team and audience. Over `http://` (local use) the guard is off and the admin login of Django applies |
| `HSTS_SECONDS` | empty | Empty means one hour in HTTPS and off in HTTP |
| `DEV_EMAILS` | empty | Addresses the `oauth` engine may serve (development only) |
| `BETA_CAPACITY` | `80` | Accounts accepted before new sign-ups go to a waiting list |
| `LOGIN_MAIL_DAILY_CAP` | `60` | Sign-in mails per day for the whole site |
| `CV_DAILY_CAP`, `SUMMARY_DAILY_CAP`, `ON_DEMAND_DAILY_CAP` | `40`, `150`, `40` | Daily caps on Claude calls made from the site |
| `NIGHT_SCORE_CAP`, `PREPASS_NIGHT_CAP` | `2000`, `2000` | Caps on Claude calls per night |
| `TRUST_CF_CONNECTING_IP` | off | Read the client IP from Cloudflare's header; only behind a Cloudflare tunnel |
| `SCRAPER_PROXY_URL` | empty | HTTP proxy the collector goes out by |
| `SCRAPER_PROXY_REQUIRED` | off | If on, `collecte` refuses to run without the proxy |
| `SCRAPER_MAX_RESULTS_PER_SOURCE`, `SCRAPER_MAX_MINUTES_PER_SOURCE`, `SCRAPER_TIMEOUT_S` | `1000`, `30`, `5400` | Collector limits |

Nothing personal is written in the code: the sender, the reply-to address and the publisher's name come from the
environment, so an instance never writes in someone else's name.

### Running it

```bash
docker compose up -d --build        # migrates the database, then serves on http://localhost:8000
docker compose exec web python manage.py createsuperuser
```

Run management commands with `docker compose exec web python manage.py …` in the running container (or
`docker compose run --rm web python manage.py …`: the entrypoint runs a given command as it is, without migrating).

`docker-compose.yml` is written for local use: it publishes port 8000 directly and does not list
`OPENROUTER_API_KEY` nor `RESEND_API_KEY` (it does pass `CLAUDE_ENGINE` and `OPENROUTER_KEYS_FERNET` from your shell). Add the
keys you use to its `environment:` block for a real instance, and put the site behind an HTTPS proxy. The admin is at
`/admin/`, a health check at `/healthz`.

### Running the night

`docker compose up` starts two services from the same image: `web`, and `scheduler`
(`manage.py planificateur`), which starts `manage.py nuit <slot>` at three times of the day, Paris time:

| Slot | Time | Steps |
|---|---|---|
| `nuit` | 04:00 | `collecte` and `notation` (weekdays), then the purges (offers, sign-in codes, journal, sessions, task records); `synthese_apprise` on Saturdays |
| `matin` | 07:00 | `envoi`, the morning mail (weekdays) |
| `soir` | 18:00 | `relance_onboarding` |

Each pass is recorded (`TaskRun`), pinged to healthchecks.io when `HEALTHCHECKS_PING_URL` is set, and mailed to
`ALERT_EMAIL` when it fails or is killed. The admin shows the figures at `/admin/tableau/`. A slot can be run by hand:
`docker compose exec web python manage.py nuit matin --dry-run`.

The collection may take more than an hour. Back up `/data` (the database, and `/data/scraper`, where the collector
keeps the list of offers it has already seen); `docker/litestream.yml` and [docs/restauration.md](docs/restauration.md)
describe a continuous replica of the database to an S3-compatible bucket.

### Deploying on a server

`scripts/deploy.sh` deploys over SSH to a host that runs its apps with `midclt` (TrueNAS SCALE), from your workstation; every host, path and address
comes from `TRI_DEPLOY_*` variables, and the script keeps the secrets out of the compose file. It is a personal tool
written for that setup: read [docs/deploiement.md](docs/deploiement.md) (in French) before adapting it.

## Development

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python manage.py test                       # the test suite, with the fake Claude engine
.venv/bin/python manage.py test e2e.test_onboarding   # end-to-end tests, Playwright and axe-core
DJANGO_DEBUG=1 DATABASE_PATH=/tmp/tri-offres-dev.sqlite3 .venv/bin/python manage.py migrate
DJANGO_DEBUG=1 DATABASE_PATH=/tmp/tri-offres-dev.sqlite3 .venv/bin/python manage.py runserver
```

With `DJANGO_DEBUG=1` and no `RESEND_API_KEY`, the sign-in code is printed in the console. The tests never call
Claude nor the network. The visual system is described in [DESIGN.md](DESIGN.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Contributions need a signed Contributor License Agreement.

## License

tri-offres is free software under the [GNU Affero General Public License v3.0 or later](LICENSE) (`AGPL-3.0-or-later`). If you run a modified
version as a network service, you must offer its source to its users.

Third-party material shipped in this repository keeps its own license:

- axe-core, used by the end-to-end tests only: MPL-2.0 ([details](e2e/vendor/LICENCES.md)).
- `jobs/data/rome-titles.json.gz`: built from the ROME of France Travail, Licence Ouverte; `jobs/data/communes.json.gz`:
  built from the API Découpage administratif (geo.api.gouv.fr), Licence Ouverte Etalab 2.0. Build scripts in
  `scripts/`.
