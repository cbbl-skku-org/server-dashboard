"""Reservations, for every user: ask the admins to reserve CPUs for a deadline, follow and cancel one's own requests,
and see every reservation on the cluster. The admin helper keeps the requests (sudo; every user may run its four
request commands, for their own requests only) and checks the rules when an admin approves (reservation_limits in the
settings): each request at most `cpus` CPUs for at most `days` days, starting within `ahead` days; a user's approved
reservations that have not ended hold at most `cpus` CPUs at once and cost at most `budget` CPU-days (CPUs x days
reserved, used or not)."""
import time
import urllib.parse

from . import admin, slurm
from .adminpriority import DATE_FORMAT, fld, public_card
from .config import SETTINGS, node_list
from .layout import badge, empty, head, icon, meter, notice, page
from .response import redirect
from .util import fmt_time, h
from .views import attempt, card, link, problems_notice

DURATIONS = [("1d", "For 1 day", 86400), ("3d", "For 3 days", 3 * 86400), ("7d", "For 1 week", 7 * 86400),
             ("date", "Until a date (next field)", None)]
STATES = {"pending": ("Waiting for an admin", "wait"), "approved": ("Approved", "run"), "declined": ("Declined", "bad"),
          "withdrawn": ("Withdrawn", "muted"), "ended": ("Ended", "done"), "expired": ("Not decided in time", "muted")}
DONE_TEXT = {"request": "Your request is sent. The admins decide; you see the answer here (and in Telegram if you "
                        "chose it on the Account page).",
             "withdraw": "Your request is withdrawn.", "cancel": "Your reservation is cancelled: its CPUs are free again."}


def when(r):
    return f"{fmt_time(r['start'])} – {fmt_time(r['until'])}"


def requests_table(req, rows, mine=True):
    out = []
    for r in rows:
        label, style = STATES.get(r["state"], (r["state"], "muted"))
        note = r.get("end_note") if r["state"] == "ended" else r.get("note")
        actions = ""
        if mine and r["state"] in ("pending", "approved"):
            change, text, q = (("withdraw", "Withdraw", "Withdraw this request?") if r["state"] == "pending" else
                               ("cancel", "Cancel reservation", "Cancel this reservation? Its CPUs become free for "
                                                                "everyone, also for the other members."))
            actions = (f'<form method="post" action="{link(req, "/reservations/" + r["id"] + "/" + change)}" '
                       f'data-confirm="{h(q)}"><button class="btn btn-sm" type="submit">{icon("x")}{h(text)}</button>'
                       "</form>")
        out.append(f'<tr><td>{badge(label, style)}{"<div class=small>" + h(note) + "</div>" if note else ""}</td>'
                   f'<td>{r["cores"]} CPUs of {h(r["node"])}</td><td>{h(when(r))}</td>'
                   f'<td>{h(", ".join(r["users"]))}</td><td class="name">{h(r["reason"])}</td><td>{actions}</td></tr>')
    return ('<div class="table-wrap"><table class="table"><thead><tr><th>State</th><th>What</th><th>When</th>'
            '<th>For</th><th>Reason</th><th></th></tr></thead><tbody>' + "".join(out) + "</tbody></table></div>")


def until_from(form, start):
    choice = form.get("duration", "")
    seconds = {k: s for k, _, s in DURATIONS}
    if choice not in seconds:
        return None, "Choose how long."
    if choice == "date":
        try:
            return int(time.mktime(time.strptime(form.get("until_date", ""), DATE_FORMAT))), None
        except ValueError:
            return None, "Give the end date and time."
    return int(start + seconds[choice]), None


