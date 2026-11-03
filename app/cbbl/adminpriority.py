"""The admins' Priority page: put one waiting job first, give one member more priority for a while, or reserve CPUs
for members (the maintainer guide's "Putting someone first"). Each change lasts until a chosen time; only "one person
first" can also be permanent (a member who should always have a larger share; the admin's decision, 2026-10-27).

The root helper (admin_helper in the settings) makes and undoes the changes and keeps each one with the value it
replaced; a root timer (every 5 minutes) undoes them when their time is up, also when nobody is signed in. Nothing here stops a
running job: priority only decides which waiting job starts next, and a reservation is the only guarantee. Everyone
sees the active changes (not the reasons) on Cluster status; members can choose a Telegram message on their Account
page."""
import time
import urllib.parse

from . import admin, slurm
from .adminviews import NOTE_LIMIT, admin_only, one_line, user_link
from .config import SETTINGS
from .layout import badge, empty, head, icon, notice, page
from .response import redirect
from .util import fmt_duration, fmt_time, h, plural
from .views import attempt, card, link, problems_notice

JOB_LEVELS = [("front", "To the front", "first among the waiting jobs (nice −10000)"),
              ("up", "Move up", "ahead of most waiting jobs (nice −3000)")]
USER_LEVELS = [("more", "More", "2 shares instead of 1: their usage counts half"),
               ("much", "Much more", "10 shares: their usage counts a tenth")]
DURATIONS = [("1d", "For 1 day", 86400), ("3d", "For 3 days", 3 * 86400), ("7d", "For 1 week", 7 * 86400),
             ("14d", "For 2 weeks", 14 * 86400), ("date", "Until a date (below)", None),
             ("permanent", "Permanent (until an admin ends it)", None)]
KIND_LABELS = {"job": "One job first", "user": "One person first", "reservation": "Reserved CPUs"}
DONE_TEXT = {"added": "Done. Everyone sees it on Cluster status.", "ended": "Ended: the change is undone.",
             "extended": "The end time is changed.",
             "approve": "Approved: the reservation is made, and the member sees it on their Reservations page.",
             "decline": "Declined: the member sees your note on their Reservations page."}
DATE_FORMAT = "%Y-%m-%dT%H:%M"     # <input type="datetime-local">, in the server's time zone (KST)


def level_label(b):
    levels = dict((k, label) for k, label, _ in JOB_LEVELS + USER_LEVELS)
    return levels.get(b.get("level"), "")


def what(b):
    """A change in a few words, for the admins' tables and everyone's Cluster status."""
    if b["kind"] == "job":
        return f"Job {b.get('job')}: {level_label(b).lower()}"
    if b["kind"] == "user":
        return f"{level_label(b)} priority for all their jobs"
    where = f"{b['cores']} CPUs of {b['node']}" if b.get("cores") else f"all of {b.get('node')}"
    start = f", from {fmt_time(b['start'])}" if b.get("start") and b["start"] > (b.get("since") or 0) + 60 else ""
    return f"Reserved: {where}{start}"


def until_text(until):
    return fmt_time(until) if until else "permanent"


def until_from(form, now):
    """(epoch or None for permanent, error text or None). Only "one person first" may be permanent."""
    choice = form.get("duration", "")
    if choice == "permanent" and form.get("kind") != "user":
        return None, "Only “One person first” can be permanent: choose an end."
    seconds = {k: s for k, _, s in DURATIONS}
    if choice not in seconds:
        return None, "Choose how long."
    if choice == "permanent":
        return None, None
    if choice == "date":
        try:
            return int(time.mktime(time.strptime(form.get("until_date", ""), DATE_FORMAT))), None
        except ValueError:
            return None, "Give the end date and time."
    return int(now + seconds[choice]), None


def fld(html, wide=False):
    """One field (label, control, hint) in the forms' three-column grid."""
    return f'<div class="fld{" wide" if wide else ""}">{html}</div>'


def duration_options(permanent, chosen=""):
    return "".join(f'<option value="{k}"{" selected" if k == chosen else ""}>{h(label)}</option>'
                   for k, label, _ in DURATIONS if permanent or k != "permanent")


