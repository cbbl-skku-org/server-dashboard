"""The New job form: checks the fields, then builds the batch script and the equivalent terminal command."""
import os
import re
import shlex
import time

from .util import MB_PER_GB, fmt_mb, fmt_minutes, limit_text, parse_time_limit, slurm_time

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


# ---------------------------------------------------------------------------------------------------- the other way
# A command typed or pasted into the Equivalent command box fills the form: an sbatch (or srun) command line, with
# an optional "cd <folder> &&" before it, a batch script with #SBATCH lines, or "sbatch <script>" (its #SBATCH lines
# are read). Options the form has no field for are listed as notes and left out.

SHORT_ALL = dict({v: k for k, v in SHORT.items()},  # short option -> long name
                 **{"-D": "chdir", "-o": "output", "-e": "error", "-n": "ntasks", "-N": "nodes", "-a": "array",
                    "-d": "dependency", "-A": "account", "-G": "gpus", "-q": "qos", "-x": "exclude", "-i": "input",
                    "-b": "begin", "-L": "licenses", "-F": "nodefile", "-m": "distribution"})
SHORT_FLAGS = {"-H", "-h", "-I", "-k", "-K", "-l", "-O", "-Q", "-s", "-u", "-v", "-V", "-X", "-Z", "-E"}
LONG_FLAGS = {"exclusive", "hold", "parsable", "requeue", "no-requeue", "test-only", "wait", "overcommit",
              "oversubscribe", "quiet", "verbose", "contiguous", "use-min-nodes", "spread-job", "ignore-pbs", "pty",
              "overlap", "unbuffered", "label", "no-kill", "help", "usage", "version", "get-user-env", "x11",
              "immediate", "kill-on-bad-exit", "exact", "whole", "nice"}
SEPARATORS = set(";&\n")
MAX_SCRIPT = 256 * 1024
CONDA_RE = re.compile(r"\A\s*(?:source\s+(\S+)/etc/profile\.d/conda\.sh\s*(?:&&|;|\n)\s*)?"
                      r"conda\s+activate\s+(\S+)\s*(?:&&|;|\n|\Z)\s*")


