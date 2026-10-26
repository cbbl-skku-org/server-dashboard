"""A fake Slurm for tests and screenshots: patches cbbl.slurm.run and cbbl.slurm.me with canned answers."""
import json
import os
import pwd
import time

NOW = time.time()
USER = "alice"
SUBMITTED = []      # scripts given to sbatch
CANCELLED = []      # job ids given to scancel
ADMIN_CALLS = []    # scontrol update / hold / release / requeue (admin pages)
ADMINS = {"alice"}
LEVEL = ["Administrator"]   # the user's Slurm admin level


def num(n):
    return {"set": True, "infinite": False, "number": n}


UNSET = {"set": False, "infinite": False, "number": 0}


def squeue_job(job_id, name, state, cpus, mem_mb, node, start, limit_min, reason="None", array=None, task=None,
               workdir="/home/alice/esm", user=USER):
    return {
        "job_id": job_id, "array_job_id": num(array or 0), "array_task_id": num(task) if task is not None else UNSET,
        "name": name, "user_name": user, "job_state": [state], "state_reason": reason, "cpus": num(cpus),
        "tres_alloc_str": f"cpu={cpus},mem={mem_mb}M,node=1,billing={cpus}" if state == "RUNNING" else "",
        "tres_req_str": f"cpu={cpus},mem={mem_mb}M,node=1,billing={cpus}",
        "memory_per_node": num(mem_mb), "memory_per_cpu": UNSET, "time_limit": num(limit_min),
        "submit_time": num(int(start - 600)), "start_time": num(int(start)) if start else num(0),
        "end_time": num(int(start + limit_min * 60)) if start else num(0), "nodes": node,
        "current_working_directory": workdir, "command": "(null)", "partition": "cpu", "features": "",
        "required_nodes": "", "standard_output": f"{workdir}/slurm-%j.out", "standard_error": f"{workdir}/slurm-%j.out",
        "admin_comment": "",
    }


def sacct_job(job_id, name, state, cpus, mem_mb, start, elapsed, rc=0, sig=None, node="node2", limit_min=1440,
              total_cpu=None, peak=None, task=None, array=None, script=None, workdir="/home/alice/esm", user=USER):
    rec = {
        "job_id": job_id, "name": name, "user": user, "state": {"current": [state], "reason": "None"},
        "array": {"job_id": array or 0, "task_id": num(task) if task is not None else UNSET},
        "time": {"elapsed": elapsed, "start": int(start) if start else 0, "end": int(start + elapsed) if start else 0,
                 "submission": int((start or NOW) - 300), "limit": num(limit_min),
                 "total": {"seconds": int(total_cpu or 0), "microseconds": 0}},
        "tres": {"allocated": [{"type": "cpu", "name": "", "count": cpus}, {"type": "mem", "name": "", "count": mem_mb}],
                 "requested": [{"type": "cpu", "name": "", "count": cpus}, {"type": "mem", "name": "", "count": mem_mb}]},
        "exit_code": {"return_code": num(rc) if sig is None else UNSET,
                      "signal": {"id": num(sig) if sig else UNSET, "name": str(sig or "")}},
        "required": {"CPUs": cpus, "memory_per_node": num(mem_mb), "memory_per_cpu": UNSET},
        "nodes": node if start else "None assigned", "working_directory": workdir, "partition": "cpu",
        "stdout": f"{workdir}/slurm-%j.out", "stdout_expanded": f"{workdir}/slurm-{job_id}.out", "stderr": "",
        "stderr_expanded": f"{workdir}/slurm-{job_id}.out", "submit_line": "sbatch train.sh",
        "script": script or f"#!/bin/bash -l\n#SBATCH --job-name={name}\n#SBATCH --cpus-per-task={cpus}\n"
                            f"#SBATCH --mem={mem_mb // 1024}G\npython {name}.py\n",
        "constraints": "", "steps": [], "comment": {"administrator": "", "job": "", "system": ""},
    }
    if peak is not None:
        rec["steps"] = [{"step": {"id": f"{job_id}.batch", "name": "batch"},
                         "tres": {"requested": {"max": [{"type": "mem", "name": "", "count": peak}]}}}]
    return rec


