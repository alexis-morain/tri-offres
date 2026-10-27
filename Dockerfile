FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/data/tri-offres.sqlite3

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app \
    && mkdir /data && chown app:app /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The actor, run by `manage.py collecte` in a subprocess (jobs/scraper.py), with its own venv: no version conflict
# with Django's dependencies. vendor/actor comes from scripts/fetch_actor.sh and must be at the commit of ACTOR_REF.
# The image also builds without it (`vendor*` may match nothing): the collection then says the actor is missing.
# vendor must be a real folder or absent: the build context does not follow a symbolic link (a worktree whose
# vendor is one fails with `"/vendor": not found`; copy vendor/actor into a real folder, or remove the link).
COPY ACTOR_REF vendor* /opt/stage/
RUN if [ -f /opt/stage/actor/REF ]; then \
        if [ "$(cat /opt/stage/actor/REF)" != "$(cat /opt/stage/ACTOR_REF)" ]; then \
            echo "vendor/actor n’est pas au commit de ACTOR_REF : relancer scripts/fetch_actor.sh." >&2; exit 1; fi \
        && mv /opt/stage/actor /opt/actor \
        && python -m venv /opt/actor-venv \
        && /opt/actor-venv/bin/pip install --no-cache-dir -r /opt/actor/requirements.txt; \
    else echo "Image sans actor : vendor/actor est absent." >&2; fi \
    && rm -rf /opt/stage
ENV SCRAPER_DIR=/opt/actor \
    SCRAPER_PYTHON=/opt/actor-venv/bin/python \
    SCRAPER_STORAGE=/data/scraper

COPY . .
# The secret is a throwaway for collectstatic only; the real one comes from the environment at run time.
RUN DJANGO_SECRET_KEY=build-only-collectstatic python manage.py collectstatic --noinput \
    && chmod +x docker/entrypoint.sh

USER app
# The `claude` CLI for the oauth engine (TRI_DEPLOY_ENGINE=oauth, scripts/deploy.sh), at the version tried
# on the workstation.
ENV HOME=/home/app PATH=/home/app/.local/bin:$PATH
RUN curl -fsSL https://claude.ai/install.sh | bash -s 2.1.296 && claude --version
VOLUME /data
EXPOSE 8000

ENTRYPOINT ["docker/entrypoint.sh"]
