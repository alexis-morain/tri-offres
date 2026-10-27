"""OPENROUTER_KEYS_FERNET (contract 5.2, 5.14): the list of Fernet keys that seal the people's OpenRouter keys."""
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.env import fernet_keys


class FernetKeysSettingTests(SimpleTestCase):
    def test_absent_is_refused_under_the_api_engine(self):
        for environ in ({}, {"OPENROUTER_KEYS_FERNET": ""}, {"OPENROUTER_KEYS_FERNET": " , "}):
            with self.subTest(environ=environ), self.assertRaises(ImproperlyConfigured):
                fernet_keys("api", environ)

    def test_absent_is_fine_under_the_other_engines(self):
        for engine in ("fake", "oauth", ""):
            self.assertEqual(fernet_keys(engine, {}), [])

    def test_malformed_is_refused_and_the_error_never_repeats_the_value(self):
        good = Fernet.generate_key().decode()
        for engine in ("api", "fake"):
            for value in ("pas-une-cle", "dGVzdA==", f"{good},pas-une-cle"):
                with self.subTest(engine=engine, value=value), self.assertRaises(ImproperlyConfigured) as caught:
                    fernet_keys(engine, {"OPENROUTER_KEYS_FERNET": value})
                self.assertNotIn("pas-une-cle", str(caught.exception))
                self.assertNotIn(good, str(caught.exception))

    def test_a_list_keeps_its_order_first_key_seals(self):
        first, second = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        self.assertEqual(fernet_keys("api", {"OPENROUTER_KEYS_FERNET": f" {first} , {second} "}), [first, second])
