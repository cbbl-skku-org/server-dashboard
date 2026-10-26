"""Tests for the portal with a fake Slurm. Run: python3 -m unittest discover -s tests (from the repository root)."""
import atexit
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)

import fakeslurm  # noqa: E402
from cbbl import jobform, slurm, util, web  # noqa: E402

TMP = tempfile.mkdtemp(prefix="cbbl-portal-test-")
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
HOME = fakeslurm.make_home(TMP)
fakeslurm.install(HOME)
HOST = "cluster.example.org"


def call(url, method="GET", form=None, headers=None):
    path, _, query = url.partition("?")
    body = urllib.parse.urlencode(form or {}).encode()
    environ = {"REQUEST_METHOD": method, "SCRIPT_NAME": "/pun/sys/cbbl", "PATH_INFO": path, "QUERY_STRING": query,
               "wsgi.input": io.BytesIO(body), "CONTENT_LENGTH": str(len(body)),
               "CONTENT_TYPE": "application/x-www-form-urlencoded", "HTTP_HOST": HOST}
    environ.update(headers or {})
    out = {}

    def start_response(status, hdrs):
        out["status"], out["headers"] = status, dict(hdrs)
    data = b"".join(web.application(environ, start_response))
    return int(out["status"].split()[0]), out["headers"], data.decode("utf-8", "replace")


ORIGIN = {"HTTP_ORIGIN": f"https://{HOST}"}


class Pages(unittest.TestCase):
    def test_every_page_renders(self):
        for url, text in [("/", "Hello, alice"), ("/jobs", "train-esm2"), ("/new", "Equivalent command"),
                          ("/timeline", "tl-bar"), ("/cluster", "node2"), ("/history", "CPU-hours per week"),
                          ("/tests", "Test runs"), ("/account", "SSH public keys"), ("/job/1187", "Cancel job"),
                          ("/job/1186", "Usage and efficiency"), ("/job/1190", "Not started yet"),
                          ("/job/1191_3", "md-sweep"), ("/history?days=7", "7 days")]:
            with self.subTest(url=url):
                status, headers, body = call(url)
                self.assertEqual(status, 200, body[-2000:])
                self.assertIn(text, body)
                self.assertIn("Content-Security-Policy", headers)

    def test_new_job_starts_with_defaults(self):
        _, _, body = call("/new")
        for field, value in (("cpus", "1"), ("mem", "4"), ("time", "1d"), ("name", "train"), ("workdir", HOME)):
            self.assertIn(f'name="{field}" type="text" value="{value}"', body)
        self.assertIn('<option value="">none</option>', body)
        self.assertIn('name="mem_default" value="4"', body)
        self.assertIn("-J train -c 1 --mem=4G -t 1-00:00:00 --wrap=", body)

    def test_job_page_watches_its_job(self):
        _, _, body = call("/job/1190")
        self.assertIn('data-job-watch="/pun/sys/cbbl/job/1190/state.json" data-state="PENDING"', body)
        self.assertIn('data-refresh="/pun/sys/cbbl/job/1190?part=details"', body)
        _, _, body = call("/job/1186")                     # finished: nothing to watch
        self.assertNotIn("data-job-watch", body)
        status, headers, body = call("/job/1187/state.json")
        self.assertEqual(status, 200)
        self.assertIn('"state": "RUNNING", "active": true', body)
        self.assertIn('"active": false', call("/job/1186/state.json")[2])
        self.assertEqual(call("/job/99999/state.json")[0], 404)
        _, _, body = call("/job/1187?part=details", headers={"HTTP_X_PARTIAL": "1"})
        self.assertTrue(body.startswith('<dl class="kv">'))
        self.assertIn("Run time", body)

    def test_overview_numbers(self):
        _, _, body = call("/")
        self.assertIn("2 waiting", body)          # two pending jobs
        self.assertIn(">48<", body)               # 32 + 16 CPUs in use
        self.assertIn("96 CPUs", body)            # biggest node
        self.assertIn("14 days", body)
        self.assertIn("0.73", body)               # fair share

    def test_names_are_escaped(self):
        _, _, body = call("/history")
        self.assertNotIn("<script>alert(1)", body)
        self.assertIn("&lt;script&gt;alert(1)", body)

    def test_partials(self):
        status, _, body = call("/cluster?partial=1")
        self.assertEqual(status, 200)
        self.assertNotIn("<html", body)
        self.assertIn("node3", body)
        status, _, body = call("/jobs", headers={"HTTP_X_PARTIAL": "1"})
        self.assertNotIn("<html", body)
        self.assertIn("blast-uniref50", body)

    def test_log_shows_last_progress_state(self):
        _, _, body = call("/job/1187/log")
        self.assertIn("epoch  14/40", body)
        self.assertIn("15/40", body)
        self.assertNotIn("14/40 [3:38:02", body)

    def test_usage_json(self):
        status, headers, body = call("/job/1187/usage.json")
        self.assertEqual(status, 200)
        self.assertIn('"running": true', body)
        self.assertIn('"cpu_seconds": 303000.0', body)

    def test_not_found(self):
        self.assertEqual(call("/job/99999")[0], 404)
        self.assertEqual(call("/nope")[0], 404)
        self.assertEqual(call("/job/1187/cancel")[0], 405)

    def test_csv(self):
        status, headers, body = call("/history.csv?days=30")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertTrue(body.startswith("job_id,name,state"))
        self.assertIn("1183,train-esm2-lr1e-4,COMPLETED", body)


