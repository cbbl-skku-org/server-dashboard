"""Everything the portal asks Slurm.

The portal runs as the logged-in user (Open OnDemand per-user server), so every command here runs with that user's
own rights, exactly as if they had typed it in a terminal.
"""
import collections
import json
import math
import os
import pwd
import re
import stat
import subprocess
import time

from .config import SETTINGS

SLURM_BIN = SETTINGS["slurm_bin"]
PARTITION = SETTINGS["partition"]
TIMEOUT = 20

# squeue lists these; everything else is finished.
ACTIVE_STATES = {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED", "REQUEUED", "REQUEUE_HOLD",
                 "REQUEUE_FED", "RESIZING", "SIGNALING", "STAGE_OUT", "STOPPED"}
RUNNING_STATES = {"RUNNING", "CONFIGURING", "COMPLETING", "SIGNALING", "STAGE_OUT"}

# label, badge style (run = blue, wait = amber, done = neutral, bad = red, muted = neutral)
STATES = {
    "PENDING": ("Waiting", "wait"), "RUNNING": ("Running", "run"), "CONFIGURING": ("Starting", "run"),
    "COMPLETING": ("Finishing", "run"), "SIGNALING": ("Running", "run"), "STAGE_OUT": ("Finishing", "run"),
    "SUSPENDED": ("Suspended", "wait"), "STOPPED": ("Stopped", "wait"), "REQUEUED": ("Requeued", "wait"),
    "REQUEUE_HOLD": ("Held", "wait"), "RESIZING": ("Resizing", "run"),
    "COMPLETED": ("Completed", "done"), "CANCELLED": ("Cancelled", "muted"), "FAILED": ("Failed", "bad"),
    "TIMEOUT": ("Timed out", "bad"), "OUT_OF_MEMORY": ("Out of memory", "bad"), "NODE_FAIL": ("Node failure", "bad"),
    "BOOT_FAIL": ("Boot failure", "bad"), "DEADLINE": ("Deadline", "bad"), "PREEMPTED": ("Preempted", "bad"),
    "REVOKED": ("Revoked", "muted"), "SPECIAL_EXIT": ("Special exit", "bad"),
}

# Why a job is waiting, in words (squeue's Reason column).
REASONS = {
    "Resources": "waiting for free CPUs or memory",
    "Priority": "other jobs are ahead in the queue (fair share)",
    "Dependency": "waiting for another job to finish",
    "DependencyNeverSatisfied": "its dependency can never be met: cancel it",
    "BeginTime": "waiting for its requested start time",
    "JobHeldUser": "held by you (scontrol release <job id>)",
    "JobHeldAdmin": "held by the admin",
    "ReqNodeNotAvail": "a node it asked for is not available",
    "NodeDown": "a node it needs is down",
    "PartitionTimeLimit": "its time limit is above the maximum",
    "PartitionNodeLimit": "it asks for more nodes than allowed",
    "BadConstraints": "no node can satisfy the request",
    "Reservation": "waiting for a reservation",
    "launch failed requeued held": "could not start and is held: tell the admin",
}

FEATURES = SETTINGS["features"]

# Written by the idle-job check (root, every 5 minutes): job ids and times only. Missing until the check is installed.
IDLE_STATUS = SETTINGS["idle_status"]
IDLE_RULE = SETTINGS["idle_rule"]


class SlurmError(Exception):
    pass


def me():
    return pwd.getpwuid(os.getuid())


def clean_env():
    """A minimal environment for Slurm commands. Jobs inherit the environment of sbatch, and the portal's own
    variables (Passenger, nginx) must not leak into them; the job script's login shell sets up the rest."""
    pw = me()
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": pw.pw_dir, "USER": pw.pw_name, "LOGNAME": pw.pw_name,
            "SHELL": pw.pw_shell or "/bin/bash", "LANG": "C.UTF-8"}


