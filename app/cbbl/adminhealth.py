"""The admins' Health page: the routine checks on every node, read by the admin
helper as root (disks, mounts, services, timers, the home backup, logs, clocks; read-only), plus Slurm's view of the
nodes and the idle check's status file. Problems come first; the page refreshes itself every minute. Each node's card
also has its Slurm side: state, CPUs and memory in use, running jobs, and drain/resume (formerly a separate Nodes
page). Node actions run as the admin, with their own Slurm Administrator level."""
import math
import time
import urllib.parse

from . import admin, slurm
from .config import SETTINGS, all_nodes
from .adminviews import NOTE_LIMIT, admin_only, level_notice, one_line, user_link
from .layout import badge, empty, head, icon, meter, notice, page
from .response import redirect
from .util import fmt_bytes, fmt_duration, fmt_mb, fmt_time, h, plural
from .views import attempt, card, kv_rows, link, not_found, problems_notice

_H = SETTINGS["health"]                   # thresholds and names: see config.py
DISK_WARN, DISK_BAD = _H["disk_warn"], _H["disk_bad"]
CLOCK_WARN, CLOCK_BAD = _H["clock_warn"], _H["clock_bad"]
MEM_WARN = _H["mem_warn"]
IDLE_STALE = _H["idle_stale"]
BACKUP_MAX_AGE, BACKUP_LONG_RUN = _H["backup_max_age"], _H["backup_long_run"]
# timer: (name on the page, schedule, a last run older than this is a problem)
TIMERS = {name: tuple(t) for name, t in _H["timers"].items()}
LOGS = [tuple(entry) for entry in _H["logs"]]
BACKUP_NODE, BACKUP_TIMER = _H["backup_node"], _H["backup_timer"]
CONTROLLER = SETTINGS["login_node"]


def used_pct(disk):
    total = disk["used"] + disk["avail"]
    return math.ceil(disk["used"] * 100 / total) if total else 0   # rounded up, like df's Use%


def ago(now, t):
    return f"{fmt_duration(now - t)} ago" if t else "never"


def offset_text(off):
    a = abs(off)
    size = f"{a * 1e6:.0f} µs" if a < 0.001 else f"{a * 1000:.1f} ms" if a < 1 else f"{a:.1f} s"
    return f"{size} {'ahead' if off > 0 else 'behind'}"


def by_name(report):
    return sorted((report.get("nodes") or {}).items())


