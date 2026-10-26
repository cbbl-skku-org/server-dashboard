"""Who the cluster admins are (Unix group admin_group in the settings) and the root helper admin_helper
(sudo) that changes accounts for them. Their Slurm actions are in slurm.py: they run with the admin's own rights."""
import grp
import json
import pwd
import subprocess
import time

from . import slurm
from .config import SETTINGS

ADMIN_GROUP = SETTINGS["admin_group"]
HELPER = SETTINGS["admin_helper"]
LEVEL_TTL = 300
_levels = {}


def admins():
    try:
        g = grp.getgrnam(ADMIN_GROUP)
    except KeyError:
        return set()
    return set(g.gr_mem) | {p.pw_name for p in pwd.getpwall() if p.pw_gid == g.gr_gid}


def is_admin(user):
    return user in admins()


def slurm_level(user):
    """The user's Slurm admin level, asked at most every 5 minutes; None if sacctmgr does not answer."""
    hit = _levels.get(user)
    if hit and time.time() - hit[1] < LEVEL_TTL:
        return hit[0]
    try:
        level = slurm.admin_level(user)
    except slurm.SlurmError:
        return None
    _levels[user] = (level, time.time())
    return level


def helper(cmd, data=None, timeout=180):
    """Run the admin helper through sudo. Returns its JSON answer (or {"ok": False, "error": ...})."""
    try:
        p = subprocess.run(["/usr/bin/sudo", "-n", HELPER, cmd], input=json.dumps(data) if data is not None else "",
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout,
                           env=slurm.clean_env(), cwd="/")
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "error": "The admin helper did not answer."}
    try:
        answer = json.loads((p.stdout.strip().splitlines() or [""])[-1])
        if isinstance(answer, dict):
            return answer
    except ValueError:
        pass
    reason = (p.stderr.strip().splitlines() or ["no answer"])[-1][:200]
    return {"ok": False, "error": f"The admin helper is not available ({reason})."}
