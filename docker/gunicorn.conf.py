"""gunicorn's settings (read by docker/entrypoint.sh: `gunicorn config.wsgi:application -c docker/gunicorn.conf.py`).

No access log, on purpose: the path of a request holds a credential (/desinscription/<token>/ and
/connexion/lien/<token>/), and gunicorn would write it for every request. Only errors are written, on the
container's standard error. Keep it that way: no `--access-logfile`, no GUNICORN_CMD_ARGS (a test guards both).
"""
bind = "0.0.0.0:8000"
workers = 2
worker_class = "gthread"
threads = 4
# With gthread this no longer cuts a request: the engines' own delays bound the calls to Claude.
timeout = 90
accesslog = None
errorlog = "-"
capture_output = False