def run(args, input_text=None, timeout=TIMEOUT):
    cmd = [os.path.join(SLURM_BIN, args[0])] + [str(a) for a in args[1:]]
    try:
        p = subprocess.run(cmd, input=input_text, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                           timeout=timeout, env=clean_env(), cwd="/")
    except subprocess.TimeoutExpired:
        raise SlurmError(f"{args[0]} did not answer within {timeout} seconds")
    except OSError as e:
        raise SlurmError(f"could not run {args[0]}: {e.strerror}")
    if p.returncode != 0:
        msg = (p.stderr.strip() or p.stdout.strip() or f"exit code {p.returncode}")[:600]
        raise SlurmError(f"{args[0]}: {msg}")
    return p.stdout


def run_json(args, timeout=TIMEOUT):
    out = run(list(args) + ["--json"], timeout=timeout)
    try:
        data = json.loads(out)
    except ValueError:
        raise SlurmError(f"{args[0]} returned output the portal cannot read")
    errors = [e.get("description") or e.get("error") for e in data.get("errors") or [] if isinstance(e, dict)]
    errors = [str(e) for e in errors if e]
    if errors:
        raise SlurmError(f"{args[0]}: " + "; ".join(errors))
    return data


def num(v, default=None):
    """Slurm JSON numbers are plain, or {"set": bool, "infinite": bool, "number": n}."""
    if isinstance(v, dict):
        if v.get("infinite"):
            return math.inf
        return v.get("number") if v.get("set") else default
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return v
    return default


def tres_str(text):
    """'cpu=4,mem=16G,node=1' -> {'cpu': '4', 'mem': '16G', 'node': '1'}"""
    out = {}
    for part in (text or "").split(","):
        key, _, value = part.partition("=")
        if key:
            out[key] = value
    return out


def tres_list(items):
    """sacct JSON TRES list -> {'cpu': 4, 'mem': 16384, ...}"""
    out = {}
    for t in items or []:
        if isinstance(t, dict) and t.get("type"):
            key = t["type"] + ("/" + t["name"] if t.get("name") else "")
            out[key] = t.get("count")
    return out


_UNITS = {"": 1.0, "K": 1 / 1024, "M": 1.0, "G": 1024.0, "T": 1024.0 ** 2, "P": 1024.0 ** 3}


def mem_mb(text):
    """'16G' -> 16384.0, '500M' -> 500.0, '4096' -> 4096.0 (Slurm's unitless memory is MB)."""
    m = re.fullmatch(r"\s*([\d.]+)\s*([KMGTP]?)B?\s*", text or "", re.I)
    return float(m.group(1)) * _UNITS[m.group(2).upper()] if m else None


def bytes_of(text):
    """sstat values: '588K', '1.50G', '1234' (bytes) -> bytes."""
    m = re.fullmatch(r"\s*([\d.]+)\s*([KMGTP]?)\s*", text or "", re.I)
    if not m:
        return None
    return float(m.group(1)) * {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4,
                                "P": 1024 ** 5}[m.group(2).upper()]


def seconds_of(text):
    """sstat CPU times: '01:02:03', '1-02:03:04', '02:03.456', '12.5' -> seconds."""
    t = (text or "").strip()
    if not t:
        return None
    days = 0
    if "-" in t:
        d, t = t.split("-", 1)
        days = int(d)
    parts = [float(p) for p in t.split(":")]
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return days * 86400 + secs


def _secs(obj):
    if isinstance(obj, dict):
        return (obj.get("seconds") or 0) + (obj.get("microseconds") or 0) / 1e6
    return None


def state_label(state):
    return STATES.get(state, (state.replace("_", " ").capitalize() if state else "Unknown", "muted"))


def reason_text(reason):
    if not reason or reason == "None":
        return ""
    return REASONS.get(reason, reason)


