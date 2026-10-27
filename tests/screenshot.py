"""Screenshots of every portal page with the fake Slurm.

Needs PyQt5 with QtWebEngine (pip install PyQt5 PyQtWebEngine):
  QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software QTWEBENGINE_DISABLE_SANDBOX=1 \
      python -I tests/screenshot.py <output dir> [light]
The portal runs in this process on 127.0.0.1 (a random port) only while the screenshots are taken.
"""
import json
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
from cbbl import slurm, web  # noqa: E402

from PyQt5.QtCore import QCoreApplication, Qt, QTimer, QUrl  # noqa: E402

QCoreApplication.setAttribute(Qt.AA_UseSoftwareOpenGL)  # no GPU on the servers
QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
from PyQt5.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

PAGES = [("overview", "/"), ("my_jobs", "/jobs"), ("new_job", "/new"), ("timeline", "/timeline"),
         ("my_timeline", "/timeline?view=mine"), ("cluster_status", "/cluster"), ("history_and_stats", "/history"),
         ("account", "/account"), ("job_running", "/job/1187"), ("job_finished", "/job/1183"),
         ("job_pending", "/job/1190"), ("job_oom", "/job/1180"),
         ("admin_health", "/admin/health"), ("admin_jobs", "/admin/jobs"), ("admin_job", "/admin/job/1300"),
         ("admin_users", "/admin/users"), ("admin_user", "/admin/user/bob"), ("admin_log", "/admin/log"),
         ("admin_priority", "/admin/priority"), ("admin_job_waiting", "/admin/job/1302"),
         ("reservations", "/reservations")]


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def main():
    out = sys.argv[1]
    theme = sys.argv[2] if len(sys.argv) > 2 else "dark"
    width = int(os.environ.get("SHOT_WIDTH", "1837"))
    os.makedirs(out, exist_ok=True)
    home = fakeslurm.make_home(tempfile.mkdtemp(prefix="cbbl-shot-"))
    fakeslurm.install(home)
    now = fakeslurm.NOW    # a few Priority page changes, for its page and Cluster status
    sample = [{"id": "p3", "kind": "reservation", "users": ["bob", "carol"], "node": "node2", "cores": 12, "level": "",
               "start": now + 86400, "since": now, "until": now + 4 * 86400, "by": "erin",
               "reason": "revision experiments, due Nov 5"},
              {"id": "p4", "kind": "user", "users": ["bob"], "level": "much", "since": now - 3600,
               "until": now + 3 * 86400, "by": "alice", "reason": "thesis deadline on Nov 30"},
              {"id": "p5", "kind": "job", "users": ["carol"], "job": "1302", "level": "front", "since": now - 600,
               "until": now + 7 * 86400, "by": "alice", "reason": "results needed for Friday's meeting"}]
    fakeslurm.PRIORITIES.update(active=sample, ended=[dict(sample[1], id="p1", level="more", ended=now - 86400,
                                                           end_note="time is up: shares back to 1")])
    fakeslurm.REQUESTS[:] = [
        {"id": "r2", "user": "alice", "users": ["bob", "alice"], "node": "node2", "cores": 24, "start": now + 2 * 86400,
         "until": now + 5 * 86400, "reason": "revision experiments, due Nov 30", "at": now - 3600, "state": "pending"},
        {"id": "r1", "user": "alice", "users": ["alice"], "node": "node3", "cores": 16, "start": now - 86400,
         "until": now + 86400, "reason": "benchmark for the paper", "at": now - 2 * 86400, "state": "approved",
         "note": "ok, node3 is quiet this week"}]
    at = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))   # noqa: E731
    fakeslurm.RESERVATIONS.append(f"ReservationName=prio-alice-p2 StartTime={at(now - 86400)} EndTime={at(now + 86400)} "
                                  "Nodes=node3 CoreCnt=16 Flags=MAGNETIC,IGNORE_JOBS Users=alice State=ACTIVE")
    with open(slurm.PRIORITY_FILE, "w") as f:
        json.dump({"updated": now, "active": [{k: v for k, v in b.items() if k != "reason"} for b in sample]}, f)

    def app(environ, start_response):  # serve the app at its real address, the login page at /login
        path = environ.get("PATH_INFO", "")
        if path == "/login":
            with open(os.path.join(HERE, "..", "login", "login.html"), "rb") as f:
                body = f.read()
            start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
            return [body]
        if path.startswith("/pun/sys/cbbl"):
            environ["SCRIPT_NAME"], environ["PATH_INFO"] = "/pun/sys/cbbl", path[len("/pun/sys/cbbl"):] or "/"
        environ["HTTP_ORIGIN"] = "http://" + environ.get("HTTP_HOST", "")
        return web.application(environ, start_response)

    server = make_server("127.0.0.1", 0, app, handler_class=Quiet)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    qt = QApplication(sys.argv[:1])
    view = QWebEngineView()
    view.resize(width, 900)
    queue = [("login", "/login")] + [(name, "/pun/sys/cbbl" + url) for name, url in PAGES]

    def set_theme_then(next_step):
        js = f"try{{localStorage.setItem('cbbl-theme','{theme}')}}catch(e){{}};" \
             f"document.documentElement.setAttribute('data-theme','{theme}');"
        view.page().runJavaScript(js, lambda _: next_step())

    def shoot():
        name = queue[0][0]

        def grab(height):
            h = max(700, min(int(height or 900), 3000))
            view.resize(width, h)
            QTimer.singleShot(700, lambda: (view.grab().save(os.path.join(out, f"{name}-{theme}.png")), step()))
        view.page().runJavaScript("document.documentElement.scrollHeight", grab)

    def loaded(ok):
        set_theme_then(lambda: QTimer.singleShot(1300, shoot))

    def step():
        queue.pop(0)
        if not queue:
            qt.quit()
            return
        view.resize(width, 900)
        view.load(QUrl(f"http://127.0.0.1:{port}{queue[0][1]}"))

    view.loadFinished.connect(loaded)
    view.load(QUrl(f"http://127.0.0.1:{port}{queue[0][1]}"))
    view.show()
    qt.exec_()
    server.shutdown()
    print("screenshots in", out)


if __name__ == "__main__":
    main()