class Idle(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(TMP, "idle-status.json")
        slurm.IDLE_STATUS = self.path
        self.write(time.time())

    def write(self, updated):
        now = time.time()
        with open(self.path, "w") as f:
            json.dump({"updated": updated, "mode": "enforce",
                       "idle": {"1187": {"since": now - 50 * 60, "cancel_at": now + 10 * 60, "warning": True}},
                       "cancelled": {"1170": {"at": now - 3600, "since": now - 7200}},
                       "rule": {"cpu_threshold": 0.05, "idle_minutes": 60, "warn_minutes": 45, "partitions": ["cpu"]}}, f)

    def tearDown(self):
        os.remove(self.path)

    def test_warning_badges_and_reasons(self):
        _, _, body = call("/jobs")
        self.assertIn('>Idle</span>', body)
        self.assertIn("Idle: 1187.", body)
        _, _, body = call("/job/1187")
        self.assertIn("It will be cancelled at <b>", body)
        _, _, body = call("/job/1170")
        self.assertIn("Cancelled by the idle check at", body)
        _, _, body = call("/history")
        self.assertIn("Cancelled (idle)", body)
        _, _, body = call("/")
        self.assertIn("cancelled after 60 min under 5% CPU</dd>", body)
        _, _, body = call("/new")
        self.assertIn("A job that uses less than 5% of one CPU for 60 minutes is cancelled", body)
        self.assertIn("warning 15 minutes before. Run a pipeline's main process (Nextflow, Snakemake)", body)

    def test_note_from_accounting(self):
        os.remove(self.path)            # even without the status file
        _, _, body = call("/job/1169")
        self.assertIn("Note from the cluster: Idle check: cancelled", body)
        _, _, body = call("/history")
        self.assertEqual(body.count("Cancelled (idle)"), 1)
        open(self.path, "w").close()

    def test_stale_status_gives_no_warning(self):
        self.write(1)  # the check stopped long ago
        _, _, body = call("/job/1187")
        self.assertNotIn("will be cancelled", body)
        _, _, body = call("/job/1170")
        self.assertIn("Cancelled by the idle check", body)

    def test_missing_status_file(self):
        os.remove(self.path)
        _, _, body = call("/job/1187")
        self.assertNotIn("will be cancelled", body)
        open(self.path, "w").close()


def make_key(comment):
    import subprocess
    path = os.path.join(TMP, comment)
    subprocess.run(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", path], check=True)
    with open(path + ".pub") as f:
        return f.read().strip()


class AccountAndTests(unittest.TestCase):
    def setUp(self):
        fakeslurm.SUBMITTED.clear()
        fakeslurm.HELPER_CALLS.clear()

    def test_quick_test_job(self):
        _, _, body = call("/tests")
        self.assertIn("up to 10 minutes, 4 CPUs and 16 GB", body)
        self.assertIn("srun -p debug --pty bash", body)
        status, headers, body = call("/tests", "POST", {"cpus": "2", "workdir": HOME, "command": "python t.py"}, ORIGIN)
        self.assertEqual(status, 303, body[-2000:])
        script = fakeslurm.SUBMITTED[0]
        self.assertIn("#SBATCH --partition=debug\n", script)
        self.assertIn("#SBATCH --cpus-per-task=2\n", script)
        self.assertIn("#SBATCH --mem=8G\n", script)
        self.assertNotIn("--time", script)                           # the partition's 10 minutes
        status, _, body = call("/tests", "POST", {"cpus": "5", "workdir": HOME, "command": "x"}, ORIGIN)
        self.assertIn("At most 4 CPUs for a quick test.", body)

    def test_quick_box_on_new_job(self):
        _, _, body = call("/new")
        self.assertIn('name="quick" value="1" data-quick-time="10m"', body)
        form = {"cpus": "1", "time": "1d", "quick": "1", "workdir": HOME, "command": "python t.py", "node": "any"}
        _, _, body = call("/new", "POST", form, ORIGIN)
        self.assertIn("At most 10m for a quick test.", body)
        form["time"] = "10m"
        status, _, _ = call("/new", "POST", form, ORIGIN)
        self.assertEqual(status, 303)
        self.assertIn("#SBATCH --partition=debug\n#SBATCH --job-name=t\n", fakeslurm.SUBMITTED[0])
        _, _, body = call("/new/preview", "POST", form, ORIGIN)
        self.assertIn("sbatch -p debug -J t -c 1 --mem=4G -t 10", body)

    def test_password_change(self):
        status, _, body = call("/account/password", "POST", {"current": "x", "new": "Abcdefgh-12", "repeat": "Abcdefgh-13"}, ORIGIN)
        self.assertIn("The two new passwords differ.", body)
        self.assertEqual(fakeslurm.HELPER_CALLS, [("telegram-get", None)])   # nothing sent to the helper
        _, _, body = call("/account/password", "POST", {"current": "wrong", "new": "Abcdefgh-12", "repeat": "Abcdefgh-12"}, ORIGIN)
        self.assertIn("The current password is wrong.", body)
        status, headers, _ = call("/account/password", "POST",
                                  {"current": "Old-password-1", "new": "Abcdefgh-12", "repeat": "Abcdefgh-12"}, ORIGIN)
        self.assertEqual((status, headers["Location"]), (303, "/login?changed=1"))
        self.assertEqual(call("/account/password", "POST", {"current": "a", "new": "b", "repeat": "b"})[0], 403)

    def test_ssh_keys(self):
        key1, key2 = make_key("alice@laptop"), make_key("alice@desk")
        _, _, body = call("/account/keys", "POST", {"keys": key1 + "\nssh-dss AAAAB3NzaC1kc3M bad\nnot a key"}, ORIGIN)
        self.assertIn("Nothing was saved", body)
        self.assertIn("line 2: ssh-dss keys are not accepted", body)
        self.assertIn("line 3: not a public key", body)
        _, _, body = call("/account/keys", "POST", {"keys": key1 + "\r\n\r\n" + key2 + "\r\n"}, ORIGIN)
        self.assertIn("Saved 2 keys.", body)
        self.assertIn("The previous file is kept as ~/.ssh/authorized_keys.bak-", body)
        with open(os.path.join(HOME, ".ssh", "authorized_keys")) as f:
            self.assertEqual(f.read(), key1 + "\n\n" + key2 + "\n")
        self.assertEqual(oct(os.stat(os.path.join(HOME, ".ssh", "authorized_keys")).st_mode & 0o777), "0o600")
        _, _, body = call("/account/keys", "POST", {"keys": key1 + "\n\n" + key2}, ORIGIN)
        self.assertIn("No changes.", body)
        self.assertIn("alice@desk", body)
        self.assertIn("SHA256:", body)

    def test_telegram(self):
        _, _, body = call("/account")
        self.assertIn("https://t.me/example_cluster_bot", body)
        _, _, body = call("/account/telegram", "POST", {"chat_id": "123456789", "ev_done": "1", "action": "test"}, ORIGIN)
        self.assertIn("A test message is on its way", body)
        self.assertEqual(fakeslurm.TELEGRAM["events"], {"done": True, "failed": False, "idle": False})

    def test_temporary_password_first(self):
        import cbbl.account as account
        flag = os.path.join(account.MUST_CHANGE_DIR, "alice")
        open(flag, "w").close()
        try:
            status, headers, _ = call("/jobs")
            self.assertEqual((status, headers["Location"]), (303, "/pun/sys/cbbl/account"))
            self.assertEqual(call("/cluster?partial=1")[0], 409)
            _, _, body = call("/account")
            self.assertIn("You signed in with a temporary password", body)
            self.assertNotIn("SSH public keys", body)
            status, _, _ = call("/account/password", "POST",
                                {"current": "Old-password-1", "new": "Abcdefgh-12", "repeat": "Abcdefgh-12"}, ORIGIN)
            self.assertEqual(status, 303)
        finally:
            os.remove(flag)


class Posts(unittest.TestCase):
    def setUp(self):
        fakeslurm.SUBMITTED.clear()
        fakeslurm.CANCELLED.clear()

    def test_post_needs_same_origin(self):
        status, _, _ = call("/job/1187/cancel", "POST")
        self.assertEqual(status, 403)
        status, _, _ = call("/job/1187/cancel", "POST", headers={"HTTP_ORIGIN": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(fakeslurm.CANCELLED, [])

    def test_cancel(self):
        status, headers, _ = call("/job/1187/cancel", "POST", headers=ORIGIN)
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/pun/sys/cbbl/job/1187?cancelled=1")
        self.assertEqual(fakeslurm.CANCELLED, ["1187"])

    def test_submit(self):
        form = {"cpus": "8", "mem": "32", "time": "12h", "node": "internet", "name": "", "conda": "esm",
                "workdir": "~/esm", "command": "python train.py --epochs 40", "action": "submit"}
        status, headers, body = call("/new", "POST", form, ORIGIN)
        self.assertEqual(status, 303, body[-3000:])
        self.assertEqual(headers["Location"], "/pun/sys/cbbl/job/1200?submitted=1")
        script = fakeslurm.SUBMITTED[0]
        self.assertTrue(script.startswith("#!/bin/bash -l\n#SBATCH --job-name=train\n"))
        for line in ("#SBATCH --cpus-per-task=8", "#SBATCH --mem=32G", "#SBATCH --time=12:00:00",
                     "#SBATCH --constraint=internet", f"#SBATCH --chdir={HOME}/esm",
                     "source ~/miniconda3/etc/profile.d/conda.sh", "conda activate esm", "python train.py --epochs 40"):
            self.assertIn(line + "\n", script)

    def test_submit_errors_rerender(self):
        form = {"cpus": "500", "mem": "abc", "time": "soon", "workdir": "/no/such/dir", "command": "", "node": "node1"}
        status, _, body = call("/new", "POST", form, ORIGIN)
        self.assertEqual(status, 200)
        self.assertEqual(fakeslurm.SUBMITTED, [])
        for text in ("At most 48 CPUs on node1.", "A number of GB", "Use e.g. 30m", "does not exist",
                     "What should the job run?"):
            self.assertIn(text, body)

    def test_preview_json(self):
        form = {"cpus": "4", "command": "python a.py", "workdir": HOME}
        status, headers, body = call("/new/preview", "POST", form, ORIGIN)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertIn("cd ~ && sbatch -J a -c 4 --mem=16G --wrap='python a.py'", body)  # RAM follows the CPUs
        self.assertIn('"mem_default": "16"', body)
        self.assertIn('"name_default": "a"', body)


ADMIN_ORIGIN = dict(ORIGIN)


class AdminPages(unittest.TestCase):
    def setUp(self):
        for calls in (fakeslurm.ADMIN_CALLS, fakeslurm.CANCELLED, fakeslurm.ADMIN_HELPER_CALLS):
            calls.clear()
        fakeslurm.ADMINS.clear()
        fakeslurm.ADMINS.add("alice")
        fakeslurm.LEVEL[0] = "Administrator"
        from cbbl import admin
        admin._levels.clear()
        self.accounts = [dict(a) for a in fakeslurm.ACCOUNTS]
        self.nodes = [dict(n) for n in fakeslurm.NODES]
        self.comments = [j["admin_comment"] for j in fakeslurm.ACTIVE + fakeslurm.OTHERS]

    def tearDown(self):
        fakeslurm.ACCOUNTS[:] = self.accounts
        fakeslurm.NODES[:] = self.nodes
        for j, c in zip(fakeslurm.ACTIVE + fakeslurm.OTHERS, self.comments):
            j["admin_comment"] = c

    def test_hidden_from_other_users(self):
        fakeslurm.ADMINS.clear()
        for url in ("/admin", "/admin/jobs", "/admin/job/1300", "/admin/nodes", "/admin/users", "/admin/user/bob",
                    "/admin/log"):
            with self.subTest(url=url):
                status, _, body = call(url)
                self.assertEqual(status, 404)
                self.assertNotIn("Cluster admin", body)
        for url, form in (("/admin/job/1300/action", {"action": "cancel", "reason": "x"}),
                          ("/admin/node/node2/drain", {"reason": "x"}),
                          ("/admin/user/bob/lock", {"reason": "x", "password": fakeslurm.ADMIN_PASSWORD}),
                          ("/admin/users/add", {"user": "x1", "password": fakeslurm.ADMIN_PASSWORD})):
            self.assertEqual(call(url, "POST", form, ORIGIN)[0], 404)
        self.assertEqual((fakeslurm.CANCELLED, fakeslurm.ADMIN_CALLS, fakeslurm.ADMIN_HELPER_CALLS), ([], [], []))
        self.assertNotIn("Cluster admin", call("/")[2])
        self.assertEqual(call("/job/1300")[0], 404)              # nor through a user's own job pages

    def test_pages_render(self):
        for url, text in [("/admin/jobs", "md-run"), ("/admin/job/1300", "Requeue"), ("/admin/job/1301", "Release"),
                          ("/admin/job/1302", ">Hold</button>"), ("/admin/nodes", "Drain"),
                          ("/admin/users", "Add a user"), ("/admin/user/bob", "Lock account"),
                          ("/admin/log", "cancel job 1300 user=bob reason=&#x27;test &lt;b&gt;&#x27;")]:
            with self.subTest(url=url):
                status, headers, body = call(url)
                self.assertEqual(status, 200, body[-2000:])
                self.assertIn(text, body)
                self.assertIn("Cluster admin", body)
                self.assertIn("Content-Security-Policy", headers)
        status, headers, _ = call("/admin")
        self.assertEqual((status, headers["Location"]), (303, "/pun/sys/cbbl/admin/health"))
        self.assertNotIn("not Administrator", call("/admin/jobs")[2])

    def test_job_list_filters(self):
        _, _, body = call("/admin/jobs")
        for job_id in ("1187", "1300", "1301", "1302"):            # everyone's jobs
            self.assertIn(f"/admin/job/{job_id}", body)
        self.assertIn("Held jobs", body)
        _, _, body = call("/admin/jobs?user=carol")
        self.assertNotIn("/admin/job/1300", body)
        self.assertIn("/admin/job/1302", body)
        _, _, body = call("/admin/jobs?state=held&partial=1")
        self.assertIn("/admin/job/1301", body)
        self.assertNotIn("/admin/job/1302", body)
        _, _, body = call("/admin/jobs?user=%3Cx%3E")
        self.assertIn('data-refresh="/pun/sys/cbbl/admin/jobs?user=%3Cx%3E&amp;partial=1"', body)

    def test_level_warning(self):
        fakeslurm.LEVEL[0] = "None"
        _, _, body = call("/admin/jobs")
        self.assertIn("Your Slurm admin level is “None”, not Administrator", body)

    def test_job_actions(self):
        _, _, body = call("/admin/job/1300/action", "POST", {"action": "cancel", "reason": " "}, ORIGIN)
        self.assertIn("Give a reason", body)
        self.assertEqual((fakeslurm.ADMIN_CALLS, fakeslurm.CANCELLED), ([], []))
        status, headers, _ = call("/admin/job/1300/action", "POST",
                                  {"action": "cancel", "reason": "uses node3\nwhile drained"}, ORIGIN)
        self.assertEqual((status, headers["Location"]), (303, "/pun/sys/cbbl/admin/job/1300?done=cancel"))
        update = fakeslurm.ADMIN_CALLS[0]
        self.assertEqual(update[:2], ["update", "JobId=1300"])
        self.assertRegex(update[2], r"^AdminComment=Cancelled by alice on \d{4}-\d\d-\d\d \d\d:\d\d: uses node3 "
                                    r"while drained$")
        self.assertEqual(fakeslurm.CANCELLED, ["1300"])
        self.assertEqual(fakeslurm.ADMIN_HELPER_CALLS[-1], ("note", {"action": "cancel", "target": "job 1300 user=bob",
                                                                     "reason": "uses node3 while drained"}))
        _, _, body = call("/admin/job/1300?done=cancel")
        self.assertIn("Cancel request sent to Slurm.", body)
        self.assertIn("Note on this job (its user sees it): Cancelled by alice", body)
        call("/admin/job/1301/action", "POST", {"action": "release", "reason": ""}, ORIGIN)   # no reason needed
        self.assertEqual(fakeslurm.ADMIN_CALLS[-1], ["release", "1301"])
        call("/admin/job/1302/action", "POST", {"action": "hold", "reason": "waits for the new disks"}, ORIGIN)
        self.assertEqual(fakeslurm.ADMIN_CALLS[-1], ["hold", "1302"])
        _, _, body = call("/admin/job/1302/action", "POST", {"action": "delete", "reason": "x"}, ORIGIN)
        self.assertIn("Unknown action.", body)
        self.assertEqual(call("/admin/job/1302/action", "POST", {"action": "hold", "reason": "x"},
                              {"HTTP_ORIGIN": "https://evil.example"})[0], 403)

    def test_user_sees_the_note_on_an_active_job(self):
        fakeslurm.ACTIVE[2]["admin_comment"] = "Held by alice on 2026-10-26 14:00: waits for the new disks"
        _, _, body = call("/job/1190")
        self.assertIn("Note from the cluster: Held by alice", body)

    def test_nodes_drain_and_resume(self):
        _, _, body = call("/admin/node/node2/drain", "POST", {"reason": ""}, ORIGIN)
        self.assertIn("Give a reason for draining node2", body)
        status, headers, _ = call("/admin/node/node2/drain", "POST", {"reason": "disk check on Friday"}, ORIGIN)
        self.assertEqual((status, headers["Location"]), (303, "/pun/sys/cbbl/admin/nodes?done=drain&node=node2"))
        self.assertEqual(fakeslurm.ADMIN_CALLS[-1], ["update", "NodeName=node2", "State=DRAIN",
                                                     "Reason=disk check on Friday"])
        _, _, body = call("/admin/nodes?done=drain&node=node2")
        self.assertIn("node2 is draining", body)
        self.assertIn(">Draining</span>", body)                  # jobs still run there
        self.assertIn("disk check on Friday", body)
        self.assertIn("/admin/node/node2/resume", body)
        self.assertIn("drain", call("/cluster")[2])              # users' Cluster status shows it too
        call("/admin/node/node2/resume", "POST", {}, ORIGIN)
        self.assertEqual(fakeslurm.ADMIN_CALLS[-1], ["update", "NodeName=node2", "State=RESUME"])
        self.assertEqual(call("/admin/node/node99/drain", "POST", {"reason": "x"}, ORIGIN)[0], 404)

    def test_users_and_account_changes(self):
        _, _, body = call("/admin/users")
        for text in ("Carol &lt;K&gt;", "differs between nodes", "temporary password", ">admin</span>", ">80</td>"):
            self.assertIn(text, body)
        _, _, body = call("/admin/users/add", "POST", {"user": "kimjh", "name": "Kim J", "password": "wrong"}, ORIGIN)
        self.assertIn("Your password is wrong.", body)
        self.assertIn('value="kimjh"', body)                     # the form keeps what was typed
        status, headers, body = call("/admin/users/add", "POST",
                                     {"user": "kimjh", "name": "Kim J", "password": fakeslurm.ADMIN_PASSWORD}, ORIGIN)
        self.assertEqual(status, 200)
        self.assertIn("Account kimjh created (UID 2010) on node1, node2 and node3.", body)
        self.assertIn('<code id="temp-pw">Tmp7xK2mQp9a</code>', body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(fakeslurm.ADMIN_HELPER_CALLS[-2], ("add-user", {"user": "kimjh", "name": "Kim J",
                                                                         "password": fakeslurm.ADMIN_PASSWORD}))
        _, _, body = call("/admin/user/bob/password", "POST", {"password": fakeslurm.ADMIN_PASSWORD}, ORIGIN)
        self.assertIn("New password set for bob on all three nodes.", body)
        self.assertIn("New4pw7Kd2Qx", body)
        self.assertIn("This is your own account", call("/admin/user/alice")[2])
        status, headers, _ = call("/admin/user/bob/lock", "POST", {"reason": "left the lab", "cancel_jobs": "1",
                                                                  "password": fakeslurm.ADMIN_PASSWORD}, ORIGIN)
        self.assertEqual((status, headers["Location"]), (303, "/pun/sys/cbbl/admin/user/bob?done=lock&cancelled=1"))
        self.assertEqual(fakeslurm.CANCELLED, ["1300"])
        self.assertIn("(account locked): left the lab", fakeslurm.ADMIN_CALLS[0][2])
        _, _, body = call("/admin/user/bob?done=lock&cancelled=1")
        self.assertIn("The account is locked on all three nodes", body)
        self.assertIn("1 job cancelled.", body)
        self.assertIn("Unlock account", body)
        status, headers, _ = call("/admin/user/bob/unlock", "POST", {"password": fakeslurm.ADMIN_PASSWORD}, ORIGIN)
        self.assertEqual(headers["Location"], "/pun/sys/cbbl/admin/user/bob?done=unlock")
        self.assertEqual(call("/admin/user/nobody9")[0], 404)

    def test_health_page(self):
        idle_path, slurm.IDLE_STATUS = slurm.IDLE_STATUS, os.path.join(TMP, "idle-health.json")
        report = json.loads(json.dumps(fakeslurm.HEALTH_REPORT))
        try:
            with open(slurm.IDLE_STATUS, "w") as f:
                json.dump({"updated": time.time(), "idle": {}, "cancelled": {}}, f)
            status, headers, body = call("/admin/health")
            self.assertEqual(status, 200, body[-2000:])
            for text in ("Scheduled checks", "Idle-job check", "every 5 minutes", "Home backup (node2)",
                         "2026-10-26_0243.partial", "Running maintenance tasks", "folder-copy-proj_carol",
                         "Slurm controller errors", "6 known warnings not shown", "Worth a look",
                         "node1 / is 91 % full (79.2 GB free)", "node3 / is 93 % full", "running for 11h 0m",
                         'data-refresh="/pun/sys/cbbl/admin/health?partial=1"'):
                self.assertIn(text, body)
            self.assertNotIn("Needs attention", body)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)            # log lines are escaped
            self.assertNotIn("<script>alert", body)
            self.assertEqual(call("/admin")[1]["Location"], "/pun/sys/cbbl/admin/health")
            _, _, inner = call("/admin/health?partial=1")
            self.assertFalse(inner.startswith("<!doctype"))
            self.assertIn("Scheduled checks", inner)
            for disk in fakeslurm.HEALTH_REPORT["nodes"]["node1"]["disks"] + fakeslurm.HEALTH_REPORT["nodes"]["node3"]["disks"]:
                disk["used"] = disk["avail"]                                     # 50 %: nothing left to report
            self.assertIn("All checks pass on all three nodes.", call("/admin/health")[2])
            fakeslurm.HEALTH_REPORT.clear()
            fakeslurm.HEALTH_REPORT.update({"ok": False, "error": "The admin helper is not available (sudo: x)."})
            self.assertIn("Health report: The admin helper is not available", call("/admin/health")[2])
            fakeslurm.ADMINS.clear()
            self.assertEqual(call("/admin/health")[0], 404)
        finally:
            fakeslurm.HEALTH_REPORT.clear()
            fakeslurm.HEALTH_REPORT.update(report)
            os.remove(slurm.IDLE_STATUS)
            slurm.IDLE_STATUS = idle_path

    def test_health_problems(self):
        from cbbl import adminhealth
        now = fakeslurm.NOW
        r = json.loads(json.dumps(fakeslurm.HEALTH_REPORT))
        n01, n02 = r["nodes"]["node1"], r["nodes"]["node2"]
        n01["timers"][0]["started"] = now - 3600                     # the idle check is stuck
        n01["timers"][1].update(result="exit-code", status=1)        # the notifier failed
        n01["services"][0]["state"] = "failed"
        n01["clock"]["offset"] = 75.0
        n02["timers"][0]["started"] = now - 27 * 3600                # a long backup run
        n02["backup"]["at"] = now - 31 * 3600
        n02["selinux_denials"] = 3
        n02["mounts"][0].update(ok=False, source="-")
        r["nodes"]["node3"] = {"reachable": False, "error": "ssh: no route"}
        nodes = slurm.nodes()
        nodes[1].update(available=False, state=["MIXED", "DRAIN"], reason="disk check")
        found = adminhealth.find_problems(r, nodes, {"updated": now - 3600}, now)
        for item in [("bad", "node3 did not answer: ssh: no route"),
                     ("bad", "node1: slurmctld is failed"),
                     ("bad", "Idle-job check (node1) last ran 1h 0m ago"),
                     ("bad", "Telegram notifier (node1): the last run failed (exit-code, exit code 1)"),
                     ("bad", "node1: the clock is 75.0 s ahead"),
                     ("bad", "node2: /home is not mounted from 192.0.2.200:/data/home (found: -)"),
                     ("warn", "Home backup (node2) has been running for 1d 3h"),
                     ("warn", "The newest complete home backup is 1d 7h ago (a backup is running)"),
                     ("warn", "node2: 3 SELinux denials today (ausearch -m avc -ts today)"),
                     ("warn", "Slurm: node2 is mixed drain (disk check)"),
                     ("bad", "The idle check last wrote its status 1h 0m ago")]:
            self.assertIn(item, found)
        levels = [lv for lv, _ in found]
        self.assertEqual(levels, sorted(levels, key=lambda lv: lv != "bad"))   # the bad ones first

    def test_hostnames(self):
        self.assertEqual(slurm.hostnames("cn[01,200]"), ["cn01", "cn200"])
        self.assertEqual(slurm.hostnames("cn[01-02],gpu7"), ["cn01", "cn02", "gpu7"])
        self.assertEqual(slurm.hostnames("cn02"), ["cn02"])
        self.assertEqual(slurm.hostnames(""), [])


class Form(unittest.TestCase):
    nodes = slurm.nodes()
    part = slurm.partition()
    envs = slurm.conda_envs(HOME)

    def parse(self, **kw):
        form = {"command": "python train.py", "workdir": HOME}
        form.update(kw)
        return jobform.parse(form, HOME, self.nodes, self.part, self.envs)

    def test_time_limits(self):
        for text, minutes in [("30m", 30), ("12h", 720), ("2d", 2880), ("1d 12h", 2160), ("90", 90),
                              ("4:00:00", 240), ("1-12:00:00", 2160), ("2-6", 3240), ("10:30", 11), ("7d", 10080)]:
            with self.subTest(text=text):
                self.assertEqual(util.parse_time_limit(text), minutes)
        spec, errors = self.parse(time="15d")
        self.assertEqual(errors["time"], "At most 14 days.")
        self.assertEqual(util.limit_text(1440), "1d")
        self.assertEqual(util.limit_text(2160), "1d 12h")
        self.assertEqual(util.limit_text(90), "1h 30m")

    def test_memory(self):
        spec, errors = self.parse(mem="0.5")
        self.assertEqual(spec["mem_mb"], 512)
        self.assertIn("--mem=512M", jobform.command_line(spec, HOME))
        spec, errors = self.parse(mem="16GB")
        self.assertEqual(spec["mem_mb"], 16384)
        spec, errors = self.parse(mem="300", node="node1")
        self.assertIn("At most 234 GB on node1.", errors["mem"])
        spec, errors = self.parse(mem="300")
        self.assertNotIn("mem", errors)          # fits node2

    def test_defaults_follow_until_edited(self):
        spec, _ = self.parse(cpus="8", mem="4", mem_default="4")        # still the default: follows the CPUs
        self.assertEqual(spec["mem_mb"], 8 * 4096)
        spec, _ = self.parse(cpus="8", mem="10", mem_default="4")       # typed by the user: kept
        self.assertEqual(spec["mem_mb"], 10240)
        spec, _ = self.parse(cpus="96", mem="")                          # empty: default, capped by the node
        self.assertEqual(spec["mem_mb"], 96 * 4096)
        spec, _ = self.parse(command="python run.py", name="train", name_default="train")
        self.assertEqual(spec["name"], "run")
        spec, _ = self.parse(command="python run.py", name="mine", name_default="train")
        self.assertEqual(spec["name"], "mine")

    def test_names(self):
        self.assertEqual(jobform.default_name("python train.py --x 1"), "train")
        self.assertEqual(jobform.default_name("bash run_all.sh"), "run_all")
        self.assertEqual(jobform.default_name("CUDA=0 nextflow run main.nf"), "main")
        self.assertEqual(jobform.default_name("hostname"), "hostname")
        self.assertEqual(jobform.clean_name("my job; rm -rf ~"), "my_job_rm_-rf")

    def test_conda(self):
        names = [e["label"] for e in self.envs]
        self.assertEqual(names, ["base", "esm", "rnaseq"])  # the deleted env is skipped
        spec, errors = self.parse(conda="nope")
        self.assertIn("Unknown environment", errors["conda"])
        spec, errors = self.parse(conda="base")
        self.assertEqual(jobform.conda_lines(spec["conda"], HOME)[-1], "conda activate base")

    def test_workdir(self):
        spec, errors = self.parse(workdir="relative/dir")
        self.assertIn("full path", errors["workdir"])
        spec, errors = self.parse(workdir="/tmp/a b")
        self.assertIn("spaces", errors["workdir"])
        spec, errors = self.parse(workdir="~/blast")
        self.assertEqual(spec["workdir"], HOME + "/blast")

    def test_command_line_quoting(self):
        spec, errors = self.parse(command="echo 'hi there' && python x.py", name="it's")
        line = jobform.command_line(spec, HOME)
        self.assertIn("-J it_s", line)
        self.assertIn("--wrap='echo '\"'\"'hi there'\"'\"' && python x.py'", line)


class Helpers(unittest.TestCase):
    def test_expand_path(self):
        job = {"job_id": 1192, "array_id": 1191, "task": 3, "name": "md", "user": "u", "nodes": "node2"}
        self.assertEqual(slurm.expand_path("/h/slurm-%A_%a.out", job), "/h/slurm-1191_3.out")
        self.assertEqual(slurm.expand_path("/h/%x-%j-%N.log", job), "/h/md-1192-node2.log")
        self.assertEqual(slurm.expand_path("/h/%5j.out", job), "/h/01192.out")
        self.assertEqual(slurm.expand_path("/h/100%%.out", job), "/h/100%.out")
        job["workdir"] = "/home/u/run"
        self.assertEqual(slurm.expand_path("slurm-%j.out", job), "/home/u/run/slurm-1192.out")

    def test_formatting(self):
        self.assertEqual(util.fmt_duration(38 * 60 + 3), "38m 3s")
        self.assertEqual(util.fmt_duration(10 * 3600 + 40 * 60), "10h 40m")
        self.assertEqual(util.fmt_minutes(1440), "1 day")
        self.assertEqual(util.fmt_minutes(10080), "7 days")
        self.assertEqual(util.fmt_mb(240000), "234 GB")
        self.assertEqual(util.fmt_mb(512), "512 MB")
        self.assertEqual(util.fmt_mb(1536), "1.5 GB")
        self.assertEqual(util.slurm_time(30), "30")
        self.assertEqual(util.slurm_time(2880), "2-00:00:00")

    def test_numbers_and_sizes(self):
        self.assertEqual(slurm.mem_mb("16G"), 16384)
        self.assertEqual(slurm.seconds_of("1-02:03:04"), 93784)
        self.assertEqual(slurm.seconds_of("02:03.5"), 123.5)
        self.assertEqual(slurm.bytes_of("1.5K"), 1536)
        self.assertEqual(slurm.num({"set": True, "number": 5}), 5)
        self.assertIsNone(slurm.num({"set": False, "number": 0}))


if __name__ == "__main__":
    unittest.main()