def duration_fields(prefix, values, permanent=False):
    opts = duration_options(permanent, values.get("duration", "3d"))
    return (fld(f'<label for="{prefix}-dur">How long</label><select id="{prefix}-dur" name="duration">{opts}</select>')
            + fld(f'<label for="{prefix}-until">End date (for “Until a date”)</label><input id="{prefix}-until" '
                  f'name="until_date" type="datetime-local" value="{h(values.get("until_date", ""))}">'))


def level_fields(prefix, levels, values):
    chosen = values.get("level", levels[0][0])
    return fld('<div class="label">How strong</div><div class="prio-levels">' + "".join(
        f'<label class="check"><input type="radio" name="level" value="{k}"{" checked" if k == chosen else ""}>'
        f'<span><b>{h(label)}</b>: {h(hint)}</span></label>' for k, label, hint in levels) + "</div>", wide=True)


def reason_field(prefix, values, placeholder):
    return fld(f'<label for="{prefix}-reason">Reason</label><input id="{prefix}-reason" name="reason" required '
               f'maxlength="{NOTE_LIMIT}" autocomplete="off" value="{h(values.get("reason", ""))}" '
               f'placeholder="{h(placeholder)}"><div class="hint">The admin log keeps it, and the member sees it in '
               "the Telegram message if they chose one. Others see only who has priority.</div>", wide=True)


def add_form(req, kind, fields, button, note):
    return (f'<form class="admin-form" method="post" action="{link(req, "/admin/priority/add")}">'
            f'<input type="hidden" name="kind" value="{kind}"><div class="prio-grid">{fields}</div>'
            f'<div class="form-actions">{button}</div><p class="muted small">{note}</p></form>')


def job_form(req, job_id, values=None):
    """One waiting job first: on the Priority page, and on an admin's job page (a waiting job)."""
    v = values or {}
    return add_form(req, "job", f'<input type="hidden" name="job" value="{h(job_id)}">'
                    + level_fields("pj", JOB_LEVELS, v) + duration_fields("pj", v)
                    + reason_field("pj", v, "e.g. results needed for Friday's meeting"),
                    f'<button class="btn btn-primary" type="submit">{icon("star")}Put job {h(job_id)} first</button>',
                    "Back to normal at the end, or when it starts.")