def find_problems(report, nodes, idle, now):
    """[(level, text)] with level "bad" or "warn", the bad ones first."""
    out = []
    for node, d in (report.get("nodes") or {}).items():
        if not d.get("reachable"):
            out.append(("bad", f"{node} did not answer: {d.get('error', '')}"))
            continue
        for disk in d["disks"]:
            pct = used_pct(disk)
            if pct >= DISK_WARN:
                out.append(("bad" if pct >= DISK_BAD else "warn",
                            f"{node} {disk['path']} is {pct} % full ({fmt_bytes(disk['avail'])} free)"))
        for m in d["mounts"]:
            if not m["ok"]:
                out.append(("bad", f"{node}: {m['path']} is not mounted from {m['expected']} (found: {m['source']})"))
        for s in d["services"]:
            if not s["loaded"]:
                out.append(("bad", f"{node}: {s['name']} is not installed"))
            elif s["state"] != "active":
                out.append(("bad", f"{node}: {s['name']} is {s['state']}"))
        for t in d["timers"]:
            label, _, stale = TIMERS.get(t["name"], (t["name"], "", 0))
            if not t["loaded"] or t["state"] != "active":
                out.append(("bad", f"{label} ({node}): its timer is {t['state'] if t['loaded'] else 'not installed'}"))
            elif t["running"]:
                if t["started"] and now - t["started"] > BACKUP_LONG_RUN:
                    out.append(("warn", f"{label} ({node}) has been running for {fmt_duration(now - t['started'])}"))
            elif t["result"] != "success":
                out.append(("bad", f"{label} ({node}): the last run failed ({t['result']}, exit code {t['status']})"))
            elif stale and (not t["started"] or now - t["started"] > stale):
                out.append(("bad", f"{label} ({node}) last ran {ago(now, t['started'])}"))
        clock = d.get("clock") or {}
        if clock.get("offset") is None:
            out.append(("warn", f"{node}: chrony did not report the time sync"))
        elif abs(clock["offset"]) > CLOCK_WARN:
            out.append(("bad" if abs(clock["offset"]) > CLOCK_BAD else "warn",
                        f"{node}: the clock is {abs(clock['offset']):.1f} s {'ahead' if clock['offset'] > 0 else 'behind'}"))
        elif clock.get("leap") not in ("Normal", ""):
            out.append(("warn", f"{node}: time sync is “{clock['leap']}”"))
        if d.get("mem_total") and d.get("mem_avail") is not None and d["mem_avail"] / d["mem_total"] < MEM_WARN:
            out.append(("warn", f"{node}: only {fmt_bytes(d['mem_avail'] * 1024)} of memory free"))
        if d.get("selinux_denials"):
            out.append(("warn", f"{node}: {plural(d['selinux_denials'], 'SELinux denial')} today (ausearch -m avc -ts today)"))
        if (d.get("slurmctld") or {}).get("count"):
            out.append(("warn", f"Slurm controller: {plural(d['slurmctld']['count'], 'new error')} in 24 hours"))
        backup = d.get("backup")
        if backup:
            running = any(t["running"] for t in d["timers"] if t["name"] == BACKUP_TIMER)
            if backup["state"] == "failed":
                out.append(("bad", f"The last home backup failed (snapshot {backup['snapshot']}, rc={backup['rc']})"))
            elif backup["state"] != "ok":
                out.append(("bad", "The home backup has no status file"))
            elif not backup["at"] or now - backup["at"] > BACKUP_MAX_AGE:
                out.append(("warn", f"The newest complete home backup is {ago(now, backup['at'])}"
                                    + (" (a backup is running)" if running else "")))
    for n in nodes:
        if not n["available"]:
            state = " ".join(s.lower() for s in n["state"]) or "unknown"
            out.append(("warn" if "DRAIN" in n["state"] and "DOWN" not in n["state"] else "bad",
                        f"Slurm: {n['name']} is {state}" + (f" ({n['reason']})" if n["reason"] else "")))
    if idle is None:
        out.append(("bad", "The idle check's status file is missing"))
    elif now - (idle.get("updated") or 0) > IDLE_STALE:
        out.append(("bad", f"The idle check last wrote its status {ago(now, idle.get('updated'))}"))
    return sorted(out, key=lambda p: p[0] != "bad")


def summary(problems):
    if not problems:
        return notice("ok", f"All checks pass on {all_nodes()}.")
    html = ""
    for level, kind, title in (("bad", "error", "Needs attention"), ("warn", "warn", "Worth a look")):
        items = [t for lv, t in problems if lv == level]
        if items:
            html += notice(kind, f"<b>{title}</b><ul>" + "".join(f"<li>{h(t)}</li>" for t in items) + "</ul>")
    return html


def slurm_state(n, here):
    if "DRAIN" in n["state"]:
        return badge("Draining" if here or n["cpus_alloc"] else "Drained", "wait")
    if {"DOWN", "NOT_RESPONDING", "FAIL"} & set(n["state"]):
        return badge("Down", "bad")
    return badge("Idle", "done") if n["cpus_alloc"] == 0 else badge("In use", "run")


