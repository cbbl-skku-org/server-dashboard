"""Browser check of the New job form (fake Slurm): RAM follows the CPUs and the job name follows the command until
the user types their own value. Run like screenshot.py:
  QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software QTWEBENGINE_DISABLE_SANDBOX=1 \\
      python -I tests/js_form_check.py
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

# (description, JavaScript that changes the form, field to read, expected value)
STEPS = [
    ("starts with the defaults", "", "mem", "4"),
    ("CPUs 8 -> RAM follows", "set('cpus', '8')", "mem", "32"),
    ("RAM typed by the user", "set('mem', '10')", "mem", "10"),
    ("CPUs 2 -> typed RAM kept", "set('cpus', '2')", "mem", "10"),
    ("RAM emptied, user still in it", "focusIt('mem'); set('mem', '')", "mem", ""),
    ("RAM left empty -> default again", "document.activeElement.blur(); set('mem', '')", "mem", "8"),
    ("command -> name follows", "set('command', 'python run_md.py --steps 5')", "name", "run_md"),
    ("name typed by the user", "set('name', 'my-run')", "name", "my-run"),
    ("command changed -> typed name kept", "set('command', 'bash other.sh')", "name", "my-run"),
    ("equivalent command", "", "#cmd-text", "cd ~ && sbatch -J my-run -c 2 --mem=8G -t 1-00:00:00 --wrap='bash other.sh'"),
    ("quick test box -> 10 minutes", "tick('quick', true)", "time", "10m"),
    ("quick test in the command", "", "#cmd-text", "cd ~ && sbatch -p debug -J my-run -c 2 --mem=8G -t 10 --wrap='bash other.sh'"),
    ("quick test box off -> back to 1d", "tick('quick', false)", "time", "1d"),
]
HELPER = """window.set = function (name, value) {
  var el = document.querySelector('[name="' + name + '"]');
  el.value = value; el.dispatchEvent(new Event('input', {bubbles: true}));
};
window.tick = function (name, on) {
  var el = document.querySelector('[name="' + name + '"]');
  el.checked = on; el.dispatchEvent(new Event('change', {bubbles: true}));
};
window.focusIt = function (name) { document.querySelector('[name="' + name + '"]').focus(); };
window.get = function (sel) {
  var el = sel[0] === '#' ? document.querySelector(sel) : document.querySelector('[name="' + sel + '"]');
  return el.value !== undefined ? el.value : el.textContent;
};"""


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
        return web.application(environ, start_response)

    server = make_server("127.0.0.1", 0, app, handler_class=Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    qt = QApplication(sys.argv[:1])
    view = QWebEngineView()
    steps = list(STEPS)
    failures = []

    def run_step():
        if not steps:
            qt.quit()
            return
        desc, js, field, expected = steps.pop(0)
        view.page().runJavaScript(js or "0", lambda _: QTimer.singleShot(2500, lambda: check(desc, field, expected)))

    def check(desc, field, expected):
        def got(value):
            ok = value == expected
            print(f"{'ok  ' if ok else 'FAIL'} {desc:36s} {field} = {json.dumps(value)}" + ("" if ok else f" (expected {expected!r})"))
            if not ok:
                failures.append(desc)
            run_step()
        view.page().runJavaScript(f"get({json.dumps(field)})", got)

    # waits are long because the software renderer is slow; the page itself answers in well under a second
    view.loadFinished.connect(lambda ok: view.page().runJavaScript(HELPER, lambda _: QTimer.singleShot(2500, run_step)))
    view.load(QUrl(f"http://127.0.0.1:{server.server_address[1]}/pun/sys/cbbl/new"))
    QTimer.singleShot(150000, qt.quit)
    qt.exec_()
    server.shutdown()
    sys.exit(1 if failures or steps else 0)


if __name__ == "__main__":
    main()
