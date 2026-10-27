"""Cluster admin pages (Unix group admin_group in the settings): every user's jobs, the accounts and the admin log (the nodes: adminhealth).

Job and node actions run as the admin, with their own Slurm Administrator level (Slurm checks it and records who did
what); account changes go through the root helper (admin_helper), which asks for the admin's own password. Every action
is written to the admin log. For everyone else these pages do not exist."""
import time
import urllib.parse

from . import admin, slurm
from .config import SETTINGS, all_nodes, node_list
from .layout import badge, empty, head, icon, notice, page, stat_card
from .response import redirect
from .util import fmt_mb, fmt_num, fmt_time, h, plural
from .views import (attempt, card, idle_badge, job_details, job_start_cell, job_time_cell, job_where_cell, kv_rows,
                    link, not_found, problems_notice)

USAGE_DAYS = 30
NOTE_LIMIT = 200
# action: (button, word for the note on the job, message after it)
JOB_ACTIONS = {
    "cancel": ("Cancel job", "Cancelled", "Cancel request sent to Slurm."),
    "hold": ("Hold", "Held", "The job is held: it does not start until it is released."),
    "release": ("Release", "Released", "The job is released: it waits for its turn again."),
    "requeue": ("Requeue", "Requeued", "The job is back in the queue: it starts again from the beginning."),
}
HELD_REASONS = {"JobHeldAdmin", "JobHeldUser"}
STATE_FILTERS = [("", "All states"), ("running", "Running"), ("waiting", "Waiting"), ("held", "Held")]


def admin_only(view):
    """For anyone but the admins these pages do not exist ("not found", not "forbidden")."""
    def wrapped(req, *args, **kw):
        if not req.is_admin:
            return not_found(req)
        return view(req, *args, **kw)
    wrapped.__name__ = view.__name__
    return wrapped


def one_line(text, limit=NOTE_LIMIT):
    return " ".join((text or "").split())[:limit]


def level_notice(req):
    level = admin.slurm_level(req.user)
    if level == "Administrator":
        return ""
    return notice("warn", f"Your Slurm admin level is “{h(level or 'unknown')}”, not Administrator: Slurm refuses the "
                          f"job and node actions until root sets it (sacctmgr modify user {h(req.user)} set "
                          "adminlevel=Administrator).")


def held(j):
    return j["state"] == "REQUEUE_HOLD" or (j["state"] == "PENDING" and j["reason"] in HELD_REASONS)


def state_badge(j):
    label, style = ("Held", "wait") if held(j) else slurm.state_label(j["state"])
    return badge(label, style)


def user_link(req, user):
    return f'<a href="{link(req, "/admin/user/" + user)}">{h(user)}</a>' if user else "—"


