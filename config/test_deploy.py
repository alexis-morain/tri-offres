"""scripts/deploy.sh (sub-step 10.4, requirements S15, S19 to S23, S26, S29 to S31 of the security review of 21/10).

The script is never run against the hôte here: every test runs `--dry-run` (or a refusal that happens before any
connection) on a throwaway git repository, with fake `ssh`, `scp`, `midclt` and `docker` first in the PATH that record
their call and fail. All the values are fictitious (documentation addresses, example.org). The files the script builds
are read through TRI_DEPLOY_KEEP_DIR, a private folder of the test.
"""
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE = Path(settings.BASE_DIR)
SCRIPT = BASE / "scripts" / "deploy.sh"
DOC = BASE / "docs" / "deploiement.md"

INFRA = {
    "TRI_DEPLOY_HOST": "nas.example.org",
    "TRI_DEPLOY_USER": "deployer",
    "TRI_DEPLOY_KEY": "/nonexistent/test-key",
    "TRI_DEPLOY_LAN_IP": "192.0.2.10",
    "TRI_DEPLOY_PORT": "31999",
    "TRI_DEPLOY_DOMAIN": "tri.example.org",
    "TRI_DEPLOY_DIR": "/mnt/test/tri-offres-app",
}
ACTOR_COMMIT = "0123456789abcdef0123456789abcdef01234567"

SECRETS = {
    "DJANGO_SECRET_KEY": "k3y-" + "x7Qp2LmZ9vRt4WnB8cYd6HsJ1aFg5UeO3iKb0TqXr2NwMz-fictive",
    "DJANGO_SECRET_KEY_FALLBACKS": "old-fictive-key-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "RESEND_API_KEY": "mail-key-fictive-0000111122223333",
    "OPENROUTER_API_KEY": "sk-or-fictive-0000111122223333",
    "OPENROUTER_KEYS_FERNET": ("dGVzdC1vbmx5LWZlcm5ldC1rZXktMDEyMzQ1Njc4OWE=,"
                               "Zm9ydC1hbnRoLWZlcm5ldC1rZXktMDEyMzQ1Njc4OWE="),
    # Read by no engine any more: never forwarded.
    "ANTHROPIC_API_KEY": "api-key-fictive-0000111122223333",
    "ADMIN_ACCESS_AUD": "aud-fictive-0123456789abcdef0123456789abcdef",
    "SCRAPER_PROXY_URL": "http://proxyuser:proxypass-fictive@192.0.2.20:8888",
    "HEALTHCHECKS_PING_URL": "https://hc-ping.example.org/fictive-ping-key-0123456789",
    "LITESTREAM_ACCESS_KEY_ID": "r2-access-fictive-0123456789",
    "LITESTREAM_SECRET_ACCESS_KEY": "r2-secret-fictive-abcdefghijklmnop",
    "LITESTREAM_HEARTBEAT_URL": "https://hc-ping.example.org/fictive-heartbeat-key-9876543210",
    # Never allowed into any file the script writes.
    "CLAUDE_CODE_OAUTH_TOKEN": "oauth-fictive-token-AAAABBBBCCCCDDDD",
    "APIFY_TOKEN": "apify-fictive-token-EEEEFFFFGGGGHHHH",
    "UNKNOWN_EXTRA_KEY": "unknown-fictive-value-IIIIJJJJKKKK",
}
PLAIN = {
    "LEGAL_ADDRESS": "12 rue Fictive <bâtiment B>, 75000 Paris",
    "LEGAL_SIRET": "00000000000000",
    "LEGAL_NAME": "Nom Fictif",
    "DEFAULT_FROM_EMAIL": "tri-offres <noreply@example.org>",
    "MAIL_REPLY_TO": "contact@example.org",
    "ADMIN_ACCESS_TEAM_DOMAIN": "equipe-fictive.cloudflareaccess.example.org",
    "HSTS_SECONDS": "3600",
    "ALERT_EMAIL": "alerte@example.org",
    "LITESTREAM_R2_BUCKET": "bucket-fictif",
    "LITESTREAM_R2_ENDPOINT": "https://compte-fictif.eu.r2.example.org",
    "CLAUDE_ENGINE": "oauth",
    "DJANGO_DEBUG": "1",
    "DEV_EMAILS": "dev@example.org",
}
ENV_VALUES = {**SECRETS, **PLAIN}
# Values long enough to be searched for in an output (a value like "1" or "3600" appears anywhere).
SEARCHABLE = {key: value for key, value in ENV_VALUES.items() if len(value) >= 8}

