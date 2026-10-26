"""Browser check of the job page (fake Slurm): a waiting job's page switches to "Running" by itself when the job
starts, as after Submit. Run like screenshot.py:
  QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software QTWEBENGINE_DISABLE_SANDBOX=1 \\
      python -I tests/js_job_live_check.py
"""
import os
import sys
import tempfile
import threading
import time
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


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def main():
    home = fakeslurm.make_home(tempfile.mkdtemp(prefix="cbbl-js-"))
    fakeslurm.install(home)
    job = next(j for j in fakeslurm.ACTIVE if j["job_id"] == 1190)  # waiting ("Resources")

    def app(environ, start_response):
        path = environ.get("PATH_INFO", "")
        environ["SCRIPT_NAME"], environ["PATH_INFO"] = "/pun/sys/cbbl", path[len("/pun/sys/cbbl"):] or "/"
        return web.application(environ, start_response)

    server = make_server("127.0.0.1", 0, app, handler_class=Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    qt = QApplication(sys.argv[:1])
    view = QWebEngineView()
    results = []
    probe = ("[document.querySelector('.page-head .badge').textContent, location.search, "
             "!!document.querySelector('.n-ok'), !!document.querySelector('[data-usage]')].join('|')")

    def record(label, expected, then):
        def got(value):
            ok = value == expected
            print(f"{'ok  ' if ok else 'FAIL'} {label:40s} {value!r}" + ("" if ok else f" (expected {expected!r})"))
            results.append(ok)
            then()
        view.page().runJavaScript(probe, got)

    def start_job():  # the scheduler starts the job
        now = time.time()
        job.update(job_state=["RUNNING"], start_time=fakeslurm.num(int(now)), nodes="node2",
                   tres_alloc_str=job["tres_req_str"])
        QTimer.singleShot(12000, lambda: record("after the job started (no click)", "Running||false|true", qt.quit))

    loaded = {"n": 0}

    def on_load(ok):
        loaded["n"] += 1
        view.page().runJavaScript("document.visibilityState", lambda v: print(f"     (page visibility: {v})"))
        if loaded["n"] == 1:
            QTimer.singleShot(2500, lambda: record("waiting job, just submitted", "Waiting|?submitted=1|true|false",
                                                   start_job))

    view.resize(1400, 900)
    view.show()  # a page in a window that is never shown counts as hidden, and the portal pauses polling then
    view.loadFinished.connect(on_load)
    view.load(QUrl(f"http://127.0.0.1:{server.server_address[1]}/pun/sys/cbbl/job/1190?submitted=1"))
    QTimer.singleShot(60000, qt.quit)
    qt.exec_()
    server.shutdown()
    print(f"page loads: {loaded['n']} (expected 2: the first one and one reload)")
    sys.exit(0 if results == [True, True] and loaded["n"] == 2 else 1)


if __name__ == "__main__":
    main()