H = 3600
D = 86400
ACTIVE = [
    squeue_job(1187, "train-esm2", "RUNNING", 32, 131072, "node2", NOW - 3 * H - 40 * 60, 1440),
    squeue_job(1188, "blast-uniref50", "RUNNING", 16, 65536, "node3", NOW - 25 * 60, 720, workdir="/home/alice/blast"),
    squeue_job(1190, "alphafold-batch", "PENDING", 48, 204800, "", NOW + 2 * H + 10 * 60, 2880, reason="Resources"),
    squeue_job(1192, "md-sweep", "PENDING", 8, 32768, "", 0, 240, reason="Priority", array=1191, task=3),
]
# other users' jobs: only the admin pages list them
OTHERS = [
    squeue_job(1300, "md-run", "RUNNING", 24, 98304, "node3", NOW - 2 * H, 2880, user="bob", workdir="/home/bob/md"),
    squeue_job(1301, "assembly", "PENDING", 96, 409600, "", 0, 1440, reason="JobHeldAdmin", user="carol",
               workdir="/home/carol/asm"),
    squeue_job(1302, "qc", "PENDING", 4, 16384, "", NOW + H, 120, reason="Priority", user="carol",
               workdir="/home/carol/qc"),
]
IDLE_NOTE = "Idle check: cancelled 2026-10-24 21:36, under 0.05 CPU for 60 min"
FINISHED = [
    sacct_job(1186, "esm2-predict", "COMPLETED", 16, 65536, NOW - 5 * H, 38 * 60 + 3, total_cpu=16 * 2200, peak=21 * 1024 ** 3),
    sacct_job(1185, "esm2-predict", "COMPLETED", 16, 65536, NOW - 5.5 * H, 44 * 60 + 56, total_cpu=16 * 2600, peak=22 * 1024 ** 3),
    sacct_job(1184, "titletest", "FAILED", 4, 16384, NOW - 7 * H, 19, rc=1, total_cpu=12),
    sacct_job(1183, "train-esm2-lr1e-4", "COMPLETED", 32, 131072, NOW - 20 * H, 7 * H + 53 * 60, total_cpu=32 * 7 * H * 0.9, peak=88 * 1024 ** 3),
    sacct_job(1180, "featurize", "OUT_OF_MEMORY", 8, 16384, NOW - 26 * H, 14 * 60, sig=125, peak=16 * 1024 ** 3, total_cpu=8 * 700),
    sacct_job(1179, "featurize", "COMPLETED", 8, 65536, NOW - 25 * H, 42 * 60, total_cpu=8 * 2400, peak=31 * 1024 ** 3),
    sacct_job(1171, "download-pdb", "TIMEOUT", 2, 8192, NOW - 2 * D, 4 * H, node="node3", limit_min=240, total_cpu=900),
    sacct_job(1170, "blast-test", "CANCELLED", 16, 65536, NOW - 2.2 * D, 6 * 60, total_cpu=16 * 300),
    dict(sacct_job(1169, "jupyter", "CANCELLED", 4, 16384, NOW - 2.5 * D, 75 * 60, total_cpu=40),
         comment={"administrator": IDLE_NOTE, "job": "", "system": ""}),
    sacct_job(1166, "train-esm2-s2026", "COMPLETED", 32, 131072, NOW - 3 * D, 6 * H + 32 * 60, total_cpu=32 * 6 * H * 0.6, peak=40 * 1024 ** 3),
    sacct_job(1165, "train-esm2-s2027", "COMPLETED", 32, 131072, NOW - 3 * D, 6 * H + 31 * 60, total_cpu=32 * 6 * H * 0.6, peak=40 * 1024 ** 3),
    sacct_job(1150, "never-started", "CANCELLED", 4, 16384, 0, 0),
    sacct_job(1120, "msa-build", "COMPLETED", 24, 98304, NOW - 9 * D, 10 * H + 40 * 60, total_cpu=24 * 10 * H, peak=50 * 1024 ** 3),
    sacct_job(1101, "msa-build", "COMPLETED", 24, 98304, NOW - 12 * D, 9 * H, total_cpu=24 * 8 * H),
    sacct_job(1050, "qc-<script>alert(1)</script>", "COMPLETED", 1, 4096, NOW - 20 * D, 3 * 60 + 6, total_cpu=150),
]
NODES = [
    {"name": "node1", "partitions": ["cpu", "pilot"], "cpus": 96, "effective_cpus": 48, "alloc_cpus": 0,
     "real_memory": 480000, "specialized_memory": 240000, "alloc_memory": 0, "cpu_load": 310, "features": ["internet"],
     "state": ["IDLE"], "reason": "", "weight": 3},
    {"name": "node2", "partitions": ["cpu", "admin"], "cpus": 96, "effective_cpus": 96, "alloc_cpus": 72,
     "real_memory": 480000, "specialized_memory": 0, "alloc_memory": 360448, "cpu_load": 6890, "features": ["offline"],
     "state": ["MIXED"], "reason": "", "weight": 1},
    {"name": "node3", "partitions": ["cpu", "admin"], "cpus": 96, "effective_cpus": 48, "alloc_cpus": 48,
     "real_memory": 480000, "specialized_memory": 240000, "alloc_memory": 196608, "cpu_load": 4720,
     "features": ["internet", "nfs-server"], "state": ["ALLOCATED"], "reason": "", "weight": 2},
]
PARTITION = {"name": "cpu", "defaults": {"time": num(1440), "partition_memory_per_cpu": num(4096)},
             "maximums": {"time": num(20160)}}