REQUIRED = (
    "DJANGO_SECRET_KEY", "RESEND_API_KEY", "OPENROUTER_API_KEY", "OPENROUTER_KEYS_FERNET", "LEGAL_ADDRESS",
    "LEGAL_SIRET", "LEGAL_NAME", "DEFAULT_FROM_EMAIL", "MAIL_REPLY_TO", "SCRAPER_PROXY_URL",
    "LITESTREAM_ACCESS_KEY_ID", "LITESTREAM_SECRET_ACCESS_KEY", "LITESTREAM_R2_BUCKET", "LITESTREAM_R2_ENDPOINT",
)
NEVER = ("CLAUDE_CODE_OAUTH_TOKEN", "APIFY_TOKEN", "UNKNOWN_EXTRA_KEY", "ANTHROPIC_API_KEY")
WEB_ONLY_ABSENT = ("SCRAPER_PROXY_URL", "HEALTHCHECKS_PING_URL", "ALERT_EMAIL")
FIXED = {
    "DJANGO_DEBUG": "0",
    "SITE_URL": "https://tri.example.org",
    "DJANGO_ALLOWED_HOSTS": "tri.example.org",
    "CLAUDE_ENGINE": "api",
    "TRUST_CF_CONNECTING_IP": "1",
    "SCRAPER_PROXY_REQUIRED": "1",
    "DEV_EMAILS": "",
}


def parse_env(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


class DeployCase(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tri-deploy-test."))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tmpdir = self.tmp / "tmpdir"
        self.tmpdir.mkdir()
        self.keep = self.tmp / "keep"
        self.keep.mkdir(mode=0o700)
        self.keep.chmod(0o700)
        self.calls = self.tmp / "forbidden-calls.txt"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name in ("ssh", "scp", "midclt", "docker"):
            fake = self.bin / name
            fake.write_text(f'#!/bin/sh\necho "{name} $*" >> "{self.calls}"\nexit 97\n')
            fake.chmod(0o755)
        self.repo = self.make_repo()
        self.compose_out = self.tmp / "compose.yaml"

    def make_repo(self, actor="matching", symlinked_vendor=False):
        """A throwaway repository with only what the script reads from it."""
        repo = self.tmp / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "docker").mkdir()
        shutil.copy(SCRIPT, repo / "scripts" / "deploy.sh")
        shutil.copy(BASE / "docker-compose.yml", repo / "docker-compose.yml")
        shutil.copy(BASE / "docker" / "litestream.yml", repo / "docker" / "litestream.yml")
        (repo / "ACTOR_REF").write_text(ACTOR_COMMIT + "\n")
        (repo / "Dockerfile").write_text("FROM scratch\n")
        (repo / ".gitignore").write_text("/vendor\n.env\n")
        if actor:
            target = (self.tmp / "vendor-elsewhere") if symlinked_vendor else (repo / "vendor")
            (target / "actor").mkdir(parents=True)
            (target / "actor" / "REF").write_text((ACTOR_COMMIT if actor == "matching" else "f" * 40) + "\n")
            (target / "actor" / "main.py").write_text("print('actor')\n")
            (target / "actor" / "__pycache__").mkdir()
            (target / "actor" / "__pycache__" / "x.pyc").write_bytes(b"\0")
            if symlinked_vendor:
                (repo / "vendor").symlink_to(target)
        self.git(repo, "init", "-q")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "fixture")
        return repo

    def git(self, repo, *args):
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
               "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.org",
               "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.org"}
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)

    def write_env(self, drop=(), override=None, raw=None):
        values = {**ENV_VALUES, **(override or {})}
        for key in drop:
            values.pop(key, None)
        path = self.tmp / "fake.env"
        text = "# fictitious\n\n" + "".join(f"{key}={value}\n" for key, value in values.items())
        path.write_bytes((raw if raw is not None else text).encode("utf-8"))
        return path

    def run_script(self, *args, env_file=None, infra=None, extra=None, repo=None):
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "TMPDIR": str(self.tmpdir),
            "LANG": "C.UTF-8",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "ENV_FILE": str(env_file or self.write_env()),
            "COMPOSE_OUT": str(self.compose_out),
            "TRI_DEPLOY_KEEP_DIR": str(self.keep),
            **(INFRA if infra is None else infra),
            **(extra or {}),
        }
        repo = repo or self.repo
        return subprocess.run(
            ["bash", str(repo / "scripts" / "deploy.sh"), *args],
            capture_output=True, text=True, env=env, timeout=120, stdin=subprocess.DEVNULL,
        )

    def output(self, result):
        return result.stdout + result.stderr

    def assertNoConnection(self):
        self.assertFalse(self.calls.exists(), self.calls.read_text() if self.calls.exists() else "")

    def assertStageGone(self):
        self.assertEqual(list(self.tmpdir.iterdir()), [], "the temporary folder was left behind")

    def assertRefused(self, result, *needles):
        self.assertNotEqual(result.returncode, 0, self.output(result))
        for needle in needles:
            self.assertIn(needle, self.output(result))
        self.assertNoConnection()
        self.assertStageGone()
        self.assertFalse(self.compose_out.exists())

    def dry_run(self, **kwargs):
        result = self.run_script("--dry-run", **kwargs)
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertNoConnection()
        self.assertStageGone()
        return result

    def kept(self, name):
        return parse_env(self.keep / f"{name}.env")


