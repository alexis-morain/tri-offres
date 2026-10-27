"""Settings overrides and checks shared by the apps' test suites."""
import re
from collections import Counter

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

# The manifest storage needs collectstatic; tests swap in the plain backend.
PLAIN_STATIC = {**settings.STORAGES,
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


def visible_text(html):
    """Text of a page or a mail, without tags, attributes or the document head."""
    if "<body" in html:
        html = html.split("<body", 1)[1]
    return re.sub(r"<[^>]+>", " ", html)


def assert_french_copy(testcase, html):
    """House style for French copy: no em dash, curly apostrophes only."""
    text = visible_text(html)
    testcase.assertNotIn("—", text)
    testcase.assertNotRegex(text, r"[A-Za-zÀ-ÿ]'[A-Za-zÀ-ÿ]")


def assert_unique_ids(testcase, html):
    ids = Counter(re.findall(r'\sid="([^"]+)"', html))
    testcase.assertEqual([i for i, n in ids.items() if n > 1], [])


class MigrationTestCase(TransactionTestCase):
    """Applies `migrate_to` to rows written at the state `migrate_from`, by Django's MigrationExecutor.

    setUpBeforeMigration(apps) writes the rows with the historical models; afterwards self.apps holds the models of
    `migrate_to`. migrate(target) moves the database again, to test a migration backwards.
    """

    migrate_from = None  # [(app, migration name)]
    migrate_to = None

    def setUp(self):
        super().setUp()
        self.addCleanup(self.migrate, None)  # back to the latest migrations for the next tests
        self.setUpBeforeMigration(self.migrate(self.migrate_from))
        self.apps = self.migrate(self.migrate_to)

    def setUpBeforeMigration(self, apps):
        pass

    @staticmethod
    def migrate(target):
        """Migrate to `target` ([(app, name)], None for the latest) and return the models at that state."""
        executor = MigrationExecutor(connection)
        target = target or executor.loader.graph.leaf_nodes()
        executor.migrate(target)
        executor.loader.build_graph()
        return executor.loader.project_state(target).apps