DEBUG = {"name": "debug", "defaults": {"time": num(10), "partition_memory_per_cpu": num(4096)},
         "maximums": {"time": num(10), "cpus_per_node": num(4), "partition_memory_per_node": num(16384)}}
HELPER_CALLS = []          # (command, input) given to the account helper
TELEGRAM = {"chat_id": "", "events": {"done": False, "failed": True, "idle": True}}


def find_active(jid):
    return [j for j in ACTIVE + OTHERS if str(j["job_id"]) == jid or
            f'{j["array_job_id"]["number"]}_{j["array_task_id"].get("number")}' == jid]


def fake_run(args, input_text=None, timeout=None):
    cmd = args[0]
    js = "--json" in args
    if cmd == "squeue" and js:
        if "-j" in args:
            jobs = find_active(args[args.index("-j") + 1])
            if not jobs:
                from cbbl.slurm import SlurmError
                raise SlurmError("squeue: error: Invalid job id specified")
            return json.dumps({"jobs": jobs})
        return json.dumps({"jobs": ACTIVE if "-u" in args else ACTIVE + OTHERS})
    if cmd == "squeue":
        return "RUNNING\n" * 5 + "PENDING\n" * 3
    if cmd == "sacct":
        if "-j" in args:
            jid = args[args.index("-j") + 1]
            recs = [j for j in FINISHED if str(j["job_id"]) == jid]
            if not recs:
                recs = [sacct_job(j["job_id"], j["name"], j["job_state"][0], j["cpus"]["number"], 1024, NOW - 600, 600,
                                  user=j["user_name"]) for j in find_active(jid)]
            return json.dumps({"jobs": recs})
        running = [sacct_job(j["job_id"], j["name"], j["job_state"][0], j["cpus"]["number"],
                             j["memory_per_node"]["number"], j["start_time"]["number"] if j["job_state"][0] == "RUNNING" else 0,
                             int(NOW - j["start_time"]["number"]) if j["job_state"][0] == "RUNNING" else 0)
                   for j in ACTIVE]
        return json.dumps({"jobs": FINISHED + running})
    if cmd == "scontrol" and args[1:3] == ["show", "node"]:
        return json.dumps({"nodes": NODES})
    if cmd == "scontrol" and args[1:3] == ["show", "partition"]:
        return json.dumps({"partitions": [DEBUG if args[3] == "debug" else PARTITION]})
    if cmd == "scontrol" and args[1:3] == ["write", "batch_script"]:
        return "#!/bin/bash -l\n#SBATCH --job-name=train-esm2\n#SBATCH --cpus-per-task=32\n#SBATCH --mem=128G\n" \
               "source ~/miniconda3/etc/profile.d/conda.sh\nconda activate esm\npython train.py --epochs 40\n"
    if cmd == "scontrol" and args[1] == "update" and args[2].startswith("JobId="):
        ADMIN_CALLS.append(args[1:])
        for j in find_active(args[2][len("JobId="):]):
            j["admin_comment"] = args[3].split("=", 1)[1]
        return ""
    if cmd == "scontrol" and args[1] == "update" and args[2].startswith("NodeName="):
        ADMIN_CALLS.append(args[1:])
        node = next(n for n in NODES if n["name"] == args[2][len("NodeName="):])
        if args[3] == "State=DRAIN":
            node.update(state=[node["state"][0], "DRAIN"], reason=args[4].split("=", 1)[1], reason_set_by_user=USER,
                        reason_changed_at=num(int(NOW)))
        else:
            node.update(state=[node["state"][0]], reason="", reason_set_by_user="")
        return ""
    if cmd == "scontrol" and args[1] in ("hold", "release", "requeue"):
        ADMIN_CALLS.append(args[1:])
        return ""
    if cmd == "sacctmgr":
        return LEVEL[0] + "\n"
    if cmd == "sshare" and "-a" in args:
        return "|\nroot|1.000000\n|\nbob|0.941176\nalice|0.734000\ncarol|0.250000\n"
    if cmd == "sshare":
        return "|\nalice|0.734000\n"
    if cmd == "sreport":
        return "|125\n|125\nbob|80\nalice|19\ncarol|26\n"
    if cmd == "sstat":
        return "1187.extern|cpu=00:00:00,mem=0|cpu=00:00:00,mem=0\n1187.batch|cpu=3-12:10:00,mem=97.5G|cpu=3-12:10:00,mem=101.2G\n"
    if cmd == "sbatch":
        SUBMITTED.append(input_text)
        return "1200\n"
    if cmd == "scancel":
        CANCELLED.append(args[1])
        return ""
    raise AssertionError(f"unexpected command {args}")


