"""The site's only upload handler: files stay in memory, 5 MB at most (the design plan, 8.1.1).

Set for the whole site in settings.FILE_UPLOAD_HANDLERS, so no upload ever goes to a temporary file on disk. It runs
when anything first reads request.POST, which the CSRF middleware does before any view:

- a body over 10 MB (Content-Length) is not read at all: the request gets empty data, so the CSRF check fails (403)
  unless the token came as a header, and the view says the file is too heavy;
- files over 5 MB in total are dropped (SkipFile) and the request is flagged: the view says the file is too heavy.
"""
from io import BytesIO

from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.uploadhandler import FileUploadHandler, SkipFile
from django.http import QueryDict
from django.utils.datastructures import MultiValueDict

from onboarding import cv_extract

MAX_REQUEST_BYTES = 10 * 1024 * 1024
_FLAG = "upload_too_large"


def too_large(request):
    """Whether this request carried a file, or a body, over the limits."""
    return getattr(request, _FLAG, False)


class CappedMemoryUploadHandler(FileUploadHandler):
    def __init__(self, request=None):
        super().__init__(request)
        self.received = 0  # bytes of every file of the request so far
        self.buffer = None

    def _flag(self):
        if self.request is not None:
            setattr(self.request, _FLAG, True)

    def handle_raw_input(self, input_data, META, content_length, boundary, encoding=None):
        if content_length > MAX_REQUEST_BYTES:
            self._flag()
            return QueryDict(encoding=encoding), MultiValueDict()
        return None  # parse as usual

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.buffer = BytesIO()

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > cv_extract.MAX_INPUT:
            self.buffer = None
            self._flag()
            raise SkipFile
        self.buffer.write(raw_data)
        return None  # kept here: no later handler gets the chunk

    def file_complete(self, file_size):
        if self.buffer is None:
            return None
        self.buffer.seek(0)
        uploaded = InMemoryUploadedFile(file=self.buffer, field_name=self.field_name, name=self.file_name,
                                        content_type=self.content_type, size=file_size, charset=self.charset,
                                        content_type_extra=self.content_type_extra)
        self.buffer = None
        return uploaded