def slurm_part(req, n, here):
    """Slurm's side of a node card: CPUs and memory in use, running jobs, the drain reason."""
    reason = ""
    if n["reason"]:
        by = f" · {h(n['reason_by'])}" if n["reason_by"] else ""
        at = f", {h(fmt_time(n['reason_at']))}" if n["reason_at"] else ""
        reason = (f'<p class="node-reason">{icon("info")}<span>{h(n["reason"])}'
                  f'<span class="muted">{by}{at}</span></span></p>')
    users = sorted({j["user"] for j in here})
    jobs_line = (plural(len(here), "running job") + ": " + ", ".join(user_link(req, u) for u in users)
                 if here else "no running jobs")
    return (f'{meter(n["cpus_alloc"] / n["cpus"] if n["cpus"] else 0)}'
            f'<div class="node-text">{n["cpus_alloc"]} of {n["cpus"]} CPUs and {h(fmt_mb(n["mem_alloc_mb"]))} of '
            f'{h(fmt_mb(n["mem_mb"]))} in use</div>{meter(n["mem_alloc_mb"] / n["mem_mb"] if n["mem_mb"] else 0, "mem")}'
            f'<div class="node-text">{jobs_line}</div>{reason}')


def node_action(req, n):
    name = h(n["name"])
    if "DRAIN" in n["state"] or {"DOWN", "NOT_RESPONDING", "FAIL"} & set(n["state"]):
        return (f'<form class="inline-form" method="post" action="{link(req, "/admin/node/" + n["name"] + "/resume")}" '
                f'data-confirm="Put {name} back in service?"><button class="btn btn-sm" type="submit">'
                f'{icon("play")}Resume</button></form>')
    return (f'<form class="inline-form" method="post" action="{link(req, "/admin/node/" + n["name"] + "/drain")}" '
            f'data-confirm="Drain {name}? Its running jobs finish; no new jobs start there until you '
            f'resume it."><input name="reason" maxlength="{NOTE_LIMIT}" required autocomplete="off" '
            f'placeholder="Reason, e.g. disk replacement" aria-label="Reason for draining {name}">'
            f'<button class="btn btn-sm" type="submit">{icon("pause")}Drain</button></form>')


def node_card(req, name, d, n, here, now):
    """One node: Slurm's side (n, from scontrol; None if Slurm does not know it) and the system's (d, from the helper)."""
    title = f"<b>{h(name)}</b>" + (f" · {n['cpus']} CPUs · {h(fmt_mb(n['mem_mb']))}" if n else "")
    state = (slurm_state(n, here) if n else "") + ("" if d.get("reachable") else " " + badge("no answer", "bad"))
    top = f'<div class="card node"><div class="node-head"><span>{title}</span><span>{state}</span></div>'
    top += slurm_part(req, n, here) if n else '<p class="muted small">Slurm does not report this node.</p>'
    action = node_action(req, n) if n else ""
    if not d.get("reachable"):
        return top + f'<p class="muted small">Health report: {h(d.get("error") or "no answer")}</p>{action}</div>'
    disks = ""
    for x in d["disks"]:
        pct = used_pct(x)
        cls = "bad" if pct >= DISK_BAD else "warn" if pct >= DISK_WARN else ""
        disks += (f'<div class="usage"><div class="usage-top"><span class="mono">{h(x["path"])}</span><span class="muted">'
                  f'{pct} % · {h(fmt_bytes(x["avail"]))} free of {h(fmt_bytes(x["size"]))}</span></div>'
                  f'{meter(pct / 100, cls)}</div>')
    services = "".join(badge(s["name"], "ok" if s["loaded"] and s["state"] == "active" else "bad") for s in d["services"])
    mounts = "".join(badge(m["path"], "ok" if m["ok"] else "bad") for m in d["mounts"])
    clock = d.get("clock") or {}
    off = clock.get("offset")
    load = (d.get("load") or [None])[0]
    rows = [("Up", h(fmt_duration(d["uptime"])) if d.get("uptime") else "—"),
            ("Load", f"{load:.1f} on {d.get('cpus') or '?'} CPUs" if load is not None else "—"),
            ("Memory free", f"{h(fmt_bytes(d['mem_avail'] * 1024))} of {h(fmt_bytes(d['mem_total'] * 1024))}"
             if d.get("mem_total") else "—"),
            ("Clock", h(offset_text(off)) if off is not None else '<span class="bad">no time sync</span>')]
    return (f'{top}<div class="section-title spaced">System</div>{kv_rows(rows)}'
            f'<div class="section-title spaced">Disks</div>{disks}'
            f'<div class="section-title">Services</div><div class="badges">{services}</div>'
            f'<div class="section-title spaced">Mounts</div><div class="badges">{mounts or "—"}</div>{action}</div>')