class FakePw:
    def __init__(self, home):
        self.pw_name, self.pw_dir, self.pw_uid, self.pw_shell = USER, home, 1010, "/bin/bash"


def make_home(base):
    """A fake home with two conda environments and a job output file."""
    home = os.path.join(base, "alice")
    for d in ("miniconda3/conda-meta", "miniconda3/etc/profile.d", "miniconda3/envs/esm/conda-meta",
              "miniconda3/envs/rnaseq/conda-meta", ".conda", "esm", "blast", ".ssh"):
        os.makedirs(os.path.join(home, d), exist_ok=True)
    open(os.path.join(home, "miniconda3/etc/profile.d/conda.sh"), "w").close()
    with open(os.path.join(home, ".conda/environments.txt"), "w") as f:
        f.write("\n".join(os.path.join(home, p) for p in ("miniconda3", "miniconda3/envs/esm", "miniconda3/envs/rnaseq",
                                                          "gone/envs/old")) + "\n")
    with open(os.path.join(home, "esm/slurm-1187.out"), "w") as f:
        for epoch in range(1, 15):
            f.write(f"epoch {epoch:3d}/40  loss {2.5 / epoch:.4f}  val_loss {2.7 / epoch:.4f}  lr 1.0e-04\n")
        f.write("training: 35%|###5      | 14/40 [3:38:02<6:45:10, 935.0s/it]\r"
                "training: 36%|###6      | 15/40 [3:40:00<6:30:00, 936.0s/it]\n")
    with open(os.path.join(home, ".ssh/authorized_keys"), "w") as f:
        f.write("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample alice@laptop\nssh-rsa AAAAB3NzaC1yc2EAAAADAQAB lab-pc\n")
    return home


def _rehome(obj, home):
    if isinstance(obj, dict):
        return {k: _rehome(v, home) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rehome(v, home) for v in obj]
    if isinstance(obj, str) and obj.startswith("/home/alice"):
        return home + obj[len("/home/alice"):]
    return obj