def forms(req, values, problems):
    v = {k: (values if values.get("kind") == k else {}) for k in ("job", "user", "reservation")}
    waiting = [j for j in attempt(problems, slurm.all_active_jobs, default=[]) if j["state"] == "PENDING"]
    chosen = v["job"].get("job") or req.arg("job")
    job_opts = "".join(f'<option value="{h(j["id"])}"{" selected" if j["id"] == chosen else ""}>{h(j["id"])} · '
                       f'{h(j["user"])} · {h(j["name"])}</option>' for j in waiting)
    job = add_form(
        req, "job", fld(f'<label for="pj-job">Waiting job</label><select id="pj-job" name="job" required>{job_opts}'
                        "</select>")
        + duration_fields("pj", v["job"]) + level_fields("pj", JOB_LEVELS, v["job"])
        + reason_field("pj", v["job"], "e.g. results needed for Friday's meeting"),
        f'<button class="btn btn-primary" type="submit">{icon("star")}Put first</button>',
        "Back to normal at the end, or when the job starts.") if waiting else empty(
        "No job is waiting: there is nothing to put first.")
    users = sorted(u for u in attempt(problems, slurm.fairshare_all, default={}) or {} if u != "root")
    chosen = v["user"].get("user", "")
    user_opts = "".join(f'<option value="{h(u)}"{" selected" if u == chosen else ""}>{h(u)}</option>' for u in users)
    person = add_form(
        req, "user", fld(f'<label for="pu-user">Member</label><select id="pu-user" name="user" required>{user_opts}'
                         "</select>")
        + duration_fields("pu", v["user"], permanent=True) + level_fields("pu", USER_LEVELS, v["user"])
        + reason_field("pu", v["user"], "e.g. thesis deadline on Nov 30"),
        f'<button class="btn btn-primary" type="submit">{icon("star")}Give priority</button>',
        "All their waiting jobs, also new ones. Back to normal at the end, or never with “Permanent”: for a member "
        "who should always have a larger share (e.g. a funded project).")
    r = v["reservation"]
    nodes = [n["name"] for n in attempt(problems, slurm.nodes, default=[])]
    node_opts = "".join(f'<option value="{h(n)}"{" selected" if n == r.get("node") else ""}>{h(n)}</option>'
                        for n in nodes)
    start = r.get("start", "now")
    reserve = add_form(
        req, "reservation",
        fld(f'<label for="pr-users">Members</label><input id="pr-users" name="users" required list="pr-known" '
            f'value="{h(r.get("users", ""))}" autocomplete="off" placeholder="e.g. alice, bob">'
            '<datalist id="pr-known">' + "".join(f'<option value="{h(u)}">' for u in users) + "</datalist>")
        + fld(f'<label for="pr-node">Node</label><select id="pr-node" name="node" required>{node_opts}</select>')
        + fld(f'<label for="pr-cores">CPUs</label><input id="pr-cores" name="cores" inputmode="numeric" '
              f'value="{h(r.get("cores", ""))}" placeholder="empty: the whole node">'
              '<div class="hint">CPU threads, as jobs count them (-c). Empty reserves the whole node.</div>')
        + fld(f'<label for="pr-start">Start</label><select id="pr-start" name="start">'
              f'<option value="now"{" selected" if start == "now" else ""}>Now</option>'
              f'<option value="date"{" selected" if start == "date" else ""}>At a date (next field)</option></select>')
        + fld(f'<label for="pr-start-date">Start date</label><input id="pr-start-date" name="start_date" '
              f'type="datetime-local" value="{h(r.get("start_date", ""))}">')
        + '<div class="fld"></div>' + duration_fields("pr", r)
        + reason_field("pr", r, "e.g. revision experiments, due Dec 1")
        + fld('<label class="check"><input type="checkbox" name="confirm" value="1"><span>I know a whole node for '
              "more than 14 days is closed to everyone else</span></label>", wide=True),
        f'<button class="btn btn-primary" type="submit">{icon("lock")}Reserve</button>',
        "Their jobs use it automatically. Others cannot use it while it lasts, even when it sits idle. Jobs already "
        "running there keep running; their CPUs join the reservation as they end. Duration counts from the start.")
    return card("One job first", job) + card("One person first", person) + card("Reserve CPUs", reserve)


def requests_card(req, info):
    """Members' reservation requests (their Reservations page): approve as asked or with changes, or decline."""
    pending = info.get("pending") or []
    rows = []
    for r in pending:
        base = link(req, f"/admin/request/{r['id']}")
        others = [u for u in r["users"] if u != r["user"]]
        check = (f'<div class="bad small">{h(r["problem"])}: approve less, or reserve it yourself below.</div>'
                 if r.get("problem") else '<div class="small muted">Fits their limit.</div>')
        rows.append(
            f'<tr><td>{user_link(req, r["user"])}' + (f'<div class="small">with {h(", ".join(others))}</div>' if others
                                                      else "")
            + f'</td><td>{r["cores"]} CPUs of {h(r["node"])}{check}</td>'
            f'<td>{"as soon as approved, " + h(fmt_duration(r["length"])) if r.get("length") else h(fmt_time(r["start"]) + " – " + fmt_time(r["until"]))}'
            f'</td><td class="name">{h(r["reason"])}</td><td>{h(fmt_time(r["at"]))}</td><td><div class="prio-actions">'
            f'<form class="inline-form" method="post" action="{base}/approve"><input name="cores" inputmode="numeric" '
            f'aria-label="CPUs" placeholder="CPUs ({r["cores"]})"><input name="note" aria-label="Note" placeholder="Note '
            f'(optional)"><button class="btn btn-sm btn-primary" type="submit">{icon("check")}Approve</button></form>'
            f'<form class="inline-form" method="post" action="{base}/decline"><input name="note" required '
            'aria-label="Why" placeholder="Why (the member sees it)"><button class="btn btn-sm" type="submit">'
            f'{icon("x")}Decline</button></form></div></td></tr>')
    table = ('<div class="table-wrap"><table class="table"><thead><tr><th>Member</th><th>Asks for</th><th>When</th>'
             '<th>Reason</th><th>Asked</th><th></th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"
             ) if rows else empty("No requests are waiting.")
    lim = SETTINGS["reservation_limits"]
    return card(f"Reservation requests ({len(pending)} waiting)", table + '<p class="muted small after">Members ask on '
                f"their Reservations page. Their limit: at most {lim['cpus']} CPUs at once and {lim['budget']} CPU-days "
                "over their approved "
                "reservations that have not ended (yours below do not count). Approve with fewer CPUs by typing "
                "them. They and the admins can cancel an approved one; it then shows under Active as a reservation."
                "</p>", "flush-head")


