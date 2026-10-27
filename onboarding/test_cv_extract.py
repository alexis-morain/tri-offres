"""The CV extractor, onboarding/cv_extract.py: its checks in process, then the real child process."""
import ast
import io
import os
import subprocess
import sys
import tempfile
import zipfile

from django.test import SimpleTestCase

from onboarding import cv_extract as x
from onboarding.testing import (CV_LINES, make_docx, make_encrypted_pdf, make_pdf, make_pdf_bomb,
                                patch_central_directory, rezip)

LINUX_ONLY = "RLIMIT_AS is not enforced on macOS: memory and CPU limits are tested in the Linux container"


def refused(test, data, code):
    with test.assertRaises(x.Refused) as caught:
        x.extract(data)
    test.assertEqual(caught.exception.code, code)


class SignatureTests(SimpleTestCase):
    def test_types_by_signature_only(self):
        self.assertEqual(x.detect_type(b"%PDF-1.7\n..."), "pdf")
        self.assertEqual(x.detect_type(b"\xef\xbb\xbfjunk" + b" " * 900 + b"%PDF-1.4"), "pdf")
        self.assertEqual(x.detect_type(b"PK\x03\x04rest"), "docx")
        for head in (b"MZ\x90\x00", b"{\\rtf1", b"\xd0\xcf\x11\xe0", b"", b" " * 1024 + b"%PDF-1.4", b"xPK\x03\x04"):
            with self.subTest(head=head[:8]):
                self.assertIsNone(x.detect_type(head))

    def test_extract_refuses_another_type(self):
        refused(self, b"MZ\x90\x00 an executable named cv.pdf", x.WRONG_TYPE)


class DocxTests(SimpleTestCase):
    def test_paragraphs_and_tables_in_order(self):
        text = x.extract(make_docx(["Médiatrice culturelle"], table=[["2019-2023", "Musée de l’Exemple"]]))
        self.assertIn("Médiatrice culturelle", text)
        self.assertLess(text.index("Médiatrice"), text.index("Musée de l’Exemple"))
        self.assertIn("2019-2023", text)

    def test_text_boxes_are_read_once(self):
        document = make_docx(["Corps"])
        with zipfile.ZipFile(io.BytesIO(document)) as archive:
            xml = archive.read("word/document.xml").decode()
        box = ('<w:p><w:r><mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
               '<mc:Choice Requires="wps"><w:drawing><w:txbxContent><w:p><w:r><w:t>Dans la boîte</w:t></w:r></w:p>'
               '</w:txbxContent></w:drawing></mc:Choice><mc:Fallback><w:pict><w:txbxContent><w:p><w:r>'
               '<w:t>Dans la boîte</w:t></w:r></w:p></w:txbxContent></w:pict></mc:Fallback></mc:AlternateContent>'
               '</w:r></w:p>')
        xml = xml.replace("<w:sectPr", box + "<w:sectPr", 1)
        text = x.extract(rezip(document, drop={"word/document.xml"}, extra=[("word/document.xml", xml)]))
        self.assertEqual(text.count("Dans la boîte"), 1)

    def test_empty_document_is_unreadable(self):
        refused(self, make_docx([]), x.UNREADABLE)

    def test_archive_without_a_word_document_is_another_type(self):
        refused(self, rezip(make_docx(), drop={"word/document.xml"}), x.WRONG_TYPE)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("content.xml", "<office:document/>")  # an ODT, say
        refused(self, archive.getvalue(), x.WRONG_TYPE)

    def test_zip_bomb_by_total_size(self):
        bomb = rezip(make_docx(), stream_entry=("word/media/image1.png", 60))
        self.assertLess(len(bomb), 1024 * 1024)
        refused(self, bomb, x.TOO_BIG)

    def test_zip_bomb_by_ratio(self):
        refused(self, rezip(make_docx(), stream_entry=("word/media/image1.png", 2)), x.TOO_BIG)

    def test_too_many_entries(self):
        extra = [(f"word/media/{n}.txt", b"x") for n in range(x.MAX_ENTRIES)]
        refused(self, rezip(make_docx(), extra=extra), x.TOO_BIG)

    def test_encrypted_entry(self):
        refused(self, patch_central_directory(make_docx(), "word/document.xml", flag_bits=0x1), x.UNREADABLE)

    def test_zero_compressed_size(self):
        refused(self, patch_central_directory(make_docx(), "word/document.xml", compress_size=0), x.TOO_BIG)

    def test_lying_uncompressed_size_is_caught_on_reading(self):
        """The declared size passes the checks; reading stops there and the CRC check fails."""
        document = rezip(make_docx(), drop={"word/document.xml"},
                         extra=[("word/document.xml", zipfile.ZipFile(io.BytesIO(make_docx())).read(
                             "word/document.xml") + b"<!--" + b" " * 2_000_000 + b"-->")])
        refused(self, patch_central_directory(document, "word/document.xml", file_size=4000), x.UNREADABLE)

    def test_broken_archive(self):
        refused(self, b"PK\x03\x04" + b"\0" * 200, x.UNREADABLE)


