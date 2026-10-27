"""The Cloudflare Access guard on /admin (S7 to S9, arbitrage A of the design plan).

No test reaches the network: the keys come from a stand-in opener that serves a JWKS made of a key generated here.
"""
import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from unittest import mock

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import Client, SimpleTestCase, TestCase, override_settings

from accounts.models import User
from config import admin_access
from config.http import RefuseRedirects
from config.testing import PLAIN_STATIC
from mail.tests import fake_opener, fake_response

TEAM = "equipe-test.cloudflareaccess.com"
AUD = "a" * 64
ISSUER = f"https://{TEAM}"
HEADER = "HTTP_CF_ACCESS_JWT_ASSERTION"
KID = "key-1"
SITE = "https://tri.example.org"
GUARDED = {"SITE_URL": SITE, "ADMIN_ACCESS_TEAM_DOMAIN": TEAM, "ADMIN_ACCESS_AUD": AUD, "STORAGES": PLAIN_STATIC,
           "ALLOWED_HOSTS": ["tri.example.org", "testserver"]}


def new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


KEY, OTHER_KEY = new_key(), new_key()


def jwk_of(key, kid=KID):
    return {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())), "kid": kid, "alg": "RS256",
            "use": "sig"}


def certs_body(*jwks):
    return json.dumps({"keys": list(jwks), "public_cert": {"kid": "x", "cert": "-----BEGIN"}}).encode()


def claims(**changes):
    now = int(time.time())
    base = {"aud": [AUD], "iss": ISSUER, "exp": now + 600, "iat": now - 5, "nbf": now - 5,
            "email": "admin@example.org", "type": "app"}
    base.update(changes)
    return {key: value for key, value in base.items() if value is not None}


def token(key=KEY, kid=KID, **changes):
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(claims(**changes), key, algorithm="RS256", headers=headers)


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def forged(header, payload, secret=None):
    """A token made by hand: the signature is an HMAC of `secret` when given, empty otherwise."""
    signing = f"{b64(json.dumps(header).encode())}.{b64(json.dumps(payload).encode())}"
    signature = b64(hmac.new(secret, signing.encode(), hashlib.sha256).digest()) if secret else ""
    return f"{signing}.{signature}"


class GuardTestCase(TestCase):
    """Settings in https with the guard configured; the keys fetched through `self.opener`."""

    def setUp(self):
        admin_access.clear_cache()
        self.addCleanup(admin_access.clear_cache)
        self.settings = override_settings(**GUARDED)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.opener = fake_opener(fake_response(body=certs_body(jwk_of(KEY))))
        patcher = mock.patch("config.admin_access.urllib.request.build_opener", return_value=self.opener)
        self.build_opener = patcher.start()
        self.addCleanup(patcher.stop)
        self.client = Client(raise_request_exception=False)

    def get(self, path="/admin/login/", value=None, **extra):
        headers = {} if value is None else {HEADER: value}
        return self.client.get(path, **headers, **extra)

    def assertRefused(self, value, path="/admin/login/"):
        response = self.get(path, value)
        self.assertEqual(response.status_code, 404, value)
        return response

    def assertPasses(self, value, path="/admin/login/"):
        response = self.get(path, value)
        self.assertNotEqual(response.status_code, 404)
        return response