@admin_only
def request_change(req, rid, change):
    data = {"id": rid, "note": one_line(req.form.get("note"))}
    if change == "approve" and (req.form.get("cores") or "").strip():
        data["cores"] = req.form["cores"].strip()
    answer = admin.helper("request-" + change, data)
    if not answer.get("ok"):
        return priority(req, answer.get("error", "Nothing was changed."))
    return redirect(req.base + "/admin/priority?done=" + change)


def end_forms(req, b):
    base = link(req, f"/admin/priority/{b['id']}")
    opts = duration_options(b["kind"] == "user")
    return (f'<div class="prio-actions"><form method="post" action="{base}/end" data-confirm="End this now and undo it?">'
            f'<button class="btn btn-sm" type="submit">{icon("x")}End now</button></form>'
            f'<form class="inline-form" method="post" action="{base}/extend"><input type="hidden" name="kind" '
            f'value="{h(b["kind"])}"><select name="duration" '
            f'aria-label="New end">{opts}</select><input name="until_date" type="datetime-local" aria-label="End date">'
            + ('<label class="check"><input type="checkbox" name="confirm" value="1"><span>whole node, '
               "&gt; 14 days</span></label>" if b["kind"] == "reservation" and not b.get("cores") else "")
            + f'<button class="btn btn-sm" type="submit">{icon("clock")}Change end</button></form></div>')


def table(req, rows, ended=False):
    if not rows:
        return empty("Nothing ended in the last 30 days." if ended else "No priority changes are active.")
    out = []
    for b in rows:
        members = ", ".join(user_link(req, u) for u in b.get("users") or [])
        problem = (f'<div class="bad small">{h(b["problem"])}</div>' if b.get("problem") else "")
        last = (f'<td>{h(fmt_time(b["ended"]))}</td><td class="name">{h(b.get("end_note", ""))}</td>' if ended
                else f'<td>{h(until_text(b.get("until")))}</td><td>{end_forms(req, b)}</td>')
        out.append(f'<tr><td>{badge(KIND_LABELS.get(b["kind"], b["kind"]), "run")}<div>{h(what(b))}</div>{problem}</td>'
                   f'<td>{members}</td><td>{h(b.get("by"))}</td><td class="name">{h(b.get("reason"))}</td>'
                   f'<td>{h(fmt_time(b.get("since")))}</td>{last}</tr>')
    tail = "<th>Ended</th><th>How</th>" if ended else "<th>Until</th><th></th>"
    return ('<div class="table-wrap"><table class="table"><thead><tr><th>What</th><th>For</th><th>By</th>'
            f'<th>Reason</th><th>Since</th>{tail}</tr></thead><tbody>' + "".join(out) + "</tbody></table></div>")


@admin_only
def priority(req, message="", values=None):
    problems = []
    info = admin.helper("priorities")
    if not info.get("ok"):
        problems.append("Priority changes: " + info.get("error", "no answer"))
    asked = admin.helper("requests")
    if not asked.get("ok"):
        problems.append("Reservation requests: " + asked.get("error", "no answer"))
    active, ended = info.get("active") or [], info.get("ended") or []
    msgs = notice("ok", h(DONE_TEXT[req.arg("done")])) if req.arg("done") in DONE_TEXT else ""
    if message:
        msgs += notice("error", h(message))
    body = "".join([
        head("Priority", "Put a waiting job or a member first (a member also permanently), or reserve CPUs for members. "
                         "Nothing here stops a running job: priority decides which waiting job starts next, and only "
                         "a reservation guarantees CPUs. Everyone sees the active changes on Cluster status, without "
                         "the reasons."),
        problems_notice(problems), msgs, '<div class="stack">', requests_card(req, asked),
        card(f"Active ({len(active)})", table(req, active), "flush-head"),
        forms(req, values or {}, problems),
        card("Ended in the last 30 days", table(req, ended, ended=True), "flush-head"), "</div>",
    ])
    return page(req, "admin-priority", "Priority", body)


