"""The api engine and the people's OpenRouter keys (contract 5.5 to 5.8, 5.14). OpenRouter is never reached: the SDK
client is a mock, or a real one on a mock transport when the headers are the point."""
import json
import logging
import os
from types import SimpleNamespace
from unittest import mock

import anthropic
import httpx2
from cryptography.fernet import Fernet
from django.contrib.auth.models import AnonymousUser
from django.core import mail as django_mail
from django.test import TestCase, override_settings

import llm
from accounts import openrouter
from accounts.models import OpenRouterKey, User
from llm import api as api_engine, fake
from supervision.models import AlertSent

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
FERNET = Fernet.generate_key().decode()
OTHER_FERNET = Fernet.generate_key().decode()
KEY = "sk-or-v1-CANARY0123456789abcdefABCDEF"  # fictive
OTHER_KEY = "sk-or-v1-OTHERKEY456789abcdefABCDEF"  # fictive
NAS_KEY = "sk-or-v1-NASKEY00000000000000000000"
SENTINEL = "SENTINEL-CV-7f3a9c Jeanne Témoin"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["level"],
          "properties": {"level": {"type": "string", "enum": ["junior", "senior"]}}}
GOOD = {"level": "senior"}
ENV = {"OPENROUTER_API_KEY": NAS_KEY, "ANTHROPIC_API_KEY": "sk-ant-ENVKEY", "ANTHROPIC_AUTH_TOKEN": "tok-ENVTOKEN"}


ME = object()  # the account made in setUp (None is a caller of its own, to be refused)


def message(text=None):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(GOOD) if text is None else text)],
                           stop_reason="end_turn", _request_id="req_1", usage=None, model=None)


def api_error(status, body=None, headers=None):
    request = httpx2.Request("POST", "https://openrouter.ai/api/v1/messages")
    response = httpx2.Response(status, request=request, headers=headers or {}, json=body or {})
    cls = {401: anthropic.AuthenticationError, 403: anthropic.PermissionDeniedError}.get(status,
                                                                                         anthropic.APIStatusError)
    return cls(message="echo: " + SENTINEL, response=response, body=body)


def limit_body(source="openrouter_key_limit"):
    return {"error": {"code": 403, "message": "Budget limit exceeded " + SENTINEL, "metadata": {"limit_source": source}}}


@override_settings(CLAUDE_ENGINE="api", OPENROUTER_KEYS_FERNET=[FERNET], EMAIL_BACKEND=LOCMEM, DEV_EMAILS=[],
                   SITE_URL="https://jobs.example.org", ALERT_EMAIL="alerte@example.fr")
class KeyCase(TestCase):
    def setUp(self):
        fake.reset()
        self.user = User.objects.create_user("nora@example.fr")
        openrouter.store(self.user.pk, KEY)

    def ask(self, user=ME, environ=None, side_effect=None, text=None):
        """One call under the api engine with the SDK mocked. Returns the output; the mock is self.client_cls."""
        with mock.patch.dict(os.environ, ENV if environ is None else environ, clear=environ is not None), \
                mock.patch("anthropic.Anthropic") as client_cls:
            create = client_cls.return_value.beta.messages.create
            create.return_value = message(text)
            create.side_effect = side_effect
            self.client_cls, self.create = client_cls, create
            return llm.complete_json("cv_extract", "Extract.", SENTINEL, SCHEMA, self.user if user is ME else user)

    def bearer(self):
        return self.client_cls.call_args.kwargs["auth_token"]