class RefusalTests(GuardTestCase):
    def test_no_header(self):
        self.assertRefused(None)
        self.assertRefused("")
        self.assertEqual(self.opener.open.call_count, 0)  # nothing to verify, no network

    def test_a_present_header_is_not_enough(self):
        for value in ("1", "true", "Bearer " + token(), "x" * 40):
            with self.subTest(value=value[:12]):
                self.assertRefused(value)

    def test_malformed_tokens(self):
        for value in ("a.b.c", "a.b", "....", "\x00\x01", "é.é.é", "e30.e30.", token()[:-5], token() + "x.y",
                      "x" * 100_000):
            with self.subTest(value=value[:12]):
                self.assertRefused(value)

    def test_wrong_audience(self):
        self.assertRefused(token(aud=["b" * 64]))
        self.assertRefused(token(aud=AUD + "x"))
        self.assertRefused(token(aud=[]))
        self.assertRefused(token(aud=None))

    def test_audience_may_be_a_list_or_a_string_and_must_contain_ours(self):
        self.assertPasses(token(aud=[AUD, "c" * 64]))
        self.assertPasses(token(aud=AUD))

    def test_wrong_issuer(self):
        self.assertRefused(token(iss="https://autre.cloudflareaccess.com"))
        self.assertRefused(token(iss=TEAM))
        self.assertRefused(token(iss=None))

    def test_expired_or_without_expiry(self):
        self.assertRefused(token(exp=int(time.time()) - 120))
        self.assertRefused(token(exp=None))

    def test_not_yet_valid(self):
        self.assertRefused(token(nbf=int(time.time()) + 300))
        self.assertRefused(token(iat=int(time.time()) + 300, nbf=None))

    def test_sixty_seconds_of_tolerance_on_the_clock(self):
        self.assertPasses(token(exp=int(time.time()) - 30))
        self.assertPasses(token(nbf=int(time.time()) + 30, iat=int(time.time()) + 30))
        self.assertRefused(token(exp=int(time.time()) - 90))

    def test_alg_none(self):
        self.assertRefused(forged({"alg": "none", "typ": "JWT", "kid": KID}, claims()))
        self.assertRefused(forged({"alg": "None", "typ": "JWT", "kid": KID}, claims()))

    def test_hs256_signed_with_the_public_key(self):
        pem = KEY.public_key().public_bytes(serialization.Encoding.PEM,
                                            serialization.PublicFormat.SubjectPublicKeyInfo)
        self.assertRefused(forged({"alg": "HS256", "typ": "JWT", "kid": KID}, claims(), secret=pem))
        self.assertRefused(forged({"alg": "HS256", "typ": "JWT", "kid": KID}, claims(), secret=b"secret"))

    def test_only_rs256(self):
        for alg in ("RS384", "RS512", "ES256", "PS256", "HS512", ""):
            with self.subTest(alg=alg):
                self.assertRefused(forged({"alg": alg, "typ": "JWT", "kid": KID}, claims(), secret=b"x"))

    def test_signed_by_another_key_under_the_right_kid(self):
        self.assertRefused(token(key=OTHER_KEY))

    def test_unknown_or_missing_or_odd_kid(self):
        self.assertRefused(token(kid="inconnu"))
        self.assertRefused(token(kid=None))
        self.assertRefused(token(kid="k" * 500))
        self.assertRefused(forged({"alg": "RS256", "kid": 5}, claims()))
        self.assertRefused(forged({"alg": "RS256", "kid": ["key-1"]}, claims()))

    def test_keys_unreachable(self):
        for error in (urllib.error.URLError("down"), TimeoutError(), OSError("boom"), ValueError("x")):
            admin_access.clear_cache()
            self.opener.open.side_effect = error
            with self.subTest(error=type(error).__name__):
                self.assertRefused(token())

    def test_keys_answered_with_garbage_or_an_error_page(self):
        for body in (b"", b"<html>", b"[]", b'{"keys": 3}', b'{"keys": [{"kid": "key-1"}]}',
                     b'{"keys": [{"kid": "key-1", "kty": "RSA", "n": "x", "e": "y"}]}', b"\xff\xfe"):
            admin_access.clear_cache()
            self.opener.open.return_value = fake_response(body=body)
            with self.subTest(body=body[:12]):
                self.assertRefused(token())

    def test_oversized_key_document_is_not_read_whole(self):
        self.opener.open.return_value = fake_response(body=b" " * (admin_access.MAX_CERTS_BYTES + 10))
        self.assertRefused(token())

    def test_refused_even_in_a_head_post_or_deep_path(self):
        for path in ("/admin/", "/admin", "/admin/tableau/", "/admin/accounts/user/", "/admin/login/?next=/admin/"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path).status_code, 404)
        self.assertEqual(self.client.post("/admin/login/", {"username": "x", "password": "y"}).status_code, 404)
        self.assertEqual(self.client.head("/admin/login/").status_code, 404)

    def test_the_refusal_says_nothing_about_why(self):
        for value in (None, "x", token(aud=["b" * 64]), token(exp=int(time.time()) - 999)):
            body = self.get("/admin/login/", value).content
            self.assertEqual(body, self.get("/admin/login/").content)
        self.assertNotIn(b"Access", body)