def expand_path(pattern, job):
    """Expand sbatch filename patterns (%j, %A, %a, %x, %u, %N, %%) for a job. Slurm reports the default
    output name relative to the working directory (slurm-%j.out): make it absolute."""
    if not pattern:
        return ""
    if not pattern.startswith("/") and job.get("workdir"):
        pattern = os.path.join(job["workdir"], pattern)
    if "%" not in pattern and "\\" not in pattern:
        return pattern
    if "\\" in pattern:  # a backslash turns off pattern replacement
        return pattern.replace("\\", "")
    first_node = re.split(r"[,\[]", job.get("nodes") or "")[0] if job.get("nodes") else ""
    values = {"j": str(job.get("job_id") or ""), "A": str(job.get("array_id") or job.get("job_id") or ""),
              "a": "" if job.get("task") is None else str(job["task"]), "x": job.get("name") or "",
              "u": job.get("user") or "", "N": first_node, "n": "0", "t": "0", "s": "batch", "%": "%"}

    def repl(m):
        key = m.group(2)
        val = values.get(key)
        if val is None:
            return m.group(0)
        width = m.group(1)
        return val.zfill(int(width)) if width and val.isdigit() else val
    return re.sub(r"%(\d*)([jAaxuNnts%])", repl, pattern)


# ---------------------------------------------------------------------------------------------------- jobs

def _display_id(job_id, array_id, task):
    return f"{array_id}_{task}" if array_id and task is not None else str(job_id)


def job_from_squeue(j):
    state = (j.get("job_state") or ["UNKNOWN"])[0]
    job_id = num(j.get("job_id"))
    array_id = num(j.get("array_job_id"), 0) or None
    task = num(j.get("array_task_id"))
    if task is not None and math.isinf(task):
        task = None
    tres = tres_str(j.get("tres_alloc_str")) or tres_str(j.get("tres_req_str"))
    cpus = num(j.get("cpus")) or (int(tres["cpu"]) if tres.get("cpu", "").isdigit() else None)
    mem = mem_mb(tres.get("mem"))
    if mem is None:
        per_node, per_cpu = num(j.get("memory_per_node")), num(j.get("memory_per_cpu"))
        mem = per_node if per_node else (per_cpu * cpus if per_cpu and cpus else None)
    limit = num(j.get("time_limit"))
    job = {
        "id": _display_id(job_id, array_id, task), "job_id": job_id, "array_id": array_id, "task": task,
        "name": j.get("name") or "", "user": j.get("user_name") or "", "state": state,
        "reason": j.get("state_reason") or "", "cpus": cpus, "mem_mb": mem,
        "time_limit": None if limit is None or math.isinf(limit) else limit,
        "submit": num(j.get("submit_time")) or None, "start": num(j.get("start_time")) or None,
        "end": num(j.get("end_time")) or None, "nodes": j.get("nodes") or "",
        "workdir": j.get("current_working_directory") or "", "command": j.get("command") or "",
        "partition": j.get("partition") or "", "features": j.get("features") or "",
        "required_nodes": j.get("required_nodes") or "", "active": True, "exit": None, "signal": None,
        "array_tasks": j.get("array_task_string") or "" if task is None and array_id else "",
        "elapsed": None, "total_cpu": None, "max_rss": None, "script": None, "submit_line": "",
        "admin_note": j.get("admin_comment") or "",
    }
    job["stdout"] = expand_path(j.get("standard_output"), job)
    job["stderr"] = expand_path(j.get("standard_error"), job)
    if state in RUNNING_STATES and job["start"]:
        job["elapsed"] = max(0, int(time.time() - job["start"]))
    return job