def jobs_table(req, jobs, idle=None):
    now = time.time()
    rows = []
    for j in jobs:
        url = link(req, f"/admin/job/{j['id']}")
        rows.append(
            f'<tr data-href="{url}"><td class="mono"><a href="{url}">{h(j["id"])}</a></td>'
            f'<td>{user_link(req, j["user"])}</td><td class="name">{h(j["name"])}</td>'
            f'<td>{state_badge(j)}{idle_badge(j, idle)}</td><td>{h(j["cpus"] or "—")}</td>'
            f'<td>{h(fmt_mb(j["mem_mb"]))}</td><td>{job_where_cell(j, now)}</td><td>{h(job_time_cell(j, now))}</td>'
            f'<td>{job_start_cell(j, now)}</td></tr>')
    return ('<div class="table-wrap"><table class="table clickable"><thead><tr><th>ID</th><th>User</th><th>Name</th>'
            '<th>State</th><th>CPUs</th><th>Memory</th><th>Node</th><th>Time</th><th>Start</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table></div>")


def password_field(fid):
    return (f'<label for="{fid}">Your password</label><input id="{fid}" name="password" type="password" '
            'autocomplete="current-password" required>')


@admin_only
def home(req):
    return redirect(req.base + "/admin/health")


# ---------------------------------------------------------------------------------------------------- jobs

def _state_matches(j, which):
    if which == "running":
        return j["state"] in slurm.RUNNING_STATES
    if which == "held":
        return held(j)
    if which == "waiting":
        return j["state"] == "PENDING" and not held(j)
    return True


@admin_only
def jobs(req):
    problems = []
    every = attempt(problems, slurm.all_active_jobs, default=[])
    nodes = attempt(problems, slurm.nodes, default=[])
    idle = slurm.idle_status()
    who, which = req.arg("user"), req.arg("state")
    which = which if which in dict(STATE_FILTERS) else ""
    shown = [j for j in every if (not who or j["user"] == who) and _state_matches(j, which)]
    running = [j for j in every if j["state"] in slurm.RUNNING_STATES]
    waiting = [j for j in every if j["state"] == "PENDING" and not held(j)]
    cards = "".join([
        stat_card("Running jobs", str(len(running)), plural(len({j["user"] for j in running}), "user"), "layers"),
        stat_card("Waiting jobs", str(len(waiting)), "in the queue", "clock"),
        stat_card("Held jobs", str(sum(1 for j in every if held(j))), "by their users or an admin", "pause"),
        stat_card("CPUs in use", str(sum(j["cpus"] or 0 for j in running)), f"of {sum(n['cpus'] for n in nodes)}", "cpu"),
    ])
    table = jobs_table(req, shown, idle) if shown else empty(
        "No jobs match." if who or which else "No running or waiting jobs.")
    inner = problems_notice(problems) + f'<div class="grid stats">{cards}</div>' + card("", table, "flush")
    if req.partial:
        return inner
    users = sorted({j["user"] for j in every if j["user"]} | ({who} if who else set()))
    user_opts = '<option value="">All users</option>' + "".join(
        f'<option value="{h(u)}"{" selected" if u == who else ""}>{h(u)}</option>' for u in users)
    state_opts = "".join(f'<option value="{v}"{" selected" if v == which else ""}>{h(label)}</option>'
                         for v, label in STATE_FILTERS)
    filters = (f'<form class="filters" method="get" action="{link(req, "/admin/jobs")}"><select name="user" '
               f'data-autosubmit aria-label="User">{user_opts}</select><select name="state" data-autosubmit '
               f'aria-label="State">{state_opts}</select><noscript><button class="btn">Show</button></noscript></form>')
    query = urllib.parse.urlencode([(k, v) for k, v in (("user", who), ("state", which)) if v] + [("partial", "1")])
    body = "".join([
        head("All jobs", "Every user's running and waiting jobs. Open a job to cancel, hold, release or requeue it.",
             filters),
        level_notice(req),
        f'<div data-refresh="{link(req, "/admin/jobs?" + query)}" data-interval="15000">{inner}</div>',
    ])
    return page(req, "admin-jobs", "All jobs", body)


@admin_only
def job(req, job_id, message=""):
    try:
        j = slurm.any_job(job_id)
    except slurm.SlurmError as e:
        return page(req, "admin-jobs", f"Job {job_id}", head(f"Job {job_id}", "") + notice("error", h(str(e))))
    if j is None:
        return not_found(req, f"Slurm does not know job {job_id} (any more).")
    msgs = ""
    done = req.arg("done")
    if done in JOB_ACTIONS:
        msgs += notice("ok", h(JOB_ACTIONS[done][2]))
    if message:
        msgs += notice("error", h(message))
    if j.get("admin_note"):
        msgs += notice("info", f"Note on this job (its user sees it): {h(j['admin_note'])}")
    where = f" · on {h(j['nodes'])}" if j["nodes"] else ""
    subtitle = (f'{state_badge(j)} <span class="job-name">{h(j["name"])}</span> · {user_link(req, j["user"])}'
                f'{where}')
    if j["active"]:
        buttons = [("cancel", "btn btn-danger", "x", f"Cancel job {j['id']} of {j['user']}?")]
        if j["state"] == "PENDING" and not held(j):
            buttons.append(("hold", "btn", "pause", f"Hold job {j['id']}? It does not start until it is released."))
        if held(j):
            buttons.append(("release", "btn", "play", f"Release job {j['id']}?"))
        if j["state"] in slurm.RUNNING_STATES:
            buttons.append(("requeue", "btn", "redo", f"Requeue job {j['id']}? It stops now and later starts again "
                                                      "from the beginning."))
        btns = "".join(f'<button class="{cls}" type="submit" name="action" value="{a}" data-confirm="{h(q)}">'
                       f'{icon(ic)}{h(JOB_ACTIONS[a][0])}</button>' for a, cls, ic, q in buttons)
        form = (f'<form class="admin-form" method="post" action="{link(req, "/admin/job/" + j["id"] + "/action")}">'
                f'<label for="j-reason">Reason</label><input id="j-reason" name="reason" maxlength="{NOTE_LIMIT}" '
                'autocomplete="off" placeholder="e.g. it asks for 96 CPUs but uses one">'
                '<div class="hint">The user sees it on the job\'s page as a note from the cluster, and the admin log '
                'keeps it. Needed for everything except Release.</div>'
                f'<div class="form-actions">{btns}</div></form>')
    else:
        form = '<p class="muted">The job has ended: there is nothing to do.</p>'
    script = slurm.job_script(j)
    details = job_details(j) + ('<p class="muted small after">The output files stay private to the user: their '
                                "home is not readable by others.</p>")
    body = "".join([
        head(f"Job {j['id']}", subtitle, f'<a class="btn" href="{link(req, "/admin/jobs")}">{icon("back")}All jobs</a>'),
        level_notice(req), msgs,
        '<div class="grid two-one"><div class="stack">', card("Admin actions", form),
        card("Batch script", f'<pre class="code script">{h(script)}</pre>' if script else
             '<p class="muted">Not available: Slurm keeps a job\'s script only until a few minutes after it ends.</p>'),
        '</div><div class="stack">', card("Details", details), "</div></div>",
    ])
    return page(req, "admin-jobs", f"Job {j['id']}", body)


def note_text(req, action, reason, why=""):
    return f"{JOB_ACTIONS[action][1]} by {req.user} on {time.strftime('%Y-%m-%d %H:%M')}{why}: {reason}"


@admin_only
def job_action(req, job_id):
    action, reason = req.form.get("action", ""), one_line(req.form.get("reason"))
    if action not in JOB_ACTIONS:
        return job(req, job_id, "Unknown action.")
    try:
        j = slurm.any_job(job_id)
    except slurm.SlurmError as e:
        return job(req, job_id, str(e))
    if j is None or not j["active"]:
        return job(req, job_id, "This job is no longer running or waiting.")
    if not reason and action != "release":
        return job(req, job_id, "Give a reason: the user sees it on the job's page.")
    try:
        if reason:
            slurm.set_admin_note(j["id"], note_text(req, action, reason))
        slurm.job_action(j["id"], action)
    except slurm.SlurmError as e:
        return job(req, job_id, str(e))
    admin.helper("note", {"action": action, "target": f"job {j['id']} user={j['user']}", "reason": reason})
    return redirect(req.base + f"/admin/job/{j['id']}?done={action}")


# ---------------------------------------------------------------------------------------------------- users

def accounts(problems):
    info = admin.helper("users")
    if not info.get("ok"):
        problems.append("Accounts: " + info.get("error", "no answer"))
        return []
    problems.extend(info.get("problems") or [])
    return info.get("users") or []


def status_badges(u):
    out = []
    if u["admin"]:
        out.append(badge("admin", "run"))
    if u["locked"]:
        out.append(badge("locked", "bad"))
    if u["must_change"]:
        out.append(badge("temporary password", "wait"))
    if u["missing"] or u["uid_differs"]:
        out.append(badge("differs between nodes", "bad"))
    return " ".join(out) or badge("active", "done")


def usage(problems):
    every = attempt(problems, slurm.all_active_jobs, default=[])
    share = attempt(problems, slurm.fairshare_all, default={}) or {}
    hours = attempt(problems, slurm.cpu_hours_by_user, USAGE_DAYS, default={}) or {}
    return every, share, hours


@admin_only
def users(req, message="", values=None):
    problems = []
    accts = accounts(problems)
    every, share, hours = usage(problems)
    rows = []
    for u in accts:
        mine = [j for j in every if j["user"] == u["user"]]
        running = [j for j in mine if j["state"] in slurm.RUNNING_STATES]
        url = link(req, "/admin/user/" + u["user"])
        name = h(u["name"]) if u["name"] else '<span class="muted">—</span>'
        rows.append(f'<tr data-href="{url}"><td class="mono"><a href="{url}">{h(u["user"])}</a></td>'
                    f'<td class="name">{name}</td><td>{u["uid"]}</td><td>{status_badges(u)}</td>'
                    f'<td>{len(running)}</td><td>{len(mine) - len(running)}</td>'
                    f'<td>{sum(j["cpus"] or 0 for j in running)}</td>'
                    f'<td>{h(fmt_num(hours.get(u["user"], 0), 1))}</td><td>{h(fmt_num(share.get(u["user"]), 2))}</td></tr>')
    table = ('<div class="table-wrap"><table class="table clickable"><thead><tr><th>User</th><th>Name</th><th>UID</th>'
             f'<th>Status</th><th>Running</th><th>Waiting</th><th>CPUs in use</th><th>CPU-h ({USAGE_DAYS} d)</th>'
             '<th>Fair share</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"
             ) if rows else empty("No accounts could be read.")
    v = values or {}
    add_form = (
        f'<form class="admin-form" method="post" action="{link(req, "/admin/users/add")}" '
        f'data-confirm="Create this account on {h(node_list())}?">'
        f'<label for="u-user">User name</label><input id="u-user" name="user" value="{h(v.get("user", ""))}" required '
        'pattern="[a-z][a-z0-9_\\-]{1,31}" maxlength="32" autocomplete="off" spellcheck="false" placeholder="e.g. kimjh">'
        '<div class="hint">2 to 32 characters: lower-case letters, digits, _ or -, starting with a letter.</div>'
        f'<label for="u-name">Full name (optional)</label><input id="u-name" name="name" value="{h(v.get("name", ""))}" '
        'maxlength="64" autocomplete="off">' + password_field("u-pw")
        + '<div class="hint">Account changes need your own password.</div>'
        f'<div class="form-actions"><button class="btn btn-primary" type="submit">{icon("plus")}Create account</button>'
        f'</div><p class="muted small">The next free UID on {all_nodes()}, a home and a /shared folder '
        f'({h(SETTINGS["shared_folder_node"])}), a Slurm user in account {h(SETTINGS["slurm_account"])}, and a '
        "temporary password, shown once.</p></form>")
    body = "".join([
        head("Users", f"{plural(len(accts), 'account')} on {h(node_list())}. CPU-hours over the last "
                      f"{USAGE_DAYS} days, from Slurm's hourly accounting."),
        problems_notice(problems), notice("error", h(message)) if message else "",
        f'<div class="stack">{card("", table, "flush")}<div class="grid two">{card("Add a user", add_form)}</div></div>',
    ])
    return page(req, "admin-users", "Users", body)


def secret_notice(intro, user, password):
    return notice("ok", f'{h(intro)} Temporary password for <b>{h(user)}</b>:'
                        f'<div class="secret"><code id="temp-pw">{h(password)}</code><button class="btn btn-sm" '
                        f'type="button" data-copy="#temp-pw">{icon("copy")}Copy</button></div>'
                        '<span class="small">Give it to them personally. It is shown only now and stored nowhere; at '
                        "their first sign-in the portal asks them to choose their own.</span>")


DONE_TEXT = {"lock": f"The account is locked on {all_nodes()}: the password and SSH keys no longer work.",
             "unlock": f"The account is unlocked on {all_nodes()}."}


@admin_only
def user(req, name, message="", outcome=None):
    problems = []
    accts = accounts(problems)
    u = next((a for a in accts if a["user"] == name), None)
    if u is None:
        if accts:
            return not_found(req, f"There is no account {name}.")
        body = head(name, "") + problems_notice(problems)
        return page(req, "admin-users", name, body)
    every, share, hours = usage(problems)
    mine = [j for j in every if j["user"] == name]
    running = [j for j in mine if j["state"] in slurm.RUNNING_STATES]
    msgs = ""
    if outcome and outcome.get("password"):
        intro = (f"Account {name} created (UID {outcome['uid']}) on {node_list()}."
                 if outcome.get("uid") else f"New password set for {name} on {all_nodes()}."
                 + (" The account is still locked: unlock it to let them sign in." if outcome.get("locked") else ""))
        msgs += secret_notice(intro, name, outcome["password"])
    done = req.arg("done")
    if done in DONE_TEXT:
        msgs += notice("ok", h(DONE_TEXT[done]) + (f" {h(plural(int(req.arg('cancelled')), 'job'))} cancelled."
                                                   if req.arg("cancelled").isdigit() and int(req.arg("cancelled")) else ""))
    if message:
        msgs += notice("error", h(message))
    rows = [("UID / GID", str(u["uid"])), ("Name", h(u["name"]) if u["name"] else "—"),
            ("Home", f'<span class="mono">/home/{h(name)}</span>'), ("Status", status_badges(u)),
            ("On the nodes", h(" · ".join(f"{n} {s}" for n, s in u["nodes"].items())))]
    if u["missing"]:
        rows.append(("Missing on", f'<span class="bad">{h(", ".join(u["missing"]))}</span>'))
    if u["uid_differs"]:
        rows.append(("Other UID on", f'<span class="bad">{h(", ".join(u["uid_differs"]))}</span>'))
    info = u.get("lock_info")
    if u["locked"] and info:
        rows.append(("Locked", f'by {h(info.get("by"))}, {h(fmt_time(info.get("at")))}: {h(info.get("reason"))}'))
    rows += [("Running jobs", f"{len(running)} ({sum(j['cpus'] or 0 for j in running)} CPUs)"),
             ("Waiting jobs", str(len(mine) - len(running))),
             (f"CPU-hours ({USAGE_DAYS} days)", h(fmt_num(hours.get(name, 0), 1))),
             ("Fair share", h(fmt_num(share.get(name), 2)))]
    base = link(req, "/admin/user/" + name)
    if name == req.user:
        actions = (f'<p class="muted">This is your own account: change your password on the '
                   f'<a href="{link(req, "/account")}">Account</a> page.</p>')
    else:
        actions = (f'<form class="admin-form" method="post" action="{base}/password" data-confirm="Give {h(name)} a '
                   f'new temporary password? The current one stops working on {all_nodes()}.">'
                   '<div class="section-title">Reset password</div><p class="muted small">A new temporary password on '
                   f"{all_nodes()}, shown once; the portal asks for their own at the next sign-in.</p>"
                   + password_field("p-reset")
                   + f'<div class="form-actions"><button class="btn" type="submit">{icon("key")}Reset password</button>'
                   "</div></form>")
        if u["locked"]:
            actions += (f'<form class="admin-form" method="post" action="{base}/unlock" data-confirm="Unlock '
                        f'{h(name)} on {all_nodes()}?"><div class="section-title">Unlock</div>'
                        + password_field("p-unlock")
                        + f'<div class="form-actions"><button class="btn" type="submit">{icon("play")}Unlock account'
                        "</button></div></form>")
        elif u["admin"]:
            actions += ('<div class="section-title">Lock</div><p class="muted small">Admins are not locked here: root '
                        f"removes them from {h(admin.ADMIN_GROUP)} first.</p>")
        else:
            actions += (f'<form class="admin-form" method="post" action="{base}/lock" data-confirm="Lock {h(name)}? '
                        f'Their password and SSH keys stop working on {all_nodes()}.">'
                        '<div class="section-title">Lock</div><p class="muted small">Their password and SSH keys stop '
                        f"working on {all_nodes()}; open SSH sessions and running jobs carry on unless you cancel "
                        "the jobs here.</p>"
                        f'<label for="l-reason">Reason</label><input id="l-reason" name="reason" required '
                        f'maxlength="{NOTE_LIMIT}" autocomplete="off" placeholder="e.g. left the lab">'
                        '<label class="check"><input type="checkbox" name="cancel_jobs" value="1"><span>Also cancel '
                        f"their running and waiting jobs ({len(mine)})</span></label>" + password_field("p-lock")
                        + f'<div class="form-actions"><button class="btn btn-danger" type="submit">{icon("lock")}Lock '
                        "account</button></div></form>")
    body = "".join([
        head(name, f"{status_badges(u)} {h(u['name'])}",
             f'<a class="btn" href="{link(req, "/admin/users")}">{icon("back")}Users</a>'),
        problems_notice(problems), msgs,
        '<div class="grid two-one"><div class="stack">',
        card("Running and waiting jobs", jobs_table(req, mine, slurm.idle_status()) if mine else
             empty("No running or waiting jobs."), "flush-head"),
        '</div><div class="stack">', card("Account", kv_rows(rows)), card("Account actions", actions), "</div></div>",
    ])
    return page(req, "admin-users", name, body)


@admin_only
def users_add(req):
    values = {"user": req.form.get("user", "").strip(), "name": req.form.get("name", "").strip()}
    answer = admin.helper("add-user", dict(values, password=req.form.get("password", "")))
    if not answer.get("ok"):
        return users(req, answer.get("error", "The account was not created."), values)
    return user(req, answer["user"], outcome=answer)   # not a redirect: the password is shown only in this answer


def cancel_jobs_of(req, name, reason):
    """Cancels every running and waiting job of a user being locked, each with a note."""
    mine = [j for j in attempt([], slurm.all_active_jobs, default=[]) if j["user"] == name]
    done, failed = 0, []
    for j in mine:
        try:
            slurm.set_admin_note(j["id"], note_text(req, "cancel", reason, " (account locked)"))
            slurm.job_action(j["id"], "cancel")
            done += 1
        except slurm.SlurmError as e:
            failed.append(f"job {j['id']}: {e}")
    if mine:
        admin.helper("note", {"action": "cancel-jobs", "target": f"user={name} jobs={done}", "reason": reason})
    return done, failed


@admin_only
def user_change(req, name, change):
    data = {"user": name, "password": req.form.get("password", "")}
    if change == "password":
        answer = admin.helper("reset-password", data)
        if not answer.get("ok"):
            return user(req, name, answer.get("error", "The password was not changed."))
        return user(req, name, outcome=answer)
    if change == "lock":
        reason = one_line(req.form.get("reason"))
        answer = admin.helper("lock", dict(data, reason=reason))
        if not answer.get("ok"):
            return user(req, name, answer.get("error", "The account was not locked."))
        cancelled, failed = cancel_jobs_of(req, name, reason) if req.form.get("cancel_jobs") == "1" else (0, [])
        if failed:
            return user(req, name, f"Locked, but {len(failed)} jobs could not be cancelled: " + "; ".join(failed))
        return redirect(req.base + "/admin/user/" + name + "?" + urllib.parse.urlencode(
            {"done": "lock", "cancelled": cancelled}))
    answer = admin.helper("unlock", data)
    if not answer.get("ok"):
        return user(req, name, answer.get("error", "The account was not unlocked."))
    return redirect(req.base + "/admin/user/" + name + "?done=unlock")


# ---------------------------------------------------------------------------------------------------- log

@admin_only
def log(req):
    info = admin.helper("log")
    entries = info.get("entries") or []
    rows = "".join(f'<tr><td class="mono">{h(e.get("time"))}</td><td>{h(e.get("admin") or "—")}</td>'
                   f'<td class="name">{h(e.get("text"))}</td></tr>' for e in entries)
    table = ('<div class="table-wrap"><table class="table"><thead><tr><th>Time</th><th>Admin</th><th>What</th></tr>'
             f"</thead><tbody>{rows}</tbody></table></div>") if rows else empty("Nothing has been logged yet.")
    body = "".join([
        head("Admin log", "What the admins did, newest first: accounts, jobs and nodes (the latest 300 entries, from "
                          f"{SETTINGS['admin_log']} on {SETTINGS['login_node']})."),
        "" if info.get("ok") else notice("error", h(info.get("error", "The log could not be read."))),
        card("", table, "flush"),
    ])
    return page(req, "admin-log", "Admin log", body)