class IsolationTests(KeyCase):
    def test_a_person_call_uses_their_key_alone(self):
        self.assertEqual(self.ask(), GOOD)
        kwargs = self.client_cls.call_args.kwargs
        self.assertEqual(kwargs["auth_token"], KEY)
        self.assertIsNone(kwargs["api_key"])
        self.assertEqual(kwargs["base_url"], "https://openrouter.ai/api")

    def test_the_wire_carries_one_bearer_and_no_x_api_key_whatever_the_environment_holds(self):
        seen = []

        def handler(request):
            seen.append(request)
            body = {"id": "msg_1", "type": "message", "role": "assistant", "model": "anthropic/claude-sonnet-5.5",
                    "content": [{"type": "text", "text": json.dumps(GOOD)}], "stop_reason": "end_turn",
                    "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
            return httpx2.Response(200, json=body)

        real = anthropic.Anthropic

        def factory(**kwargs):
            return real(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))

        with mock.patch.dict(os.environ, ENV), mock.patch("anthropic.Anthropic", side_effect=factory):
            self.assertEqual(llm.complete_json("cv_extract", "Extract.", SENTINEL, SCHEMA, self.user), GOOD)
        (request,) = seen
        self.assertEqual(request.url.host, "openrouter.ai")
        self.assertEqual(request.headers["authorization"], f"Bearer {KEY}")
        self.assertNotIn("x-api-key", request.headers)
        wire = str(request.headers) + request.content.decode()
        for secret in (NAS_KEY, "ENVKEY", "ENVTOKEN"):
            self.assertNotIn(secret, wire)

    def test_a_shared_call_uses_the_nas_key_never_a_persons(self):
        self.assertEqual(self.ask(user=llm.SHARED), GOOD)
        self.assertEqual(self.bearer(), NAS_KEY)

    def test_a_shared_call_without_the_nas_key_is_unavailable_even_with_the_anthropic_variable(self):
        with self.assertRaises(llm.EngineUnavailable):
            self.ask(user=llm.SHARED, environ={"ANTHROPIC_API_KEY": "sk-ant-ENVKEY"})
        self.client_cls.assert_not_called()

    def test_a_person_without_a_key_is_unavailable_and_the_nas_key_stays_unused(self):
        OpenRouterKey.objects.all().delete()
        with self.assertRaises(llm.EngineUnavailable) as caught:
            self.ask()
        self.assertNotIsInstance(caught.exception, llm.EngineKeyUnreadable)
        self.client_cls.assert_not_called()

    def test_none_and_anonymous_are_never_read_as_shared(self):
        for user in (None, AnonymousUser(), SimpleNamespace(email="nora@example.fr"), SimpleNamespace(pk="1")):
            with self.subTest(user=user), self.assertRaises(llm.EngineUnavailable):
                self.ask(user=user)
            self.client_cls.assert_not_called()

    def test_the_key_is_read_again_at_every_call(self):
        self.ask()
        openrouter.store(self.user.pk, OTHER_KEY)
        self.ask()  # the same `user` instance
        self.assertEqual(self.bearer(), OTHER_KEY)
        self.assertFalse(hasattr(self.user, "openrouter_key") and "CANARY" in repr(vars(self.user)))

    def test_one_person_never_gets_another_persons_key(self):
        other = User.objects.create_user("lea@example.fr")
        openrouter.store(other.pk, OTHER_KEY)
        self.ask(user=other)
        self.assertEqual(self.bearer(), OTHER_KEY)
        self.ask()
        self.assertEqual(self.bearer(), KEY)


