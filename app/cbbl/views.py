"""The portal's pages. Each view takes the request and returns HTML text or a Response."""
import collections
import csv
import datetime
import io
import json
import math
import os
import time

from . import account as account_mod
from . import admin, jobform, slurm
from .config import SETTINGS, all_nodes, node_list
from .layout import badge, chip, empty, head, icon, meter, notice, page, stat_card, user_colour
from .response import Response, json_response, redirect
from .util import (fmt_bytes, fmt_duration, fmt_mb, fmt_minutes, fmt_num, fmt_time, h, limit_text, plural)

HISTORY_DAYS = (7, 30, 90, 365)


def attempt(problems, fn, *args, default=None):
    """Run one Slurm query; on failure note the problem and carry on with a default."""
    try:
        return fn(*args)
    except slurm.SlurmError as e:
        problems.append(str(e))
        return default


def problems_notice(problems):
    if not problems:
        return ""
    items = "".join(f"<li>{h(p)}</li>" for p in dict.fromkeys(problems))
    return notice("error", f"Some information could not be read from Slurm:<ul>{items}</ul>")


def card(title, body, cls="", extra=""):
    head_html = f'<div class="card-head">{title}</div>' if title else ""
    return f'<section class="card {cls}"{extra}>{head_html}{body}</section>'


def kv_rows(rows):
    return '<dl class="kv">' + "".join(f"<dt>{h(k)}</dt><dd>{v}</dd>" for k, v in rows) + "</dl>"


def usage_row(label, value_html, fraction, cls=""):
    return (f'<div class="usage"><div class="usage-top"><span>{h(label)}</span><span class="muted">{value_html}'
            f'</span></div>{meter(fraction, cls)}</div>')


def link(req, path):
    return h(req.base + path)


# ---------------------------------------------------------------------------------------------------- job tables

def offline_text():
    """'node2 has no Internet' (no_internet_nodes in the settings); empty if every node has it."""
    names = SETTINGS["no_internet_nodes"]
    return f"{node_list(names)} {'has' if len(names) == 1 else 'have'} no Internet" if names else ""


def job_time_cell(j, now):
    if j["state"] in slurm.RUNNING_STATES:
        used = fmt_duration(j["elapsed"])
        return f"{used} of {fmt_minutes(j['time_limit'])}" if j["time_limit"] else used
    return f"limit {fmt_minutes(j['time_limit'])}" if j["time_limit"] else "—"


def job_where_cell(j, now):
    if j["state"] == "PENDING":
        why = slurm.reason_text(j["reason"]) or "waiting"
        return f'<span class="muted">{h(why)}</span>'
    return h(j["nodes"] or "—")


def job_start_cell(j, now):
    if j["state"] == "PENDING":
        if j["start"] and j["start"] > now:
            return f'<span class="muted">est. {h(fmt_time(j["start"]))}</span>'
        return '<span class="muted">—</span>'
    return h(fmt_time(j["start"]))


def idle_badge(j, idle):
    info = (idle or {}).get("idle", {}).get(j["id"])
    if not info or not info.get("warning"):
        return ""
    return (f' <span class="badge b-wait" title="Used almost no CPU since {h(fmt_clock(info["since"]))}: cancelled at '
            f'{h(fmt_clock(info["cancel_at"]))} unless it uses CPU">Idle</span>')


def idle_rule_short(rule):
    r = dict(slurm.IDLE_RULE, **(rule or {}))
    return (f"cancelled after {r['idle_minutes']} min under {r['cpu_threshold'] * 100:g}% CPU"
            + (f" ({r['min_cpus']}+ CPUs)" if r["min_cpus"] > 1 else ""))


def fmt_clock(epoch):
    return time.strftime("%H:%M", time.localtime(epoch)) if epoch else "—"


