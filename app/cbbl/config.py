"""Site settings: built-in defaults, overridden by a JSON file.

The defaults describe a small example cluster (node1, node2, node3: the one the fake Slurm in tests/ imitates). A real
site puts its own values in $SERVER_DASHBOARD_CONFIG, or /etc/server-dashboard/config.json. Only the keys given are
changed (one level deep for the "idle_rule" and "health" sections); an unknown key is an error, so typos show up.
config.example.json lists every key with its default."""
import json
import os

PATH = os.environ.get("SERVER_DASHBOARD_CONFIG") or "/etc/server-dashboard/config.json"

DEFAULTS = {
    # page frame and sign-in
    "title": "CPU Cluster",
    "subtitle": "Slurm portal",
    "app_uri": "/pun/sys/cbbl",                      # where Open OnDemand serves the app (/var/www/ood/apps/sys/cbbl)
    "logout_url": "/signout",
    # the cluster, for the texts on the pages
    "nodes": ["node1", "node2", "node3"],           # where accounts and passwords live
    "login_node": "node1",                          # runs slurmctld and the admin log; users run pipelines here
    "no_internet_nodes": ["node2"],
    "shared_folder_node": "node3",                  # new accounts get a /shared folder here
    # admins and the two root helpers (sudo; not part of this repository)
    "admin_group": "clusteradmin",
    "admin_helper": "/usr/local/sbin/portal-admin",
    "admin_log": "/var/log/portal-admin.log",
    "account_helper": "/usr/local/sbin/portal-account",
    "must_change_dir": "/var/lib/portal-account/must-change",
    "bot_file": "/var/lib/portal-notify/bot.json",
    # Slurm
    "slurm_bin": "/usr/bin",
    "slurm_account": "lab",
    "partition": "cpu",
    "debug_partition": "debug",
    "features": {"internet": "Internet", "offline": "no Internet", "nfs-server": "file server"},
    "idle_status": "/var/lib/portal-idle-jobs/status.json",
    "idle_rule": {"cpu_threshold": 0.05, "idle_minutes": 60, "warn_minutes": 45, "min_cpus": 1},
    "idle_note_prefix": "Idle check",               # the idle check's note on the jobs it cancels starts with this
    # the admins' Health page
    "health": {
        "disk_warn": 90, "disk_bad": 95,             # % used, as df counts it
        "clock_warn": 1.0, "clock_bad": 60.0,        # seconds; munge refuses beyond 5 minutes
        "mem_warn": 0.10,                            # free memory below 10 %
        "idle_stale": 15 * 60,                       # the idle check writes its status every 5 minutes
        "backup_max_age": 30 * 3600, "backup_long_run": 26 * 3600,
        # timer: [name on the page, schedule, a last run older than this many seconds is a problem]
        "timers": {"idle-jobs": ["Idle-job check", "every 5 minutes", 15 * 60],
                   "notify": ["Telegram notifier", "every minute", 5 * 60],
                   "home-backup": ["Home backup", "nightly", 30 * 3600]},
        # [node, log key in the helper's report, title]
        "logs": [["node1", "idle-jobs", "Idle-job check"], ["node1", "notify", "Telegram notifier"],
                 ["node2", "home-backup", "Home backup"]],
        "backup_node": "node2",
        "backup_timer": "home-backup",
        "backup_note": "",                           # shown under the backup card
        "controller_note": "",                       # e.g. where the known slurmctld warnings are explained
    },
}
NESTED = ("idle_rule", "health")


def load(path=PATH):
    settings = json.loads(json.dumps(DEFAULTS))
    try:
        with open(path) as f:
            given = json.load(f)
    except FileNotFoundError:
        return settings
    except ValueError as e:
        raise ValueError(f"{path} is not valid JSON: {e}") from None
    if not isinstance(given, dict):
        raise ValueError(f"{path} must hold one JSON object")
    for key, value in given.items():
        if key.startswith("_"):          # comments
            continue
        if key not in DEFAULTS:
            raise ValueError(f"{path}: unknown setting {key!r}")
        if key in NESTED:
            unknown = set(value) - set(DEFAULTS[key])
            if unknown:
                raise ValueError(f"{path}: unknown setting {key}.{sorted(unknown)[0]}")
            settings[key].update(value)
        else:
            settings[key] = value
    return settings


SETTINGS = load()
NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def all_nodes():
    """'all three nodes' (or 'the node' for a one-node cluster)."""
    n = len(SETTINGS["nodes"])
    return "the node" if n == 1 else "both nodes" if n == 2 else f"all {NUMBER_WORDS.get(n, n)} nodes"


def node_list(names=None):
    """'node1, node2 and node3'"""
    names = list(SETTINGS["nodes"] if names is None else names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
