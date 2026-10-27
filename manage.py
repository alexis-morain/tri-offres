#!/usr/bin/env python
import atexit
import os
import shutil
import sys
import tempfile


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    if sys.argv[1:2] == ["test"]:
        # Tests run with DEBUG off, like production, and must not need a real secret.
        os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key")
        # Assigned, not setdefault: an exported CLAUDE_ENGINE=oauth or api must never reach Claude from a test.
        os.environ["CLAUDE_ENGINE"] = "fake"
        # Assigned too: an exported BETA=0 must not change the default of the tests (they use override_settings).
        os.environ["BETA"] = "1"
        # Same for the ping key: no test may reach healthchecks.io, whatever the shell exports.
        os.environ["HEALTHCHECKS_PING_URL"] = ""
        # And no test writes to the real account journal (accounts/journal.py): a throwaway folder, removed at exit.
        journal_dir = tempfile.mkdtemp(prefix="tri-offres-test-journal-")
        atexit.register(shutil.rmtree, journal_dir, ignore_errors=True)
        os.environ["ACCOUNT_JOURNAL_PATH"] = os.path.join(journal_dir, "journal-comptes.txt")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
