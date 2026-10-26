"""Run the portal on your own computer with the fake Slurm, to click through every page (admin pages included).

  python3 tests/dev_server.py                 # http://127.0.0.1:8080/ , signed in as "alice", a cluster admin
  python3 tests/dev_server.py --user-only     # the same without the admin pages
  python3 tests/dev_server.py --host 0.0.0.0 --port 8000

No cluster, Slurm or Open OnDemand is needed (Python 3.9+, standard library only). Nothing real happens: submitted
jobs, cancels and account changes only reach the fake Slurm, which forgets them when the server stops. The sign-in page
is at /login and accepts anything. Code changes need a restart.
"""
import argparse
import atexit
import os
import shutil
import signal
import sys
import tempfile
from wsgiref.simple_server import WSGIRequestHandler, make_server

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)

import fakeslurm  # noqa: E402
from cbbl import web  # noqa: E402

LOGIN = os.path.join(HERE, "..", "login", "login.html")


class Handler(WSGIRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write((fmt % args if args else fmt) + "\n")


def app(environ, start_response):
    """What Apache and Open OnDemand do in production: /login, /signin, /signout and the app at its address."""
    path = environ.get("PATH_INFO", "") or "/"
    if path == "/login":
        with open(LOGIN, "rb") as f:
            body = f.read()
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]
    if path in ("/", "/signin", "/signout"):
        target = "/login?signedout" if path == "/signout" else web.APP_URI + "/"
        start_response("303 See Other", [("Location", target), ("Content-Length", "0")])
        return [b""]
    if path == web.APP_URI or path.startswith(web.APP_URI + "/"):
        environ["SCRIPT_NAME"], environ["PATH_INFO"] = web.APP_URI, path[len(web.APP_URI):] or "/"
        return web.application(environ, start_response)
    start_response("404 Not Found", [("Content-Type", "text/plain")])
    return [b"not found"]


def main():
    parser = argparse.ArgumentParser(description="The portal with a fake Slurm, for development.")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="port (default 8080)")
    parser.add_argument("--user-only", action="store_true", help="sign in as a user who is not a cluster admin")
    args = parser.parse_args()

    base = tempfile.mkdtemp(prefix="portal-dev-")
    atexit.register(shutil.rmtree, base, ignore_errors=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))   # so the fake home is removed on kill too
    fakeslurm.install(fakeslurm.make_home(base))
    if args.user_only:
        fakeslurm.ADMINS.clear()

    server = make_server(args.host, args.port, app, handler_class=Handler)
    print(f"Portal with the fake Slurm: http://{args.host}:{args.port}/  (signed in as {fakeslurm.USER}"
          f"{'' if args.user_only else ', a cluster admin'}; Ctrl-C stops it)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