class PassTests(GuardTestCase):
    def test_a_valid_token_reaches_the_admin(self):
        response = self.assertPasses(token())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<form")

    def test_a_valid_token_also_reaches_the_dashboard_login_redirect(self):
        response = self.assertPasses(token(), "/admin/tableau/")
        self.assertEqual(response.status_code, 302)  # admin_view: sign in first
        self.assertIn("/admin/login/", response["Location"])

    def test_keys_are_fetched_from_the_team_without_proxy_nor_redirect(self):
        self.assertPasses(token())
        self.assertEqual(self.opener.open.call_args.args[0], f"https://{TEAM}/cdn-cgi/access/certs")
        self.assertLessEqual(self.opener.open.call_args.kwargs["timeout"], 5)
        handlers = self.build_opener.call_args.args
        self.assertTrue(any(isinstance(h, urllib.request.ProxyHandler) and h.proxies == {} for h in handlers),
                        "ProxyHandler({}) expected")
        self.assertTrue(any(isinstance(h, RefuseRedirects) for h in handlers))

    def test_keys_are_cached_for_an_hour_by_kid(self):
        self.assertPasses(token())
        self.assertPasses(token())
        self.assertEqual(self.opener.open.call_count, 1)
        with mock.patch("config.admin_access.time.monotonic", return_value=time.monotonic() + 3500):
            self.assertPasses(token())
        self.assertEqual(self.opener.open.call_count, 1)
        with mock.patch("config.admin_access.time.monotonic", return_value=time.monotonic() + 3700):
            self.assertPasses(token())
        self.assertEqual(self.opener.open.call_count, 2)

    def test_an_unknown_kid_does_not_make_every_request_fetch_the_keys(self):
        self.assertPasses(token())
        for _ in range(5):
            self.assertRefused(token(kid="inconnu"))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_a_rotated_key_is_picked_up_once_the_refresh_pause_has_passed(self):
        self.assertPasses(token())
        self.opener.open.return_value = fake_response(body=certs_body(jwk_of(KEY), jwk_of(OTHER_KEY, "key-2")))
        self.assertRefused(token(key=OTHER_KEY, kid="key-2"))  # too soon after the last fetch
        with mock.patch("config.admin_access.time.monotonic", return_value=time.monotonic() + 120):
            self.assertPasses(token(key=OTHER_KEY, kid="key-2"))
        self.assertEqual(self.opener.open.call_count, 2)

    def test_unreachable_keys_are_not_retried_on_every_request(self):
        self.opener.open.side_effect = urllib.error.URLError("down")
        for _ in range(5):
            self.assertRefused(token())
        self.assertEqual(self.opener.open.call_count, 1)

    def test_an_expired_cache_with_unreachable_keys_refuses(self):
        self.assertPasses(token())
        self.opener.open.side_effect = urllib.error.URLError("down")
        with mock.patch("config.admin_access.time.monotonic", return_value=time.monotonic() + 3700):
            self.assertRefused(token())

    def test_non_rsa_keys_of_the_document_are_ignored(self):
        junk = {"kid": "ec", "kty": "EC", "crv": "P-256", "x": "AA", "y": "AA", "alg": "ES256"}
        self.opener.open.return_value = fake_response(body=certs_body(junk, jwk_of(KEY)))
        self.assertPasses(token())

    def test_other_paths_are_never_touched(self):
        for path in ("/", "/healthz", "/mentions-legales/", "/confidentialite/"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path).status_code, 200)
        for path in ("/static/ui/css/tri.css", "/administrateur/", "/adminx"):
            self.get(path)  # whatever the answer, it is not the guard's
        self.assertEqual(self.opener.open.call_count, 0)

    def test_guarded_paths(self):
        for path in ("/admin", "/admin/", "/admin/x/y/", "//admin/login/", "///admin", "/ADMIN/", "/Admin/login/"):
            with self.subTest(path=path):
                self.assertTrue(admin_access.is_guarded_path(path))
        for path in ("/", "/administrateur/", "/adminx", "/x/admin/", "/healthz", "/static/admin/css/base.css", ""):
            with self.subTest(path=path):
                self.assertFalse(admin_access.is_guarded_path(path))


