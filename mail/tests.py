import json
import re
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from io import BytesIO, StringIO
from unittest import mock

from django.core import mail as django_mail
from django.core.mail import BadHeaderError, EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings

import mail
from config.testing import assert_french_copy
from mail.backends import ResendBackend

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
CODE_CONTEXT = {"code": "482 913", "link": "https://tri.example.fr/connexion/lien/abc/", "minutes": 15}


@override_settings(EMAIL_BACKEND=LOCMEM, CLAUDE_ENGINE="", DEV_EMAILS=[])
class SendTests(SimpleTestCase):
    def test_renders_subject_text_and_html_with_sender_and_reply_to(self):
        mail.send("login_code", "nora@example.fr", CODE_CONTEXT)
        self.assertEqual(len(django_mail.outbox), 1)
        message = django_mail.outbox[0]
        self.assertEqual(message.subject, "482 913 est votre code tri-offres")
        self.assertEqual(message.to, ["nora@example.fr"])
        self.assertEqual(message.from_email, "tri-offres <noreply@example.org>")
        self.assertEqual(message.reply_to, ["contact@example.org"])
        self.assertEqual(message.cc, [])
        self.assertEqual(message.bcc, [])
        self.assertIn("482 913", message.body)
        self.assertIn(CODE_CONTEXT["link"], message.body)
        html, mimetype = message.alternatives[0]
        self.assertEqual(mimetype, "text/html")
        self.assertIn("Me connecter sur cet appareil", html)
        self.assertIn(f'href="{CODE_CONTEXT["link"]}"', html)
        self.assertNotIn("<img", html)

    def test_code_mail_copy(self):
        mail.send("login_code", "nora@example.fr", CODE_CONTEXT)
        message = django_mail.outbox[0]
        html = message.alternatives[0][0]
        for part in (message.body, html):
            with self.subTest(part=part[:20]):
                self.assertIn("Ce code et ce lien restent valables 15 minutes.", part)
                self.assertIn("Vous n’avez rien demandé ? Ignorez ce message.", part)
                assert_french_copy(self, part)

    def test_waitlist_mail_copy(self):
        mail.send("waitlist", "nora@example.fr", {})
        message = django_mail.outbox[0]
        self.assertEqual(message.subject, "Vous êtes sur la liste d’attente de tri-offres")
        html = message.alternatives[0][0]
        for part in (message.body, html):
            with self.subTest(part=part[:20]):
                self.assertIn("La bêta de tri-offres est complète pour le moment.", part)
                self.assertIn("nous vous écrirons dès qu’une place se libère", part)
                self.assertIn("nous ne vous écrirons pas d’autre mail sans place libre", part)
                assert_french_copy(self, part)

    def test_headers_are_handed_to_the_message(self):
        mail.send("login_code", "nora@example.fr", CODE_CONTEXT, headers={"List-Unsubscribe": "<https://a.example/>"})
        self.assertEqual(django_mail.outbox[0].extra_headers, {"List-Unsubscribe": "<https://a.example/>"})
        mail.send("login_code", "nora@example.fr", CODE_CONTEXT)
        self.assertEqual(django_mail.outbox[1].extra_headers, {})

    def test_a_line_break_in_a_header_is_refused_by_django(self):
        with self.assertRaises(BadHeaderError):
            mail.send("login_code", "nora@example.fr", CODE_CONTEXT, headers={"List-Unsubscribe": "a\nBcc: x"})

    def test_the_mails_people_ask_for_carry_no_unsubscribe_footer(self):
        for template, context in (("login_code", CODE_CONTEXT), ("waitlist", {})):
            with self.subTest(template=template):
                django_mail.outbox.clear()
                mail.send(template, "nora@example.fr", context)
                for part in (django_mail.outbox[0].body, django_mail.outbox[0].alternatives[0][0]):
                    self.assertNotIn("Pour ne plus recevoir", part)
                    self.assertNotIn("desinscription", part)

    def test_subject_is_a_single_line(self):
        mail.send("waitlist", "nora@example.fr", {})
        self.assertNotIn("\n", django_mail.outbox[0].subject)

    def test_invalid_recipient_is_refused(self):
        for address in ("", "pas-une-adresse", '"nora"@example.fr', "nora@example.fr, eve@example.fr",
                        "nora@[127.0.0.1]", "a" * 250 + "@example.fr", "nora@example.fr\nBcc: eve@example.fr"):
            with self.subTest(address=address), self.assertRaises(mail.MailRefused):
                mail.send("waitlist", address, {})
        self.assertEqual(django_mail.outbox, [])