def job_from_sacct(j):
    st = j.get("state") or {}
    state = (st.get("current") or ["UNKNOWN"])[0]
    arr = j.get("array") or {}
    array_id = num(arr.get("job_id"), 0) or None
    task = num(arr.get("task_id"))
    job_id = j.get("job_id")
    t = j.get("time") or {}
    tres = j.get("tres") or {}
    alloc, req = tres_list(tres.get("allocated")), tres_list(tres.get("requested"))
    exit_code = j.get("exit_code") or {}
    limit = num(t.get("limit"))
    steps = j.get("steps") or []
    peaks = [tres_list(((s.get("tres") or {}).get("requested") or {}).get("max")).get("mem") for s in steps]
    peaks = [p for p in peaks if isinstance(p, (int, float))]
    job = {
        "id": _display_id(job_id, array_id, task), "job_id": job_id, "array_id": array_id, "task": task,
        "name": j.get("name") or "", "user": j.get("user") or "", "state": state,
        "reason": st.get("reason") or "", "cpus": alloc.get("cpu") or req.get("cpu") or (j.get("required") or {}).get("CPUs"),
        "mem_mb": alloc.get("mem") or req.get("mem"),
        "time_limit": None if limit is None or math.isinf(limit) else limit,
        "submit": t.get("submission") or None, "start": t.get("start") or None, "end": t.get("end") or None,
        "nodes": j.get("nodes") if j.get("nodes") not in (None, "None assigned") else "",
        "workdir": j.get("working_directory") or "", "command": "", "partition": j.get("partition") or "",
        "features": j.get("constraints") or "", "required_nodes": "", "active": state in ACTIVE_STATES,
        "exit": num(exit_code.get("return_code")), "signal": num((exit_code.get("signal") or {}).get("id")),
        "elapsed": t.get("elapsed"), "total_cpu": _secs(t.get("total")), "max_rss": max(peaks) if peaks else None,
        "script": j.get("script") or None, "submit_line": j.get("submit_line") or "",
        # the admin's note on the job (the idle check writes why it cancelled a job here)
        "admin_note": (j.get("comment") or {}).get("administrator") or "" if isinstance(j.get("comment"), dict) else "",
    }
    job["stdout"] = expand_path(j.get("stdout_expanded") or j.get("stdout"), job)
    job["stderr"] = expand_path(j.get("stderr_expanded") or j.get("stderr"), job)
    if not job["mem_mb"] and (j.get("required") or {}):
        r = j["required"]
        per_node, per_cpu = num(r.get("memory_per_node")), num(r.get("memory_per_cpu"))
        job["mem_mb"] = per_node or (per_cpu * job["cpus"] if per_cpu and job["cpus"] else None)
    return job


def _sort_active(jobs):
    jobs.sort(key=lambda j: (j["state"] not in RUNNING_STATES, -(j["job_id"] or 0)))
    return jobs


def my_active_jobs(user):
    data = run_json(["squeue", "-u", user])
    jobs = [job_from_squeue(j) for j in data.get("jobs") or []]
    return _sort_active([j for j in jobs if j["state"] in ACTIVE_STATES and j["user"] in ("", user)])


def queue_counts():
    """Running and waiting jobs on the whole cluster (counts only, no names)."""
    out = run(["squeue", "-h", "-o", "%T"])
    c = collections.Counter(line.strip() for line in out.splitlines() if line.strip())
    return sum(c[s] for s in RUNNING_STATES), c["PENDING"]


def _since(days):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - days * 86400))


def my_jobs_since(user, days):
    """All of the user's jobs active at some point in the last `days` days (one line per job, no steps)."""
    data = run_json(["sacct", "-u", user, "-X", "-S", _since(days), "-E", "now"], timeout=40)
    return [job_from_sacct(j) for j in data.get("jobs") or []]


JOB_ID_RE = re.compile(r"^\d{1,10}(_\d{1,7})?$")


def job_detail(user, job_id):
    """The job as squeue sees it (if still active) merged with its accounting record. None if not the user's."""
    job = any_job(job_id)
    if job is None or job.get("user") not in (user, ""):
        return None
    return job


