"""Account page: SSH keys (written by the user's own portal server, in ~/.ssh) and, through the root helper
account_helper (sudo), the password on every node and Telegram settings."""
import glob
import json
import os
import re
import subprocess
import time

from . import slurm
from .config import SETTINGS

HELPER = SETTINGS["account_helper"]
MUST_CHANGE_DIR = SETTINGS["must_change_dir"]   # set at the portal's launch: temporary password in use
BOT_FILE = SETTINGS["bot_file"]
KEY_TYPES = {"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
             "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com"}
MAX_KEYS_BYTES = 64 * 1024
KEEP_BACKUPS = 5


def helper(cmd, data=None):
    """Run the root helper through sudo; it acts only on this user. Returns its JSON answer."""
    try:
        p = subprocess.run(["/usr/bin/sudo", "-n", HELPER, cmd], input=json.dumps(data) if data is not None else "",
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=90,
                           env=slurm.clean_env(), cwd="/")
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "error": "The account helper did not answer. Please tell the admin."}
    try:
        answer = json.loads((p.stdout.strip().splitlines() or [""])[-1])
        if isinstance(answer, dict):
            return answer
    except ValueError:
        pass
    reason = (p.stderr.strip().splitlines() or ["no answer"])[-1][:200]
    return {"ok": False, "error": f"The account helper is not available ({reason}). Please tell the admin."}


def must_change(user):
    return os.path.exists(os.path.join(MUST_CHANGE_DIR, user))


def bot_name():
    try:
        with open(BOT_FILE) as f:
            return json.load(f).get("username") or ""
    except (OSError, ValueError):
        return ""


def password_problem(user, new, repeat):
    """The same rule as the helper's, for an answer before anything is sent."""
    if new != repeat:
        return "The two new passwords differ."
    if not 10 <= len(new) <= 128:
        return "Use 10 to 128 characters."
    if not new.isprintable():
        return "Use printable characters only."
    if not re.search(r"[A-Za-z]", new) or not re.search(r"[^A-Za-z]", new):
        return "Use letters plus at least one digit or symbol."
    if user.lower() in new.lower():
        return "The password must not contain your username."
    return None


# ---------------------------------------------------------------------------------------------------- SSH keys

def keys_path(home):
    return os.path.join(home, ".ssh", "authorized_keys")


def read_keys(home):
    try:
        with open(keys_path(home)) as f:
            return f.read()
    except FileNotFoundError:
        return ""


def check_key(line):
    """One authorized_keys line -> (info, problem). info: bits, fingerprint, comment, type."""
    tokens = line.split()
    kind = next((t for t in tokens if t in KEY_TYPES or t.startswith(("ssh-", "ecdsa-", "sk-"))), None)
    if kind is None:
        return None, "not a public key"
    if kind not in KEY_TYPES:
        return None, f"{kind} keys are not accepted (use ed25519 or rsa)"
    try:
        p = subprocess.run(["/usr/bin/ssh-keygen", "-l", "-f", "-"], input=line + "\n", stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, timeout=10, env=slurm.clean_env(), cwd="/")
    except (OSError, subprocess.TimeoutExpired):
        return None, "could not be checked"
    m = re.match(r"(\d+) (\S+) (.*) \((\S+)\)$", p.stdout.strip())
    if p.returncode != 0 or not m:
        return None, "not a valid public key"
    bits, fingerprint, comment, kind_name = int(m.group(1)), m.group(2), m.group(3), m.group(4)
    if kind == "ssh-rsa" and bits < 2048:
        return None, f"RSA key with {bits} bits is too weak (at least 2048)"
    return {"bits": bits, "fingerprint": fingerprint, "comment": "" if comment == "no comment" else comment,
            "type": kind_name}, None


def check_keys(text):
    """All lines -> (list of (line number, info), list of (line number, problem))."""
    good, bad = [], []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        info, problem = check_key(line)
        if problem:
            bad.append((i, problem))
        else:
            good.append((i, info))
    return good, bad


def save_keys(home, text):
    """Write ~/.ssh/authorized_keys (0600) after keeping the previous file as authorized_keys.bak-<time>.
    Returns (changed, backup name or None)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    text = text + "\n" if text else ""
    if len(text.encode()) > MAX_KEYS_BYTES:
        raise ValueError("The keys file is too large.")
    ssh_dir = os.path.join(home, ".ssh")
    os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
    path = keys_path(home)
    old = read_keys(home)
    if old == text:
        return False, None
    backup = None
    if os.path.exists(path):
        backup = f"authorized_keys.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        with open(os.path.join(ssh_dir, backup), "w") as f:
            f.write(old)
        os.chmod(os.path.join(ssh_dir, backup), 0o600)
        for stale in sorted(glob.glob(os.path.join(ssh_dir, "authorized_keys.bak-*")))[:-KEEP_BACKUPS]:
            os.remove(stale)
    tmp = os.path.join(ssh_dir, f".authorized_keys.portal-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return True, backup