def timers_card(report, now):
    rows = []
    for node, d in by_name(report):
        for t in d.get("timers") or []:
            label, schedule, _ = TIMERS.get(t["name"], (t["name"], "", 0))
            if t["running"]:
                result = badge(f"running for {fmt_duration(now - t['started'])}" if t["started"] else "running", "run")
            elif t["result"] == "success" and t["status"] in (0, None):
                result = badge("ok", "ok")
            else:
                result = badge(f"{t['result'] or 'unknown'} ({t['status']})", "bad")
            if not t["loaded"] or t["state"] != "active":
                result += " " + badge("timer " + (t["state"] if t["loaded"] else "missing"), "bad")
            rows.append(f'<tr><td>{h(label)}<div class="muted small">{h(schedule)}</div></td><td>{h(node)}</td>'
                        f'<td>{h(fmt_time(t["started"]))}<div class="muted small">{h(ago(now, t["started"]))}</div></td>'
                        f'<td>{result}</td></tr>')
    table = ('<div class="table-wrap"><table class="table"><thead><tr><th>Check</th><th>Node</th><th>Last start</th>'
             '<th>Result</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")
    return card("Scheduled checks", table if rows else empty("No timers reported."), "flush-head")


def backup_card(report, now):
    d = (report.get("nodes") or {}).get(BACKUP_NODE) or {}
    b = d.get("backup")
    if not b:
        return card("Home backup", f'<p class="muted">{h(BACKUP_NODE)} did not report the backup.</p>')
    run = next((t for t in d.get("timers", []) if t["name"] == BACKUP_TIMER), None)
    state = badge(b["state"], "ok" if b["state"] == "ok" else "bad")
    rows = [("Last result", f"{state} snapshot <span class=\"mono\">{h(b['snapshot'])}</span>"),
            ("Finished", f"{h(fmt_time(b['at']))} <span class=\"muted\">({h(ago(now, b['at']))})</span>"
             if b["at"] else "—"),
            ("Now", f"running since {h(fmt_time(run['started']))} ({h(fmt_duration(now - run['started']))})"
             if run and run["running"] and run["started"] else "not running"),
            ("Latest snapshots", "<br>".join(f'<span class="mono">{h(s)}</span>' for s in b["snapshots"]) or "—")]
    return card(f"Home backup ({h(BACKUP_NODE)})", kv_rows(rows)
                + (f'<p class="muted small after">{h(_H["backup_note"])}</p>' if _H["backup_note"] else ""))


def tasks_card(report, now):
    rows = []
    for node, d in by_name(report):
        for t in d.get("tasks") or []:
            rows.append(f'<tr><td class="mono">{h(t["unit"].removesuffix(".service"))}</td><td>{h(node)}</td>'
                        f'<td>{h(fmt_time(t["since"]))} <span class="muted">({h(ago(now, t["since"]))})</span></td>'
                        f'<td class="name">{h(t["description"])}</td></tr>')
    if not rows:
        return ""
    return '<div class="grid">' + card(
        "Running maintenance tasks", '<div class="table-wrap"><table class="table"><thead><tr><th>Task</th><th>Node</th>'
        '<th>Since</th><th>What</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>", "flush-head") + "</div>"


def logs_cards(report):
    cards = []
    for node, key, title in LOGS:
        d = (report.get("nodes") or {}).get(node) or {}
        if not d.get("reachable"):
            continue
        lines = (d.get("logs") or {}).get(key) or []
        cards.append(card(f'{h(title)} <span class="muted small">{h(node)} · last {plural(len(lines), "line")}</span>',
                          f'<pre class="log health-log">{h(chr(10).join(lines)) or "(empty)"}</pre>'))
    ctl = ((report.get("nodes") or {}).get(CONTROLLER) or {}).get("slurmctld")
    if ctl is not None:
        text = h("\n".join(ctl["errors"])) if ctl["errors"] else "No new errors in the last 24 hours."
        note = f' ({h(_H["controller_note"])})' if _H["controller_note"] else ""
        cards.append(card(f'Slurm controller errors <span class="muted small">{h(CONTROLLER)} · last 24 hours · '
                          f'{plural(ctl["known"], "known warning")} not shown{note}</span>',
                          f'<pre class="log health-log">{text}</pre>'))
    return '<div class="grid two">' + "".join(cards) + "</div>"


@admin_only
def health(req, message=""):
    now = time.time()
    problems = []
    report = admin.helper("health")
    if not report.get("ok"):
        problems.append("Health report: " + report.get("error", "no answer"))
        report = {"nodes": {}}
    nodes = attempt(problems, slurm.nodes, default=[])
    running_on = {}
    for j in attempt(problems, slurm.all_active_jobs, default=[]):
        if j["state"] in slurm.RUNNING_STATES:
            for name in slurm.hostnames(j["nodes"]):
                running_on.setdefault(name, []).append(j)
    found = find_problems(report, nodes, slurm.idle_status(), now)
    by_slurm = {n["name"]: n for n in nodes}
    names = sorted(set(by_slurm) | set(report.get("nodes") or {}))
    cards = "".join(node_card(req, name, (report.get("nodes") or {}).get(name) or {"error": "not in the health report"},
                              by_slurm.get(name), running_on.get(name, []), now) for name in names)
    inner = "".join([
        problems_notice(problems), summary(found),
        f'<div class="grid nodes">{cards}</div>',
        f'<p class="muted small">Checked {h(fmt_time(report.get("at") or now))}</p>',
        '<div class="grid two">', timers_card(report, now), backup_card(report, now), "</div>",
        tasks_card(report, now), logs_cards(report),
    ])
    if req.partial:
        return inner
    msgs = ""
    done, which = req.arg("done"), req.arg("node")
    if done == "drain":
        msgs += notice("ok", f"{h(which)} is draining: its running jobs finish, no new jobs start there.")
    elif done == "resume":
        msgs += notice("ok", f"{h(which)} is back in service.")
    if message:
        msgs += notice("error", h(message))
    body = (head("Health", f"The routine checks and Slurm's view of {all_nodes()}, read as root. Drain a node before "
                           "maintenance: its running jobs finish and no new jobs start there; Resume puts it back in "
                           "service. Updated every minute.")
            + level_notice(req) + msgs
            + f'<div data-refresh="{h(req.base)}/admin/health?partial=1" data-interval="60000">{inner}</div>')
    return page(req, "admin-health", "Health", body)


@admin_only
def node_state(req, name, state):
    if name not in {n["name"] for n in attempt([], slurm.nodes, default=[])}:
        return not_found(req, f"There is no node {name}.")
    reason = one_line(req.form.get("reason"))
    if state == "drain" and not reason:
        return health(req, f"Give a reason for draining {name}: Slurm shows it to everyone.")
    try:
        slurm.update_node(name, state.upper(), reason)
    except slurm.SlurmError as e:
        return health(req, str(e))
    admin.helper("note", {"action": state, "target": name, "reason": reason})
    return redirect(req.base + "/admin/health?" + urllib.parse.urlencode({"done": state, "node": name}))
