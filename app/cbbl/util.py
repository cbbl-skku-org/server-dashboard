"""Formatting and parsing helpers shared by the pages."""
import html
import math
import re
import time

# Memory is shown the way Slurm counts it: 1 GB = 1024 MB, so "16 GB" in the portal is --mem=16G.
MB_PER_GB = 1024


def h(value):
    """HTML-escape any value for text and attribute positions."""
    return html.escape("" if value is None else str(value), quote=True)


def fmt_mb(mb, digits=0):
    if mb is None:
        return "—"
    if mb >= 1024 * MB_PER_GB:
        return f"{mb / (1024 * MB_PER_GB):.1f} TB"
    if mb >= MB_PER_GB or mb == 0:
        gb = mb / MB_PER_GB
        if digits == 0 and gb >= 10:
            return f"{math.floor(gb)} GB"  # rounded down: 468.75 GB must not read as 469 (--mem=469G does not fit)
        return f"{gb:.{max(digits, 1)}f} GB".replace(".0 GB", " GB")
    return f"{mb:.0f} MB"


def fmt_bytes(n):
    return "—" if n is None else fmt_mb(n / (1024 * 1024), digits=1)


def fmt_duration(seconds):
    """10h 40m, 35m 38s, 0m 19s, 2d 4h (as in the GPU dashboard)."""
    if seconds is None:
        return "—"
    s = int(max(seconds, 0))
    d, s = divmod(s, 86400)
    hh, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {hh}h"
    if hh:
        return f"{hh}h {m}m"
    return f"{m}m {s}s"


def fmt_minutes(minutes):
    """A time limit in minutes: 30m, 4h, 1 day, 7 days, 1d 12h."""
    if minutes is None:
        return "—"
    m = int(minutes)
    d, rest = divmod(m, 1440)
    hh, mm = divmod(rest, 60)
    if d and not rest:
        return f"{d} day" if d == 1 else f"{d} days"
    if d:
        return f"{d}d {hh}h" + (f" {mm}m" if mm else "")
    if hh:
        return f"{hh}h" + (f" {mm}m" if mm else "")
    return f"{mm}m"


def fmt_time(epoch, with_year=False):
    """Oct 22, 05:14 PM in the server's time zone (KST)."""
    if not epoch:
        return "—"
    t = time.localtime(epoch)
    return time.strftime("%b %d %Y, %I:%M %p" if with_year else "%b %d, %I:%M %p", t)


def fmt_num(x, digits=2):
    if x is None:
        return "—"
    text = f"{x:,.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


_LIMIT_RE = re.compile(r"^\s*(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\s*$", re.I)


def parse_time_limit(text):
    """'30m', '12h', '2d', '1d 12h', '90' (minutes), '1-12:00:00', '4:00:00' -> minutes. ValueError if unreadable."""
    t = (text or "").strip().lower()
    if not t:
        raise ValueError("empty")
    if t.isdigit():
        return int(t)
    m = re.fullmatch(r"(\d+)-(\d+)", t)  # days-hours
    if m:
        return int(m.group(1)) * 1440 + int(m.group(2)) * 60
    m = re.fullmatch(r"(?:(\d+)-)?(\d+):(\d{1,2})(?::(\d{1,2}))?", t)
    if m:
        d, a, b, c = m.groups()
        if c is None:  # with a day part 'D-HH:MM' is hours:minutes, without it 'MM:SS' is minutes:seconds
            hours, minutes, seconds = (int(a), int(b), 0) if d else (0, int(a), int(b))
        else:
            hours, minutes, seconds = int(a), int(b), int(c)
        return int(d or 0) * 1440 + hours * 60 + minutes + (1 if seconds else 0)
    m = _LIMIT_RE.match(t)
    if m and any(m.groups()):
        d, hh, mm = (int(g) if g else 0 for g in m.groups())
        return d * 1440 + hh * 60 + mm
    raise ValueError(text)


def limit_text(minutes):
    """Minutes -> what the New job form accepts: 1d, 12h, 30m, 1d 12h."""
    d, rest = divmod(int(minutes), 1440)
    hh, mm = divmod(rest, 60)
    return " ".join(p for p in (f"{d}d" if d else "", f"{hh}h" if hh else "", f"{mm}m" if mm else "") if p) or "0m"


def slurm_time(minutes):
    """Minutes -> the sbatch -t form people type: 30, 4:00:00, 2-00:00:00."""
    d, rest = divmod(int(minutes), 1440)
    hh, mm = divmod(rest, 60)
    if d:
        return f"{d}-{hh:02d}:{mm:02d}:00"
    if hh:
        return f"{hh}:{mm:02d}:00"
    return str(mm)


def plural(n, word, words=None):
    return f"{n} {word if n == 1 else (words or word + 's')}"
