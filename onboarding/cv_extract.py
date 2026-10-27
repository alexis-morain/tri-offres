"""Text of a CV (PDF or DOCX), extracted in a child process: the design plan, sections 3 and 8.2.

Run as `[sys.executable, "-I", <absolute path of this file>]`: the file's bytes on stdin, the text in UTF-8 on stdout,
and an exit code per outcome (OK or one of the refusals below). Before reading anything the child caps its own
address space, CPU time, core dumps and file writes; the parent adds a 15 second timeout. Those limits are the real
defence against a hostile file; the archive and page checks below only refuse the obvious cases early.

Imports the standard library, pypdf and python-docx only (tested). The parent imports this module for its
constants and `detect_type()`: nothing runs on import, and pypdf and python-docx load only in the child.
"""
import io
import os
import resource
import sys
import zipfile

SCRIPT = os.path.abspath(__file__)

SIGNATURE_BYTES = 1024
MAX_INPUT = 5 * 1024 * 1024  # the CV's 5 MB, for the whole site: upload handler, view and child
MAX_CHARS = 60_000
MEMORY_LIMIT = 512 * 1024 * 1024
CPU_SECONDS = 10
MAX_ENTRIES = 500
MAX_UNCOMPRESSED = 50 * 1024 * 1024
MAX_RATIO = 100
MAX_PAGES = 30
MIN_PDF_CHARS = 200  # fewer non-blank characters than this in the whole PDF: a scanned image
DOCX_MAIN_PART = "word/document.xml"

# Exit codes. 1 and 2 are Python's own (uncaught exception, bad usage); a negative code is a signal (CPU limit).
OK = 0
SCANNED = 10
PROTECTED = 11
UNREADABLE = 12
TOO_BIG = 13
WRONG_TYPE = 14
NO_LIMITS = 15

LIMITS = (("RLIMIT_AS", MEMORY_LIMIT), ("RLIMIT_CPU", CPU_SECONDS), ("RLIMIT_CORE", 0), ("RLIMIT_FSIZE", 0))

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# Text boxes come twice: the drawing (mc:Choice) and a VML copy for old readers (mc:Fallback). The copy is skipped.
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"


class Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def detect_type(head):
    """"pdf", "docx" or None, from the first bytes only. Never the extension nor the browser's Content-Type."""
    head = head[:SIGNATURE_BYTES]
    if head.startswith(b"PK\x03\x04"):
        return "docx"
    if b"%PDF-" in head:
        return "pdf"
    return None


def apply_limits():
    """Cap memory, CPU time, core dumps and file size, never above a hard limit already set.
    macOS refuses RLIMIT_AS (it would not enforce it anyway): there only, that one is skipped."""
    for name, value in LIMITS:
        which = getattr(resource, name)
        _, hard = resource.getrlimit(which)
        if hard != resource.RLIM_INFINITY:
            value = min(value, hard)
        try:
            resource.setrlimit(which, (value, value))
        except (ValueError, OSError):
            if name == "RLIMIT_AS" and sys.platform == "darwin":
                continue
            raise


def check_archive(archive):
    """Refuse a DOCX archive before anything is decompressed: entry count, encryption, sizes and ratios as
    declared. A declared size that lies is caught on reading: zipfile stops there and the CRC check fails."""
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise Refused(TOO_BIG)
    total = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise Refused(UNREADABLE)
        if info.file_size and (info.compress_size == 0 or info.file_size > MAX_RATIO * info.compress_size):
            raise Refused(TOO_BIG)
        total += info.file_size
    if total > MAX_UNCOMPRESSED:
        raise Refused(TOO_BIG)
    if DOCX_MAIN_PART not in archive.namelist():
        raise Refused(WRONG_TYPE)


def _docx_body_text(body):
    """Text of the document body in reading order: paragraphs, tables and text boxes, one line per paragraph."""
    parts = []
    size = 0
    stack = [(body, False)]
    while stack and size < MAX_CHARS:
        element, closing = stack.pop()
        if closing:
            parts.append("\n")
            size += 1
            continue
        tag = element.tag
        if not isinstance(tag, str) or tag == _MC_FALLBACK:
            continue  # comments, processing instructions, duplicated text boxes
        if tag == _W + "t":
            text = element.text or ""
            parts.append(text)
            size += len(text)
        elif tag == _W + "tab":
            parts.append("\t")
        elif tag in (_W + "br", _W + "cr"):
            parts.append("\n")
        if tag == _W + "p":
            stack.append((element, True))
        stack.extend((child, False) for child in reversed(element))
    return "".join(parts)


def docx_text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        check_archive(archive)
    import docx  # in the child only

    text = _docx_body_text(docx.Document(io.BytesIO(data)).element.body)
    if not text.strip():
        raise Refused(UNREADABLE)
    return text


def pdf_text(data):
    from pypdf import PdfReader  # in the child only

    reader = PdfReader(io.BytesIO(data))
    # Restrictions without a password (printing, copying) open with an empty one; only a real password refuses.
    if reader.is_encrypted and not reader.decrypt(""):
        raise Refused(PROTECTED)
    count = len(reader.pages)
    if count == 0:
        raise Refused(UNREADABLE)
    if count > MAX_PAGES:
        raise Refused(TOO_BIG)
    parts = []
    size = 0
    for page in reader.pages:
        text = page.extract_text(extraction_mode="plain") or ""
        parts.append(text)
        size += len(text)
        if size >= MAX_CHARS:
            break
    text = "\n".join(parts)
    # Counted for the whole file: a short last page is no sign of a scan.
    if len("".join(text.split())) < MIN_PDF_CHARS:
        raise Refused(SCANNED)
    return text


def extract(data):
    """The text of `data`, at most MAX_CHARS characters. Raises Refused with the exit code otherwise."""
    if len(data) > MAX_INPUT:
        raise Refused(TOO_BIG)
    kind = detect_type(data)
    if kind is None:
        raise Refused(WRONG_TYPE)
    try:
        text = pdf_text(data) if kind == "pdf" else docx_text(data)
    except Refused:
        raise
    except MemoryError:
        raise Refused(TOO_BIG) from None
    except Exception:  # noqa: BLE001 - any parser failure on a hostile or broken file
        raise Refused(UNREADABLE) from None
    return text.replace("\x00", "")[:MAX_CHARS]


def main():
    sys.dont_write_bytecode = True  # with RLIMIT_FSIZE at 0 nothing could be written anyway
    try:
        apply_limits()
    except (ValueError, OSError):
        return NO_LIMITS  # never parse a file without the limits
    data = sys.stdin.buffer.read(MAX_INPUT + 1)
    try:
        text = extract(data)
    except Refused as refusal:
        return refusal.code
    sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()
    return OK


if __name__ == "__main__":
    sys.exit(main())