def request_form(req, limits, values, problems):
    v = values or {}
    nodes = [n["name"] for n in attempt(problems, slurm.nodes, default=[])]
    node_opts = "".join(f'<option value="{h(n)}"{" selected" if n == v.get("node") else ""}>{h(n)}</option>'
                        for n in nodes)
    dur = v.get("duration", "1d")
    dur_opts = "".join(f'<option value="{k}"{" selected" if k == dur else ""}>{h(label)}</option>'
                       for k, label, _ in DURATIONS)
    start = v.get("start", "now")
    fields = "".join([
        fld(f'<label for="rq-node">Node</label><select id="rq-node" name="node" required>{node_opts}</select>'
            + (f'<div class="hint">{h(node_list(SETTINGS["no_internet_nodes"]))} '
               f'{"has" if len(SETTINGS["no_internet_nodes"]) == 1 else "have"} no Internet.</div>'
               if SETTINGS["no_internet_nodes"] else "")),
        fld(f'<label for="rq-cpus">CPUs</label><input id="rq-cpus" name="cores" inputmode="numeric" required '
            f'value="{h(v.get("cores", ""))}" placeholder="1 to {limits["cpus"]}"><div class="hint">CPU threads, as '
            "jobs count them (-c).</div>"),
        fld('<label for="rq-others">Other members (optional)</label><input id="rq-others" name="others" '
            f'value="{h(v.get("others", ""))}" autocomplete="off" placeholder="e.g. alice, bob">'
            '<div class="hint">Their jobs may use it too.</div>'),
        fld(f'<label for="rq-start">Start</label><select id="rq-start" name="start">'
            f'<option value="now"{" selected" if start == "now" else ""}>As soon as approved</option>'
            f'<option value="date"{" selected" if start == "date" else ""}>At a date (next field)</option></select>'),
        fld('<label for="rq-start-date">Start date</label><input id="rq-start-date" name="start_date" '
            f'type="datetime-local" value="{h(v.get("start_date", ""))}">'),
        '<div class="fld"></div>',
        fld(f'<label for="rq-dur">How long</label><select id="rq-dur" name="duration">{dur_opts}</select>'),
        fld('<label for="rq-until">End date (for “Until a date”)</label><input id="rq-until" name="until_date" '
            f'type="datetime-local" value="{h(v.get("until_date", ""))}">'),
        '<div class="fld"></div>',
        fld('<label for="rq-reason">Reason</label><input id="rq-reason" name="reason" required maxlength="200" '
            f'autocomplete="off" value="{h(v.get("reason", ""))}" placeholder="e.g. revision experiments, due Nov 30">'
            '<div class="hint">For the admins. Others see only who has the reservation.</div>', wide=True),
    ])
    return (f'<form class="admin-form" method="post" action="{link(req, "/reservations/request")}">'
            f'<div class="prio-grid">{fields}</div><div class="form-actions"><button class="btn btn-primary" '
            f'type="submit">{icon("send")}Send request</button></div><p class="muted small">Each request: at most '
            f'{limits["cpus"]} CPUs, at most {limits["days"]} days, starting within {limits["ahead"]} days. Ask as '
            "often as you like; the admins approve only what fits your limit.</p></form>")


def budget_card(info):
    lim, used = info["limits"], info["used"]
    return card("Your limit", (
        f'<div class="node-text">At most {lim["cpus"]} CPUs at once: you hold up to <b>{used["peak"]}</b></div>'
        + meter(used["peak"] / lim["cpus"])
        + f'<div class="node-text">At most {lim["budget"]} CPU-days reserved: you have <b>{used["cpu_days"]:g}</b></div>'
        + meter(used["cpu_days"] / lim["budget"], "mem")
        + '<p class="muted small after">Counted over your approved reservations that have not ended: CPUs × days '
          "reserved, used or not. The limit refills as they end, or at once when you cancel one. Reservations the "
          "admins make for you themselves do not count.</p>"))


def reservations(req, message="", values=None):
    problems = []
    info = admin.helper("requests-mine")
    if not info.get("ok"):
        problems.append("Your requests: " + info.get("error", "no answer"))
        info = {"requests": [], "limits": SETTINGS["reservation_limits"], "used": {"peak": 0, "cpu_days": 0}}
    msgs = notice("ok", h(DONE_TEXT[req.arg("done")])) if req.arg("done") in DONE_TEXT else ""
    if message:
        msgs += notice("error", h(message))
    mine = info["requests"]
    body = "".join([
        head("Reservations", "CPUs kept for you on one node for a while, for a deadline: your jobs start there without "
                             "waiting for others. Ask here; the cluster admins decide. Cancel your own at any time."),
        problems_notice(problems), msgs, '<div class="stack">',
        '<div class="grid two-one">', card("Ask for a reservation", request_form(req, info["limits"], values, problems)),
        budget_card(info), "</div>",
        card(f"Your requests ({len(mine)})", requests_table(req, mine) if mine else
             empty("You have no requests in the last 30 days."), "flush-head"),
        public_card(req, problems), "</div>",
    ])
    return page(req, "reservations", "Reservations", body)


def request_post(req):
    form = {k: (req.form.get(k) or "").strip() for k in ("node", "cores", "others", "start", "start_date", "duration",
                                                         "until_date", "reason")}
    now = int(time.time())
    start, error = now, None
    if form["start"] == "date":
        try:
            start = int(time.mktime(time.strptime(form["start_date"], DATE_FORMAT)))
        except ValueError:
            error = "Give the start date and time."
    until, error2 = until_from(form, start)
    error = error or error2
    if not form["cores"].isdigit():
        error = error or "Give the number of CPUs."
    if error:
        return reservations(req, error, form)
    answer = admin.helper("request", {"node": form["node"], "cores": int(form["cores"]), "others": form["others"],
                                      "start": "now" if form["start"] != "date" else start, "until": until,
                                      "reason": " ".join(form["reason"].split())})
    if not answer.get("ok"):
        return reservations(req, answer.get("error", "The request was not sent."), form)
    return redirect(req.base + "/reservations?done=request")


def request_change(req, rid, change):
    answer = admin.helper("request-withdraw" if change == "withdraw" else "request-cancel", {"id": rid})
    if not answer.get("ok"):
        return reservations(req, answer.get("error", "Nothing was changed."))
    return redirect(req.base + "/reservations?" + urllib.parse.urlencode({"done": change}))