class PdfTests(SimpleTestCase):
    def test_text_of_every_page(self):
        text = x.extract(make_pdf([CV_LINES, ["Deuxième page : bénévolat au festival"]]))
        self.assertIn("Médiatrice culturelle", text)
        self.assertIn("Deuxième page", text)

    def test_a_real_password_is_protected(self):
        refused(self, make_encrypted_pdf(), x.PROTECTED)

    def test_restrictions_without_a_password_are_read(self):
        self.assertIn("Médiatrice culturelle", x.extract(make_encrypted_pdf(user_password="")))

    def test_under_two_hundred_characters_for_the_whole_pdf_is_scanned(self):
        refused(self, make_pdf([[]]), x.SCANNED)
        refused(self, make_pdf([["Nora Testard"], []]), x.SCANNED)
        # Counted for the whole file, not per page: a short last page is fine.
        self.assertIn("Médiatrice culturelle", x.extract(make_pdf([CV_LINES, [], ["Page 3"]])))

    def test_thirty_pages_at_most(self):
        x.extract(make_pdf([CV_LINES] * x.MAX_PAGES))
        refused(self, make_pdf([CV_LINES] * (x.MAX_PAGES + 1)), x.TOO_BIG)

    def test_garbage_is_unreadable(self):
        with self.assertLogs("pypdf", "WARNING"):  # pypdf says so; in the child this goes to /dev/null
            refused(self, b"%PDF-1.4\n" + bytes(range(256)) * 20, x.UNREADABLE)

    def test_text_is_cut_at_sixty_thousand_characters(self):
        long_lines = ["Compétence " + "x" * 80] * 60
        text = x.extract(make_pdf([long_lines] * 15))
        self.assertEqual(len(text), x.MAX_CHARS)


class InputTests(SimpleTestCase):
    def test_more_than_five_megabytes_is_too_big(self):
        refused(self, b"%PDF-1.4\n" + b" " * x.MAX_INPUT, x.TOO_BIG)


class ImportTests(SimpleTestCase):
    def test_only_the_standard_library_pypdf_and_python_docx(self):
        tree = ast.parse(open(x.__file__, encoding="utf-8").read())
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module.split(".")[0])
        self.assertEqual(modules - set(sys.stdlib_module_names), {"pypdf", "docx"})


def run_child(data, timeout=30):
    """The extractor as the view runs it: isolated mode, absolute path, bytes on stdin."""
    return subprocess.run([sys.executable, "-I", x.SCRIPT], input=data, capture_output=True, env={},
                          timeout=timeout)


