"""Read-only smoke test against the real Slurm: renders every GET page for one user.
Run as root on the cluster's login node: /usr/bin/python3 -I tests/smoke_real.py <user>. Slurm commands only read; nothing is changed.
Files in the user's home cannot be read by root (NFS root_squash), so conda environments and logs show as missing."""
import io
import os
import pwd
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
from cbbl import slurm, web  # noqa: E402

if len(sys.argv) != 2:
    sys.exit("usage: smoke_real.py <user>")
user = sys.argv[1]
pw = pwd.getpwnam(user)
slurm.me = lambda: pw
failed = 0
jobs = slurm.my_jobs_since(user, 30)
urls = ["/", "/jobs", "/new", "/timeline", "/cluster", "/history", "/history?days=365", "/history.csv",
        "/tests", "/account"] + [f"/job/{j['id']}" for j in jobs[-6:]]
for url in urls:
    path, _, query = url.partition("?")
    env = {"REQUEST_METHOD": "GET", "SCRIPT_NAME": "/pun/sys/cbbl", "PATH_INFO": path, "QUERY_STRING": query,
           "wsgi.input": io.BytesIO(b""), "HTTP_HOST": "localhost"}
    out = {}
    body = b"".join(web.application(env, lambda s, h: out.update(status=s))).decode()
    bad = not out["status"].startswith("200") or "could not be read from Slurm" in body or "hit an error" in body
    failed += bad
    print(f"{'FAIL' if bad else 'ok  '} {out['status'][:3]} {url:22s} {len(body):7d} bytes")
    if bad:
        print(body[-1500:])
print(f"{len(jobs)} jobs in 30 days; states: {sorted(set(j['state'] for j in jobs))}")
print("nodes:", [(n["name"], n["cpus"], n["mem_mb"], n["available"]) for n in slurm.nodes()])
print("partition:", slurm.partition(), "fairshare:", slurm.fairshare(user), "queue:", slurm.queue_counts())
sys.exit(1 if failed else 0)
