"""Page frame (sidebar, top bar), icons and small building blocks shared by the pages."""
import os

from .config import SETTINGS
from .util import h

TITLE = SETTINGS["title"]
SUBTITLE = SETTINGS["subtitle"]
LOGOUT_URL = SETTINGS["logout_url"]

NAV = [
    ("overview", "Overview", "/", "grid"),
    ("jobs", "My jobs", "/jobs", "list"),
    ("new", "New job", "/new", "plus"),
    ("timeline", "My timeline", "/timeline", "calendar"),
    ("cluster", "Cluster status", "/cluster", "cpu"),
    ("tests", "Test runs", "/tests", "flask"),
    ("history", "History & stats", "/history", "history"),
    ("account", "Account", "/account", "user"),
]
# shown only to the cluster admins (admin_group)
ADMIN_NAV = [
    ("admin-health", "Health", "/admin/health", "pulse"),
    ("admin-jobs", "All jobs", "/admin/jobs", "layers"),
    ("admin-users", "Users", "/admin/users", "users"),
    ("admin-log", "Admin log", "/admin/log", "file"),
]

# Icon outlines in the style of the Lucide set (24×24, stroked).
_ICONS = {
    "pulse": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>'
            '<rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/>',
    "list": '<path d="m3 17 2 2 4-4"/><path d="m3 7 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/>'
            '<path d="M13 18h8"/>',
    "plus": '<circle cx="12" cy="12" r="10"/><path d="M8 12h8"/><path d="M12 8v8"/>',
    "calendar": '<path d="M8 2v4"/><path d="M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/>'
                '<path d="M3 10h18"/><path d="M8 14h.01"/><path d="M12 14h.01"/><path d="M16 14h.01"/>'
                '<path d="M8 18h.01"/><path d="M12 18h.01"/><path d="M16 18h.01"/>',
    "cpu": '<rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9" rx="1"/>'
           '<path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/><path d="M20 15h2"/>'
           '<path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/>',
    "flask": '<path d="M10 2v7.53a2 2 0 0 1-.21.9L4.72 20.55a1 1 0 0 0 .9 1.45h12.76a1 1 0 0 0 .9-1.45l-5.07-10.13'
             'A2 2 0 0 1 14 9.53V2"/><path d="M8.5 2h7"/><path d="M7 16h10"/>',
    "history": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>'
               '<path d="M12 7v5l4 2"/>',
    "user": '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/>'
           '<path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/>'
           '<path d="m19.07 4.93-1.41 1.41"/>',
    "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
    "copy": '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10'
            'c1.1 0 2 .9 2 2"/>',
    "send": '<path d="M14.54 21.69a.5.5 0 0 0 .94-.03l6.5-19a.5.5 0 0 0-.64-.63l-19 6.5a.5.5 0 0 0-.02.93l7.93 3.18'
            'a2 2 0 0 1 1.11 1.11z"/><path d="m21.85 2.15-10.94 10.94"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    "memory": '<path d="M6 19v-3"/><path d="M10 19v-3"/><path d="M14 19v-3"/><path d="M18 19v-3"/><path d="M8 11V9"/>'
              '<path d="M16 11V9"/><path d="M12 11V9"/><path d="M2 15h20"/><path d="M2 7a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2'
              'v1.1a2 2 0 0 0 0 3.84V17a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-5.1a2 2 0 0 0 0-3.84Z"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "x": '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    "back": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    "eye": '<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/>'
           '<circle cx="12" cy="12" r="3"/>',
    "file": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>'
            '<path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/>'
             '<path d="M12 17h.01"/>',
    "check": '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    "redo": '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
    "layers": '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 '
              '0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1'
              '-1.66 0L2 12.65"/>',
    "server": '<rect width="20" height="8" x="2" y="2" rx="2" ry="2"/><rect width="20" height="8" x="2" y="14" rx="2" '
              'ry="2"/><path d="M6 6h.01"/><path d="M6 18h.01"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0'
             ' 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "pause": '<rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/>',
    "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
    "lock": '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "key": '<path d="m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4"/><path d="m21 2-9.6 9.6"/>'
           '<circle cx="7.5" cy="15.5" r="5.5"/>',
}


def icon(name, cls="ic"):
    return (f'<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_ICONS[name]}</svg>')


def static_url(base, name):
    """/static/<name>?v=<mtime>: browsers fetch the new file after an update."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public", "static", name)
    try:
        version = int(os.path.getmtime(path))
    except OSError:
        version = 0
    return f"{base}/static/{name}?v={version}"


def _nav(base, items, active):
    out = []
    for key, label, url, ic in items:
        cls = "nav-item active" if key == active else "nav-item"
        current = ' aria-current="page"' if key == active else ""
        out.append(f'<a class="{cls}" href="{h(base + url)}"{current}>{icon(ic)}<span>{h(label)}</span></a>')
    return "".join(out)


def page(req, active, title, body):
    base = req.base
    admin_nav = (f'<div class="nav-label">Cluster admin</div><nav class="nav">{_nav(base, ADMIN_NAV, active)}</nav>'
                 if getattr(req, "is_admin", False) else "")
    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{h(title)} · {h(TITLE)}</title>
<link rel="icon" href="{h(static_url(base, 'favicon.svg'))}" type="image/svg+xml">
<script src="{h(static_url(base, 'theme.js'))}"></script>
<link rel="stylesheet" href="{h(static_url(base, 'portal.css'))}">
<script defer src="{h(static_url(base, 'portal.js'))}"></script>
</head>
<body data-base="{h(base)}">
<div class="shell">
  <aside class="sidebar">
    <a class="brand" href="{h(base + '/')}">{icon('pulse', 'ic brand-ic')}<span><span class="brand-name">{h(TITLE)}</span>
      <span class="brand-sub">{h(SUBTITLE)}</span></span></a>
    <div class="nav-label">My workspace</div>
    <nav class="nav">{_nav(base, NAV, active)}</nav>
    {admin_nav}
  </aside>
  <div class="main">
    <header class="topbar">
      <span class="who">{h(req.user)}</span>
      <button class="icon-btn" type="button" data-theme-toggle title="Light or dark" aria-label="Switch light or dark">{icon('sun', 'ic only-dark')}{icon('moon', 'ic only-light')}</button>
      <a class="logout" href="{LOGOUT_URL}">{icon('logout')}<span>Log out</span></a>
    </header>
    <main class="content">
{body}
    </main>
  </div>
</div>
</body>
</html>
"""


def head(title, subtitle, actions=""):
    return (f'<div class="page-head"><div><h1>{h(title)}</h1><p class="sub">{subtitle}</p></div>'
            f'<div class="actions">{actions}</div></div>')


def badge(label, style):
    return f'<span class="badge b-{h(style)}">{h(label)}</span>'


def meter(fraction, cls=""):
    pct = 0.0 if not fraction or fraction < 0 else min(fraction, 1.0) * 100
    return f'<div class="meter {cls}"><div class="fill" style="width:{pct:.1f}%"></div></div>'


def stat_card(label, value, sub, ic):
    return (f'<div class="card stat"><div class="stat-top"><span>{h(label)}</span>{icon(ic, "ic stat-ic")}</div>'
            f'<div class="stat-value">{value}</div><div class="stat-sub">{sub}</div></div>')


def notice(kind, html_text):
    ic = {"info": "info", "ok": "check", "error": "alert", "warn": "alert"}.get(kind, "info")
    return f'<div class="notice n-{kind}">{icon(ic)}<div>{html_text}</div></div>'


def empty(text):
    return f'<div class="empty">{h(text)}</div>'