class ChildProcessTests(SimpleTestCase):
    """The real child: starts in isolated mode, sets its limits, reads stdin and answers with an exit code."""

    def test_pdf_and_docx(self):
        for data in (make_pdf(), make_docx()):
            with self.subTest(kind=data[:4]):
                completed = run_child(data)
                self.assertEqual(completed.returncode, x.OK)
                self.assertIn("Médiatrice culturelle", completed.stdout.decode("utf-8"))

    def test_exit_codes(self):
        cases = [(make_encrypted_pdf(), x.PROTECTED), (make_pdf([[]]), x.SCANNED), (b"MZ\x90", x.WRONG_TYPE),
                 (rezip(make_docx(), stream_entry=("word/media/a.png", 60)), x.TOO_BIG),
                 (b"PK\x03\x04" + b"\0" * 200, x.UNREADABLE), (b"%PDF-" + b" " * x.MAX_INPUT, x.TOO_BIG)]
        for data, code in cases:
            with self.subTest(code=code):
                completed = run_child(data)
                self.assertEqual(completed.returncode, code)
                self.assertEqual(completed.stdout, b"")

    def test_limits_are_set_in_the_child(self):
        script = ("import importlib.util, resource, sys\n"
                  f"spec = importlib.util.spec_from_file_location('cv_extract', {x.SCRIPT!r})\n"
                  "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n"
                  "module.apply_limits()\n"
                  "print([resource.getrlimit(getattr(resource, n))[1] for n in ('RLIMIT_CPU', 'RLIMIT_CORE',"
                  " 'RLIMIT_FSIZE')])\n"
                  "if sys.platform != 'darwin': print(resource.getrlimit(resource.RLIMIT_AS)[1])\n")
        completed = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, env={}, timeout=30)
        lines = completed.stdout.decode().split()
        self.assertEqual(completed.stdout.decode().splitlines()[0], "[10, 0, 0]")
        if sys.platform != "darwin":
            self.assertEqual(int(lines[-1]), 512 * 1024 * 1024)

    def test_the_child_cannot_write_files(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "written.bin")
            script = ("import importlib.util, os\n"
                      f"spec = importlib.util.spec_from_file_location('cv_extract', {x.SCRIPT!r})\n"
                      "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n"
                      "module.apply_limits()\n"
                      f"descriptor = os.open({target!r}, os.O_WRONLY | os.O_CREAT)\n"
                      "try:\n"
                      "    os.write(descriptor, b'x' * 10); print('written')\n"
                      "except OSError:\n"
                      "    print('refused')\n")
            completed = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, env={},
                                       timeout=30)
            self.assertEqual(completed.stdout.decode().strip(), "refused")
            self.assertEqual(os.path.getsize(target), 0)


class ContainerOnlyTests(SimpleTestCase):
    """Run in the Linux container (step 10, or by hand): macOS does not enforce RLIMIT_AS."""

    def setUp(self):
        if not sys.platform.startswith("linux"):
            self.skipTest(LINUX_ONLY)

    def test_memory_bomb_is_stopped(self):
        completed = run_child(make_pdf_bomb(1024), timeout=60)
        self.assertNotEqual(completed.returncode, x.OK)
        self.assertLess(len(completed.stdout), x.MAX_CHARS * 4 + 1)

    def test_endless_loop_is_stopped_by_the_cpu_limit(self):
        script = ("import importlib.util\n"
                  f"spec = importlib.util.spec_from_file_location('cv_extract', {x.SCRIPT!r})\n"
                  "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n"
                  "module.apply_limits()\n"
                  "while True:\n"
                  "    pass\n")
        completed = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, env={}, timeout=30)
        self.assertLess(completed.returncode, 0)  # killed by SIGXCPU or SIGKILL

    def test_memory_limit_stops_a_large_allocation(self):
        script = ("import importlib.util\n"
                  f"spec = importlib.util.spec_from_file_location('cv_extract', {x.SCRIPT!r})\n"
                  "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n"
                  "module.apply_limits()\n"
                  "try:\n"
                  "    block = bytearray(1024 * 1024 * 1024)\n"
                  "    print('allocated')\n"
                  "except MemoryError:\n"
                  "    print('refused')\n")
        completed = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, env={}, timeout=30)
        self.assertEqual(completed.stdout.decode().strip(), "refused")