def fake_helper(cmd, data=None):
    HELPER_CALLS.append((cmd, data))
    if cmd == "passwd":
        if data["current"] != "Old-password-1":
            return {"ok": False, "error": "The current password is wrong."}
        return {"ok": True, "nodes": ["node2", "node3", "node1"]}
    if cmd == "telegram-get":
        return dict(TELEGRAM, ok=True)
    if cmd == "telegram-set":
        TELEGRAM.update(chat_id=data["chat_id"], events=data["events"])
        return dict(TELEGRAM, ok=True)
    if cmd == "telegram-test":
        return {"ok": True} if TELEGRAM["chat_id"] else {"ok": False, "error": "Save a chat ID first."}
    raise AssertionError(cmd)


ADMIN_PASSWORD = "Admin-password-1"
ADMIN_HELPER_CALLS = []    # (command, input) given to the admin helper
ACCOUNTS = [
    {"user": "bob", "uid": 1005, "name": "", "admin": False, "must_change": False, "locked": False,
     "nodes": {"node2": "active", "node3": "active", "node1": "active"}, "missing": [], "uid_differs": [],
     "lock_info": None},
    {"user": "alice", "uid": 1003, "name": "", "admin": True, "must_change": False, "locked": False,
     "nodes": {"node2": "active", "node3": "active", "node1": "active"}, "missing": [], "uid_differs": [],
     "lock_info": None},
    {"user": "carol", "uid": 1002, "name": "Carol <K>", "admin": False, "must_change": True, "locked": False,
     "nodes": {"node2": "active", "node3": "active", "node1": "active"}, "missing": ["node3"],
     "uid_differs": [], "lock_info": None},
]


def _svc(*names):
    return [{"name": n, "loaded": True, "state": "active"} for n in names]


def _nfs(*paths):
    return [{"path": p, "source": f"192.0.2.200:/data{p}", "fstype": "nfs4", "expected": f"192.0.2.200:/data{p}",
             "ok": True} for p in paths]


# the admin helper's health report (from the real one of 2026-10-26 14:03)
HEALTH_REPORT = {"ok": True, "at": NOW, "nodes": {
    "node2": {"reachable": True,
               "disks": [{"path": "/", "size": 929326735360, "used": 563906662400, "avail": 365420072960},
                         {"path": "/scratch", "size": 110000026419200, "used": 42184541908992, "avail": 67815484510208}],
               "mounts": _nfs("/home", "/shared"), "services": _svc("slurmd", "munge", "chronyd", "crond", "sshd", "nfs-server"),
               "timers": [{"name": "home-backup", "loaded": True, "state": "active", "running": True,
                           "result": "success", "started": int(NOW - 11 * H), "status": 0}],
               "tasks": [{"unit": "home-backup.service", "description": "Backup of /home", "state": "activating",
                          "since": int(NOW - 11 * H)}],
               "load": [25.7, 25.8, 25.9], "cpus": 96, "mem_total": 527461604, "mem_avail": 486504076, "uptime": 44398604.0,
               "clock": {"offset": -0.00004, "leap": "Normal"},
               "logs": {"home-backup": ["2026-10-26 02:43:05 start 2026-10-26_0243 (previous snapshot: 2026-10-25_1326)",
                                        "<script>alert(1)</script>"]},
               "backup": {"state": "ok", "snapshot": "2026-10-25_1326", "at": int(NOW - 20 * H), "rc": 0,
                          "snapshots": ["2026-10-25_1326", "2026-10-26_0243.partial"]},
               "selinux_denials": 0},
    "node3": {"reachable": True,
                "disks": [{"path": "/", "size": 929326735360, "used": 857592270848, "avail": 71734464512},
                          {"path": "/data", "size": 259998538006528, "used": 15283587211264, "avail": 244714950795264}],
                "mounts": [{"path": "/home", "source": "/dev/sda1[/home]", "fstype": "xfs", "expected": "[/home]", "ok": True}],
                "services": _svc("slurmd", "munge", "chronyd", "crond", "sshd", "nfs-server"), "timers": [],
                "tasks": [{"unit": f"folder-copy-{n}.service", "description": f"/usr/local/sbin/copy-folder.sh {n}",
                           "state": "active", "since": int(NOW - 11 * H)} for n in ("dave", "proj_carol")],
                "load": [8.9, 8.6, 8.7], "cpus": 96, "mem_total": 527456404, "mem_avail": 499391872, "uptime": 11923058.0,
                "clock": {"offset": 0.0, "leap": "Normal"},
                "logs": {"folder-move": ["2026-10-26 12:14:26 finished proj_dash -> /home/bob/proj_dash (634104 entries)"]}},
    "node1": {"reachable": True,
               "disks": [{"path": "/", "size": 929326735360, "used": 844286365696, "avail": 85040369664}],
               "mounts": _nfs("/home", "/shared"),
               "services": _svc("slurmctld", "slurmdbd", "mariadb", "httpd", "slurmd", "munge", "chronyd", "crond", "sshd"),
               "timers": [{"name": "idle-jobs", "loaded": True, "state": "active", "running": False,
                           "result": "success", "started": int(NOW - 120), "status": 0},
                          {"name": "notify", "loaded": True, "state": "active", "running": False,
                           "result": "success", "started": int(NOW - 30), "status": 0}],
               "tasks": [], "load": [0.2, 0.2, 0.2], "cpus": 96, "mem_total": 527462744, "mem_avail": 513733080,
               "uptime": 3292759.0, "clock": {"offset": 0.000003, "leap": "Normal"},
               "logs": {"idle-jobs": ["2026-10-24 22:07:18 cancel job=70 user=alice cpus=2 node=node2 idle_min=4"],
                        "notify": ["2026-10-25 23:49:03 sent user=alice messages=1"]},
               "slurmctld": {"errors": [], "count": 0, "known": 6}},
}}


