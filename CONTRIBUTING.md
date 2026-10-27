# Contributing to tri-offres

Thanks for your interest. tri-offres is a small side project run by one person: answers may take a few days, and a
change that does not fit the project may be declined even if it is good work. For anything larger than a fix, open
an issue first and describe what you want to change.

## Contributor License Agreement

Every contribution needs a signed [Contributor License Agreement](docs/cla.md) (CLA). On your first pull request,
the CLA Assistant bot posts a comment with a link; you sign once with your GitHub account, and it covers all your
later contributions. A pull request is not merged before the CLA is signed.

Why a CLA: you keep the copyright of your work, and you give the maintainer a licence wide enough to keep offering
the project under the AGPL-3.0 and, if needed, under other terms (for example for the hosted service). Read
[docs/cla.md](docs/cla.md) before you sign.

## Security issues

Do not open a public issue for a vulnerability. Write to the contact address shown on the legal notice of the
hosted instance (`/mentions-legales/`), with the steps to reproduce.

## Working on the code

- Python 3.12. Set up with `python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`.
- Tests first: write the failing test, then the code. `.venv/bin/python manage.py test` must pass, and
  `DJANGO_DEBUG=1 .venv/bin/python manage.py makemigrations --check --dry-run` must report no change.
- Tests never call Claude nor the network: they run with the `fake` engine (`manage.py test` forces it). Mail is
  tested with Django's in-memory backend, the Resend backend by replacing its HTTP opener.
- Pages must work without JavaScript. A new page gets an end-to-end test (`e2e/`) at both widths, with no axe-core
  violation. New colour pairs go into the contrast test of `ui/tests.py`. The visual system is in
  [DESIGN.md](DESIGN.md).
- Code, comments, identifiers and commit messages in English. Text shown to users (templates, mails, messages) in
  French, with the formal « vous », the typographic apostrophe ’ and French quotes « ».
- Lines of 120 characters at most. Comment density like the surrounding code.

## Data rules

These come from the project's legal and privacy constraints; a pull request that breaks one is not merged.

- **No real data in the repository.** Test fixtures are fictional: no real job offer text, no real person, no real
  mail address outside reserved domains such as `example.fr`.
- **No secrets.** Settings come from environment variables; never commit a `.env` file, a key or a token.
- **Never store** the full text of an offer beyond its retention, nor a raw CV: a CV is handled in memory only.
- **Logs** carry account keys, never mail addresses, tokens or profile content.
- **Text written by Claude** and shown again on the site goes through `matching.text.clean_text`.
- **A new source** gets its sheet in [docs/sources/](docs/sources/README.md) in the same pull request that adds it
  to `jobs/actor_sources.py`.

## Pull requests

One change per pull request, with a description of what it does and why, and how you tested it. Keep unrelated
reformatting out of it.
