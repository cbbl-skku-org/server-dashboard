"""WSGI entry point: request parsing, routing, safety checks."""
import mimetypes
import os
import re
import sys
import time
import traceback
import urllib.parse

from . import account, admin, adminhealth, adminviews, config, slurm, views
from .layout import head, page
from .response import Response, json_response
from .util import h

APP_URI = config.SETTINGS["app_uri"]
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public", "static")
MAX_BODY = 1024 * 1024

ROUTES = [
    ("GET", r"/", views.overview),
    ("GET", r"/jobs", views.my_jobs),
    ("GET", r"/job/(?P<job_id>\d+(?:_\d+)?)", views.job_page),
    ("GET", r"/job/(?P<job_id>\d+(?:_\d+)?)/log", views.job_log),
    ("GET", r"/job/(?P<job_id>\d+(?:_\d+)?)/usage\.json", views.job_usage),
    ("GET", r"/job/(?P<job_id>\d+(?:_\d+)?)/state\.json", views.job_state),
    ("POST", r"/job/(?P<job_id>\d+(?:_\d+)?)/cancel", views.job_cancel),
    ("GET", r"/new", views.new_job),
    ("POST", r"/new", views.new_job_post),
    ("POST", r"/new/preview", views.new_job_preview),
    ("GET", r"/timeline", views.timeline),
    ("GET", r"/cluster", views.cluster),
    ("GET", r"/history", views.history),
    ("GET", r"/history\.csv", views.history_csv),
    ("GET", r"/tests", views.tests),
    ("POST", r"/tests", views.tests_post),
    ("GET", r"/account", views.account),
    ("POST", r"/account/password", views.account_password),
    ("POST", r"/account/keys", views.account_keys),
    ("POST", r"/account/telegram", views.account_telegram),
    # cluster admins only (the others get "not found")
    ("GET", r"/admin", adminviews.home),
    ("GET", r"/admin/health", adminhealth.health),
    ("GET", r"/admin/jobs", adminviews.jobs),
    ("GET", r"/admin/job/(?P<job_id>\d+(?:_\d+)?)", adminviews.job),
    ("POST", r"/admin/job/(?P<job_id>\d+(?:_\d+)?)/action", adminviews.job_action),
    ("GET", r"/admin/nodes", adminviews.nodes),
    ("POST", r"/admin/node/(?P<name>[a-z][a-z0-9-]{0,31})/(?P<state>drain|resume)", adminviews.node_state),
    ("GET", r"/admin/users", adminviews.users),
    ("POST", r"/admin/users/add", adminviews.users_add),
    ("GET", r"/admin/user/(?P<name>[a-z][a-z0-9_-]{0,31})", adminviews.user),
    ("POST", r"/admin/user/(?P<name>[a-z][a-z0-9_-]{0,31})/(?P<change>password|lock|unlock)",
     adminviews.user_change),
    ("GET", r"/admin/log", adminviews.log),
]
ROUTES = [(m, re.compile(p + r"/?\Z"), fn) for m, p, fn in ROUTES]


class BadRequest(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status = status


class Request:
    def __init__(self, environ):
        self.environ = environ
        self.method = environ.get("REQUEST_METHOD", "GET").upper()
        base = environ.get("SCRIPT_NAME", "").rstrip("/")
        path = environ.get("PATH_INFO", "") or "/"
        if not base and path.startswith(APP_URI):  # in case the server passes the full path
            base, path = APP_URI, path[len(APP_URI):] or "/"
        self.base, self.path = base, path
        self.query = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        pw = slurm.me()
        self.user, self.home, self.uid = pw.pw_name, pw.pw_dir, pw.pw_uid
        self.is_admin = admin.is_admin(self.user)
        self.partial = environ.get("HTTP_X_PARTIAL") == "1" or self.arg("partial") == "1"
        self._form = None

    def arg(self, name, default=""):
        values = self.query.get(name)
        return values[0] if values else default

    @property
    def form(self):
        if self._form is None:
            self._form = {}
            if self.method == "POST":
                try:
                    length = int(self.environ.get("CONTENT_LENGTH") or 0)
                except ValueError:
                    length = 0
                if length > MAX_BODY:
                    raise BadRequest(413, "Request too large.")
                body = self.environ["wsgi.input"].read(length) if length else b""
                if "application/x-www-form-urlencoded" in self.environ.get("CONTENT_TYPE", ""):
                    parsed = urllib.parse.parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
                    self._form = {k: v[-1] for k, v in parsed.items()}
        return self._form

    def same_origin(self):
        """POSTs must come from the portal's own pages (the session cookie is also SameSite=Lax)."""
        source = self.environ.get("HTTP_ORIGIN") or self.environ.get("HTTP_REFERER") or ""
        host = (self.environ.get("HTTP_X_FORWARDED_HOST") or self.environ.get("HTTP_HOST") or "").split(",")[0]
        return bool(source) and urllib.parse.urlsplit(source).netloc == host.strip()


def static(path):
    """Fallback for running without nginx (tests); the per-user nginx serves public/ itself."""
    name = os.path.normpath(path[len("/static/"):])
    full = os.path.join(STATIC_DIR, name)
    if name.startswith(("..", "/")) or not os.path.isfile(full):
        return Response("not found", 404, "text/plain")
    ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
    with open(full, "rb") as f:
        return Response(f.read(), 200, ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))


def dispatch(req):
    if req.path.startswith("/static/") and req.method in ("GET", "HEAD"):
        return static(req.path)
    method = "GET" if req.method == "HEAD" else req.method
    # a temporary password (set at the portal's launch) must be replaced before anything else
    if account.must_change(req.user) and req.path.rstrip("/") not in ("/account", "/account/password"):
        if req.partial or req.path.endswith(".json"):
            return Response("", 409, "text/plain")
        return Response("", 303, headers=[("Location", req.base + "/account")])
    allowed = False
    for m, pattern, fn in ROUTES:
        match = pattern.match(req.path)
        if not match:
            continue
        allowed = True
        if m != method:
            continue
        if method == "POST" and not req.same_origin():
            raise BadRequest(403, "This form must be sent from the portal's own page.")
        result = fn(req, **match.groupdict())
        return result if isinstance(result, Response) else Response(result)
    if allowed:
        return Response("Method not allowed", 405, "text/plain")
    return views.not_found(req)


def error_page(req, status, text):
    if req is None or req.partial or req.path.endswith(".json"):
        return json_response({"error": text}, status) if req and req.path.endswith(".json") else \
            Response(f'<div class="notice n-error">{h(text)}</div>', status)
    return Response(page(req, "", "Error", head("Something went wrong", h(text))), status)


def application(environ, start_response):
    req = None
    try:
        req = Request(environ)
        resp = dispatch(req)
    except BadRequest as e:
        resp = error_page(req, e.status, str(e))
    except Exception:  # keep the details in the per-user server's log, not in the page
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[cbbl portal {stamp}] {environ.get('REQUEST_METHOD')} {environ.get('PATH_INFO')}\n"
              + traceback.format_exc(), file=sys.stderr, flush=True)
        resp = error_page(req, 500, f"The portal hit an error ({stamp}). Please tell the admin.")
    if environ.get("REQUEST_METHOD", "GET").upper() == "HEAD":
        resp.body = b""
    return resp(start_response)
