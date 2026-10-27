"""The one masking function (S3): everything that reaches TaskRun.error, a mail or the container output goes by it."""
import time

from django.test import SimpleTestCase, override_settings

from supervision.redaction import redact_text

PROXY = "http://vpn-user:S3cr3t-pw@10.0.0.5:8888"


class RedactTextTests(SimpleTestCase):
    def test_plain_text_is_unchanged(self):
        text = "collecte : 12 offres, 3 sources — rien à signaler (45,2 s)\n== notation =="
        self.assertEqual(redact_text(text), text)

    def test_link_tokens_go_by_the_log_filter(self):
        out = redact_text("GET /connexion/lien/abc:123:sig/ et /desinscription/tok-en/un-clic/")
        self.assertNotIn("abc:123", out)
        self.assertNotIn("tok-en", out)

    def test_addresses_are_masked(self):
        for address in ("nora.dupont+offres@example.fr", "o'brien@exemple.co.uk", "élodie@exemple.fr"):
            with self.subTest(address=address):
                out = redact_text(f"Erreur pour {address} : refusé ({address})")
                self.assertNotIn(address, out)
                self.assertNotIn("@", out)

    def test_credentials_in_urls_are_removed_whatever_the_scheme(self):
        out = redact_text("connexion à postgres://admin:p@ss:word@db.example.fr:5432/base puis https://u@host.fr/x")
        self.assertNotIn("admin", out)
        self.assertNotIn("p@ss", out)
        self.assertNotIn("u@host", out)
        self.assertIn("://db.example.fr:5432/base", out)
        self.assertIn("://host.fr/x", out)

    def test_an_at_sign_in_a_query_is_not_taken_for_credentials(self):
        out = redact_text("voir https://exemple.fr/page?next=a@b/c")
        self.assertIn("https://exemple.fr/page?next=", out)

    def test_urlsafe_strings_of_43_characters_or_more_are_masked(self):
        token = "Ab3_-" * 9  # 45 characters
        self.assertNotIn(token, redact_text(f"clé {token} fin"))
        self.assertNotIn("A" * 43, redact_text("A" * 60))
        exactly = "x" * 43
        self.assertNotIn(exactly, redact_text(exactly))

    def test_shorter_strings_and_separator_lines_stay(self):
        short = "x" * 42
        self.assertIn(short, redact_text(short))
        rule = "-" * 60
        self.assertEqual(redact_text(rule), rule)

    @override_settings(SCRAPER_PROXY_URL=PROXY)
    def test_the_exact_proxy_url_and_its_password_are_masked(self):
        out = redact_text(f"Proxy {PROXY} refusé ; mot de passe S3cr3t-pw ; encore {PROXY}/chemin")
        self.assertNotIn("S3cr3t-pw", out)
        self.assertNotIn("vpn-user", out)
        self.assertNotIn("10.0.0.5", out)

    @override_settings(SCRAPER_PROXY_URL="http://vpn:p%40ss%2Fw@10.0.0.5:8888")
    def test_a_percent_encoded_password_is_masked_in_both_forms(self):
        out = redact_text("raw p@ss/w and encoded p%40ss%2Fw")
        self.assertNotIn("p@ss", out)
        self.assertNotIn("p%40ss", out)

    @override_settings(SCRAPER_PROXY_URL="http://10.0.0.5:8888")
    def test_a_proxy_without_password_masks_the_url_only(self):
        out = redact_text("proxy http://10.0.0.5:8888 en panne")
        self.assertNotIn("10.0.0.5", out)
        self.assertIn("en panne", out)

    @override_settings(SCRAPER_PROXY_URL="")
    def test_no_proxy_setting_masks_nothing_extra(self):
        self.assertEqual(redact_text("rien"), "rien")

    @override_settings(HEALTHCHECKS_PING_URL="https://hc-ping.com/AbCdEfGh12345678")
    def test_the_ping_url_and_its_key_are_masked_too(self):
        out = redact_text("POST https://hc-ping.com/AbCdEfGh12345678/tri-offres-nuit refusé, clé AbCdEfGh12345678")
        self.assertNotIn("AbCdEfGh12345678", out)

    def test_is_idempotent_and_takes_non_strings(self):
        once = redact_text("a@b.fr " + "z" * 50)
        self.assertEqual(redact_text(once), once)
        self.assertEqual(redact_text(None), "")
        self.assertEqual(redact_text(404), "404")


class RedactTextPerformanceTests(SimpleTestCase):
    """The actor's log is cut at a megabyte and masked whole (S18): no input of that kind may take a quadratic time.
    The bound is wide on purpose; a quadratic pass on 200 kB would take minutes."""

    def test_long_runs_without_a_space_stay_fast(self):
        for filler in ("x", "-", "_", "a-", "é", "1.", "a@", "@", ".", "/", ":", "://", "a:"):
            text = filler * (200_000 // len(filler))
            with self.subTest(filler=filler):
                started = time.monotonic()
                redact_text(text)
                self.assertLess(time.monotonic() - started, 5)
