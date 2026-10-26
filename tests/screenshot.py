"""Screenshots of every portal page with the fake Slurm.

Needs PyQt5 with QtWebEngine (pip install PyQt5 PyQtWebEngine):
  QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software QTWEBENGINE_DISABLE_SANDBOX=1 \
      python -I tests/screenshot.py <output dir> [light]
The portal runs in this process on 127.0.0.1 (a random port) only while the screenshots are taken.
"""
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

QCoreApplication.setAttribute(Qt.AA_UseSoftwareOpenGL)  # no GPU on the servers
QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
from PyQt5.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

PAGES = [("overview", "/"), ("my_jobs", "/jobs"), ("new_job", "/new"), ("my_timeline", "/timeline"),
         ("cluster_status", "/cluster"), ("test_runs", "/tests"), ("history_and_stats", "/history"),
         ("account", "/account"), ("job_running", "/job/1187"), ("job_finished", "/job/1183"),
         ("job_pending", "/job/1190"), ("job_oom", "/job/1180"),
         ("admin_health", "/admin/health"), ("admin_jobs", "/admin/jobs"), ("admin_job", "/admin/job/1300"), ("admin_nodes", "/admin/nodes"),
         ("admin_users", "/admin/users"), ("admin_user", "/admin/user/bob"), ("admin_log", "/admin/log")]


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
