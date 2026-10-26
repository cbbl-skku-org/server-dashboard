"""The New job form: checks the fields, then builds the batch script and the equivalent terminal command."""
import os
import re
import shlex
import time

from .util import MB_PER_GB, fmt_mb, fmt_minutes, parse_time_limit, slurm_time

FIELDS = ("cpus", "mem", "time", "node", "name", "conda", "workdir", "command", "mem_default", "name_default", "quick")
EXAMPLE_COMMAND = "python train.py"  # shown in the equivalent command until the user types one
MAX_COMMAND = 20000
SAFE_PATH_RE = re.compile(r"^[^\s'\"\\$`]+$")


def node_choices(nodes):
    """(value, label) pairs for the Node select."""
    choices = [("any", "any (fastest start)")]
    if any("internet" in n["features"] for n in nodes):
        names = ", ".join(n["name"] for n in nodes if "internet" in n["features"])
        choices.append(("internet", f"needs Internet ({names})"))
    for n in nodes:
        note = " (no Internet)" if "offline" in n["features"] else ""
        choices.append((n["name"], n["name"] + note))
    return choices


def default_name(command):
    """Job name from the command: 'python train.py --x' -> 'train'; 'bash run_all.sh' -> 'run_all'."""
    try:
        words = shlex.split(command.strip().splitlines()[0]) if command.strip() else []
    except ValueError:
        words = command.split()
    pick = next((w for w in words if re.search(r"\.(py|sh|r|R|pl|jl|m|nf|smk|ipynb)$", w)), None)
    if pick is None:
        pick = next((w for w in words if "=" not in w), "job")
    name = os.path.splitext(os.path.basename(pick))[0] or "job"
    return clean_name(name)


def gb_text(mb):
    """MB -> the GB number shown in the RAM field: 4, 0.5, 12.25."""
    if not mb:
        return ""
    return f"{mb / MB_PER_GB:.2f}".rstrip("0").rstrip(".")


def clean_name(name):
    name = re.sub(r"[^A-Za-z0-9._+-]+", "_", name.strip())[:64].strip("_")
    return name or "job"


def tilde(path, home):
    """Show a path the way a user types it in a terminal: ~/proj instead of /home/user/proj."""
    if path == home:
        return "~"
    if home and path.startswith(home.rstrip("/") + "/"):
        rest = path[len(home.rstrip("/")) + 1:]
        if re.fullmatch(r"[\w./+-]+", rest):
            return "~/" + rest
    return shlex.quote(path)