def size_mb(text):
    """'16G', '512M', '1.5T', '16000' (MB) -> MB. ValueError if unreadable."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGT]?)B?", text.strip().upper())
    if not m:
        raise ValueError(text)
    factor = {"K": 1 / 1024, "": 1, "M": 1, "G": MB_PER_GB, "T": MB_PER_GB * 1024}[m.group(2)]
    return int(round(float(m.group(1)) * factor))


def split_words(text):
    """Shell words, with ';', '&&', '&' and line ends as separate items (None). ValueError on an open quote."""
    lex = shlex.shlex(text, posix=True, punctuation_chars=";&|<>()\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    words = []
    for w in lex:
        if w and set(w) <= SEPARATORS:
            if words and words[-1] is not None:
                words.append(None)
        else:
            words.append(w)
    return words


def read_options(words):
    """sbatch/srun words after the program name -> ([(long name, value or None)], the words after the options)."""
    opts = []
    i = 0
    while i < len(words):
        w = words[i]
        if w == "--":
            i += 1
            break
        if w.startswith("--") and len(w) > 2:
            name, eq, value = w[2:].partition("=")
            if not eq and name not in LONG_FLAGS and i + 1 < len(words):
                i += 1
                value = words[i]
            opts.append((name, value if eq or name not in LONG_FLAGS else None))
        elif w.startswith("-") and len(w) > 1:
            flag, attached = w[:2], w[2:]
            name = SHORT_ALL.get(flag, flag)
            if flag in SHORT_FLAGS:
                opts.append((name, None))
            elif attached:
                opts.append((name, attached.removeprefix("=")))
            elif i + 1 < len(words):
                i += 1
                opts.append((name, words[i]))
            else:
                opts.append((name, None))
        else:
            break
        i += 1
    return opts, words[i:]


def script_options(text):
    """A batch script -> ([(long name, value)] from its #SBATCH lines, the script's commands without comments).
    As in sbatch, #SBATCH lines after the first command are comments."""
    opts, body, started = [], [], False
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if not started and stripped.startswith("#SBATCH"):
            try:
                opts += read_options(shlex.split(stripped[len("#SBATCH"):], comments=True))[0]
            except ValueError:
                pass
        elif stripped.startswith("#") or (not started and not stripped):
            continue
        else:
            started = True
            body.append(line)
    return opts, "\n".join(body).strip("\n")


def interpreter(text):
    """The program a script's #! line names: '#!/usr/bin/env python3' -> 'python3'; bash if there is none."""
    first = text.split("\n", 1)[0]
    if not first.startswith("#!"):
        return "bash"
    words = first[2:].split()
    if words and os.path.basename(words[0]) == "env":
        words = [w for w in words[1:] if not w.startswith("-")]
    return os.path.basename(words[0]) if words else "bash"


def expand(path, home, base=None):
    if path == "~" or path.startswith("~/"):
        return home + path[1:]
    if base and not path.startswith("/"):
        return os.path.normpath(os.path.join(base, path))
    return path


def from_command(text, home, part, nodes, envs, workdir=""):
    """The other way round: an sbatch/srun command line or a batch script -> (form values, notes, error).
    Fields the command does not set get the form's defaults; the working directory stays as it is unless the command
    names one (cd or --chdir). workdir: the form's current one, where "sbatch <script>" looks for a relative script."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\\\n", " ").strip()
    if not text:
        return {}, [], "Type or paste an sbatch command."
    notes = []
    cwd = None  # the folder of "cd <folder> &&"
    if text.startswith("#!") or re.search(r"^\s*#SBATCH", text, re.M):  # a pasted batch script
        opts, body = script_options(text)
    else:
        try:
            words = split_words(text)
        except ValueError:
            return {}, [], "A quote is not closed."
        segments, seg = [], []
        for w in words + [None]:
            if w is None:
                if seg:
                    segments.append(seg)
                seg = []
            else:
                seg.append(w)
        job = next((i for i, s in enumerate(segments) if os.path.basename(s[0]) in ("sbatch", "srun")), None)
        if job is None:
            return {}, [], "Start with sbatch (or srun), or paste a batch script."
        for s in segments[:job]:
            if s[0] == "cd" and len(s) <= 2:
                cwd = expand(s[1] if len(s) == 2 else "~", home, expand(cwd, home, workdir) if cwd else workdir)
            else:
                notes.append(f"“{' '.join(s)}” is not part of the job: left out.")
        program = os.path.basename(segments[job][0])
        if segments[job + 1:]:
            notes.append(f"What follows the {program} command is left out.")
        opts, rest = read_options(segments[job][1:])
        wrap = next((v for k, v in reversed(opts) if k == "wrap"), None)
        if program == "srun":
            notes.append("srun runs a command while you wait; Submit sends it as a batch job.")
            body = shlex.join(rest)
        elif wrap is not None:
            body = wrap
            if rest:
                notes.append(f"“{' '.join(rest)}” after --wrap is left out.")
        elif rest:  # sbatch <script> [arguments]: the script's own #SBATCH lines count, the command line's win
            path = expand(rest[0], home, cwd or workdir)
            try:
                if not os.path.isfile(path):
                    raise OSError
                with open(path, errors="replace") as f:
                    content = f.read(MAX_SCRIPT)
                file_opts = script_options(content)[0]
                if file_opts:
                    notes.append(f"Read the #SBATCH lines of {rest[0]}.")
                opts = file_opts + opts
                prog = interpreter(content)
            except OSError:
                notes.append(f"Could not open {rest[0]}: its #SBATCH lines, if any, are not in the form.")
                prog = "bash"
            # the job may start in another folder (--chdir): name the script by its full path then
            chdir = next((v for k, v in reversed(opts) if k == "chdir" and v), None)
            script_arg = tilde(path, home) if chdir and not rest[0].startswith(("/", "~")) else shlex.quote(rest[0])
            body = " ".join([prog, script_arg] + [shlex.quote(a) for a in rest[1:]])
        else:
            body = ""

    values = {"cpus": "1", "mem": "", "time": limit_text(part["default_time"]) if part.get("default_time") else "",
              "node": "any", "name": "", "conda": "", "quick": ""}
    m = CONDA_RE.match(body)  # the conda lines the form writes itself
    if m:
        target = m.group(2)
        env = next((e for e in envs if target in (e["label"], e["name"]) or expand(target, home) == e["prefix"]), None)
        if env and target == "base" and m.group(1):  # the base of the conda install that the source line names
            env = next((e for e in envs if e["prefix"] == expand(m.group(1), home)), env)
        if env:
            values["conda"] = env["label"]
            body = body[m.end():]
        else:
            notes.append(f"Conda environment {target} is not in ~/.conda/environments.txt: it stays in the command.")
    values["command"] = body.strip("\n")

    names = {n["name"] for n in nodes}
    mem_per_cpu = None
    for k, v in opts:
        if k == "wrap":
            continue
        if v is None and k not in LONG_FLAGS:
            notes.append(f"{k if k.startswith('-') else '--' + k} has no value: left out.")
        elif k == "chdir":
            cwd = expand(v, home, cwd or workdir)
        elif k == "cpus-per-task":
            if v.isdigit() and int(v) >= 1:
                values["cpus"] = v
            else:
                notes.append(f"-c {v}: not a number of CPUs.")
        elif k == "mem":
            try:
                mb = size_mb(v)
            except ValueError:
                notes.append(f"--mem={v}: not a size such as 16G.")
                continue
            if mb == 0:
                notes.append("--mem=0 (all of a node's memory) is not offered: RAM follows the CPUs.")
            else:
                values["mem"], mem_per_cpu = gb_text(mb), None
        elif k == "mem-per-cpu":
            try:
                mem_per_cpu = size_mb(v)
            except ValueError:
                notes.append(f"--mem-per-cpu={v}: not a size such as 4G.")
        elif k == "time":
            try:
                values["time"] = limit_text(parse_time_limit(v))
            except ValueError:
                values["time"] = v  # the form says what is wrong with it
        elif k == "partition":
            if v == "debug" and part.get("debug"):
                values["quick"] = "1"
            elif v != "cpu":
                notes.append(f"Partition {v} is not offered here: the form uses cpu"
                             + (" (or debug: the Quick test box)." if part.get("debug") else "."))
        elif k == "job-name":
            values["name"] = v
        elif k == "nodelist":
            if v in names:
                values["node"] = v
            else:
                notes.append(f"-w {v}: the form runs a job on one node, one of {', '.join(sorted(names))}.")
        elif k == "constraint":
            if v == "internet" and any("internet" in n["features"] for n in nodes):
                values["node"] = "internet"
            else:
                notes.append(f"-C {v}: the form knows only the constraint internet.")
        elif k in ("ntasks", "nodes") and v == "1":
            continue
        elif k == "nodes":
            notes.append(f"-N {v}: the form runs a job on one node.")
        elif k == "ntasks":
            notes.append(f"-n {v}: the form asks for one task with several CPUs (-c). For a program that runs {v} "
                         "threads or processes on one node, set CPUs.")
        elif k in ("output", "error"):
            notes.append(f"--{k}={v} is left out: output goes to slurm-<job id>.out in the working directory.")
        else:
            notes.append(f"{k if k.startswith('-') else '--' + k}{'' if v is None else '=' + v} is not in the form: "
                         "left out.")
    if mem_per_cpu:
        mb = mem_per_cpu * int(values["cpus"])
        values["mem"] = gb_text(mb)
        notes.append(f"--mem-per-cpu × {values['cpus']} CPUs = {fmt_mb(mb)} in all.")
    if values["quick"] and not any(k == "time" for k, _ in opts):  # sbatch -p debug without -t: debug's own default
        quick = part["debug"]
        values["time"] = limit_text(quick.get("default_time") or quick.get("max_time") or 10)
    if cwd:
        values["workdir"] = tilde(cwd, home) if cwd.startswith("/") else cwd
    return values, notes, ""
