"""The container's probe: GET /healthz on the local port with the Host header of the site.

Django refuses a Host that is not in DJANGO_ALLOWED_HOSTS, so a probe that says `localhost` fails as soon as the
list is the site's name alone (S16). The first entry of the list is used. A failed request ends with a non-zero code.
"""
import os
import urllib.request

URL = "http://127.0.0.1:8000/healthz"


def probe_host(environ):
    first = environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")[0].strip().lstrip(".")
    return "localhost" if first in ("", "*") else first


def probe(environ):
    request = urllib.request.Request(URL, headers={"Host": probe_host(environ)})
    urllib.request.urlopen(request, timeout=3)


if __name__ == "__main__":
    probe(os.environ)