def parse(form, home, nodes, part, envs):
    """form: dict of field -> str. Returns (spec, errors) with errors as field -> message."""
    errors = {}
    spec = {}
    get = lambda k: (form.get(k) or "").strip()  # noqa: E731

    node = get("node") or "any"
    choice_values = [v for v, _ in node_choices(nodes)]
    if node not in choice_values:
        errors["node"] = "Unknown node choice."
        node = "any"
    spec["node"] = node
    candidates = [n for n in nodes if node == "any" or (node == "internet" and "internet" in n["features"])
                  or n["name"] == node]
    max_cpus = max((n["cpus"] for n in candidates), default=0)
    max_mem = max((n["mem_mb"] for n in candidates), default=0)
    # quick test: the debug partition's smaller limits (at most 10 minutes, 4 CPUs, 16 GB; scheduled first)
    quick = part.get("debug") if get("quick") else None
    if get("quick") and not quick:
        errors["quick"] = "Quick test jobs are not set up on this cluster."
    spec["quick"] = bool(quick)
    if quick:
        max_cpus = min(max_cpus, quick.get("max_cpus_per_node") or max_cpus)
        max_mem = min(max_mem, quick.get("max_mem_per_node") or max_mem)
    where = "a quick test" if quick else "one node" if node in ("any", "internet") else node

    cpus = get("cpus")
    spec["cpus"] = None
    if cpus:
        if not cpus.isdigit() or int(cpus) < 1:
            errors["cpus"] = "A whole number, at least 1."
        elif max_cpus and int(cpus) > max_cpus:
            errors["cpus"] = f"At most {max_cpus} CPUs for {where}." if quick else f"At most {max_cpus} CPUs on {where}."
        else:
            spec["cpus"] = int(cpus)

    # RAM: the form starts with Slurm's default (memory per CPU × CPUs) and keeps following the CPUs until the user
    # types a value of their own. The page sends back the value it last filled in (mem_default) to tell them apart.
    unit = lambda text: text.upper().removesuffix("GB").removesuffix("G").strip()  # noqa: E731
    mem, mem_default = unit(get("mem")), unit(get("mem_default"))
    per_cpu = part.get("default_mem_per_cpu")
    auto_mb = per_cpu * (spec["cpus"] or 1) if per_cpu else None
    if auto_mb and max_mem:
        auto_mb = min(auto_mb, max_mem)
    spec["mem_auto_mb"] = auto_mb
    spec["mem_auto"] = not mem or (bool(mem_default) and mem == mem_default)
    spec["mem_mb"] = auto_mb if spec["mem_auto"] else None
    if not spec["mem_auto"]:
        try:
            gb = float(mem)
            if gb <= 0:
                raise ValueError
            mb = int(round(gb * MB_PER_GB))
            if max_mem and mb > max_mem:
                errors["mem"] = f"At most {fmt_mb(max_mem)} for {where}." if quick else f"At most {fmt_mb(max_mem)} on {where}."
            else:
                spec["mem_mb"] = mb
        except ValueError:
            errors["mem"] = "A number of GB, e.g. 16 or 0.5."

    limit = get("time")
    spec["minutes"] = None
    max_time = quick["max_time"] if quick and quick.get("max_time") else part.get("max_time")
    if limit:
        try:
            minutes = parse_time_limit(limit)
            if minutes < 1:
                errors["time"] = "At least 1 minute."
            elif max_time and minutes > max_time:
                errors["time"] = f"At most {fmt_minutes(max_time)}" + (" for a quick test." if quick else ".")
            else:
                spec["minutes"] = minutes
        except ValueError:
            errors["time"] = "Use e.g. 30m, 12h, 2d or 1d 12h."

    command = (form.get("command") or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not command.strip():
        errors["command"] = "What should the job run?"
    elif len(command) > MAX_COMMAND:
        errors["command"] = f"At most {MAX_COMMAND} characters; put long scripts in a file."
    spec["command"] = command

    # Job name: taken from the command until the user types one (name_default: the name the page filled in).
    name, name_default = get("name"), get("name_default")
    spec["name_derived"] = default_name(command or EXAMPLE_COMMAND)
    spec["name_auto"] = not name or (bool(name_default) and name == name_default)
    spec["name"] = spec["name_derived"] if spec["name_auto"] else clean_name(name)

    workdir = get("workdir") or home
    if workdir == "~" or workdir.startswith("~/"):
        workdir = home + workdir[1:]
    workdir = os.path.normpath(workdir) if workdir.startswith("/") else workdir
    if not workdir.startswith("/"):
        errors["workdir"] = "A full path, e.g. /home/you/project or ~/project."
    elif not SAFE_PATH_RE.match(workdir):
        errors["workdir"] = "Folders with spaces, quotes or $ are not supported here."
    elif not os.path.isdir(workdir):
        errors["workdir"] = "This folder does not exist (or you cannot open it)."
    spec["workdir"] = workdir

    conda = get("conda")
    spec["conda"] = None
    if conda:
        want = home + conda[1:] if conda.startswith("~/") else conda
        env = next((e for e in envs if want in (e["label"], e["prefix"])), None)
        if env is None:
            env = next((e for e in envs if e["name"] == want), None)
        if env is None:
            known = ", ".join(e["label"] for e in envs[:8]) or "none found in ~/.conda/environments.txt"
            errors["conda"] = f"Unknown environment. Yours: {known}."
        else:
            spec["conda"] = env
    return spec, errors


def sbatch_options(spec):
    """(option, value) pairs in the order they are shown."""
    opts = [("partition", "debug")] if spec.get("quick") else []
    opts.append(("job-name", spec["name"]))
    if spec.get("cpus"):
        opts.append(("cpus-per-task", str(spec["cpus"])))
    if spec.get("mem_mb"):
        mb = spec["mem_mb"]
        opts.append(("mem", f"{mb // MB_PER_GB}G" if mb % MB_PER_GB == 0 else f"{mb}M"))
    if spec.get("minutes"):
        opts.append(("time", slurm_time(spec["minutes"])))
    if spec.get("node") == "internet":
        opts.append(("constraint", "internet"))
    elif spec.get("node") not in (None, "", "any"):
        opts.append(("nodelist", spec["node"]))
    return opts


def conda_lines(env, home):
    if not env:
        return []
    root, prefix = env.get("root"), env["prefix"]
    if not root:  # no conda.sh found: rely on the login shell's conda (conda init)
        return ["conda activate " + tilde(prefix, home)]
    if prefix == root:
        target = "base"
    elif prefix == os.path.join(root, "envs", env["name"]):
        target = shlex.quote(env["name"])
    else:
        target = tilde(prefix, home)
    return ["source " + tilde(os.path.join(root, "etc", "profile.d", "conda.sh"), home), "conda activate " + target]


def script(spec, home, user):
    lines = ["#!/bin/bash -l"]
    lines += [f"#SBATCH --{k}={v}" for k, v in sbatch_options(spec)]
    lines.append(f"#SBATCH --chdir={spec['workdir']}")
    lines.append(f"# Made with the web portal by {user} on {time.strftime('%Y-%m-%d %H:%M')}.")
    lines.append("# Output: slurm-<job id>.out in the working directory.")
    lines += conda_lines(spec.get("conda"), home)
    lines.append(spec["command"])
    return "\n".join(lines) + "\n"


SHORT = {"partition": "-p", "job-name": "-J", "cpus-per-task": "-c", "time": "-t", "constraint": "-C", "nodelist": "-w"}


def command_line(spec, home):
    """The terminal command that submits the same job."""
    parts = []
    for k, v in sbatch_options(spec):
        parts.append(f"{SHORT[k]} {shlex.quote(v)}" if k in SHORT else f"--{k}={shlex.quote(v)}")
    body = " && ".join(conda_lines(spec.get("conda"), home) + [spec["command"] or EXAMPLE_COMMAND])
    wd = tilde(spec["workdir"], home)
    return f"cd {wd} && sbatch {' '.join(parts)} --wrap={shlex.quote(body)}"
