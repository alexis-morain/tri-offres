from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

from jobs.actor_sources import ACTOR_SOURCES

SHEETS = Path(settings.BASE_DIR) / "docs" / "sources"


# .dockerignore leaves docs/ out of the image: the sheets are checked in the repository only.
@skipUnless(SHEETS.is_dir(), "docs/sources is not in this copy (Docker image)")
class SourceSheetTests(SimpleTestCase):
    """Every source of the actor has its public sheet (step 11), named after its key and listed in the index."""

    def test_every_source_has_a_sheet_with_its_key(self):
        for source in ACTOR_SOURCES:
            with self.subTest(source=source.key):
                sheet = SHEETS / f"{source.key}.md"
                self.assertTrue(sheet.is_file(), f"docs/sources/{source.key}.md is missing")
                self.assertIn(f"`{source.key}`", sheet.read_text(encoding="utf-8"))

    def test_the_index_lists_every_source(self):
        index = (SHEETS / "README.md").read_text(encoding="utf-8")
        for source in ACTOR_SOURCES:
            with self.subTest(source=source.key):
                self.assertIn(f"]({source.key}.md)", index)

    def test_no_sheet_without_a_source(self):
        keys = {source.key for source in ACTOR_SOURCES}
        sheets = {path.stem for path in SHEETS.glob("*.md")} - {"README"}
        self.assertEqual(sheets - keys, set())
