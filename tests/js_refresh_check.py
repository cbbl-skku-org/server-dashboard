"""Browser check of the live refresh on Health (fake Slurm): a drain reason being typed is not wiped by the refresh,
and the refresh goes on once the field is left empty. Run like screenshot.py:
  QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software QTWEBENGINE_DISABLE_SANDBOX=1 \\
      /apps/application/anaconda3/bin/python -I tests/js_refresh_check.py
The test server shortens Health's refresh from 60 s to 1 s.
"""
import json
import os
import sys
import tempfile
import threading
from wsgiref.simple_server import WSGIRequestHandler, make_server

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)

import fakeslurm  # noqa: E402
from cbbl import web  # noqa: E402

from PyQt5.QtCore import QCoreApplication, Qt, QTimer, QUrl  # noqa: E402

QCoreApplication.setAttribute(Qt.AA_UseSoftwareOpenGL)
QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
from PyQt5.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

FIELD = "document.querySelector('[action$=\"/admin/node/node1/drain\"] input[name=reason]')"
MARK = "document.querySelector('[data-refresh] .grid.nodes')"
# (description, JavaScript run first, JavaScript read after the wait, expected value)
STEPS = [
    ("drain reason being typed", f"{FIELD}.focus(); {FIELD}.value = 'disk check'; {MARK}.setAttribute('data-mark', '1')",
     f"{FIELD}.value + ' ' + ({MARK}.getAttribute('data-mark') || 'replaced')", "disk check 1"),
    ("typed, then focus elsewhere", "document.activeElement.blur()",
     f"{FIELD}.value + ' ' + ({MARK}.getAttribute('data-mark') || 'replaced')", "disk check 1"),
    ("field emptied: refresh goes on", f"{FIELD}.value = ''",
     f"{FIELD}.value + ' ' + ({MARK}.getAttribute('data-mark') || 'replaced')", " replaced"),
]


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def main():
    home = fakeslurm.make_home(tempfile.mkdtemp(prefix="cbbl-js-"))
    fakeslurm.install(home)

    def app(environ, start_response):
        path = environ.get("PATH_INFO", "")
        environ["SCRIPT_NAME"], environ["PATH_INFO"] = "/pun/sys/cbbl", path[len("/pun/sys/cbbl"):] or "/"
        environ["HTTP_ORIGIN"] = "http://" + environ.get("HTTP_HOST", "")
        body = b"".join(web.application(environ, start_response))
        return [body.replace(b'data-interval="60000"', b'data-interval="01000"')]   # same length (Content-Length)

    server = make_server("127.0.0.1", 0, app, handler_class=Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    qt = QApplication(sys.argv[:1])
    view = QWebEngineView()
    view.resize(1280, 900)
    view.show()                      # a hidden page (document.hidden) does not refresh
    steps = list(STEPS)
    failures = []

    def run_step():
        if not steps:
            qt.quit()
            return
        desc, js, read, expected = steps.pop(0)
        view.page().runJavaScript(js, lambda _: QTimer.singleShot(4000, lambda: check(desc, read, expected)))

    def check(desc, read, expected):
        def got(value):
            ok = value == expected
            print(f"{'ok  ' if ok else 'FAIL'} {desc:34s} {json.dumps(value)}" + ("" if ok else f" (expected {expected!r})"))
            if not ok:
                failures.append(desc)
            run_step()
        view.page().runJavaScript(read, got)

    view.loadFinished.connect(lambda ok: QTimer.singleShot(2500, run_step))
    view.load(QUrl(f"http://127.0.0.1:{server.server_address[1]}/pun/sys/cbbl/admin/health"))
    QTimer.singleShot(90000, qt.quit)
    qt.exec_()
    server.shutdown()
    sys.exit(1 if failures or steps else 0)


if __name__ == "__main__":
    main()