class RefusalTests(KeyCase):
    def test_a_401_rejects_the_key_erases_it_and_mails_once(self):
        with self.assertRaises(llm.EngineKeyRejected) as caught:
            self.ask(side_effect=api_error(401, {"error": {"code": 401, "message": SENTINEL}}))
        self.assertIsInstance(caught.exception, llm.LLMError)
        self.assertNotIn("SENTINEL", str(caught.exception))
        self.assertFalse(OpenRouterKey.objects.exists())
        self.assertEqual([m.to for m in django_mail.outbox], [["nora@example.fr"]])

    def test_a_402_without_retry_after_exhausts_the_credit_and_keeps_the_key(self):
        with self.assertRaises(llm.EngineCreditExhausted) as caught:
            self.ask(side_effect=api_error(402, {"error": {"code": 402, "message": SENTINEL}}))
        self.assertNotIn("SENTINEL", str(caught.exception))
        self.assertNotIsInstance(caught.exception, llm.EngineKeyLimitReached)
        row = OpenRouterKey.objects.get()
        self.assertIsNotNone(row.refused_since)
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn("crédit", django_mail.outbox[0].body)
        with self.assertRaises(llm.EngineCreditExhausted):
            self.ask(side_effect=api_error(402))
        self.assertEqual(len(django_mail.outbox), 1)  # one mail per episode

    def test_a_402_with_retry_after_is_an_ordinary_error_with_no_state_and_no_mail(self):
        with self.assertRaises(llm.EngineError) as caught:
            self.ask(side_effect=api_error(402, {"error": {"code": 402, "metadata": {
                "limit_source": "openrouter_in_flight_budget"}}}, headers={"Retry-After": "7"}))
        self.assertNotIsInstance(caught.exception, (llm.EngineCreditExhausted, llm.EngineKeyRejected))
        self.assertIsNone(OpenRouterKey.objects.get().refused_since)
        self.assertEqual(django_mail.outbox, [])

    def test_a_403_of_the_key_limit_is_a_limit_with_its_own_mail(self):
        for body in (limit_body(), {"error": {"limit_source": "openrouter_key_limit"}}):
            OpenRouterKey.objects.update(refused_since=None)
            django_mail.outbox.clear()
            with self.subTest(body=body), self.assertRaises(llm.EngineKeyLimitReached) as caught:
                self.ask(side_effect=api_error(403, body))
            self.assertIsInstance(caught.exception, llm.EngineCreditExhausted)
            self.assertNotIn("SENTINEL", str(caught.exception))
            self.assertIsNotNone(OpenRouterKey.objects.get().refused_since)
            self.assertIn("limite", django_mail.outbox[0].subject.lower())

    def test_any_other_403_is_the_offers_error_and_leaves_the_key_alone(self):
        bodies = [limit_body("openrouter_guardrail_budget"), limit_body("openrouter_workspace_budget"),
                  {"error": {"code": 403, "message": "Request blocked " + SENTINEL, "metadata": {"patterns": ["x"]}}},
                  None, {"error": "openrouter_key_limit"}, {"error": {"message": "openrouter_key_limit"}}]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(llm.EngineError) as caught:
                self.ask(side_effect=api_error(403, body))
            self.assertNotIsInstance(caught.exception, (llm.EngineCreditExhausted, llm.EngineKeyRejected))
        self.assertIsNone(OpenRouterKey.objects.get().refused_since)
        self.assertEqual(django_mail.outbox, [])

    def test_ordinary_failures_have_no_effect_on_the_key(self):
        for status in (400, 429, 500, 503):
            with self.subTest(status=status), self.assertRaises(llm.EngineError):
                self.ask(side_effect=api_error(status))
        self.assertIsNone(OpenRouterKey.objects.get().refused_since)

    def test_a_success_closes_the_episode(self):
        with self.assertRaises(llm.EngineCreditExhausted):
            self.ask(side_effect=api_error(402))
        self.ask()
        self.assertIsNone(OpenRouterKey.objects.get().refused_since)
        with self.assertRaises(llm.EngineCreditExhausted):
            self.ask(side_effect=api_error(402))
        self.assertEqual(len(django_mail.outbox), 2)

    def test_a_401_on_the_shared_key_changes_nothing_for_anyone(self):
        with self.assertRaises(llm.EngineError) as caught:
            self.ask(user=llm.SHARED, side_effect=api_error(401))
        self.assertNotIsInstance(caught.exception, llm.EngineKeyRejected)
        self.assertEqual(OpenRouterKey.objects.count(), 1)
        self.assertEqual(django_mail.outbox, [])

    def test_the_errors_are_exported_and_typed(self):
        for cls in (llm.EngineKeyRejected, llm.EngineCreditExhausted, llm.EngineKeyLimitReached):
            self.assertTrue(issubclass(cls, llm.LLMError))
            self.assertTrue(issubclass(cls, llm.EngineKeyError))
        self.assertTrue(issubclass(llm.EngineKeyUnreadable, llm.EngineUnavailable))
        self.assertFalse(issubclass(llm.EngineKeyUnreadable, llm.EngineKeyError))

    def test_the_mail_failing_never_hides_the_typed_error(self):
        with mock.patch("accounts.openrouter.mail.send", side_effect=RuntimeError("smtp " + KEY)), \
                self.assertLogs("accounts.openrouter", "WARNING") as logs, \
                self.assertRaises(llm.EngineKeyRejected):
            self.ask(side_effect=api_error(401))
        self.assertNotIn("CANARY", "\n".join(logs.output))


