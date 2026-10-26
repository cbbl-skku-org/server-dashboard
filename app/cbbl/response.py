"""Minimal HTTP responses for the WSGI app."""
import json

STATUS = {200: "200 OK", 303: "303 See Other", 400: "400 Bad Request", 403: "403 Forbidden", 404: "404 Not Found",
          405: "405 Method Not Allowed", 409: "409 Conflict", 413: "413 Payload Too Large",
          500: "500 Internal Server Error",
          502: "502 Bad Gateway"}

# Our pages load scripts and styles only from the portal itself; inline style attributes carry computed widths.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'self'")


class Response:
    def __init__(self, body="", status=200, ctype="text/html; charset=utf-8", headers=None):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = [("Content-Type", ctype), ("Cache-Control", "no-store"),
                        ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "same-origin")]
        if ctype.startswith("text/html"):
            self.headers.append(("Content-Security-Policy", CSP))
        self.headers += headers or []

    def __call__(self, start_response):
        start_response(STATUS.get(self.status, f"{self.status} Status"),
                       self.headers + [("Content-Length", str(len(self.body)))])
        return [self.body]


def redirect(url):
    return Response("", 303, headers=[("Location", url)])


def json_response(obj, status=200):
    return Response(json.dumps(obj), status, "application/json")
