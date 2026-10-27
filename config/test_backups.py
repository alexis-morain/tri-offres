"""Backups (sub-step 10.3, requirements S24, S27, S29 of the security review of 21/10): Litestream's configuration, its
service in the compose file, and the 30 days that the privacy page announces. The files are read as text (no YAML
library in the project); the keys were checked against the Litestream 0.5 reference and by loading the file with
`litestream databases` in the pinned image."""
import re

from django.conf import settings
from django.test import SimpleTestCase

from config import backups

BASE = settings.BASE_DIR
CONFIG = BASE / "docker" / "litestream.yml"
COMPOSE = BASE / "docker-compose.yml"
DIGEST = re.compile(r"^litestream/litestream:0\.5\.17@sha256:[0-9a-f]{64}$")


def top_level_block(text, name):
    """The indented lines under a top-level `name:` key of a YAML file, without comment lines."""
    lines = text.splitlines()
    for number, line in enumerate(lines):
        if line.rstrip() == f"{name}:" or line.startswith(f"{name}:"):
            block = []
            for following in lines[number + 1:]:
                if following.strip() and not following.startswith((" ", "#")):
                    break
                if following.strip() and not following.lstrip().startswith("#"):
                    block.append(following)
            return block
    return None


def scalar(block, key):
    for line in block:
        match = re.match(rf"^\s*(?:- )?{re.escape(key)}:\s*(.*?)\s*$", line)
        if match:
            return match.group(1)
    return None


def hours(value):
    match = re.fullmatch(r"(\d+)h", value or "")
    assert match, f"not a duration in hours: {value!r}"
    return int(match.group(1))


def service_blocks(text):
    """{service name: its text} for a compose file whose services sit at two spaces."""
    services = text.split("\nservices:\n", 1)[1]
    parts = re.split(r"(?m)^  ([a-z][a-z0-9_-]*):\s*$", services)
    return dict(zip(parts[1::2], parts[2::2]))


class LitestreamConfigTests(SimpleTestCase):
    def setUp(self):
        self.text = CONFIG.read_text()
        self.code = "\n".join(line for line in self.text.splitlines() if not line.lstrip().startswith("#"))

    def test_backup_days_is_thirty(self):
        self.assertEqual(backups.BACKUP_DAYS, 30)

    def test_retention_plus_interval_is_the_announced_duration(self):
        snapshot = top_level_block(self.text, "snapshot")
        self.assertEqual(scalar(snapshot, "interval"), "24h")
        self.assertEqual(scalar(snapshot, "retention"), "696h")
        # A snapshot is dropped once older than the retention, and one is taken every interval: the oldest restore
        # point is at most retention + interval old.
        self.assertEqual(hours(scalar(snapshot, "retention")) + hours(scalar(snapshot, "interval")),
                         backups.BACKUP_DAYS * 24)

    def test_retention_is_active(self):
        block = top_level_block(self.text, "retention")
        self.assertEqual(scalar(block, "enabled"), "true")

    def test_one_database_the_one_of_the_image_and_nothing_else_is_replicated(self):
        dockerfile = (BASE / "Dockerfile").read_text()
        self.assertIn("DATABASE_PATH=/data/tri-offres.sqlite3", dockerfile)
        dbs = top_level_block(self.text, "dbs")
        self.assertEqual([scalar([line], "path") for line in dbs if re.match(r"^  - ", line)],
                         ["/data/tri-offres.sqlite3"])
        self.assertNotIn("dir:", "\n".join(dbs))  # the account journal sits next to the database: not replicated

    def test_replica_is_r2_over_https_from_the_environment(self):
        dbs = top_level_block(self.text, "dbs")
        self.assertEqual(scalar(dbs, "type"), "s3")
        self.assertEqual(scalar(dbs, "bucket"), "${LITESTREAM_R2_BUCKET}")
        self.assertEqual(scalar(dbs, "endpoint"), "${LITESTREAM_R2_ENDPOINT}")
        self.assertIn("      path: tri-offres\n", self.text)  # the replica's path; the database's is under `- path:`
        self.assertEqual(scalar(dbs, "region"), "auto")

    def test_heartbeat_comes_from_the_environment(self):
        self.assertEqual(scalar(self.code.splitlines(), "heartbeat-url").lstrip(), "${LITESTREAM_HEARTBEAT_URL}")

    def test_no_secret_in_the_file(self):
        for line in self.code.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.strip().lstrip("- ") in ("access-key-id", "secret-access-key"):
                self.fail(f"{key.strip()} must come from LITESTREAM_ACCESS_KEY_ID / LITESTREAM_SECRET_ACCESS_KEY")
            self.assertNotRegex(value, r"^\s*[A-Za-z0-9+/_-]{32,}\s*$", line)
        self.assertNotIn("hc-ping.com", self.code)

    def test_no_age_no_listening_port_no_socket(self):
        for forbidden in (r"^\s*age:", r"^\s*addr:", r"^\s*mcp-addr:", r"^\s*socket:", r"^\s*sse-", r"skip-verify"):
            with self.subTest(forbidden=forbidden):
                self.assertNotRegex(self.code, re.compile(forbidden, re.M))

    def test_endpoint_is_never_http(self):
        self.assertNotIn("http://", self.code)

    def test_comment_ties_the_file_to_the_privacy_page(self):
        self.assertIn("config/backups.py", self.text)