class ScriptTests(DeployCase):
    def test_bash_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_shellcheck_when_installed(self):
        if not shutil.which("shellcheck"):
            self.skipTest("shellcheck is not installed")
        result = subprocess.run(["shellcheck", "-s", "bash", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_strict_mode_and_no_trace(self):
        text = SCRIPT.read_text()
        self.assertIn("set -euo pipefail", text)
        self.assertIn("umask 077", text)
        self.assertIn("trap ", text)
        self.assertNotRegex(text, r"(?m)^\s*set\s+-\w*x")
        self.assertNotIn("xtrace", text)
        self.assertNotIn("StrictHostKeyChecking=no", text)
        # The env file is read value by value, never sourced.
        self.assertNotRegex(text, r"(?m)^\s*(source|\.)\s+\S*(ENV_FILE|\.env)")

    def test_no_real_infrastructure_value_in_script_or_doc(self):
        for path in (SCRIPT, DOC):
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for address in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text):
                self.assertTrue(
                    address.startswith(("192.0.2.", "127.0.0.", "0.0.0.0", "198.51.100.", "203.0.113.")),
                    f"{path.name}: {address}")
            for forbidden in ("morain", "truenas_admin", ".ssh/", "id_rsa", "id_ed25519", "100.66", "192.168"):
                self.assertNotIn(forbidden, text.lower(), f"{path.name}: {forbidden}")

    def test_rm_only_touches_the_exact_src_folder_or_temporary_files(self):
        lines = [line for line in SCRIPT.read_text().splitlines()
                 if re.search(r"(?<![-\w])rm\s", line) and not line.lstrip().startswith("#")]
        self.assertTrue(any("REMOTE_SRC" in line for line in lines))
        for line in lines:
            self.assertRegex(line, r'REMOTE_SRC|"\$STAGE"|tmp_remote|log_remote|"\$tree"', line)
            command = line[re.search(r"(?<![-\w])rm\s", line).start():]
            self.assertNotRegex(command, r"/data|/env|\*", line)

    def test_secrets_never_travel_as_command_arguments(self):
        text = SCRIPT.read_text()
        # Environment files go through the standard input of sudo tee, nothing else carries a value.
        self.assertIn("sudo tee", text)
        self.assertNotRegex(text, r"(?i)echo\s+[\"']?\$VALUE")
        self.assertNotRegex(text, r"ssh[^\n]*\$VALUE")

    def test_unknown_option_is_refused(self):
        result = self.run_script("--unknown")
        self.assertEqual(result.returncode, 2, self.output(result))
        self.assertNoConnection()

    def test_missing_infra_variable_is_named_before_anything_else(self):
        for name in INFRA:
            with self.subTest(name):
                infra = {key: value for key, value in INFRA.items() if key != name}
                result = self.run_script("--dry-run", infra=infra)
                self.assertRefused(result, name)

    def test_empty_infra_variable_is_missing_too(self):
        result = self.run_script("--dry-run", infra={**INFRA, "TRI_DEPLOY_HOST": ""})
        self.assertRefused(result, "TRI_DEPLOY_HOST")

    def test_infra_values_are_validated(self):
        bad = {
            "TRI_DEPLOY_LAN_IP": ["0.0.0.0", "300.1.1.1", "192.0.2.10; id", "nas"],
            "TRI_DEPLOY_PORT": ["0", "70000", "80 80", "http"],
            "TRI_DEPLOY_DOMAIN": ["tri.example.org,evil.example.org", "https://tri.example.org", "localhost", "TRI.example.org/x"],
            "TRI_DEPLOY_DIR": ["/", "/mnt", "relative/dir", "/mnt/../etc", "/mnt/a b", "/mnt/x/", "/mnt/$HOME/x", "/mnt/./x"],
            "TRI_DEPLOY_HOST": ["nas;id", "nas example"],
            "TRI_DEPLOY_USER": ["root;id", "-oProxyCommand=x"],
        }
        for name, values in bad.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    result = self.run_script("--dry-run", infra={**INFRA, name: value})
                    self.assertRefused(result, name)


class DryRunTests(DeployCase):
    def test_dry_run_builds_the_three_files_and_the_compose(self):
        result = self.dry_run()
        text = self.output(result)
        self.assertTrue(self.compose_out.exists())
        for name in ("web", "scheduler", "litestream"):
            self.assertIn(f"{name}.env", text)
        # The names of the kept keys are shown.
        for key in ("DJANGO_SECRET_KEY", "OPENROUTER_API_KEY", "OPENROUTER_KEYS_FERNET", "LITESTREAM_R2_BUCKET"):
            self.assertIn(key, text)
        self.assertIn("docker compose -f", text)

    def test_nothing_secret_in_the_output(self):
        result = self.dry_run()
        text = self.output(result)
        for key, value in SEARCHABLE.items():
            self.assertNotIn(value, text, key)
        compose = self.compose_out.read_text()
        for key, value in SEARCHABLE.items():
            self.assertNotIn(value, compose, key)

    def test_forbidden_names_and_values_never_appear(self):
        result = self.dry_run()
        text = self.output(result) + self.compose_out.read_text()
        for name in NEVER:
            self.assertNotIn(name, text)
            self.assertNotIn(SECRETS[name], text)
        for name in ("web", "scheduler", "litestream"):
            content = (self.keep / f"{name}.env").read_text()
            for key in NEVER:
                self.assertNotIn(key, content)
                self.assertNotIn(SECRETS[key], content)
            self.assertNotIn("APIFY", content)
            self.assertNotIn("OAUTH", content)

    def test_files_are_a_whitelist_with_the_expected_split(self):
        self.dry_run()
        web, scheduler, litestream = self.kept("web"), self.kept("scheduler"), self.kept("litestream")
        for key in ("DJANGO_SECRET_KEY", "DJANGO_SECRET_KEY_FALLBACKS", "RESEND_API_KEY", "OPENROUTER_API_KEY",
                    "OPENROUTER_KEYS_FERNET",
                    "LEGAL_ADDRESS", "LEGAL_SIRET", "LEGAL_NAME", "DEFAULT_FROM_EMAIL", "MAIL_REPLY_TO",
                    "ADMIN_ACCESS_TEAM_DOMAIN", "ADMIN_ACCESS_AUD", "HSTS_SECONDS"):
            self.assertIn(key, web)
        for key in WEB_ONLY_ABSENT:
            self.assertNotIn(key, web)
            self.assertIn(key, scheduler)
        for key in ("DJANGO_SECRET_KEY", "RESEND_API_KEY", "OPENROUTER_API_KEY", "OPENROUTER_KEYS_FERNET",
                    "LEGAL_NAME", "DEFAULT_FROM_EMAIL",
                    "MAIL_REPLY_TO"):
            self.assertIn(key, scheduler)
        self.assertNotIn("ADMIN_ACCESS_AUD", scheduler)
        self.assertNotIn("ADMIN_ACCESS_TEAM_DOMAIN", scheduler)
        self.assertEqual(
            set(litestream),
            {"LITESTREAM_ACCESS_KEY_ID", "LITESTREAM_SECRET_ACCESS_KEY", "LITESTREAM_R2_BUCKET",
             "LITESTREAM_R2_ENDPOINT", "LITESTREAM_HEARTBEAT_URL"})
        for name in ("web", "scheduler"):
            for key in kept_keys(self.keep / f"{name}.env"):
                self.assertFalse(key.startswith("LITESTREAM_"), (name, key))
        self.assertEqual(web["DJANGO_SECRET_KEY"], SECRETS["DJANGO_SECRET_KEY"])
        self.assertEqual(web["LEGAL_ADDRESS"], PLAIN["LEGAL_ADDRESS"])
        self.assertEqual(scheduler["SCRAPER_PROXY_URL"], SECRETS["SCRAPER_PROXY_URL"])
        self.assertEqual(litestream["LITESTREAM_SECRET_ACCESS_KEY"], SECRETS["LITESTREAM_SECRET_ACCESS_KEY"])

    def test_fixed_values_override_the_env_file(self):
        self.dry_run()
        for name in ("web", "scheduler"):
            values = self.kept(name)
            for key, expected in FIXED.items():
                self.assertEqual(values.get(key), expected, (name, key))

    def test_each_line_is_a_plain_assignment_of_a_legal_name(self):
        self.dry_run()
        for name in ("web", "scheduler", "litestream"):
            keys = kept_keys(self.keep / f"{name}.env")
            self.assertEqual(len(keys), len(set(keys)), f"duplicate key in {name}.env")
            for line in (self.keep / f"{name}.env").read_text().splitlines():
                self.assertRegex(line, r"^[A-Z][A-Z0-9_]*=[^$\r\n\x00]*$")

    def test_files_are_private(self):
        self.dry_run()
        for name in ("web", "scheduler", "litestream"):
            mode = stat.S_IMODE((self.keep / f"{name}.env").stat().st_mode)
            self.assertEqual(mode, 0o600, name)

    def test_keep_dir_must_be_private(self):
        self.keep.chmod(0o755)
        result = self.run_script("--dry-run")
        self.assertRefused(result, "TRI_DEPLOY_KEEP_DIR")

    def test_real_modes_need_the_ssh_key_before_any_connection(self):
        result = self.run_script("--build-only", infra={**INFRA, "TRI_DEPLOY_KEY": str(self.tmp / "missing-key")})
        self.assertRefused(result, "TRI_DEPLOY_KEY")

    def test_engine_is_api_even_if_the_env_file_says_oauth(self):
        result = self.dry_run()
        self.assertEqual(self.kept("web")["CLAUDE_ENGINE"], "api")
        self.assertEqual(self.kept("scheduler")["CLAUDE_ENGINE"], "api")
        self.assertIn("CLAUDE_ENGINE", self.output(result))

    def test_oauth_engine_is_opt_in_from_the_shell(self):
        result = self.dry_run(extra={"TRI_DEPLOY_ENGINE": "oauth"},
                              env_file=self.write_env(override={"DEV_EMAILS": "dev@example.org,b@example.org"}))
        for name in ("web", "scheduler"):
            values = self.kept(name)
            self.assertEqual(values["CLAUDE_ENGINE"], "oauth")
            self.assertEqual(values["DEV_EMAILS"], "dev@example.org,b@example.org")
            self.assertEqual(values["CLAUDE_CODE_OAUTH_TOKEN"], SECRETS["CLAUDE_CODE_OAUTH_TOKEN"])
            self.assertEqual(values["BETA_CAPACITY"], "2")
            self.assertNotIn("ANTHROPIC_API_KEY", values)
            self.assertNotIn("OPENROUTER_API_KEY", values)
            self.assertNotIn("OPENROUTER_KEYS_FERNET", values)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", self.kept("litestream"))
        self.assertNotIn(SECRETS["CLAUDE_CODE_OAUTH_TOKEN"], self.output(result))

    def test_oauth_engine_needs_dev_emails_and_the_token(self):
        for key in ("DEV_EMAILS", "CLAUDE_CODE_OAUTH_TOKEN"):
            with self.subTest(key=key):
                result = self.run_script("--dry-run", extra={"TRI_DEPLOY_ENGINE": "oauth"},
                                         env_file=self.write_env(drop=(key,)))
                self.assertRefused(result, key)

    def test_the_openrouter_keys_go_to_the_site_and_the_scheduler_never_to_litestream(self):
        result = self.dry_run()
        for name in ("web", "scheduler"):
            values = self.kept(name)
            self.assertEqual(values["OPENROUTER_API_KEY"], SECRETS["OPENROUTER_API_KEY"])
            self.assertEqual(values["OPENROUTER_KEYS_FERNET"], SECRETS["OPENROUTER_KEYS_FERNET"])
            self.assertNotIn("ANTHROPIC_API_KEY", values)
        litestream = self.kept("litestream")
        for key in ("OPENROUTER_API_KEY", "OPENROUTER_KEYS_FERNET", "ANTHROPIC_API_KEY"):
            self.assertNotIn(key, litestream)
        for key in ("OPENROUTER_API_KEY", "OPENROUTER_KEYS_FERNET", "ANTHROPIC_API_KEY"):
            self.assertNotIn(SECRETS[key], self.output(result))
            self.assertNotIn(SECRETS[key], self.compose_out.read_text())

    def test_the_fernet_keys_are_checked_and_the_error_never_repeats_them(self):
        for value in ("pas-une-cle", "dGVzdA==", SECRETS["OPENROUTER_KEYS_FERNET"] + ",pas-une-cle"):
            with self.subTest(value=value):
                env_file = self.write_env(override={"OPENROUTER_KEYS_FERNET": value})
                result = self.run_script("--dry-run", env_file=env_file)
                self.assertRefused(result, "OPENROUTER_KEYS_FERNET")
                self.assertNotIn(value, self.output(result))

    def test_unknown_engine_is_refused(self):
        self.assertRefused(self.run_script("--dry-run", extra={"TRI_DEPLOY_ENGINE": "max"}), "TRI_DEPLOY_ENGINE")

    def test_s31_checks(self):
        self.dry_run()
        for name in ("web", "scheduler"):
            text = (self.keep / f"{name}.env").read_text().splitlines()
            self.assertIn("SCRAPER_PROXY_REQUIRED=1", text)
            self.assertIn("DJANGO_DEBUG=0", text)
            self.assertIn("DEV_EMAILS=", text)

    def test_optional_ping_urls_warn_when_empty(self):
        result = self.dry_run(env_file=self.write_env(drop=("HEALTHCHECKS_PING_URL", "LITESTREAM_HEARTBEAT_URL")))
        text = self.output(result)
        self.assertIn("HEALTHCHECKS_PING_URL", text)
        self.assertIn("LITESTREAM_HEARTBEAT_URL", text)
        self.assertNotIn("HEALTHCHECKS_PING_URL", self.kept("scheduler"))
        self.assertEqual(self.kept("litestream")["LITESTREAM_HEARTBEAT_URL"], "")

    def test_admin_access_missing_warns_that_the_admin_is_closed(self):
        result = self.dry_run(env_file=self.write_env(drop=("ADMIN_ACCESS_AUD", "ADMIN_ACCESS_TEAM_DOMAIN")))
        self.assertIn("ADMIN_ACCESS_AUD", self.output(result))
        self.assertNotIn("ADMIN_ACCESS_AUD", self.kept("web"))

    def test_next_steps_are_printed(self):
        text = self.output(self.dry_run())
        for expected in ("tri-offres-web", "tri-offres-scheduler", "tri-offres-litestream", "healthcheck.py", "/admin/"):
            self.assertIn(expected, text)

    def test_quoted_values_lose_their_quotes_and_spaces_stay(self):
        env_file = self.write_env(raw='\n'.join(
            f"{k}={v}" for k, v in ENV_VALUES.items() if k != "LEGAL_ADDRESS") + '\nLEGAL_ADDRESS="12 rue A <x>, Paris"\n')
        self.dry_run(env_file=env_file)
        self.assertEqual(self.kept("web")["LEGAL_ADDRESS"], "12 rue A <x>, Paris")

    def test_stage_folder_is_removed_after_a_failure(self):
        result = self.run_script("--dry-run", env_file=self.write_env(drop=("RESEND_API_KEY",)))
        self.assertRefused(result, "RESEND_API_KEY")


class EnvGuardTests(DeployCase):
    def test_each_required_key_missing_stops_the_script(self):
        for key in REQUIRED:
            with self.subTest(key):
                result = self.run_script("--dry-run", env_file=self.write_env(drop=(key,)))
                self.assertRefused(result, key)

    def test_empty_required_key_stops_the_script(self):
        result = self.run_script("--dry-run", env_file=self.write_env(override={"OPENROUTER_API_KEY": ""}))
        self.assertRefused(result, "OPENROUTER_API_KEY")

    def test_all_missing_keys_are_named_at_once(self):
        result = self.run_script("--dry-run", env_file=self.write_env(drop=("RESEND_API_KEY", "LEGAL_SIRET")))
        self.assertRefused(result, "RESEND_API_KEY", "LEGAL_SIRET")

    def test_missing_env_file(self):
        result = self.run_script("--dry-run", env_file=self.tmp / "absent.env")
        self.assertRefused(result)

    def test_refused_characters_name_the_key_and_never_the_value(self):
        cases = {
            "dollar": "abc$HOME",
            "carriage return": "abc\rdef",
            "open quote (value on several lines)": '"abc',
            "backslash": "abc\\ndef",
            "comment": "abc #def",
            "control character": "abc\x07def",
        }
        for label, value in cases.items():
            with self.subTest(label):
                env_file = self.write_env(override={"LEGAL_ADDRESS": value})
                result = self.run_script("--dry-run", env_file=env_file)
                self.assertRefused(result, "LEGAL_ADDRESS")
                self.assertNotIn(value, self.output(result))

    def test_nul_in_the_env_file(self):
        text = "".join(f"{k}={v}\n" for k, v in ENV_VALUES.items())
        env_file = self.write_env(raw=text + "JUNK=a\0b\n")
        result = self.run_script("--dry-run", env_file=env_file)
        self.assertRefused(result)

    def test_dollar_in_a_secret_is_refused(self):
        result = self.run_script("--dry-run", env_file=self.write_env(override={"RESEND_API_KEY": "re_a$b"}))
        self.assertRefused(result, "RESEND_API_KEY")
        self.assertNotIn("re_a$b", self.output(result))

    def test_secret_key_rules(self):
        cases = {
            "too short": "a" * 49,
            "insecure prefix": "django-insecure-" + "a" * 60,
            "dollar": "a" * 60 + "$",
        }
        for label, value in cases.items():
            with self.subTest(label):
                result = self.run_script("--dry-run", env_file=self.write_env(override={"DJANGO_SECRET_KEY": value}))
                self.assertRefused(result, "DJANGO_SECRET_KEY")
        self.dry_run(env_file=self.write_env(override={"DJANGO_SECRET_KEY": "a" * 50}))

    def test_values_have_the_shape_the_settings_expect(self):
        cases = {
            "HEALTHCHECKS_PING_URL": "http://hc.example.org/x",
            "SCRAPER_PROXY_URL": "socks5://192.0.2.20:1080",
            "LITESTREAM_R2_ENDPOINT": "http://compte.example.org",
            "HSTS_SECONDS": "un an",
            "ADMIN_ACCESS_TEAM_DOMAIN": "https://equipe.example.org",
        }
        for key, value in cases.items():
            with self.subTest(key):
                result = self.run_script("--dry-run", env_file=self.write_env(override={key: value}))
                self.assertRefused(result, key)

    def test_last_duplicate_wins_like_a_dotenv_file(self):
        text = "".join(f"{k}={v}\n" for k, v in ENV_VALUES.items()) + "LEGAL_SIRET=11111111111111\n"
        self.dry_run(env_file=self.write_env(raw=text))
        self.assertEqual(self.kept("web")["LEGAL_SIRET"], "11111111111111")


class GitGuardTests(DeployCase):
    def test_dirty_tree_is_refused(self):
        (self.repo / "dirty.txt").write_text("x")
        self.assertRefused(self.run_script("--dry-run"), "git")

    def test_modified_tracked_file_is_refused(self):
        (self.repo / "Dockerfile").write_text("FROM scratch\n# changed\n")
        self.assertRefused(self.run_script("--dry-run"), "git")

    def test_repository_without_commit_is_refused(self):
        shutil.rmtree(self.repo / ".git")
        self.git(self.repo, "init", "-q")
        self.assertRefused(self.run_script("--dry-run"), "HEAD")

    def test_actor_at_another_commit_is_refused(self):
        shutil.rmtree(self.repo)
        self.repo = self.make_repo(actor="other")
        self.assertRefused(self.run_script("--dry-run"), "ACTOR_REF", "fetch_actor.sh")

    def test_missing_actor_warns_loudly_and_still_builds(self):
        shutil.rmtree(self.repo)
        self.repo = self.make_repo(actor=None)
        result = self.dry_run()
        text = self.output(result)
        self.assertIn("SANS l’actor", text)
        self.assertIn("scripts/fetch_actor.sh", text)

    def test_actor_present_is_in_the_archive_without_caches(self):
        text = self.output(self.dry_run())
        self.assertIn("vendor/actor/REF", text)
        self.assertNotIn("SANS l’actor", text)

    def test_vendor_symlink_is_followed(self):
        shutil.rmtree(self.repo)
        self.repo = self.make_repo(symlinked_vendor=True)
        text = self.output(self.dry_run())
        self.assertIn("vendor/actor/REF", text)

    def test_litestream_image_comes_from_the_compose_file_and_is_pinned(self):
        self.dry_run()
        compose = self.compose_out.read_text()
        pinned = re.search(r"image: (litestream/litestream:\S+@sha256:[0-9a-f]{64})", compose)
        self.assertIsNotNone(pinned)
        self.assertIn(pinned.group(1), (BASE / "docker-compose.yml").read_text())

    def test_unpinned_litestream_image_is_refused(self):
        compose = self.repo / "docker-compose.yml"
        compose.write_text(re.sub(r"@sha256:[0-9a-f]{64}", "", compose.read_text()))
        self.git(self.repo, "commit", "-qam", "unpin")
        self.assertRefused(self.run_script("--dry-run"), "Litestream")


class ComposeTests(DeployCase):
    def setUp(self):
        super().setUp()
        self.dry_run()
        self.compose = self.compose_out.read_text()

    def service(self, name):
        match = re.search(rf"(?ms)^  {name}:\n(.*?)(?=^  \S|\Z)", self.compose)
        self.assertIsNotNone(match, name)
        return match.group(1)

    def test_three_services_and_no_secret(self):
        for name in ("web", "scheduler", "litestream"):
            self.service(name)
        self.assertNotRegex(self.compose, r"(?m)^\s+(environment|secrets):")
        for value in SEARCHABLE.values():
            self.assertNotIn(value, self.compose)
        self.assertNotRegex(self.compose, r"(?i)(password|token|secret_key|api_key)\s*[:=]\s*\S")

    def test_every_service_is_hardened(self):
        for name in ("web", "scheduler", "litestream"):
            block = self.service(name)
            self.assertRegex(block, r"(?m)^    cap_drop:\n      - ALL$", name)
            self.assertIn("no-new-privileges:true", block, name)
            self.assertRegex(block, r"(?m)^    mem_limit: \d+[mg]$", name)
            self.assertRegex(block, r"(?m)^    pids_limit: \d+$", name)
            self.assertIn("restart: unless-stopped", block, name)
            self.assertRegex(block, r'max-size: "20m"', name)
            self.assertRegex(block, r'max-file: "3"', name)
            self.assertIn("driver: json-file", block, name)

    def test_nothing_dangerous(self):
        self.assertNotRegex(self.compose, r"(?i)privileged|network_mode|pid: host|ipc: host|docker\.sock|cap_add|/var/run")
        self.assertNotRegex(self.compose, r"depends_on|condition:|service_healthy")
        self.assertNotIn("0.0.0.0", self.compose)
        self.assertNotRegex(self.compose, r"(?m)^\s+- \"?/(:|\s)")

    def test_web(self):
        block = self.service("web")
        self.assertIn(f'- "{INFRA["TRI_DEPLOY_LAN_IP"]}:{INFRA["TRI_DEPLOY_PORT"]}:8000"', block)
        self.assertEqual(len(re.findall(r"(?m)^      - \".*:\d+:\d+\"$", block)), 1)
        self.assertIn(f'{INFRA["TRI_DEPLOY_DIR"]}/env/web.env', block)
        self.assertNotIn("scheduler.env", block)
        self.assertIn(f'- "{INFRA["TRI_DEPLOY_DIR"]}/data:/data"', block)
        self.assertIn("pull_policy: never", block)
        self.assertRegex(block, r"image: \"tri-offres:[0-9a-f]{12}\"")
        self.assertIn('"python", "docker/healthcheck.py"', block)
        self.assertNotIn("command:", block)

    def test_scheduler(self):
        block = self.service("scheduler")
        self.assertIn('command: ["python", "manage.py", "planificateur"]', block)
        self.assertIn(f'{INFRA["TRI_DEPLOY_DIR"]}/env/scheduler.env', block)
        self.assertNotIn("web.env", block)
        self.assertNotIn("ports:", block)
        self.assertIn("pull_policy: never", block)
        self.assertIn(f'- "{INFRA["TRI_DEPLOY_DIR"]}/data:/data"', block)
        web_image = re.search(r"image: (\S+)", self.service("web")).group(1)
        self.assertIn(f"image: {web_image}", block)

    def test_litestream(self):
        block = self.service("litestream")
        self.assertRegex(block, r"image: litestream/litestream:\S+@sha256:[0-9a-f]{64}")
        self.assertIn(f'{INFRA["TRI_DEPLOY_DIR"]}/env/litestream.env', block)
        self.assertIn(f'- "{INFRA["TRI_DEPLOY_DIR"]}/litestream.yml:/etc/litestream.yml:ro"', block)
        self.assertIn('user: "1000:1000"', block)
        self.assertNotIn("ports:", block)
        self.assertNotIn("pull_policy: never", block)

    def test_only_the_expected_volumes(self):
        paths = re.findall(r'(?m)^      - "(/[^":]+):', self.compose)
        allowed = {f'{INFRA["TRI_DEPLOY_DIR"]}/data', f'{INFRA["TRI_DEPLOY_DIR"]}/litestream.yml'}
        self.assertTrue(set(paths) <= allowed | {f'{INFRA["TRI_DEPLOY_DIR"]}/env/{n}.env' for n in
                                                 ("web", "scheduler", "litestream")}, paths)


def kept_keys(path):
    return [line.split("=", 1)[0] for line in Path(path).read_text().splitlines()]