@override_settings(EMAIL_BACKEND=LOCMEM, CLAUDE_ENGINE="oauth", DEV_EMAILS=["alexis@example.fr"])
class OauthGuardTests(SimpleTestCase):
    """The Max token only serves the developers: with the oauth engine, mail goes to DEV_EMAILS only."""

    def test_refuses_address_outside_dev_emails(self):
        with self.assertRaises(mail.MailRefused):
            mail.send("login_code", "nora@example.fr", CODE_CONTEXT)
        self.assertEqual(django_mail.outbox, [])

    def test_allows_dev_address_whatever_its_case(self):
        mail.send("login_code", "Alexis@Example.fr", CODE_CONTEXT)
        self.assertEqual(len(django_mail.outbox), 1)

    @override_settings(CLAUDE_ENGINE="OAuth")
    def test_engine_name_is_compared_without_case(self):
        with self.assertRaises(mail.MailRefused):
            mail.send("login_code", "nora@example.fr", CODE_CONTEXT)

    @override_settings(CLAUDE_ENGINE="api")
    def test_api_engine_mails_anyone(self):
        mail.send("login_code", "nora@example.fr", CODE_CONTEXT)
        self.assertEqual(len(django_mail.outbox), 1)

    def test_refusal_is_a_mail_error(self):
        self.assertTrue(issubclass(mail.MailRefused, mail.MailError))
        self.assertTrue(issubclass(mail.MailDeliveryFailed, mail.MailError))


def fake_response(status=200, body=b'{"id": "x"}'):
    response = mock.MagicMock()
    response.status = status
    response.read.return_value = body
    response.__enter__.return_value = response
    return response


def fake_opener(response=None, error=None):
    opener = mock.MagicMock()
    if error is not None:
        opener.open.side_effect = error
    else:
        opener.open.return_value = response or fake_response()
    return opener


REAL_BUILD_OPENER = urllib.request.build_opener


def redirecting_opener(location):
    """A build_opener stand-in: the backend's real handlers, over an HTTPS layer that answers 302 to `location`.

    Returns the stand-in and the list of URLs actually requested.
    """
    calls = []

    class RedirectingHTTPS(urllib.request.BaseHandler):
        handler_order = 100  # before the real HTTPSHandler: nothing leaves the machine

        def https_open(self, request):
            calls.append(request.full_url)
            headers = Message()
            headers["Location"] = location
            response = urllib.response.addinfourl(BytesIO(b""), headers, request.full_url, 302)
            response.msg = "Found"
            return response

    def build(*handlers):
        return REAL_BUILD_OPENER(*handlers, RedirectingHTTPS())

    return build, calls