@admin_only
def priority_add(req):
    form = {k: (req.form.get(k) or "").strip() for k in ("kind", "job", "user", "users", "node", "cores", "level",
                                                         "duration", "until_date", "start", "start_date", "reason",
                                                         "confirm")}
    now = time.time()
    until, error = until_from(form, now)
    data = {"kind": form["kind"], "reason": one_line(form["reason"]), "until": until}
    if form["kind"] == "job":
        data.update(job=form["job"], level=form["level"])
    elif form["kind"] == "user":
        data.update(user=form["user"], level=form["level"])
    elif form["kind"] == "reservation":
        data.update(users=form["users"], node=form["node"], cores=form["cores"] or 0, confirm=form["confirm"] == "1")
        if form["start"] == "date":
            try:
                data["start"] = int(time.mktime(time.strptime(form["start_date"], DATE_FORMAT)))
            except ValueError:
                error = error or "Give the start date and time."
            if until and form["duration"] not in ("date", "permanent") and "start" in data:
                data["until"] = data["start"] + (until - int(now))     # "for 3 days" counts from the start
    else:
        return priority(req, "Unknown kind of priority.")
    if error:
        return priority(req, error, form)
    answer = admin.helper("priority", data)
    if not answer.get("ok"):
        return priority(req, answer.get("error", "Nothing was changed."), form)
    return redirect(req.base + "/admin/priority?" + urllib.parse.urlencode({"done": "added"}))


@admin_only
def priority_change(req, bid, change):
    if change == "end":
        answer = admin.helper("priority-end", {"id": bid})
    else:
        until, error = until_from(req.form, time.time())
        if error:
            return priority(req, error)
        answer = admin.helper("priority-extend", {"id": bid, "until": until,
                                                  "confirm": req.form.get("confirm") == "1"})
    if not answer.get("ok"):
        return priority(req, answer.get("error", "Nothing was changed."))
    return redirect(req.base + "/admin/priority?done=" + ("ended" if change == "end" else "extended"))


def public_card(req, problems):
    """Everyone's Cluster status: who has priority now and which CPUs are reserved (Slurm's reservations, also the
    maintenance ones). No reasons."""
    rows = []
    for b in slurm.priority_public():
        if b.get("kind") in ("job", "user"):
            who = ", ".join(b.get("users") or [])
            rows.append((who, what(b), until_text(b.get("until"))))
    for r in attempt(problems, slurm.reservations, default=[]):
        where = f"{r['cores']} CPUs of {r['nodes']}" if r["cores"] and r["name"].startswith("prio-") else r["nodes"]
        if "MAINT" in r["flags"]:
            who, label = "everyone", f"Maintenance: no jobs on {where}"
        else:
            who, label = ", ".join(r["users"]) or "—", f"Reserved: {where}"
        when = (f"{fmt_time(r['start'])} – {fmt_time(r['end'])}" if r["end"] else f"from {fmt_time(r['start'])}, "
                "no end set")
        rows.append((who, label + (" (now)" if r["state"] == "ACTIVE" else ""), when))
    if not rows:
        return card("Priority and reservations", '<p class="muted">Nobody has priority and no CPUs are reserved: '
                                                 "waiting jobs start by fair share.</p>")
    body = ('<div class="table-wrap"><table class="table"><thead><tr><th>For</th><th>What</th><th>Until</th></tr></thead>'
            "<tbody>" + "".join(f"<tr><td>{h(a)}</td><td>{h(b)}</td><td>{h(c)}</td></tr>" for a, b, c in rows)
            + "</tbody></table></div>")
    return card(f"Priority and reservations ({len(rows)})", body + '<p class="muted small after">Set by the cluster '
                "admins for deadlines, also on members' requests (Reservations page). Priority decides which waiting job "
                "starts next; it never stops a running job. "
                f"Reserved CPUs are only for the members named.</p>", "flush-head")


def health_line(report):
    s = report.get("priority") or {}
    out = (f' · Priority: {plural(s["active"], "active change")}' + (f' ({s["permanent"]} permanent)'
                                                                      if s.get("permanent") else "")
           if s.get("active") else "")
    return out + (f' · {plural(s["requests"], "reservation request")} waiting' if s.get("requests") else "")