def jobs_table(req, jobs, idle=None):
    now = time.time()
    rows = []
    for j in jobs:
        label, style = slurm.state_label(j["state"])
        url = link(req, f"/job/{j['id']}")
        tasks = f' <span class="muted">(array tasks {h(j["array_tasks"])})</span>' if j.get("array_tasks") else ""
        rows.append(
            f'<tr data-href="{url}"><td class="mono"><a href="{url}">{h(j["id"])}</a></td>'
            f'<td class="name">{h(j["name"])}{tasks}</td><td>{badge(label, style)}{idle_badge(j, idle)}</td>'
            f'<td>{h(j["cpus"] or "—")}</td>'
            f'<td>{h(fmt_mb(j["mem_mb"]))}</td><td>{job_where_cell(j, now)}</td><td>{h(job_time_cell(j, now))}</td>'
            f'<td>{job_start_cell(j, now)}</td></tr>')
    return ('<div class="table-wrap"><table class="table clickable"><thead><tr><th>ID</th><th>Name</th><th>State</th>'
            '<th>CPUs</th><th>Memory</th><th>Node</th><th>Time</th><th>Start</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table></div>")


# ---------------------------------------------------------------------------------------------------- overview

def overview(req):
    problems = []
    jobs = attempt(problems, slurm.my_active_jobs, req.user, default=[])
    nodes = attempt(problems, slurm.nodes, default=[])
    part = attempt(problems, slurm.partition, default={}) or {}
    running_all, waiting_all = attempt(problems, slurm.queue_counts, default=(0, 0))
    fairshare = attempt(problems, slurm.fairshare, req.user, default=None)
    space = slurm.home_space(req.home)
    idle = slurm.idle_status()

    running = [j for j in jobs if j["state"] in slurm.RUNNING_STATES]
    waiting = [j for j in jobs if j["state"] == "PENDING"]
    cpus_used = sum(j["cpus"] or 0 for j in running)
    mem_used = sum(j["mem_mb"] or 0 for j in running)
    total_cpus = sum(n["cpus"] for n in nodes)
    total_mem = sum(n["mem_mb"] for n in nodes)
    biggest = max(nodes, key=lambda n: (n["cpus"], n["mem_mb"]), default=None)

    cards = "".join([
        stat_card("Running jobs", str(len(running)), f"{len(waiting)} waiting", "list"),
        stat_card("CPUs in use", str(cpus_used), f"{h(fmt_mb(mem_used))} RAM reserved", "cpu"),
        stat_card("Per job", f"{biggest['cpus']} CPUs" if biggest else "—",
                  f"max {h(fmt_mb(biggest['mem_mb']))} RAM ({h(biggest['name'])})" if biggest else "", "memory"),
        stat_card("Max run time", h(fmt_minutes(part.get("max_time"))) if part.get("max_time") else "no limit",
                  f"default {h(fmt_minutes(part.get('default_time')))} · partition {h(part.get('name', 'cpu'))}"
                  if part else "", "clock"),
    ])

    usage = "".join([
        '<p class="muted small">No per-user limits: one job may use a whole node.</p>',
        usage_row("CPUs in use (all your jobs)", f"{cpus_used} / {total_cpus}",
                  cpus_used / total_cpus if total_cpus else 0),
        usage_row("RAM reserved (all your jobs)", f"{h(fmt_mb(mem_used))} / {h(fmt_mb(total_mem))}",
                  mem_used / total_mem if total_mem else 0),
        usage_row("Running jobs", f"{len(running)} of {running_all} on the cluster",
                  len(running) / running_all if running_all else 0),
        usage_row("Waiting jobs", f"{len(waiting)} of {waiting_all} in the queue",
                  len(waiting) / waiting_all if waiting_all else 0),
        usage_row("Fair-share factor", h(fmt_num(fairshare, 2)) if fairshare is not None else "—", fairshare or 0),
        '<p class="muted small">Fair share: 1 means little recent use. When jobs have to wait, users with a higher '
        "factor start first.</p>",
    ])

    internet = [n["name"] for n in nodes if "internet" in n["features"]]
    offline = [n["name"] for n in nodes if "offline" in n["features"]]
    defaults = []
    if part.get("default_mem_per_cpu"):
        defaults.append(f"{fmt_mb(part['default_mem_per_cpu'])} per CPU")
    if part.get("default_time"):
        defaults.append(fmt_minutes(part["default_time"]))
    quick = slurm.debug_partition()
    side = kv_rows([
        ("Quick test jobs", f'<a href="{link(req, "/new")}">on New job</a> · partition debug' if quick else
         '<span class="muted">not set up</span>'),
        ("Max length", h(fmt_minutes(quick["max_time"])) if quick else "—"),
        ("Size", h(f"up to {quick.get('max_cpus_per_node') or '?'} CPUs, {fmt_mb(quick.get('max_mem_per_node'))}")
         if quick else "—"),
        ("Idle jobs", h(idle_rule_short((idle or {}).get("rule")))),
        ("Default job", h(" · ".join(["1 CPU"] + defaults))),
        ("Internet", h(", ".join(internet)) + (f' <span class="muted">(not {h(", ".join(offline))})</span>'
                                               if offline else "")),
        ("Home folder", f'<span class="mono">{h(req.home)}</span>'),
        ("Free in /home", f"{h(fmt_bytes(space['free']))} of {h(fmt_bytes(space['total']))}" if space else "—"),
        ("Disk quota (/home)", "not enabled yet"),
    ])

    active = jobs_table(req, jobs, idle) if jobs else empty("No running or waiting jobs.")
    body = "".join([
        head(f"Hello, {req.user}", "Your limits, usage and jobs at a glance.",
             f'<a class="btn btn-primary" href="{link(req, "/new")}">{icon("plus")}New job</a>'),
        problems_notice(problems),
        f'<div class="grid stats">{cards}</div>',
        f'<div class="grid two-one stretch">{card("My limits and current usage", usage)}{card("Quick tests &amp; disk", side)}</div>',
        card("Active jobs", f'<div data-refresh="{link(req, "/jobs?partial=1")}" data-interval="15000">{active}</div>'),
    ])
    return page(req, "overview", "Overview", body)


# ---------------------------------------------------------------------------------------------------- my jobs

def my_jobs(req):
    problems = []
    jobs = attempt(problems, slurm.my_active_jobs, req.user, default=[])
    idle = slurm.idle_status()
    warned = [j["id"] for j in jobs if (idle or {}).get("idle", {}).get(j["id"], {}).get("warning")]
    note = ""
    if warned:
        r = idle["rule"]
        note = notice("warn", f"Idle: {h(', '.join(warned))}. These jobs have used less than "
                              f"{r['cpu_threshold'] * 100:g}% of one CPU for over {r['warn_minutes']} minutes and are "
                              f"cancelled after {r['idle_minutes']} idle minutes unless they use CPU again.")
    inner = (problems_notice(problems) + note
             + (jobs_table(req, jobs, idle) if jobs else empty("No running or waiting jobs.")))
    if req.partial:
        return inner
    body = "".join([
        head("My jobs", "Running and waiting jobs. Click a job for details, charts and logs.",
             f'<a class="btn" href="{link(req, "/new")}">{icon("plus")}New job</a>'),
        card("", f'<div data-refresh="{link(req, "/jobs?partial=1")}" data-interval="10000">{inner}</div>', "flush"),
        f'<p class="muted small after">Finished jobs are in <a href="{link(req, "/history")}">History &amp; stats</a>.</p>',
    ])
    return page(req, "jobs", "My jobs", body)


# ---------------------------------------------------------------------------------------------------- one job

def _efficiency_rows(job):
    """CPU and memory use of a finished job, with advice like Slurm's seff."""
    rows, tips = [], []
    cpus, elapsed = job.get("cpus") or 0, job.get("elapsed") or 0
    if job.get("total_cpu") is not None and cpus and elapsed:
        eff = job["total_cpu"] / (cpus * elapsed)
        busy = eff * cpus
        rows.append(usage_row("CPU use (average)", f"{fmt_num(busy, 1)} of {cpus} CPUs busy ({eff * 100:.0f}%)", eff))
        if cpus > 1 and eff < 0.5 and elapsed > 300:
            tips.append(f"It kept about {max(1, round(busy))} of its {cpus} CPUs busy: next time ask for fewer "
                        f"(-c {max(1, round(busy))}), so it starts sooner and leaves room for others.")
    mem = job.get("mem_mb")
    if job.get("max_rss") is not None and mem:
        frac = job["max_rss"] / (mem * 1024 * 1024)
        rows.append(usage_row("Memory (peak)", f"{h(fmt_bytes(job['max_rss']))} of {h(fmt_mb(mem))} ({frac * 100:.0f}%)",
                              frac, "bad" if job["state"] == "OUT_OF_MEMORY" else ""))
        if job["state"] == "OUT_OF_MEMORY":
            tips.append("The job needed more memory than it asked for and was stopped: ask for more (RAM).")
        elif frac < 0.4 and mem >= 8 * 1024 and elapsed > 300:
            need = max(1, math.ceil(job["max_rss"] / 1024 ** 3 * 1.3))
            tips.append(f"Peak memory was {fmt_bytes(job['max_rss'])}: about {need} GB would be enough next time.")
    elif job["state"] == "OUT_OF_MEMORY":
        tips.append("The job needed more memory than it asked for and was stopped: ask for more (RAM).")
    return rows, tips


def job_page(req, job_id, message=""):
    try:
        job = slurm.job_detail(req.user, job_id)
    except slurm.SlurmError as e:
        return page(req, "jobs", f"Job {job_id}", head(f"Job {job_id}", "") + notice("error", h(str(e))))
    if job is None:
        return not_found(req, f"Job {job_id} is not one of your jobs (or Slurm no longer knows it).")
    now = time.time()
    label, style = slurm.state_label(job["state"])
    running = job["state"] in slurm.RUNNING_STATES
    msgs = ""
    if req.arg("submitted"):
        msgs += notice("ok", f"Job {h(job['id'])} submitted.")
    if req.arg("cancelled"):
        msgs += notice("ok", "Cancel request sent to Slurm.")
    if message:
        msgs += notice("error", h(message))
    idle = slurm.idle_status() or {}
    info = idle.get("idle", {}).get(job["id"])
    if job["active"] and info and info.get("warning"):
        when = "would be cancelled (the idle check is in test mode)" if idle.get("mode") == "report" else "will be cancelled"
        msgs += notice("warn", f"This job has used less than {idle['rule']['cpu_threshold'] * 100:g}% of one CPU since "
                               f"{h(fmt_clock(info['since']))}. It {when} at <b>{h(fmt_clock(info['cancel_at']))}</b> "
                               "unless it uses CPU again.")
    done = idle.get("cancelled", {}).get(job["id"])
    if job.get("admin_note"):   # the idle check's, or an admin's (cancel, hold, requeue)
        msgs += notice("info", f"Note from the cluster: {h(job['admin_note'])}")
    elif done and not job["active"]:
        msgs += notice("info", f"Cancelled by the idle check at {h(fmt_clock(done['at']))}: it used "
                               f"{h(slurm.idle_rule_text(idle.get('rule')))}.")

    actions = f'<a class="btn" href="{link(req, "/jobs")}">{icon("back")}My jobs</a>'
    if job["active"]:
        actions = (f'<form method="post" action="{link(req, "/job/" + job["id"] + "/cancel")}" '
                   f'data-confirm="Cancel job {h(job["id"])}?">'
                   f'<button class="btn btn-danger" type="submit">{icon("x")}Cancel job</button></form>' + actions)
    where = f" · on {h(job['nodes'])}" if job["nodes"] else ""
    subtitle = f'{badge(label, style)} <span class="job-name">{h(job["name"])}</span>{where}'

    # usage
    if running:
        cpus, mem = job["cpus"] or 1, job["mem_mb"] or 0
        usage_body = (
            f'<div class="live" data-usage="{link(req, "/job/" + job["id"] + "/usage.json")}" data-cpus="{h(cpus)}" '
            f'data-mem-mb="{h(mem)}">'
            '<div class="usage-top"><span>CPU use</span><span class="muted" data-cpu-text>measuring…</span></div>'
            f'{meter(0, "cpu-meter")}'
            '<div class="usage-top"><span>Memory</span><span class="muted" data-mem-text>measuring…</span></div>'
            f'{meter(0, "mem-meter")}'
            '<svg class="spark" viewBox="0 0 600 120" preserveAspectRatio="none" aria-label="CPU and memory over time">'
            '<polyline class="spark-cpu" points=""/><polyline class="spark-mem" points=""/></svg>'
            '<p class="muted small"><span class="key key-cpu"></span>CPU use <span class="key key-mem"></span>memory · '
            "measured every 30 s while this page is open</p></div>")
    elif job["state"] == "PENDING":
        why = slurm.reason_text(job["reason"]) or "waiting for its turn"
        est = (f" Estimated start: <b>{h(fmt_time(job['start']))}</b>." if job["start"] and job["start"] > now
               else "")
        usage_body = f'<p>Not started yet: {h(why)}.{est}</p>'
    else:
        rows, tips = _efficiency_rows(job)
        usage_body = "".join(rows) or '<p class="muted">No usage was recorded for this job.</p>'
        usage_body += "".join(f'<p class="tip">{icon("info")}<span>{h(t)}</span></p>' for t in tips)

    # output files
    outputs = [("Output", job["stdout"])]
    if job["stderr"] and job["stderr"] != job["stdout"]:
        outputs.append(("Errors (stderr)", job["stderr"]))
    log_cards = ""
    for i, (title, path) in enumerate(outputs):
        stream = "out" if i == 0 else "err"
        log_url = link(req, f"/job/{job['id']}/log?stream={stream}")
        refresh = f' data-refresh="{log_url}" data-interval="10000" data-scroll="end"' if job["active"] else ""
        log_cards += card(f'{h(title)} <span class="mono muted path">{h(path or "—")}</span>',
                          f'<pre class="log"{refresh}>{log_html(path, job)}</pre>', "log-card")

    details = job_details(job)
    if req.arg("part") == "details":
        return details
    if job["active"]:
        details = (f'<div data-refresh="{link(req, "/job/" + job["id"] + "?part=details")}" data-interval="15000">'
                   f'{details}</div>')
    script = slurm.job_script(job)
    script_card = card("Batch script", f'<pre class="code script">{h(script)}</pre>' if script else
                       '<p class="muted">Not available: Slurm keeps a job\'s script only until a few minutes after '
                       "it ends.</p>")

    usage_title = "Usage" if running or job["state"] == "PENDING" else "Usage and efficiency"
    watch = (f'<span hidden data-job-watch="{link(req, "/job/" + job["id"] + "/state.json")}" '
             f'data-state="{h(job["state"])}"></span>' if job["active"] else "")
    body = "".join([
        head(f"Job {job['id']}", subtitle, actions), watch, msgs,
        '<div class="grid two-one"><div class="stack">',
        card(usage_title, usage_body), log_cards,
        '</div><div class="stack">', card("Details", details), script_card, "</div></div>",
    ])
    return page(req, "jobs", f"Job {job['id']}", body)


def job_details(job):
    label, style = slurm.state_label(job["state"])
    exit_text = "—"
    if not job["active"]:
        exit_text = str(job["exit"]) if job["exit"] is not None else "—"
        if job["signal"]:
            exit_text = f"{job['exit'] or 0}:{job['signal']}"
    return kv_rows([
        ("State", badge(label, style)),
        ("Node", h(job["nodes"] or "—")),
        ("CPUs", h(job["cpus"] or "—")),
        ("Memory", h(fmt_mb(job["mem_mb"]))),
        ("Time limit", h(fmt_minutes(job["time_limit"]))),
        ("Run time", h(fmt_duration(job["elapsed"])) if job["elapsed"] is not None else "—"),
        ("Submitted", h(fmt_time(job["submit"]))),
        ("Started", h(fmt_time(job["start"])) if job["state"] != "PENDING" else
         (f'<span class="muted">est. {h(fmt_time(job["start"]))}</span>' if job["start"] else "—")),
        ("Ended", h(fmt_time(job["end"])) if not job["active"] else "—"),
        ("Exit code", h(exit_text)),
        ("Partition", h(job["partition"] or "—")),
        ("Working directory", f'<span class="mono">{h(job["workdir"] or "—")}</span>'),
    ])


def log_html(path, job):
    if not path:
        return '<span class="muted">This job has no output file.</span>'
    if path == "/dev/null":
        return '<span class="muted">This job\'s output was discarded (/dev/null).</span>'
    text, size, problem = slurm.tail(path)
    if problem == "not created yet" and not job["active"]:
        problem = "not found (moved or deleted?)"
    if problem:
        return f'<span class="muted">Output file {h(problem)}.</span>'
    if not text:
        return '<span class="muted">(empty so far)</span>' if job["active"] else '<span class="muted">(empty)</span>'
    note = ""
    if size > 96 * 1024:
        note = f'<span class="muted">… showing the end of a {h(fmt_bytes(size))} file</span>\n'
    return note + h(text)


def job_log(req, job_id):
    job = slurm.job_detail(req.user, job_id)
    if job is None:
        return Response("", 404, "text/plain")
    path = job["stderr"] if req.arg("stream") == "err" and job["stderr"] else job["stdout"]
    return log_html(path, job)


def job_state(req, job_id):
    """What the job page polls to notice that its job started or ended."""
    job = slurm.job_detail(req.user, job_id)
    if job is None:
        return json_response({"error": "not found"}, 404)
    return json_response({"state": job["state"], "active": job["active"], "label": slurm.state_label(job["state"])[0]})


def job_usage(req, job_id):
    job = slurm.job_detail(req.user, job_id)
    if job is None:
        return json_response({"error": "not found"}, 404)
    data = {"state": job["state"], "running": job["state"] in slurm.RUNNING_STATES, "cpus": job["cpus"],
            "mem_mb": job["mem_mb"], "elapsed": job["elapsed"]}
    if data["running"]:
        try:
            data.update(slurm.job_usage(job["id"]) or {})
        except slurm.SlurmError as e:
            data["error"] = str(e)
    return json_response(data)


def job_cancel(req, job_id):
    job = slurm.job_detail(req.user, job_id)
    if job is None:
        return not_found(req, "Not one of your jobs.")
    try:
        slurm.cancel(job["id"])
    except slurm.SlurmError as e:
        return job_page(req, job["id"], message=str(e))
    return redirect(req.base + f"/job/{job['id']}?cancelled=1")


# ---------------------------------------------------------------------------------------------------- new job

def _form_context(req, problems):
    nodes = attempt(problems, slurm.nodes, default=[])
    part = attempt(problems, slurm.partition, default={}) or {}
    part["debug"] = slurm.debug_partition()
    envs = slurm.conda_envs(req.home)
    return nodes, part, envs


def _field(name, label, hint, errors, control):
    err = errors.get(name)
    return (f'<div class="field{" has-error" if err else ""}" data-field="{name}"><label for="f-{name}">{h(label)}'
            f'</label>{control}<div class="hint">{h(hint)}</div><div class="error-text" data-error-for="{name}">'
            f'{h(err or "")}</div></div>')


def new_job(req, values=None, errors=None, message="", preview=None):
    problems = []
    nodes, part, envs = _form_context(req, problems)
    # The form starts with the partition's defaults; RAM and job name then follow the CPUs and the command until the
    # user types values of their own (see jobform.parse).
    v = {"cpus": "1", "time": limit_text(part["default_time"]) if part.get("default_time") else "", "node": "any",
         "workdir": req.home}
    v.update(values or {})
    errors = errors or {}
    spec, _ = jobform.parse(v, req.home, nodes, part, envs)
    v["mem_default"] = jobform.gb_text(spec["mem_auto_mb"])
    if spec["mem_auto"]:
        v["mem"] = v["mem_default"]
    v["name_default"] = spec["name_derived"]
    if spec["name_auto"]:
        v["name"] = spec["name_derived"]
    biggest = max(nodes, key=lambda n: n["cpus"], default=None)
    max_mem = max((n["mem_mb"] for n in nodes), default=0)
    inp = lambda name, ph="", typ="text", extra="": (  # noqa: E731
        f'<input id="f-{name}" name="{name}" type="{typ}" value="{h(v.get(name, ""))}" placeholder="{h(ph)}" '
        f'autocomplete="off" spellcheck="false"{extra}>')
    options = "".join(f'<option value="{h(val)}"{" selected" if v.get("node", "any") == val else ""}>{h(lab)}</option>'
                      for val, lab in jobform.node_choices(nodes))
    env_options = '<option value="">none</option>' + "".join(
        f'<option value="{h(e["label"])}"{" selected" if v.get("conda") == e["label"] else ""}>{h(e["label"])}</option>'
        for e in envs)
    per_cpu = fmt_mb(part["default_mem_per_cpu"]) + " per CPU unless you change it · " \
        if part.get("default_mem_per_cpu") else ""
    fields = "".join([
        _field("cpus", "CPUs (threads)", f"up to {biggest['cpus'] if biggest else '?'} on one node",
               errors, inp("cpus", "1", "text", ' inputmode="numeric"')),
        _field("mem", "RAM (GB)", f"{per_cpu}up to {fmt_mb(max_mem)}", errors,
               inp("mem", "", "text", ' inputmode="decimal"')),
        _field("time", "Time limit", f"e.g. 30m, 12h, 2d · up to {fmt_minutes(part.get('max_time'))}", errors,
               inp("time", "")),
        _field("node", "Node", offline_text(), errors,
               f'<select id="f-node" name="node">{options}</select>'),
        _field("name", "Job name", "from the command unless you change it", errors, inp("name", "")),
        _field("conda", "Conda environment", f"{plural(len(envs), 'environment')} in ~/.conda/environments.txt"
               if envs else "none found in ~/.conda/environments.txt", errors,
               f'<select id="f-conda" name="conda">{env_options}</select>'),
    ])
    quick = part.get("debug")
    quick_row = ""
    if quick:
        checked = " checked" if v.get("quick") else ""
        quick_row = (f'<label class="check" data-field="quick"><input type="checkbox" name="quick" value="1"{checked} '
                     f'data-quick-time="{h(limit_text(quick["max_time"] or 10))}">'
                     f'<span>Quick test: up to {h(quick["max_time"] or 10)} minutes, '
                     f'{h(quick.get("max_cpus_per_node") or "?")} CPUs and {h(fmt_mb(quick.get("max_mem_per_node")))};'
                     f' starts before other jobs</span></label>'
                     f'<div class="error-text" data-error-for="quick">{h(errors.get("quick", ""))}</div>')
    hidden = (f'<input type="hidden" name="mem_default" value="{h(v["mem_default"])}">'
              f'<input type="hidden" name="name_default" value="{h(v["name_default"])}">')
    workdir = _field("workdir", "Working directory", "the job starts here; output goes to slurm-<job id>.out",
                     errors, inp("workdir", req.home))
    command = (f'<div class="field wide{" has-error" if errors.get("command") else ""}" data-field="command">'
               f'<label for="f-command">Command</label><textarea id="f-command" name="command" rows="4" '
               f'spellcheck="false" placeholder="python train.py">{h(v.get("command", ""))}</textarea>'
               f'<div class="error-text" data-error-for="command">{h(errors.get("command", ""))}</div></div>')
    cmd_text = jobform.command_line(spec, req.home)
    script_text = preview or ""
    form = (f'<form class="card job-form" method="post" action="{link(req, "/new")}" data-preview-url="'
            f'{link(req, "/new/preview")}"><div class="card-head">Resources</div><div class="form-grid">{fields}'
            f'{workdir}</div>{quick_row}{hidden}{command}<div class="form-actions"><button class="btn btn-primary" type="submit" '
            f'name="action" value="submit">{icon("send")}Submit</button><button class="btn" type="submit" '
            f'name="action" value="preview" data-preview>Preview batch script</button></div></form>')
    script_card = (f'<section class="card script-card" id="script-card"{"" if script_text else " hidden"}>'
                   f'<div class="card-head">Batch script <span class="muted small">what Submit sends to Slurm</span>'
                   f'</div><pre class="code" id="script-text">{h(script_text)}</pre></section>')
    idle_rule = dict(slurm.IDLE_RULE, **((slurm.idle_status() or {}).get("rule") or {}))
    right = "".join([
        card("Equivalent command", f'<pre class="code" id="cmd-text">{h(cmd_text)}</pre><button class="btn btn-sm" '
             f'type="button" data-copy="#cmd-text">{icon("copy")}Copy</button>'),
        f'<section class="card info">{icon("info", "ic info-ic")}<div><b>Memory is enforced</b><p>A job that uses '
        "more memory than it asked for is stopped (state “Out of memory”). Each job runs only on its own CPU "
        "threads: 8 CPUs means 8 threads.</p></div></section>",
        f'<section class="card info">{icon("info", "ic info-ic")}<div><b>Idle jobs are cancelled</b><p>A job'
        + (f" with {idle_rule['min_cpus']} or more CPUs" if idle_rule["min_cpus"] > 1 else "")
        + f" that uses {h(slurm.idle_rule_text(idle_rule))} is cancelled. Its page shows a warning "
        f"{idle_rule['idle_minutes'] - idle_rule['warn_minutes']} minutes before."
        + (" Jobs with 1 CPU, such as a pipeline's main job, are not cancelled." if idle_rule["min_cpus"] == 2 else
           " Smaller jobs are not cancelled." if idle_rule["min_cpus"] > 2 else
           " Run a pipeline's main process (Nextflow, Snakemake), which waits for its steps, on "
           f"{SETTINGS['login_node']} in screen or "
           "tmux, not as a job.")
        + "</p></div></section>",
        f'<section class="card info">{icon("info", "ic info-ic")}<div><b>{h(offline_text())}</b><p>Jobs that '
        "download anything (pip, conda, datasets, web APIs) need <i>needs Internet</i>; otherwise they usually "
        f"run on {h(node_list(SETTINGS['no_internet_nodes']))} and fail.</p></div></section>"
        if SETTINGS["no_internet_nodes"] else "",
    ])
    msg = notice("error", h(message)) if message else ""
    body = "".join([
        head("New job", "Fill in the form, or copy the equivalent sbatch command and run it in a terminal on any node."),
        problems_notice(problems), msg,
        f'<div class="grid two-one"><div class="stack">{form}{script_card}</div><div class="stack">{right}</div></div>',
    ])
    return page(req, "new", "New job", body)


def new_job_post(req):
    problems = []
    nodes, part, envs = _form_context(req, problems)
    values = {k: req.form.get(k, "") for k in jobform.FIELDS}
    spec, errors = jobform.parse(values, req.home, nodes, part, envs)
    if problems and not nodes:
        return new_job(req, values, errors, "Slurm did not answer: " + "; ".join(problems))
    if req.form.get("action") == "preview":
        return new_job(req, values, errors, preview=jobform.script(spec, req.home, req.user) if not errors else "")
    if errors:
        return new_job(req, values, errors, "Please correct the fields marked in red.")
    try:
        job_id = slurm.submit(jobform.script(spec, req.home, req.user))
    except slurm.SlurmError as e:
        return new_job(req, values, errors, str(e))
    return redirect(req.base + f"/job/{job_id}?submitted=1")


def new_job_preview(req):
    problems = []
    nodes, part, envs = _form_context(req, problems)
    values = {k: req.form.get(k, "") for k in jobform.FIELDS}
    spec, errors = jobform.parse(values, req.home, nodes, part, envs)
    return json_response({
        "command": jobform.command_line(spec, req.home),
        "script": jobform.script(spec, req.home, req.user),
        "errors": errors, "problems": problems,
        "mem_default": jobform.gb_text(spec["mem_auto_mb"]), "name_default": spec["name_derived"],
    })


# ---------------------------------------------------------------------------------------------------- timeline

PX_PER_HOUR = 112
LANE = 30
MAX_BARS = 2000
RANGES = [("24h", "last 24 h", 1), ("48h", "last 48 h", 2), ("7d", "last 7 days", 7)]
VIEWS = [("all", "All nodes"), ("mine", "My jobs")]
TL_LABEL = 116   # width of the All nodes view's row labels (px; portal.css .tl-all .tl-label)


def timeline_bands(req, problems, now, mine=True):
    """Shaded behind the jobs: maintenance windows (no jobs) and, on My jobs, the user's reservations (also those others
    asked for with them, or an admin made) and their requests still waiting for an admin. Not other people's
    reservations."""
    bands = []
    for r in attempt(problems, slurm.reservations, default=[]):
        if not r["start"]:
            continue
        end = r["end"] or r["start"] + 365 * 86400
        what = f"{r['cores']} CPUs of {r['nodes']}" if r["cores"] and r["name"].startswith("prio-") else r["nodes"]
        if "MAINT" in r["flags"]:
            bands.append({"start": r["start"], "end": end, "kind": "maint", "label": f"Maintenance: no jobs on {r['nodes']}"})
        elif mine and req.user in r["users"]:
            bands.append({"start": r["start"], "end": end, "kind": "res", "label": f"Your reservation: {what}"})
    if mine:
        info = admin.helper("requests-mine")
        if not info.get("ok"):
            problems.append("Your reservation requests: " + info.get("error", "no answer"))
        for q in info.get("requests") or []:
            if q["state"] == "pending":
                start = now if q.get("length") else q["start"]
                bands.append({"start": start, "end": start + q["length"] if q.get("length") else q["until"],
                              "kind": "req", "label": f"Requested: {q['cores']} CPUs of {q['node']} (waiting for an admin)"})
    return [b for b in bands if b["end"] > now - 7 * 86400]


def timeline_bar(j, now, t0):
    """Where a job sits on the time axis: running jobs up to now (and their time limit after it), waiting jobs from
    their estimated start, finished ones as they ran. None if it is not on the chart."""
    label, style = slurm.state_label(j["state"])
    plan_end = None
    if j["state"] == "PENDING":
        start = j["start"] if j["start"] and j["start"] > now else now
        end = start + (j["time_limit"] or 60) * 60
        kind = "wait"
    elif j["state"] in slurm.RUNNING_STATES or (j["active"] and j["start"]):
        start, end, kind = j["start"] or now, now, "run"
        if j["time_limit"]:
            plan_end = start + j["time_limit"] * 60
    elif j["start"]:
        start, end, kind = j["start"], max(j["end"] or j["start"], j["start"]), style
    else:
        return None  # cancelled before it started
    if end < t0:
        return None
    return {"job": j, "start": max(start, t0), "end": end, "plan_end": plan_end, "kind": kind, "label": label}


def pack_lanes(bars, px):
    """Put each bar in the first lane that is free at its start; returns the number of lanes."""
    lanes = []
    gap = 8 / px
    for b in sorted(bars, key=lambda b: b["start"]):
        right = max(b["end"], b["plan_end"] or 0, b["start"] + 60 / px)
        for i, last in enumerate(lanes):
            if last + gap <= b["start"]:
                lanes[i] = right
                b["lane"] = i
                break
        else:
            lanes.append(right)
            b["lane"] = len(lanes) - 1
    return len(lanes)


def time_axis(t0, t1, px):
    """The axis' day and hour labels, and the lines at midnight to draw behind the bars."""
    days, hours, majors = [], [], []
    t = t0
    while t < t1:
        lt = time.localtime(t)
        x = int((t - t0) * px)
        if lt.tm_hour == 0 or t == t0:
            day_end = min(t1, t + (24 - lt.tm_hour) * 3600)
            days.append(f'<div class="tl-day" style="left:{x}px;width:{int((day_end - t) * px)}px">'
                        f'<span>{h(time.strftime("%a %d %B", lt))}</span></div>')
        if lt.tm_hour == 0 and t != t0:
            majors.append(f'<div class="tl-major" style="left:{x}px"></div>')
        hours.append(f'<span class="tl-hour" style="left:{x}px">{lt.tm_hour:02d}:00</span>')
        t += 3600
    head_html = f'<div class="tl-days">{"".join(days)}</div><div class="tl-hours">{"".join(hours)}</div>'
    return head_html, "".join(majors)


def band_html(b, t0, t1, px, label=True):
    left, right = int((max(b["start"], t0) - t0) * px), int((min(b["end"], t1) - t0) * px)
    tip = f'{b["label"]} · {fmt_time(b["start"])} – {fmt_time(b["end"])}'
    return (f'<div class="tl-band b-{b["kind"]}" style="left:{left}px;width:{max(2, right - left)}px" '
            f'title="{h(tip)}">' + (f'<span>{h(b["label"])}</span>' if label else "") + "</div>")


def coming_up(bands, t1):
    later = sorted((b for b in bands if b["start"] >= t1), key=lambda b: b["start"])
    if not later:
        return ""
    return ('<p class="muted small">Coming up: ' + " · ".join(
        f'{h(b["label"])}, {h(fmt_time(b["start"]))} – {h(fmt_time(b["end"]))}' for b in later) + "</p>")


def timeline_mine(req, now, days, problems):
    jobs = {j["id"]: j for j in attempt(problems, slurm.my_jobs_since, req.user, days, default=[])}
    for j in attempt(problems, slurm.my_active_jobs, req.user, default=[]):
        jobs[j["id"]] = j  # squeue knows when waiting jobs are expected to start
    t0 = (int(now - days * 86400) // 3600) * 3600
    bars = [b for b in (timeline_bar(j, now, t0) for j in jobs.values()) if b]
    dropped = 0
    if len(bars) > MAX_BARS:
        bars.sort(key=lambda b: b["start"], reverse=True)
        dropped, bars = len(bars) - MAX_BARS, bars[:MAX_BARS]
    bands = timeline_bands(req, problems, now)
    horizon = max([now + 86400] + [min(b["plan_end"] or b["end"], now + 7 * 86400) for b in bars]
                  + [min(b["end"], now + 7 * 86400) for b in bands if b["start"] < now + 7 * 86400])
    t1 = (int(horizon) // 3600 + 1) * 3600
    px = PX_PER_HOUR / 3600.0
    width = int((t1 - t0) * px)
    height = max(360, pack_lanes(bars, px) * LANE + 24)
    axis, majors = time_axis(t0, t1, px)

    items = [band_html(b, t0, t1, px) for b in sorted(bands, key=lambda b: b["start"]) if b["start"] < t1]
    for b in bars:
        j = b["job"]
        left = int((b["start"] - t0) * px)
        w = max(4, int((b["end"] - b["start"]) * px))
        top = 12 + b["lane"] * LANE
        tip = (f'{j["id"]} {j["name"]} · {b["label"]} · {j["cpus"] or "?"} CPUs · {fmt_mb(j["mem_mb"])} · '
               + (f'est. start {fmt_time(j["start"])}' if b["kind"] == "wait" and j["start"] else
                  f'{fmt_time(b["start"])} → ' + ("now" if b["kind"] == "run" else fmt_time(b["end"]))))
        if b["plan_end"] and b["plan_end"] > b["end"]:
            pw = int((b["plan_end"] - b["end"]) * px)
            items.append(f'<div class="tl-plan" style="left:{left + w}px;width:{pw}px;top:{top}px" '
                         f'title="time limit"></div>')
        items.append(f'<a class="tl-bar k-{h(b["kind"])}" href="{link(req, "/job/" + j["id"])}" '
                     f'style="left:{left}px;width:{w}px;top:{top}px" title="{h(tip)}">'
                     f'<span>{h(j["id"])} {h(j["name"])}</span></a>')
    x_now = int((now - t0) * px)
    chart = (f'<div class="timeline" data-now-x="{x_now}"><div class="tl-inner" style="width:{width}px">'
             f'<div class="tl-head">{axis}</div>'
             f'<div class="tl-body" style="height:{height}px">{majors}{"".join(items)}</div>'
             f'<div class="tl-now" style="left:{x_now}px"></div></div></div>')
    legend = ('<p class="legend muted small"><span class="key k-run"></span>running <span class="key k-wait"></span>'
              'waiting (estimated start) <span class="key k-done"></span>completed <span class="key k-bad"></span>'
              'failed, timed out or out of memory <span class="key k-muted"></span>cancelled</p>')
    legend += ('<p class="legend muted small"><span class="key b-res"></span>your reservation <span class="key b-req">'
               '</span>your request, waiting for an admin <span class="key b-maint"></span>maintenance (no jobs) · '
               f'<a href="{link(req, "/reservations")}">Reservations</a></p>')
    legend += coming_up(bands, t1)
    if dropped:
        legend += f'<p class="muted small">Showing your {MAX_BARS} most recent jobs; {dropped} older ones are left out.</p>'
    return chart + legend


def bar_details(j, b, now):
    """What a click on a bar of the All nodes view shows (portal.js), as a title and rows."""
    rows = [("User", j["user"]), ("State", b["label"])]
    if b["kind"] == "wait":
        why = slurm.reason_text(j["reason"])
        if why:
            rows.append(("Why waiting", why))
        if j.get("sched_nodes"):
            rows.append(("Planned node", j["sched_nodes"]))
    else:
        rows.append(("Node", j["nodes"] or "—"))
    rows += [("CPUs", str(j["cpus"] or "—")), ("Memory", fmt_mb(j["mem_mb"]))]
    if b["kind"] == "wait":
        rows.append(("Estimated start", fmt_time(j["start"]) if j["start"] and j["start"] > now else "not known yet"))
        rows.append(("Time limit", fmt_minutes(j["time_limit"])))
    elif b["kind"] == "run":
        rows.append(("Started", fmt_time(j["start"])))
        rows.append(("Ends at the latest", fmt_time(b["plan_end"]) if b["plan_end"] else "no time limit"))
    else:
        rows += [("Started", fmt_time(j["start"])), ("Ended", fmt_time(j["end"])),
                 ("Run time", fmt_duration(j["elapsed"]) if j["elapsed"] is not None else "—")]
    return {"title": f'{j["id"]} {j["name"]}', "rows": rows}


def timeline_all(req, now, days, problems):
    t0 = (int(now - days * 86400) // 3600) * 3600
    nodes = attempt(problems, slurm.nodes, default=[])
    jobs = {j["id"]: j for j in attempt(problems, slurm.all_jobs_since, days, default=[])}
    for j in attempt(problems, slurm.all_active_jobs, default=[]):
        jobs[j["id"]] = j
    bars = [b for b in (timeline_bar(j, now, t0) for j in jobs.values()) if b]
    dropped = 0
    if len(bars) > MAX_BARS:
        bars.sort(key=lambda b: b["start"], reverse=True)
        dropped, bars = len(bars) - MAX_BARS, bars[:MAX_BARS]
    bands = timeline_bands(req, problems, now, mine=False)
    ahead = now + 2 * 86400   # long jobs run on past the chart's end; a bar's details give its end
    horizon = max([now + 86400] + [min(b["plan_end"] or b["end"], ahead) for b in bars]
                  + [min(b["end"], ahead) for b in bands if b["start"] < ahead])
    t1 = (int(horizon) // 3600 + 1) * 3600
    px = PX_PER_HOUR / 3600.0
    width = int((t1 - t0) * px)
    axis, majors = time_axis(t0, t1, px)

    # rows: each node's running jobs and the waiting jobs planned there, waiting jobs without a node, then each node's
    # finished jobs (a job on several nodes shows on each)
    current = {n["name"]: [] for n in nodes}
    finished = {n["name"]: [] for n in nodes}
    waiting = []
    for b in bars:
        j = b["job"]
        if b["kind"] == "wait":
            where, rows = slurm.hostnames(j.get("sched_nodes")), current
        else:
            where, rows = slurm.hostnames(j["nodes"]), current if b["kind"] == "run" else finished
        if not where:
            waiting.append(b)
        for name in where:
            rows.setdefault(name, []).append(dict(b))
    size = {n["name"]: f'{n["cpus"]} CPUs' for n in nodes}
    groups = [(name, size.get(name, ""), rows) for name, rows in current.items()]
    if waiting:
        groups.append(("Waiting", "no node yet", waiting))
    groups += [(name, "finished", rows) for name, rows in finished.items() if rows]

    out = []
    bands = sorted(bands, key=lambda b: b["start"])
    for i, (name, sub, rows) in enumerate(groups):
        lanes = pack_lanes(rows, px)
        items = [band_html(b, t0, t1, px, label=i == 0) for b in bands if b["start"] < t1]
        for b in rows:
            j = b["job"]
            colour = user_colour(j["user"])
            left = int((b["start"] - t0) * px)
            w = max(4, int((min(b["end"], t1) - b["start"]) * px))
            top = 6 + b["lane"] * LANE
            if b["plan_end"] and b["plan_end"] > b["end"]:
                pw = int((min(b["plan_end"], t1) - b["end"]) * px)
                items.append(f'<div class="tl-plan u" style="left:{left + w}px;width:{pw}px;top:{top}px;--uc:{colour}">'
                             "</div>")
            kind = b["kind"] if b["kind"] in ("run", "wait") else "fin"
            details = bar_details(j, b, now)
            title = details["title"] + " · " + " · ".join(f"{k}: {v}" for k, v in details["rows"])
            if j["user"] == req.user:
                details["href"] = req.base + "/job/" + j["id"]
            attrs = (f'class="tl-bar u k-{kind}" style="left:{left}px;width:{w}px;top:{top}px;--uc:{colour}" '
                     f'title="{h(title)}" data-tip="{h(json.dumps(details))}"')
            text = f'<span>{h(j["user"])} {h(plural(j["cpus"], "CPU") if j["cpus"] else "? CPUs")}</span>'
            items.append(f'<a {attrs} href="{link(req, "/job/" + j["id"])}">{text}</a>' if j["user"] == req.user else
                         f'<span {attrs} tabindex="0" role="button">{text}</span>')
        out.append(f'<div class="tl-row"><div class="tl-label"><b>{h(name)}</b><span>{h(sub)}</span></div>'
                   f'<div class="tl-track" style="width:{width}px;height:{max(1, lanes) * LANE + 12}px">{majors}'
                   f'{"".join(items)}</div></div>')
    x_now = int((now - t0) * px)
    chart = (f'<div class="timeline tl-all" data-now-x="{x_now + TL_LABEL}"><div class="tl-inner" '
             f'style="width:{width + TL_LABEL}px"><div class="tl-row tl-head-row"><div class="tl-label"></div>'
             f'<div class="tl-head" style="width:{width}px">{axis}</div></div>{"".join(out)}'
             f'<div class="tl-now" style="left:{x_now + TL_LABEL}px"></div></div></div>')
    legend = ('<p class="legend muted small">Colour = user · <span class="key k-run-key"></span>running, then '
              '<span class="key k-plan-key"></span>up to its time limit <span class="key k-wait-key"></span>waiting '
              '(estimated start) <span class="key k-fin-key"></span>finished <span class="key b-maint"></span>maintenance '
              f'(no jobs) · <a href="{link(req, "/cluster")}">Cluster status</a> lists the running jobs</p>')
    legend += coming_up(bands, t1)
    if dropped:
        legend += f'<p class="muted small">Showing the {MAX_BARS} most recent jobs; {dropped} older ones are left out.</p>'
    return chart + legend


def timeline(req):
    now = time.time()
    problems = []
    view = "mine" if req.arg("view") == "mine" else "all"
    ranges = {k: d for k, _, d in RANGES}
    chosen = req.arg("range") if req.arg("range") in ranges else ""
    rng = chosen or ("7d" if view == "mine" else "48h")
    days = ranges[rng]
    period = dict((k, label) for k, label, _ in RANGES)[rng]
    if view == "mine":
        chart = timeline_mine(req, now, days, problems)
        sub = (f"My jobs, {h(period)}. Waiting jobs are shown striped at their estimated start; your reservations, "
               "waiting requests and maintenance days are shaded behind them. Click a bar to open the job.")
    else:
        chart = timeline_all(req, now, days, problems)
        sub = ("All nodes: who runs what and until when (colour = user, label = CPUs). Striped = waiting (estimated "
               "start). Click a bar for details.")
    keep = "&range=" + chosen if chosen else ""   # the tabs keep a period the user chose
    tabs = "".join(f'<a class="tab{" active" if v == view else ""}" href="{link(req, "/timeline?view=" + v + keep)}"'
                   + (' aria-current="page"' if v == view else "") + f">{h(label)}</a>" for v, label in VIEWS)
    options = "".join(f'<option value="{k}"{" selected" if k == rng else ""}>{h(label)}</option>'
                      for k, label, _ in RANGES)
    controls = (f'<div class="tl-controls"><div class="tabs">{tabs}</div><form method="get" '
                f'action="{link(req, "/timeline")}"><input type="hidden" name="view" value="{view}"><select name="range" '
                f'data-autosubmit aria-label="Period">{options}</select><noscript><button class="btn">Show</button>'
                "</noscript></form></div>")
    body = head("Timeline", sub, controls) + problems_notice(problems) + chart
    return page(req, "timeline", "Timeline", body)


# ---------------------------------------------------------------------------------------------------- cluster

def job_name_cell(req, j):
    text = f'<span class="mono">{h(j["id"])}</span> {h(j["name"])}'
    return f'<a href="{link(req, "/job/" + j["id"])}">{text}</a>' if j["user"] == req.user else text


def ends_cell(j, now):
    if not j["time_limit"] or not j["end"]:
        return '<span class="muted">no time limit</span>'
    when = f'<span class="muted">({h(fmt_time(j["end"]))})</span>'
    return f"in {h(fmt_duration(j['end'] - now))} {when}" if j["end"] > now else f"now {when}"


def user_meter(parts, total, other=0):
    """A node's CPUs in use, one coloured part per user (and grey for jobs squeue does not list here)."""
    segs = [(u, c, user_colour(u)) for u, c in parts] + ([("other jobs", other, "var(--faint)")] if other > 0 else [])
    out = [f'<div class="seg" style="width:{min(c / total, 1) * 100:.2f}%;--uc:{colour}" '
           f'title="{h(u)}: {h(plural(c, "CPU"))}"></div>' for u, c, colour in segs if c and total]
    return '<div class="meter by-user">' + "".join(out) + "</div>"


def cluster(req):
    problems = []
    nodes = attempt(problems, slurm.nodes, default=[])
    jobs = attempt(problems, slurm.all_active_jobs, default=[])
    now = time.time()
    running = sorted((j for j in jobs if j["state"] in slurm.RUNNING_STATES),
                     key=lambda j: (j["end"] if j["time_limit"] and j["end"] else math.inf, j["job_id"] or 0))
    waiting = sorted((j for j in jobs if j["state"] == "PENDING"),
                     key=lambda j: (j["start"] if j["start"] and j["start"] > now else math.inf, j["job_id"] or 0))
    up = [n for n in nodes if n["available"]]
    est = [j["start"] for j in waiting if j["start"] and j["start"] > now]
    people = {j["user"] for j in running}
    cards = "".join([
        stat_card("Free CPUs", str(sum(n["cpus_free"] for n in up)), f"of {sum(n['cpus'] for n in nodes)}", "cpu"),
        stat_card("Free memory", h(fmt_mb(sum(n["mem_free_mb"] for n in up))),
                  f"of {h(fmt_mb(sum(n['mem_mb'] for n in nodes)))}", "memory"),
        stat_card("Running jobs", str(len(running)), f"by {plural(len(people), 'user')}" if running else "&nbsp;",
                  "list"),
        stat_card("Waiting jobs", str(len(waiting)), "no queue" if not waiting else
                  f"next start est. {h(fmt_time(min(est)))}" if est else "no start estimate yet", "clock"),
    ])
    per_node = collections.defaultdict(collections.Counter)
    for j in running:
        for name in slurm.hostnames(j["nodes"]):
            per_node[name][j["user"]] += slurm.cpus_on(j, name)
    node_cards = []
    for n in nodes:
        busy = f"{n['load'] / n['threads'] * 100:.0f}% busy" if n["load"] is not None and n["threads"] else ""
        tags = [slurm.FEATURES.get(f, f) for f in n["features"]]
        if not n["available"]:
            state = " ".join(s.lower() for s in n["state"]) or "unknown"
            tags.append(f'<span class="bad">{h(state)}</span>')
        elif n["cpus_alloc"] == 0:
            tags.append("idle")
        users = sorted(per_node[n["name"]].items(), key=lambda x: (-x[1], x[0]))
        chips = "".join(chip(u, plural(c, "CPU")) for u, c in users) or '<span class="muted">no jobs</span>'
        node_cards.append(
            f'<div class="card node"><div class="node-head"><span><b>{h(n["name"])}</b> · {n["cpus"]} CPUs</span>'
            f'<span class="muted">{h(busy)}</span></div>'
            f'{user_meter(users, n["cpus"], n["cpus_alloc"] - sum(c for _, c in users))}'
            f'<div class="node-text">{n["cpus_free"]} of {n["cpus"]} CPUs free</div>'
            f'{meter(n["mem_alloc_mb"] / n["mem_mb"] if n["mem_mb"] else 0, "mem")}'
            f'<div class="node-text">{h(fmt_mb(n["mem_free_mb"]))} free of {h(fmt_mb(n["mem_mb"]))}'
            f'{" · " + " · ".join(t if t.startswith("<") else h(t) for t in tags) if tags else ""}</div>'
            f'<div class="chips">{chips}</div></div>')

    rows = "".join(
        f'<tr><td>{chip(j["user"])}</td><td class="name">{job_name_cell(req, j)}</td><td>{h(j["nodes"] or "—")}</td>'
        f'<td>{h(j["cpus"] or "—")}</td><td>{h(fmt_mb(j["mem_mb"]))}</td><td>{h(fmt_time(j["start"]))}</td>'
        f'<td>{ends_cell(j, now)}</td></tr>' for j in running)
    run_table = ('<div class="table-wrap"><table class="table"><thead><tr><th>User</th><th>Job</th><th>Node</th>'
                 '<th>CPUs</th><th>Memory</th><th>Started</th><th>Ends</th></tr></thead><tbody>' + rows
                 + "</tbody></table></div>") if running else empty("No jobs are running.")
    rows = "".join(
        f'<tr><td>{chip(j["user"])}</td><td class="name">{job_name_cell(req, j)}</td><td>{h(j["cpus"] or "—")}</td>'
        f'<td>{h(fmt_mb(j["mem_mb"]))}</td><td>{h(fmt_minutes(j["time_limit"]))}</td>'
        f'<td class="name">{h(slurm.reason_text(j["reason"]) or "waiting")}</td><td>'
        + (h(fmt_time(j["start"])) if j["start"] and j["start"] > now else '<span class="muted">not known yet</span>')
        + "</td></tr>" for j in waiting)
    wait_table = card("Waiting jobs (next to start first)", (
        '<div class="table-wrap"><table class="table"><thead><tr><th>User</th><th>Job</th><th>CPUs</th><th>Memory</th>'
        '<th>Time limit</th><th>Why waiting</th><th>Est. start</th></tr></thead><tbody>' + rows
        + "</tbody></table></div>"), "flush-head") if waiting else ""
    inner = (problems_notice(problems) + f'<div class="grid stats">{cards}</div>'
             f'<div class="grid nodes">{"".join(node_cards)}</div>'
             + card("Running jobs (ending soonest first)", run_table, "flush-head") + wait_table)
    if req.partial:
        return inner
    body = (head("Cluster status", "Free CPUs and memory, who uses each node and when their jobs end. Updated every 5 "
                                   "seconds.")
            + f'<div data-refresh="{link(req, "/cluster?partial=1")}" data-interval="5000">{inner}</div>')
    return page(req, "cluster", "Cluster status", body)


# ---------------------------------------------------------------------------------------------------- history

def _days(req):
    try:
        days = int(req.arg("days", "30"))
    except ValueError:
        days = 30
    return days if days in HISTORY_DAYS else 30


def _finished(req, days):
    jobs = slurm.my_jobs_since(req.user, days)
    done = [j for j in jobs if not j["active"]]
    done.sort(key=lambda j: (j["job_id"] or 0, j["task"] or 0), reverse=True)
    return done


def cpu_hours(j):
    return (j["elapsed"] or 0) * (j["cpus"] or 0) / 3600.0


def week_chart(jobs, days, now):
    weeks = []
    d = datetime.date.fromtimestamp(now - days * 86400)
    end = datetime.date.fromtimestamp(now)
    while d <= end:
        y, w, _ = d.isocalendar()
        if f"{y}-W{w:02d}" not in weeks:
            weeks.append(f"{y}-W{w:02d}")
        d += datetime.timedelta(days=1)
    totals = dict.fromkeys(weeks, 0.0)
    for j in jobs:
        t = j["end"] or j["start"]
        if not t:
            continue
        y, w, _ = datetime.date.fromtimestamp(t).isocalendar()
        key = f"{y}-W{w:02d}"
        if key in totals:
            totals[key] += cpu_hours(j)
    top = max(totals.values(), default=0)
    step = nice_step(top / 3 if top else 1)
    ymax = max(step * 3, step * math.ceil(top / step) if top else step)
    W, H, L, B, T = 800, 170, 44, 24, 8
    n = max(len(weeks), 1)
    slot = (W - L - 8) / n
    bar_w = max(2, slot * 0.8)
    parts = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="CPU-hours per week">']
    ticks = int(round(ymax / step))
    digits = max(0, -math.floor(math.log10(step))) if step < 1 else 0
    for i in range(ticks + 1):
        v = step * i
        y = H - B - (H - B - T) * v / ymax
        parts.append(f'<line class="grid-line" x1="{L}" x2="{W - 4}" y1="{y:.1f}" y2="{y:.1f}"/>'
                     f'<text class="axis" x="{L - 6}" y="{y + 4:.1f}" text-anchor="end">{h(fmt_num(v, digits + 1))}</text>')
    label_every = max(1, math.ceil(n / 12))
    for i, key in enumerate(weeks):
        v = totals[key]
        x = L + slot * i + (slot - bar_w) / 2
        bh = (H - B - T) * v / ymax if ymax else 0
        parts.append(f'<rect class="bar" x="{x:.1f}" y="{H - B - bh:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" rx="2">'
                     f'<title>{h(key)}: {h(fmt_num(v, 2))} CPU-hours</title></rect>')
        if i % label_every == 0:
            parts.append(f'<text class="axis" x="{L + slot * i + slot / 2:.1f}" y="{H - 6}" text-anchor="middle">'
                         f'{h(key)}</text>')
    parts.append(f'<line class="axis-line" x1="{L}" x2="{W - 4}" y1="{H - B}" y2="{H - B}"/></svg>')
    return "".join(parts)


def nice_step(x):
    if x <= 0:
        return 1
    mag = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 2.5, 5, 10):
        if x <= m * mag:
            return m * mag
    return 10 * mag


def history(req):
    days = _days(req)
    now = time.time()
    problems = []
    jobs = attempt(problems, _finished, req, days, default=[])
    total = sum(cpu_hours(j) for j in jobs)
    idle_cancelled = (slurm.idle_status() or {}).get("cancelled", {})
    rows = []
    for j in jobs[:500]:
        label, style = slurm.state_label(j["state"])
        if j["state"] == "CANCELLED" and (j["id"] in idle_cancelled or j["admin_note"].startswith(SETTINGS["idle_note_prefix"])):
            label = "Cancelled (idle)"
        exit_text = "" if j["exit"] is None and not j["signal"] else (
            f"{j['exit'] or 0}:{j['signal']}" if j["signal"] else str(j["exit"]))
        url = link(req, f"/job/{j['id']}")
        rows.append(f'<tr data-href="{url}"><td class="mono"><a href="{url}">{h(j["id"])}</a></td>'
                    f'<td class="name">{h(j["name"])}</td><td>{badge(label, style)}</td>'
                    f'<td>{h(fmt_time(j["start"]))}</td><td>{h(fmt_duration(j["elapsed"]) if j["start"] else "—")}'
                    f'</td><td>{h(j["cpus"] or "—")}</td><td>{h(fmt_mb(j["mem_mb"]))}</td>'
                    f'<td>{h(fmt_num(cpu_hours(j), 3))}</td><td>{h(exit_text)}</td></tr>')
    table = ('<div class="table-wrap"><table class="table clickable"><thead><tr><th>ID</th><th>Name</th><th>State</th>'
             '<th>Start</th><th>Duration</th><th>CPUs</th><th>Memory</th><th>CPU-h</th><th>Exit</th></tr></thead>'
             '<tbody>' + "".join(rows) + "</tbody></table></div>") if rows else empty(f"No finished jobs in the last "
                                                                                  f"{days} days.")
    more = (f'<p class="muted small after">Showing the latest 500 of {len(jobs)} jobs; the CSV file has all of them.'
            "</p>" if len(jobs) > 500 else "")
    select = "".join(f'<option value="{d}"{" selected" if d == days else ""}>{d} days</option>' for d in HISTORY_DAYS)
    actions = (f'<form method="get" action="{link(req, "/history")}"><select name="days" data-autosubmit '
               f'aria-label="Period">{select}</select><noscript><button class="btn">Show</button></noscript></form>'
               f'<a class="btn" href="{link(req, "/history.csv?days=" + str(days))}">{icon("download")}CSV</a>')
    summary = card("", f'<div class="stat-top"><span>CPU-hours</span></div><div class="stat-value">'
                       f'{h(fmt_num(total, 2))}</div><div class="stat-sub">{plural(len(jobs), "job")} in {days} days'
                       f'</div>', "stat big")
    chart = card("CPU-hours per week", week_chart(jobs, days, now), "chart-card")
    body = "".join([
        head("History & statistics", "Finished jobs and CPU-hours (CPUs × run time).", actions),
        problems_notice(problems),
        f'<div class="grid one-two">{summary}{chart}</div>',
        card("", table, "flush"), more,
    ])
    return page(req, "history", "History & stats", body)


def history_csv(req):
    days = _days(req)
    jobs = _finished(req, days)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["job_id", "name", "state", "submit", "start", "end", "elapsed_s", "cpus", "mem_mb", "cpu_hours",
                "exit_code", "nodes", "workdir"])
    iso = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""  # noqa: E731
    for j in jobs:
        w.writerow([j["id"], j["name"], j["state"], iso(j["submit"]), iso(j["start"]), iso(j["end"]),
                    j["elapsed"] or 0, j["cpus"] or "", int(j["mem_mb"] or 0), f"{cpu_hours(j):.4f}",
                    "" if j["exit"] is None else (f"{j['exit']}:{j['signal']}" if j["signal"] else j["exit"]),
                    j["nodes"], j["workdir"]])
    name = f"jobs-{req.user}-{days}d.csv"
    return Response(out.getvalue(), 200, "text/csv; charset=utf-8",
                    [("Content-Disposition", f'attachment; filename="{name}"')])


# ---------------------------------------------------------------------------------------------------- account

EVENT_LABELS = [("done", "Job finished successfully"),
                ("failed", "Job failed, timed out, ran out of memory or was cancelled"),
                ("idle", "Idle warning (15 minutes before) and cancellation by the idle check"),
                ("priority", "An admin gives you priority or reserves CPUs for you (and when it ends), or answers "
                             "your reservation request")]
ADMIN_EVENT_LABELS = [("requests", "Cluster admins: a member asks for a reservation, or cancels one")]


def account(req, notes="", pw_error="", keys_text=None, key_problems=None):
    first = account_mod.must_change(req.user)
    pw_form = (f'<form class="account-form" method="post" action="{link(req, "/account/password")}">'
               f'<p class="muted">Your portal password is also your SSH password on {h(node_list())}.</p>'
               '<label for="a-current">Current password</label><input id="a-current" name="current" type="password" '
               'autocomplete="current-password" required>'
               '<label for="a-new">New password</label><input id="a-new" name="new" type="password" '
               'autocomplete="new-password" minlength="10" required>'
               '<div class="hint">At least 10 characters, letters plus a digit or symbol, not containing your username. '
               f'This also becomes your SSH password on {all_nodes()}.</div>'
               '<label for="a-repeat">Repeat new password</label><input id="a-repeat" name="repeat" type="password" '
               'autocomplete="new-password" minlength="10" required>'
               f'<div class="error-text">{h(pw_error)}</div>'
               '<button class="btn btn-primary btn-wide" type="submit">Change password</button></form>')
    if first:
        body = "".join([
            head("Account", f"{h(req.user)} · UID {req.uid}"),
            notice("warn", "You signed in with a temporary password. Choose your own password to continue: it "
                           f"becomes your password on the portal and for SSH on {h(node_list())}."),
            '<div class="grid two">', card("Password", pw_form), "</div>",
        ])
        return page(req, "account", "Account", body)

    # SSH keys
    try:
        current = account_mod.read_keys(req.home)
        good, bad_now = account_mod.check_keys(current)
    except OSError as e:
        current, good, bad_now = "", [], [(0, f"cannot read ~/.ssh/authorized_keys: {e.strerror}")]
    text = current if keys_text is None else keys_text
    key_list = "".join(f'<li><span class="mono">{h(i["type"])}</span> {h(i["bits"])} · <span class="mono">'
                       f'{h(i["fingerprint"])}</span> <span class="muted">{h(i["comment"] or "(no comment)")}</span></li>'
                       for _, i in good)
    problems = key_problems if key_problems is not None else bad_now
    problem_list = "".join(f"<li>{'line ' + str(n) + ': ' if n else ''}{h(p)}</li>" for n, p in problems)
    keys_form = (f'<form method="post" action="{link(req, "/account/keys")}"><textarea name="keys" rows="8" '
                 f'class="keys" spellcheck="false" placeholder="ssh-ed25519 AAAA... you@laptop">{h(text)}</textarea>'
                 + (f'<ul class="key-problems">{problem_list}</ul>' if problems else "")
                 + '<div class="form-actions"><button class="btn btn-primary" type="submit">Save keys</button></div>'
                 '<p class="muted small">One key per line. The previous file is kept as a backup in ~/.ssh. '
                 f'Keys work on {all_nodes()} (shared home).</p>'
                 + (f'<ul class="keys-list">{key_list}</ul>' if key_list else "") + "</form>")

    # Telegram
    settings = account_mod.helper("telegram-get")
    bot = account_mod.bot_name()
    if settings.get("ok"):
        events = settings.get("events") or {}
        boxes = "".join(f'<label class="check"><input type="checkbox" name="ev_{k}" value="1"'
                        f'{" checked" if events.get(k) else ""}><span>{h(label)}</span></label>'
                        for k, label in EVENT_LABELS + (ADMIN_EVENT_LABELS if req.is_admin else []))
        how = (f'Open <a href="https://t.me/{h(bot)}" rel="noopener">@{h(bot)}</a> in Telegram and press Start, '
               if bot else "Start a chat with the cluster's bot, ")
        tg = (f'<form method="post" action="{link(req, "/account/telegram")}"><label for="a-chat">Telegram chat ID'
              f'</label><input id="a-chat" name="chat_id" value="{h(settings.get("chat_id", ""))}" inputmode="numeric" '
              f'autocomplete="off" placeholder="e.g. 123456789"><div class="hint">{how}then get your chat ID from '
              '<a href="https://t.me/userinfobot" rel="noopener">@userinfobot</a>. Messages go through Telegram.</div>'
              f'{boxes}<div class="form-actions"><button class="btn btn-primary" type="submit" name="action" '
              'value="save">Save</button><button class="btn" type="submit" name="action" value="test">Save and send a '
              'test message</button></div></form>')
    else:
        tg = f'<p class="muted">Not available: {h(settings.get("error", "unknown error"))}</p>'

    body = "".join([
        head("Account", f"{h(req.user)} · UID {req.uid}"), notes,
        '<div class="grid two">', card("Password", pw_form), card("SSH public keys", keys_form),
        card("Notifications (Telegram)", tg), "</div>",
    ])
    return page(req, "account", "Account", body)


def account_password(req):
    current, new, repeat = (req.form.get(k, "") for k in ("current", "new", "repeat"))
    problem = account_mod.password_problem(req.user, new, repeat)
    if problem:
        return account(req, pw_error=problem)
    answer = account_mod.helper("passwd", {"current": current, "new": new})
    if not answer.get("ok"):
        return account(req, pw_error=answer.get("error", "The password was not changed."))
    return redirect("/login?changed=1")   # the session still holds the old password: sign in again


def account_keys(req):
    text = req.form.get("keys", "")
    good, bad = account_mod.check_keys(text)
    if bad:
        return account(req, notice("error", "Nothing was saved: fix the lines below."), keys_text=text,
                       key_problems=bad)
    try:
        changed, backup = account_mod.save_keys(req.home, text)
    except (OSError, ValueError) as e:
        return account(req, notice("error", h(f"Not saved: {getattr(e, 'strerror', None) or e}")), keys_text=text,
                       key_problems=[])
    if not changed:
        return account(req, notice("info", "No changes."))
    kept = f" The previous file is kept as ~/.ssh/{h(backup)}." if backup else ""
    return account(req, notice("ok", f"Saved {plural(len(good), 'key')}.{kept}"))


def account_telegram(req):
    data = {"chat_id": req.form.get("chat_id", "").strip(),
            "events": {k: req.form.get(f"ev_{k}") == "1" for k, _ in EVENT_LABELS + ADMIN_EVENT_LABELS}}
    answer = account_mod.helper("telegram-set", data)
    if not answer.get("ok"):
        return account(req, notice("error", h(answer.get("error", "Not saved."))))
    if req.form.get("action") == "test":
        test = account_mod.helper("telegram-test")
        if not test.get("ok"):
            return account(req, notice("error", "Saved, but the test message failed: " + h(test.get("error", ""))))
        return account(req, notice("ok", "Saved. A test message is on its way to your Telegram."))
    return account(req, notice("ok", "Notification settings saved."))


def tests_moved(req):
    """Test runs was a page of its own until 2026-10-27; quick tests are the Quick test box on New job."""
    return redirect(req.base + "/new")


def not_found(req, text="This page does not exist."):
    body = head("Not found", h(text)) + f'<p><a class="btn" href="{link(req, "/")}">{icon("back")}Overview</a></p>'
    return Response(page(req, "", "Not found", body), 404)
