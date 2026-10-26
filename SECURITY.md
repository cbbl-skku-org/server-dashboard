# Security

The portal signs people in to a shared cluster and acts with their rights, so a security problem in it can affect
every user. Please report it **privately**, not in a public issue or pull request.

**How to report:** tell a cluster admin directly, in person or in a private message. Once the repository is public,
you can also use its **Security** tab → **Report a vulnerability**, which only the admins see.

Please include what an attacker could do, the steps to reproduce it (with the fake Slurm, `tests/dev_server.py`, if
you can), and the page or file involved. Don't test against the real cluster beyond what your own account may do, and
don't look at other users' data.

The admins answer as soon as they can, fix the problem, deploy the fix, and then credit you if you wish.