class LitestreamServiceTests(SimpleTestCase):
    def setUp(self):
        self.text = COMPOSE.read_text()
        self.services = service_blocks(self.text)
        self.litestream = self.services["litestream"]

    def line(self, pattern):
        return re.search(pattern, self.litestream, re.M)

    def test_image_is_pinned_by_digest(self):
        image = self.line(r"^    image: (\S+)")
        self.assertIsNotNone(image)
        self.assertRegex(image.group(1), DIGEST)

    def test_it_is_behind_the_backup_profile(self):
        self.assertIsNotNone(self.line(r'^    profiles: \["backup"\]$'))

    def test_command_replicates_with_the_mounted_config(self):
        self.assertIsNotNone(self.line(r'^    command: \["replicate", "-config", "/etc/litestream\.yml"\]$'))

    def test_volumes_data_writable_and_config_read_only(self):
        self.assertIn("      - ./data:/data\n", self.litestream)
        self.assertIn("      - ./docker/litestream.yml:/etc/litestream.yml:ro\n", self.litestream)

    def test_hardening(self):
        self.assertIsNotNone(self.line(r'^    user: "1000:1000"$'))
        self.assertRegex(self.litestream, r"cap_drop:\n\s+- ALL")
        self.assertRegex(self.litestream, r"security_opt:\n\s+- no-new-privileges:true")
        self.assertIsNone(self.line(r"^    ports:"))
        self.assertIsNone(self.line(r"^    privileged:"))

    def test_litestream_variables_are_interpolated_and_empty_by_default(self):
        for name in ("LITESTREAM_ACCESS_KEY_ID", "LITESTREAM_SECRET_ACCESS_KEY", "LITESTREAM_R2_BUCKET",
                     "LITESTREAM_R2_ENDPOINT", "LITESTREAM_HEARTBEAT_URL"):
            with self.subTest(name=name):
                self.assertRegex(self.litestream, rf"{name}: \$\{{{name}:-\}}")

    def test_no_litestream_variable_outside_the_litestream_service(self):
        for name, block in self.services.items():
            if name != "litestream":
                with self.subTest(service=name):
                    self.assertNotIn("LITESTREAM_", block)
        self.assertIn("LITESTREAM_", self.litestream)
        # Nothing before the service either: no top-level anchor or x- block that the others could merge.
        self.assertNotIn("LITESTREAM_", self.text.split("\n  litestream:\n", 1)[0])

    def test_no_env_file_and_the_site_services_are_unchanged(self):
        self.assertNotIn("env_file:", self.litestream)
        self.assertIn("web", self.services)
        self.assertIn("scheduler", self.services)
        self.assertNotIn("profiles", self.services["web"])
        self.assertNotIn("profiles", self.services["scheduler"])