def any_job(job_id):
    """Like job_detail, for any user's job (the admin pages). None if Slurm does not know it."""
    if not JOB_ID_RE.match(job_id or ""):
        return None
    active = None
    try:
        jobs = [job_from_squeue(j) for j in run_json(["squeue", "-j", job_id]).get("jobs") or []]
        active = next((j for j in jobs if j["id"] == job_id or str(j["job_id"]) == job_id), None)
    except SlurmError:
        pass  # squeue says "Invalid job id" once the job has left the controller's memory
    record = None
    try:
        jobs = [job_from_sacct(j) for j in run_json(["sacct", "-j", job_id]).get("jobs") or []]
        record = next((j for j in jobs if j["id"] == job_id or str(j["job_id"]) == job_id), None)
    except SlurmError:
        if active is None:
            raise
    if active and active["state"] not in ACTIVE_STATES:
        active = None
    job = active or record
    if job is None:
        return None
    if active and record:  # keep live fields from squeue, add what only accounting knows
        for key in ("script", "submit_line", "total_cpu", "max_rss"):
            active[key] = record.get(key)
        for key in ("stdout", "stderr", "workdir", "admin_note"):
            active[key] = active[key] or record.get(key)
    return job


def job_script(job):
    """The batch script: from accounting if Slurm stores scripts there, else from the controller, which keeps it
    while the job runs and for a few minutes after it ends."""
    if job.get("script"):
        return job["script"]
    try:
        return run(["scontrol", "write", "batch_script", job["id"], "-"]) or None
    except SlurmError:
        return None


def job_usage(job_id):
    """Live usage of a running job from sstat: CPU seconds so far, current and peak memory (bytes)."""
    out = run(["sstat", "-n", "-P", "-a", "-j", job_id, "-o", "JobID,TRESUsageInTot,TRESUsageInMax"])
    cpu = 0.0
    mem_now = 0.0
    peak = 0.0
    seen = False
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        seen = True
        tot, mx = tres_str(parts[1]), tres_str(parts[2])
        cpu += seconds_of(tot.get("cpu")) or 0
        mem_now += bytes_of(tot.get("mem")) or 0
        peak = max(peak, bytes_of(mx.get("mem")) or 0)
    if not seen:
        return None
    return {"t": time.time(), "cpu_seconds": cpu, "mem_bytes": mem_now, "peak_bytes": max(peak, mem_now)}


def submit(script):
    out = run(["sbatch", "--parsable"], input_text=script, timeout=60)
    job_id = out.strip().split(";")[0]
    if not JOB_ID_RE.match(job_id):
        raise SlurmError(f"sbatch: unexpected answer {out.strip()[:80]!r}")
    return job_id


def cancel(job_id):
    if not JOB_ID_RE.match(job_id or ""):
        raise SlurmError("not a job id")
    run(["scancel", job_id])


# ---------------------------------------------------------------------------------------------------- cluster

def nodes():
    data = run_json(["scontrol", "show", "node"])
    result = []
    for n in data.get("nodes") or []:
        if PARTITION not in (n.get("partitions") or []):
            continue
        cpus = n.get("effective_cpus") or n.get("cpus") or 0
        alloc = n.get("alloc_cpus") or 0
        mem_total = (n.get("real_memory") or 0) - (n.get("specialized_memory") or 0)
        mem_alloc = n.get("alloc_memory") or 0
        state = [s.upper() for s in n.get("state") or []]
        load = num(n.get("cpu_load"))
        result.append({
            "name": n.get("name"), "cpus": cpus, "cpus_alloc": alloc, "cpus_free": max(cpus - alloc, 0),
            "threads": n.get("cpus") or cpus, "mem_mb": mem_total, "mem_alloc_mb": mem_alloc,
            "mem_free_mb": max(mem_total - mem_alloc, 0), "load": (load / 100.0) if load is not None else None,
            "features": n.get("features") or [], "state": state, "reason": n.get("reason") or "",
            "reason_by": n.get("reason_set_by_user") or "", "reason_at": num(n.get("reason_changed_at")) or None,
            "available": bool(state) and state[0] in ("IDLE", "MIXED", "ALLOCATED") and not
            {"DRAIN", "DOWN", "NOT_RESPONDING", "FAIL", "MAINTENANCE", "POWERED_DOWN", "REBOOT_REQUESTED"} & set(state),
            "weight": n.get("weight") or 0,
        })
    result.sort(key=lambda x: x["name"])
    return result


