"""docker/entrypoint.sh in its two modes, run with fake `python` and `gunicorn` in the PATH (no container needed)."""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from django.conf import settings

SCRIPT = settings.BASE_DIR / "docker" / "entrypoint.sh"
FAKE = "#!/bin/sh\necho \"$(basename \"$0\") $*\" >> \"$CALLS\"\n"


class EntrypointTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.calls = Path(directory.name) / "calls"
        for name in ("python", "gunicorn"):
            fake = Path(directory.name) / name
            fake.write_text(FAKE)
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.env = {"PATH": f"{directory.name}:{os.environ['PATH']}", "CALLS": str(self.calls)}

    def run_script(self, *args):
        subprocess.run(["sh", str(SCRIPT), *args], env=self.env, check=True, timeout=30)
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_script_is_a_posix_shell_script(self):
        self.assertTrue(SCRIPT.read_text().startswith("#!/bin/sh\n"))

    def test_without_argument_it_migrates_then_serves(self):
        calls = self.run_script()
        self.assertEqual(calls[0], "python manage.py migrate --noinput")
        self.assertEqual(calls[1], "gunicorn config.wsgi:application -c docker/gunicorn.conf.py")
        self.assertEqual(len(calls), 2)

    def test_with_arguments_it_runs_them_without_migrating(self):
        calls = self.run_script("python", "manage.py", "planificateur")
        self.assertEqual(calls, ["python manage.py planificateur"])