def fake_admin_helper(cmd, data=None, timeout=None):
    ADMIN_HELPER_CALLS.append((cmd, data))
    if cmd == "health":
        return json.loads(json.dumps(HEALTH_REPORT))
    if cmd == "users":
        return {"ok": True, "users": ACCOUNTS, "problems": []}
    if cmd == "log":
        return {"ok": True, "entries": [{"time": "2026-10-26 14:00:00", "admin": "alice",
                                         "text": "cancel job 1300 user=bob reason='test <b>'"}]}
    if cmd == "note":
        return {"ok": True}
    if data.get("password") != ADMIN_PASSWORD:
        return {"ok": False, "error": "Your password is wrong."}
    if cmd == "add-user":
        if data["user"] == "bob":
            return {"ok": False, "error": "bob exists already on node1."}
        ACCOUNTS.append(dict(ACCOUNTS[0], user=data["user"], uid=2010, name=data["name"], must_change=True))
        return {"ok": True, "user": data["user"], "uid": 2010, "name": data["name"], "home": f"/home/{data['user']}",
                "password": "Tmp7xK2mQp9a"}
    if cmd == "reset-password":
        return {"ok": True, "user": data["user"], "password": "New4pw7Kd2Qx", "locked": False}
    if cmd in ("lock", "unlock"):
        acct = next(a for a in ACCOUNTS if a["user"] == data["user"])
        acct["locked"] = cmd == "lock"
        return {"ok": True, "user": data["user"], "nodes": ["node2", "node3", "node1"]}
    raise AssertionError(cmd)


def install(home):
    import cbbl.account as account
    import cbbl.admin as admin
    import cbbl.slurm as slurm
    account.helper = fake_helper
    admin.helper = fake_admin_helper
    admin.admins = lambda: set(ADMINS)
    account.MUST_CHANGE_DIR = os.path.join(os.path.dirname(home), "must-change")
    account.BOT_FILE = os.path.join(os.path.dirname(home), "bot.json")
    os.makedirs(account.MUST_CHANGE_DIR, exist_ok=True)
    with open(account.BOT_FILE, "w") as f:
        json.dump({"username": "example_cluster_bot"}, f)
    ACTIVE[:] = _rehome(ACTIVE, home)
    FINISHED[:] = _rehome(FINISHED, home)
    slurm.run = fake_run
    slurm.me = lambda: FakePw(home)
