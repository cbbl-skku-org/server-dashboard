# server-dashboard

A web portal for a Slurm CPU cluster, used on the CBBL lab's cluster at Sungkyunkwan University. Users see and submit
their jobs without a terminal; cluster admins get pages for every job, the nodes, the accounts and the cluster's health.
It runs as an [Open OnDemand](https://openondemand.org/) system app and replaces Open OnDemand's own pages.

- **Pages for everyone:** Overview, My jobs (with a page per job: live output, CPU and memory use, cancel), New job
  (a form that builds the batch script and shows the equivalent `sbatch` command), My timeline, Cluster status,
  Test runs (quick jobs in a debug partition), History & stats (with CSV export) and Account (password, SSH keys,
  Telegram notifications).
- **Pages for cluster admins** (members of one Unix group): Health, All jobs (cancel, hold, release, requeue with a
  note the user sees), Nodes (drain, resume), Users (add, reset password, lock, unlock) and Admin log. Everyone else
  gets "not found".
- **Python 3.9+, standard library only.** No database, no build step, no JavaScript framework: pages are rendered on
  the server and work without JavaScript; `portal.js` only adds live refresh and small helpers.

## Try it on your computer

No cluster needed. The fake Slurm in `tests/fakeslurm.py` answers every command with made-up data:

```
git clone git@github.com:cbbl-skku-org/server-dashboard.git
cd server-dashboard
python3 tests/dev_server.py              # open http://127.0.0.1:8080/  (signed in as "alice", a cluster admin)
python3 tests/dev_server.py --user-only  # the same as a user without the admin pages
```

Nothing real happens: submitted jobs and account changes only reach the fake Slurm.

## Tests

```
python3 -m unittest discover -s tests    # fake Slurm, under a second; GitHub runs it on every pull request
```

| File | What |
|---|---|
| `tests/test_portal.py`, `tests/test_config.py` | the unit tests |
| `tests/fakeslurm.py` | the fake Slurm, fake root helpers and their sample answers (also the reference for their formats) |
| `tests/dev_server.py` | the portal with the fake Slurm in a small local web server |
| `tests/screenshot.py`, `tests/js_form_check.py`, `tests/js_job_live_check.py` | screenshots and browser checks of the JavaScript (need PyQt5 with QtWebEngine; see each file) |
| `tests/smoke_real.py` | read-only check against a real Slurm: renders every page for one user (run as root on the cluster) |

## How it works in production

```
browser ──HTTPS──▶ Apache (Open OnDemand front end)
                    /login     the sign-in page (login/login.html)
                    /signin    Apache checks the password (e.g. mod_auth_form + pwauth); the session is a cookie
                    /pun/...   signed-in users only: Open OnDemand starts a small nginx AS THAT USER
                                   └─ Passenger runs app/ as that user
                                          └─ squeue, sacct, sbatch, scancel, scontrol, sstat, sshare, sreport, sacctmgr
```

- Everything after sign-in runs with the user's own rights: no shared service submits jobs or reads files for users.
  Slurm is read through its commands' JSON output (`--json`; tested with Slurm 26.05).
- Two things need root, and go through `sudo` to small helper programs (not part of this repository): the account
  helper (password on every node, Telegram settings) and the admin helper (accounts, the health report, the admin
  log). Each gets one command as its argument and JSON on stdin, and prints a JSON object as its last line of output.
  The commands and answers the portal expects are in `fake_helper` and `fake_admin_helper` in `tests/fakeslurm.py`.
  Admin actions on jobs and nodes need the admin's Slurm `adminlevel=Administrator`.
- Optional inputs: the idle-job check's status file (`idle_status`) and a notifier's bot name (`bot_file`).
  Without them the related parts of the pages are simply missing.
- Forms are accepted only from the portal's own pages (`Origin` check; the session cookie should be `SameSite=Lax`).

## Install on a cluster

1. Copy `app/` to `/var/www/ood/apps/sys/cbbl/` (Open OnDemand serves it at `/pun/sys/cbbl`), and
   `login/login.html`, edited with your cluster's name, to where Apache serves `/login`.
2. Write your settings: copy `config.example.json` to `/etc/server-dashboard/config.json` (readable by all users)
   and change what differs. Every key is explained in `app/cbbl/config.py`; keys you leave out keep the defaults,
   which describe an example cluster (node1, node2, node3).
3. In Open OnDemand's `ood_portal.yml`, send `/` to the app, protect `/pun` with your sign-in, and block Open
   OnDemand's own dashboard if you want this portal to be the only one.
4. For the Account and admin pages: install your helpers and allow them in sudoers (users: the account helper;
   the admin group: the admin helper).

Updating is copying `app/` again: each user's server reloads it within about 10 seconds.

## Files

| Path | What |
|---|---|
| `app/passenger_wsgi.py` | entry point for Passenger → `app/cbbl/web.py` (routes, request checks) |
| `app/cbbl/config.py` | the settings and their defaults |
| `app/cbbl/slurm.py` | every Slurm command and the parsing of its JSON |
| `app/cbbl/views.py`, `layout.py` | the users' pages; the page frame and icons |
| `app/cbbl/jobform.py` | New job: checks, batch script, equivalent command |
| `app/cbbl/account.py` | Account: SSH keys (written as the user), the account helper |
| `app/cbbl/admin.py`, `adminviews.py`, `adminhealth.py` | who is an admin, the admin helper, the admin pages |
| `app/public/static/` | CSS, JavaScript, icon |
| `login/login.html` | the sign-in page (static, served by Apache) |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security problems: see [SECURITY.md](SECURITY.md), and please don't open a
public issue for them.

## Licence

Apache License 2.0, see [LICENSE](LICENSE). Copyright 2026 CBBL, Sungkyunkwan University.