class ResendBackendTests(SimpleTestCase):
    def message(self, **kwargs):
        fields = {"subject": "482 913 est votre code tri-offres", "body": "texte",
                  "from_email": "tri-offres <noreply@example.org>", "to": ["nora@example.fr"],
                  "reply_to": ["contact@example.org"], **kwargs}
        message = EmailMultiAlternatives(**fields)
        message.attach_alternative("<p>html</p>", "text/html")
        return message

    def test_posts_json_to_resend_with_ten_second_timeout(self):
        opener = fake_opener()
        with mock.patch("mail.backends.urllib.request.build_opener", return_value=opener):
            sent = ResendBackend(api_key="test-mail-key").send_messages([self.message()])
        self.assertEqual(sent, 1)
        request = opener.open.call_args.args[0]
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 10)
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-mail-key")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        payload = json.loads(request.data)
        self.assertEqual(payload, {
            "from": "tri-offres <noreply@example.org>", "to": ["nora@example.fr"],
            "subject": "482 913 est votre code tri-offres", "text": "texte", "html": "<p>html</p>",
            "reply_to": ["contact@example.org"],
        })

    def test_http_error_raises_delivery_failure(self):
        error = urllib.error.HTTPError("https://api.resend.com/emails", 429, "Too Many", {}, BytesIO(b"{}"))
        with mock.patch("mail.backends.urllib.request.build_opener", return_value=fake_opener(error=error)):
            with self.assertRaises(mail.MailDeliveryFailed):
                ResendBackend(api_key="test-mail-key").send_messages([self.message()])

    def test_redirect_is_refused_so_the_key_never_follows_it(self):
        build, calls = redirecting_opener("https://elsewhere.example/steal")
        with mock.patch("mail.backends.urllib.request.build_opener", side_effect=build):
            with self.assertRaises(mail.MailDeliveryFailed) as caught:
                ResendBackend(api_key="test-mail-key").send_messages([self.message()])
        self.assertEqual(calls, ["https://api.resend.com/emails"])
        self.assertNotIn("elsewhere.example", str(caught.exception))

    def test_timeout_and_network_errors_raise_delivery_failure(self):
        for error in (TimeoutError("timed out"), urllib.error.URLError("no route"), ConnectionResetError()):
            with self.subTest(error=error), \
                    mock.patch("mail.backends.urllib.request.build_opener", return_value=fake_opener(error=error)):
                with self.assertRaises(mail.MailDeliveryFailed):
                    ResendBackend(api_key="test-mail-key").send_messages([self.message()])

    def test_list_unsubscribe_headers_reach_the_payload_and_nothing_else_does(self):
        opener = fake_opener()
        headers = {"List-Unsubscribe": "<https://tri.example.fr/desinscription/abc:1:sig/un-clic/>",
                   "List-Unsubscribe-Post": "List-Unsubscribe=One-Click", "X-Other": "dropped",
                   "Bcc": "spy@example.fr"}
        with mock.patch("mail.backends.urllib.request.build_opener", return_value=opener):
            ResendBackend(api_key="test-mail-key").send_messages([self.message(headers=headers)])
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload["headers"], {
            "List-Unsubscribe": "<https://tri.example.fr/desinscription/abc:1:sig/un-clic/>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
        self.assertNotIn("spy@example.fr", json.dumps(payload))

    def test_no_headers_field_without_the_two_names(self):
        opener = fake_opener()
        with mock.patch("mail.backends.urllib.request.build_opener", return_value=opener):
            ResendBackend(api_key="test-mail-key").send_messages([self.message(headers={"X-Other": "dropped"})])
        self.assertNotIn("headers", json.loads(opener.open.call_args.args[0].data))

    def test_unsafe_list_unsubscribe_values_are_refused_before_any_call(self):
        link = "<https://tri.example.fr/desinscription/abc/un-clic/>"
        cases = [
            {"List-Unsubscribe": link + "\r\nBcc: spy@example.fr"}, {"List-Unsubscribe": link + "\n"},
            {"List-Unsubscribe": "<https://tri.example.fr/a b/>"}, {"List-Unsubscribe": f" {link}"},
            {"List-Unsubscribe": "https://tri.example.fr/a/"}, {"List-Unsubscribe": "<https://a/><https://b/>"},
            {"List-Unsubscribe": "<https://a/>>"}, {"List-Unsubscribe": "<<https://a/>"},
            {"List-Unsubscribe": "<http://tri.example.fr/a/>"}, {"List-Unsubscribe": "<mailto:a@example.fr>"},
            {"List-Unsubscribe": "<https://tri.example.fr/é/>"}, {"List-Unsubscribe": "<https://>"},
            {"List-Unsubscribe": ""}, {"List-Unsubscribe-Post": "List-Unsubscribe=Other"},
            {"List-Unsubscribe-Post": "List-Unsubscribe=One-Click\r\nBcc: spy@example.fr"},
            {"List-Unsubscribe-Post": "List-Unsubscribe=One-Click "},
        ]
        for headers in cases:
            opener = fake_opener()
            with self.subTest(headers=headers), \
                    mock.patch("mail.backends.urllib.request.build_opener", return_value=opener):
                with self.assertRaises(mail.MailDeliveryFailed) as caught:
                    ResendBackend(api_key="test-mail-key").send_messages([self.message(headers=headers)])
                opener.open.assert_not_called()
                self.assertNotIn("spy@example.fr", str(caught.exception))

    def test_fail_silently_swallows_failures(self):
        with mock.patch("mail.backends.urllib.request.build_opener", return_value=fake_opener(error=TimeoutError())):
            backend = ResendBackend(api_key="test-mail-key", fail_silently=True)
            self.assertEqual(backend.send_messages([self.message()]), 0)

    def test_refuses_more_than_one_recipient_or_copies(self):
        for extra in ({"to": ["a@example.fr", "b@example.fr"]}, {"cc": ["b@example.fr"]}, {"bcc": ["b@example.fr"]}):
            opener = fake_opener()
            patch = mock.patch("mail.backends.urllib.request.build_opener", return_value=opener)
            with self.subTest(extra=extra), patch:
                with self.assertRaises(mail.MailDeliveryFailed):
                    ResendBackend(api_key="test-mail-key").send_messages([self.message(**extra)])
                opener.open.assert_not_called()

    @override_settings(RESEND_API_KEY="")
    def test_missing_key_never_calls_out(self):
        opener = fake_opener()
        with mock.patch("mail.backends.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(mail.MailDeliveryFailed):
                ResendBackend().send_messages([self.message()])
            opener.open.assert_not_called()


class ConsoleBackendTests(SimpleTestCase):
    def message(self):
        return EmailMultiAlternatives("482 913 est votre code tri-offres", "corps secret", "f@example.fr",
                                      ["nora@example.fr"])

    @override_settings(DEBUG=False)
    def test_without_debug_one_line_and_no_content(self):
        from mail.backends import ConsoleBackend
        stream = StringIO()
        with self.assertLogs("mail", level="WARNING") as logs:
            sent = ConsoleBackend(stream=stream).send_messages([self.message()])
        self.assertEqual(sent, 0)
        self.assertEqual(stream.getvalue(), "")
        self.assertEqual(len(logs.output), 1)
        self.assertIn("RESEND_API_KEY", logs.output[0])
        for secret in ("nora@example.fr", "corps secret", "482 913"):
            self.assertNotIn(secret, logs.output[0])

    @override_settings(DEBUG=True)
    def test_with_debug_the_whole_mail_is_printed(self):
        from mail.backends import ConsoleBackend
        stream = StringIO()
        with self.assertLogs("mail", level="WARNING") as logs:
            sent = ConsoleBackend(stream=stream).send_messages([self.message()])
        self.assertEqual(sent, 1)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("corps secret", stream.getvalue())
        self.assertIn("nora@example.fr", stream.getvalue())
        # The last line sums the mail up, so the sign-in code is found without reading the MIME dump.
        self.assertEqual(stream.getvalue().rstrip().splitlines()[-1],
                         ">>> Mail pour nora@example.fr : 482 913 est votre code tri-offres")


UNSUBSCRIBE_URL = "https://tri.example.fr/desinscription/abc:1:sig/"


def _card(title="Chargée de production", **fields):
    return {"title": title, "employer": "Théâtre fictif", "location": "Nantes (44)", "contract": "CDI", "score": 8,
            "reason": "Vous retrouvez la régie de festival que vous cherchez.",
            "source": "Profession Spectacle", "attribution": "", "link": "https://example.fr/offre/1", **fields}


def _morning(**fields):
    return {"to_review": [], "maybe": [], "side_track": None, "read_tonight": 0,
            "sort_url": "https://tri.example.fr/tri/", "my_search_url": "https://tri.example.fr/ma-recherche/",
            "unsubscribe_url": UNSUBSCRIBE_URL, **fields}


@override_settings(EMAIL_BACKEND=LOCMEM, CLAUDE_ENGINE="", DEV_EMAILS=[])
class MorningMailTests(SimpleTestCase):
    def send(self, **fields):
        django_mail.outbox.clear()
        mail.send("morning", "nora@example.fr", _morning(**fields))
        message = django_mail.outbox[0]
        return message.subject, message.body, message.alternatives[0][0]

    def test_subject_follows_the_sections(self):
        cards = [_card(), _card()]
        cases = [
            (dict(to_review=cards + [_card()], maybe=cards + cards), "3 offres à regarder, 4 peut-être"),
            (dict(to_review=cards), "2 offres à regarder"),
            (dict(to_review=cards[:1]), "1 offre à regarder"),
            (dict(maybe=cards), "2 offres peut-être"),
            (dict(side_track=_card()), "Une piste à côté"),
            (dict(), "Rien d’assez proche aujourd’hui"),
        ]
        for fields, subject in cases:
            with self.subTest(subject=subject):
                self.assertEqual(self.send(**fields)[0], subject)

    def test_offers_sections_button_and_notice(self):
        subject, text, html = self.send(to_review=[_card()], maybe=[_card("Régisseuse")],
                                        side_track=_card("Médiatrice", attribution="Données fictives"))
        for part in (text, html):
            with self.subTest(part=part[:15]):
                for wanted in ("À regarder", "Peut-être", "Une piste à côté", "Chargée de production",
                               "Régisseuse",
                               "Théâtre fictif", "Nantes (44)", "CDI", "8 sur 10", "Vous retrouvez la régie",
                               "Source : Profession Spectacle", "Données fictives", "Voir l’offre",
                               "Trier ces offres", "Notes générées par IA, la décision vous appartient.",
                               "Pour ne plus recevoir ces mails :"):
                    self.assertIn(wanted, part)
                self.assertIn("https://tri.example.fr/tri/", part)
                self.assertNotIn("Rien d’assez proche", part)
                assert_french_copy(self, part)
        self.assertEqual(html.count("Trier ces offres"), 1)

    def test_link_is_only_the_origin_and_never_records_a_verdict(self):
        _, text, html = self.send(to_review=[_card(), _card(link="")])
        self.assertEqual(html.count("https://example.fr/offre/1"), 1)
        self.assertEqual(html.count("Voir l’offre"), 1)
        self.assertEqual(sorted(set(re.findall(r'href="([^"]+)"', html))),
                         ["https://example.fr/offre/1", "https://tri.example.fr/desinscription/abc:1:sig/",
                          "https://tri.example.fr/tri/"])

    def test_footer_links_to_the_unsubscribe_page_in_both_parts_and_no_longer_says_reply(self):
        for fields in ({}, {"to_review": [_card()]}):
            _, text, html = self.send(**fields)
            for part in (text, html):
                with self.subTest(part=part[:15], fields=list(fields)):
                    self.assertIn("Pour ne plus recevoir ces mails :", part)
                    self.assertIn(UNSUBSCRIBE_URL, part)
                    self.assertNotIn("répondez à celui-ci", part)
            self.assertIn(f'href="{UNSUBSCRIBE_URL}"', html)
            self.assertEqual(html.count(UNSUBSCRIBE_URL), 1)
            self.assertEqual(text.count(UNSUBSCRIBE_URL), 1)

    def test_offer_text_is_escaped(self):
        _, text, html = self.send(to_review=[_card("<script>x</script>")])
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("<script>x</script>", text)

    def test_empty_mail_with_and_without_a_number(self):
        for read, wanted in ((12, "J’ai lu 12 offres pour vous cette nuit, aucune n’était assez proche."),
                             (1, "J’ai lu 1 offre pour vous cette nuit, elle n’était pas assez proche."),
                             (0, "Je n’ai rien trouvé d’assez proche cette nuit.")):
            with self.subTest(read=read):
                _, text, html = self.send(read_tonight=read)
                for part in (text, html):
                    self.assertIn(wanted, part)
                    self.assertIn("Je cherche à nouveau demain.", part)
                    self.assertIn("https://tri.example.fr/ma-recherche/", part)
                    self.assertNotIn("Trier ces offres", part)
                    assert_french_copy(self, part)