DEBUG_PARTITION = SETTINGS["debug_partition"]  # quick test jobs


def partition(name=PARTITION):
    data = run_json(["scontrol", "show", "partition", name])
    p = (data.get("partitions") or [{}])[0]
    d, m = p.get("defaults") or {}, p.get("maximums") or {}
    max_time, max_cpus = num(m.get("time")), num(m.get("cpus_per_node"))
    max_mem = num(m.get("partition_memory_per_node"))
    return {"name": p.get("name", name),
            "max_time": None if max_time is None or math.isinf(max_time) else max_time,
            "default_time": num(d.get("time")),
            "default_mem_per_cpu": num(d.get("partition_memory_per_cpu")) or None,
            "max_cpus_per_node": None if max_cpus is None or math.isinf(max_cpus) else max_cpus,
            "max_mem_per_node": max_mem or None}


def debug_partition():
    """Limits of the quick-test partition, or None if it does not exist."""
    try:
        return partition(DEBUG_PARTITION)
    except SlurmError:
        return None


def fairshare(user):
    out = run(["sshare", "-u", user, "-P", "-n", "-o", "User,FairShare"])
    for line in out.splitlines():
        name, _, value = line.strip().partition("|")
        if name == user:
            try:
                return float(value)
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------------------------------- admins
# The cluster admins (Unix group admin_group) have Slurm's Administrator level: Slurm refuses the actions
# below (other users' jobs, notes on jobs, node states) to everyone else.

ADMIN_ACTIONS = {"cancel": ["scancel"], "hold": ["scontrol", "hold"], "release": ["scontrol", "release"],
                 "requeue": ["scontrol", "requeue"]}


def all_active_jobs():
    data = run_json(["squeue"])
    return _sort_active([j for j in (job_from_squeue(j) for j in data.get("jobs") or []) if j["state"] in ACTIVE_STATES])


def set_admin_note(job_id, text):
    """The note users see on their job's page ("Note from the cluster"); Slurm keeps it in accounting too."""
    if not JOB_ID_RE.match(job_id or ""):
        raise SlurmError("not a job id")
    run(["scontrol", "update", f"JobId={job_id}", f"AdminComment={text}"])


def job_action(job_id, action):
    if not JOB_ID_RE.match(job_id or "") or action not in ADMIN_ACTIONS:
        raise SlurmError("not a job id or action")
    run(ADMIN_ACTIONS[action] + [job_id])