class StaffSessionTests(GuardTestCase):
    def test_staff_signed_in_by_code_gets_a_404_without_the_token(self):
        staff = User.objects.create_user("staff@example.fr", is_staff=True, is_superuser=True)
        self.client.force_login(staff)
        for path in ("/admin/tableau/", "/admin/", "/admin/accounts/user/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
        # With the token the same session gets in.
        self.assertEqual(self.client.get("/admin/tableau/", **{HEADER: token()}).status_code, 200)

    def test_no_session_is_created_by_a_refused_request(self):
        from django.contrib.sessions.models import Session

        self.get("/admin/login/")
        self.assertEqual(Session.objects.count(), 0)


class InactiveTests(TestCase):
    """In http (local) the guard does nothing: the admin is reached as before."""

    @override_settings(SITE_URL="http://localhost:8000", ADMIN_ACCESS_TEAM_DOMAIN="", ADMIN_ACCESS_AUD="",
                       STORAGES=PLAIN_STATIC)
    def test_http_without_settings(self):
        with mock.patch("config.admin_access.urllib.request.build_opener") as build:
            self.assertEqual(self.client.get("/admin/login/").status_code, 200)
        build.assert_not_called()

    @override_settings(SITE_URL="http://localhost:8000", ADMIN_ACCESS_TEAM_DOMAIN=TEAM, ADMIN_ACCESS_AUD=AUD,
                       STORAGES=PLAIN_STATIC)
    def test_http_with_settings(self):
        self.assertEqual(self.client.get("/admin/login/").status_code, 200)

    @override_settings(SITE_URL=SITE, ADMIN_ACCESS_TEAM_DOMAIN="", ADMIN_ACCESS_AUD="", STORAGES=PLAIN_STATIC,
                       ALLOWED_HOSTS=["testserver"])
    def test_https_without_settings_closes_the_admin(self):
        self.assertEqual(self.client.get("/admin/login/").status_code, 404)
        self.assertEqual(self.client.get("/admin/login/", **{HEADER: token()}).status_code, 404)

    @override_settings(SITE_URL=SITE, ADMIN_ACCESS_TEAM_DOMAIN=TEAM, ADMIN_ACCESS_AUD="", STORAGES=PLAIN_STATIC,
                       ALLOWED_HOSTS=["testserver"])
    def test_https_with_an_empty_audience_closes_the_admin(self):
        self.assertEqual(self.client.get("/admin/login/", **{HEADER: token()}).status_code, 404)


class OrderTests(SimpleTestCase):
    def test_the_guard_sits_after_whitenoise_and_before_the_session(self):
        from django.conf import settings

        middleware = settings.MIDDLEWARE
        guard = middleware.index("config.admin_access.CloudflareAccessGuard")
        self.assertGreater(guard, middleware.index("whitenoise.middleware.WhiteNoiseMiddleware"))
        self.assertLess(guard, middleware.index("django.contrib.sessions.middleware.SessionMiddleware"))


class SettingsTests(SimpleTestCase):
    def test_team_domain_is_a_bare_host_or_empty(self):
        from django.core.exceptions import ImproperlyConfigured

        from config.env import env_hostname

        self.assertEqual(env_hostname("X", environ={}), "")
        self.assertEqual(env_hostname("X", environ={"X": " Equipe.CloudflareAccess.com "}),
                         "equipe.cloudflareaccess.com")
        for value in ("https://equipe.cloudflareaccess.com", "equipe.cloudflareaccess.com/", "equipe:8443",
                      "equipe cloudflareaccess.com", "-x.com", "a..b", "équipe.com", "user@host.com", "host\n.com"):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured) as caught:
                env_hostname("X", environ={"X": value})
            self.assertEqual(str(caught.exception), "X must be a host name without scheme, port or path.")

    def test_no_real_team_in_the_repository_settings(self):
        from django.conf import settings

        self.assertEqual(settings.ADMIN_ACCESS_TEAM_DOMAIN, "")
        self.assertEqual(settings.ADMIN_ACCESS_AUD, "")