class UnreadableKeyTests(KeyCase):
    def unreadable(self):
        return override_settings(OPENROUTER_KEYS_FERNET=[OTHER_FERNET])

    def test_an_invalid_token_is_unavailable_with_no_erasure_and_no_mail_to_the_person(self):
        with self.unreadable(), self.assertRaises(llm.EngineKeyUnreadable):
            self.ask()
        self.client_cls.assert_not_called()
        self.assertEqual(OpenRouterKey.objects.count(), 1)
        self.assertNotIn("nora@example.fr", [address for m in django_mail.outbox for address in m.to])

    def test_it_raises_one_technical_alert_a_day_to_the_operator(self):
        with self.unreadable():
            for _ in range(2):
                with self.assertRaises(llm.EngineKeyUnreadable):
                    self.ask()
        self.assertEqual([m.to for m in django_mail.outbox], [["alerte@example.fr"]])
        self.assertEqual(AlertSent.objects.filter(reason="key_unreadable").count(), 1)
        self.assertNotIn("CANARY", django_mail.outbox[0].body)

    def test_the_log_names_the_class_and_the_account_key_only(self):
        with self.unreadable(), self.assertLogs("llm", "ERROR") as logs, self.assertRaises(llm.EngineKeyUnreadable):
            self.ask()
        text = "\n".join(logs.output)
        self.assertIn(str(self.user.pk), text)
        self.assertIn("InvalidToken", text)
        self.assertNotIn("nora@example.fr", text)
        self.assertNotIn("CANARY", text)

    def test_a_good_key_after_the_alert_is_served_again(self):
        with self.unreadable(), self.assertRaises(llm.EngineKeyUnreadable):
            self.ask()
        self.assertEqual(self.ask(), GOOD)


class NothingLeaksTests(KeyCase):
    def test_the_key_is_in_no_log_line_whatever_happens(self):
        handler = logging.Handler(logging.DEBUG)
        lines = []
        handler.emit = lambda record: lines.append(record.getMessage())
        root = logging.getLogger()
        previous = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, previous)
        errors = [api_error(401), api_error(402), api_error(403, limit_body()), api_error(500),
                  anthropic.APIConnectionError(message=KEY, request=httpx2.Request("POST", "https://x"))]
        for error in errors:
            openrouter.store(self.user.pk, KEY)
            with self.assertRaises(llm.LLMError):
                self.ask(side_effect=error)
        self.ask()
        self.assertTrue(lines)
        self.assertNotIn("CANARY", "\n".join(lines))
        for mail_message in django_mail.outbox:
            self.assertNotIn("CANARY", mail_message.body + mail_message.subject)


class LeftOfTheDirectRouteTests(KeyCase):
    def test_the_anthropic_variable_serves_a_person_without_a_key_never(self):
        keyless = User.objects.create_user("x@example.fr")
        with self.assertRaises(llm.EngineUnavailable):
            self.ask(user=keyless, environ={"ANTHROPIC_API_KEY": "sk-ant-ENVKEY"})
        self.client_cls.assert_not_called()

    def test_no_model_other_than_the_slug_table_is_sent(self):
        self.ask()
        self.assertEqual(self.create.call_args.kwargs["model"], api_engine.OPENROUTER_MODELS[llm.SONNET])
        self.assertNotIn("fallbacks", self.create.call_args.kwargs)
        self.assertNotIn("betas", self.create.call_args.kwargs)