def update_node(name, state, reason=""):
    """state DRAIN (running jobs finish, no new ones start; needs a reason) or RESUME."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name or "") or state not in ("DRAIN", "RESUME"):
        raise SlurmError("not a node or state")
    run(["scontrol", "update", f"NodeName={name}", f"State={state}"] + ([f"Reason={reason}"] if reason else []))


def admin_level(user):
    """'Administrator', 'Operator' or 'None': the user's admin level in Slurm's accounting."""
    out = run(["sacctmgr", "-n", "-P", "show", "user", user, "format=AdminLevel"])
    return (out.strip().splitlines() or ["None"])[0].strip() or "None"


def fairshare_all():
    out = run(["sshare", "-a", "-P", "-n", "-o", "User,FairShare"])
    result = {}
    for line in out.splitlines():
        name, _, value = line.strip().partition("|")
        try:
            if name:
                result[name] = float(value)
        except ValueError:
            pass
    return result


def cpu_hours_by_user(days):
    """CPU-hours per user in the last `days` days (Slurm's accounting rollup; the current hour is not in it yet)."""
    out = run(["sreport", "-n", "-P", "-t", "Hours", "cluster", "AccountUtilizationByUser", f"Start={_since(days)}",
               "End=now", "format=Login,Used"], timeout=40)
    result = {}
    for line in out.splitlines():
        login, _, used = line.strip().partition("|")
        try:
            if login:
                result[login] = result.get(login, 0.0) + float(used)
        except ValueError:
            pass
    return result


def hostnames(text):
    """'cn[01,200]' or 'cn[01-02],gpu7' -> ['cn01', 'cn200'], ['cn01', 'cn02', 'gpu7'] (squeue's node lists)."""
    names = []
    for part in re.findall(r"[^,\[]+(?:\[[^\]]*\])?", text or ""):
        m = re.fullmatch(r"([^\[]+)\[([^\]]*)\]", part)
        if not m:
            names.append(part)
            continue
        for item in m.group(2).split(","):
            lo, _, hi = item.partition("-")
            if hi and lo.isdigit() and hi.isdigit():
                names += [f"{m.group(1)}{i:0{len(lo)}d}" for i in range(int(lo), int(hi) + 1)]
            else:
                names.append(m.group(1) + item)
    return names


# ---------------------------------------------------------------------------------------------------- files

def idle_status():
    """{'idle': {job id: {'since', 'cancel_at', 'warning'}}, 'cancelled': {job id: {'at', 'since'}}, 'rule': {...}}
    from the idle-job check, or None if it is not running."""
    try:
        with open(IDLE_STATUS) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if time.time() - (data.get("updated") or 0) > 3600:
        data["idle"] = {}  # stale: the check has stopped, so no warnings
    data.setdefault("idle", {})
    data.setdefault("cancelled", {})
    data["rule"] = dict(IDLE_RULE, **(data.get("rule") or {}))
    return data


def idle_rule_text(rule=None):
    r = dict(IDLE_RULE, **(rule or {}))
    return f"less than {r['cpu_threshold'] * 100:g}% of one CPU for {r['idle_minutes']} minutes"


def home_space(path):
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    return {"total": st.f_blocks * st.f_frsize, "free": st.f_bavail * st.f_frsize}


def tail(path, max_bytes=96 * 1024, max_lines=400):
    """Last lines of a job's output file, read as the user. Returns (text, size, problem)."""
    if not path:
        return None, 0, "no output file"
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None, 0, "not created yet"
    except PermissionError:
        return None, 0, "you cannot read this file"
    except OSError as e:
        return None, 0, e.strerror
    if not stat.S_ISREG(st.st_mode):
        return None, 0, "not a regular file"
    try:
        with open(path, "rb") as f:
            f.seek(max(0, st.st_size - max_bytes))
            data = f.read(max_bytes)
    except OSError as e:
        return None, st.st_size, e.strerror
    text = data.decode("utf-8", "replace")
    if st.st_size > max_bytes:
        text = text.split("\n", 1)[-1]
    # progress bars rewrite their line with \r: keep what the terminal would show
    lines = [line.rstrip("\r").rsplit("\r", 1)[-1] for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines[-max_lines:]), st.st_size, None


def conda_envs(home):
    """The user's conda environments (from ~/.conda/environments.txt) that still exist.
    Each: {'name', 'prefix', 'root'} where root holds etc/profile.d/conda.sh (None if unknown)."""
    try:
        with open(os.path.join(home, ".conda", "environments.txt")) as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    envs, seen = [], set()
    for line in lines:
        prefix = os.path.normpath(line.strip()) if line.strip() else ""
        if not prefix or prefix in seen or not os.path.isdir(os.path.join(prefix, "conda-meta")):
            continue
        seen.add(prefix)
        if os.path.isfile(os.path.join(prefix, "etc", "profile.d", "conda.sh")):
            name, root = "base", prefix
        elif os.path.basename(os.path.dirname(prefix)) == "envs":
            name, root = os.path.basename(prefix), os.path.dirname(os.path.dirname(prefix))
            if not os.path.isfile(os.path.join(root, "etc", "profile.d", "conda.sh")):
                root = None
        else:
            name, root = os.path.basename(prefix), None
        envs.append({"name": name, "prefix": prefix, "root": root})
    counts = collections.Counter(e["name"] for e in envs)
    for e in envs:  # names that appear twice (two conda installs) are offered by path
        e["label"] = e["name"] if counts[e["name"]] == 1 else e["prefix"]
    return envs
