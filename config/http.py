"""HTTP helpers for the clients that send a credential over urllib (Resend)."""
import urllib.request


class RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would copy the Authorization header to the redirect's host.

    `refusal(code)` builds the exception raised instead; it must not quote the redirect's location.
    """

    def __init__(self, refusal):
        self.refusal = refusal

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise self.refusal(code)
