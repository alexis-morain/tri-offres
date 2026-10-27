#!/bin/sh
set -e

# With a command (the scheduler, a one-off `manage.py`), run it as given: only the web role migrates.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

python manage.py migrate --noinput
# Bind, workers and the absence of an access log are in docker/gunicorn.conf.py: no other option here.
exec gunicorn config.wsgi:application -c docker/gunicorn.conf.py
