# Contributing

Thank you for helping. The portal runs on a cluster that people use every day, so changes go through a pull request,
the tests, and a review by a cluster admin before they reach `main`. Nothing reaches the cluster by itself: an admin
deploys `main` by hand.

## How

1. Create a branch from `main` (`fix-history-csv`, `job-page-gpu-column`, …).
2. Make the change and try it with the fake Slurm: `python3 tests/dev_server.py`, then <http://127.0.0.1:8080/>.
3. Run the tests: `python3 -m unittest discover -s tests`. Add a test for what you changed (`tests/test_portal.py`;
   new fake data goes in `tests/fakeslurm.py`).
4. Open a pull request. Describe what changes for users and admins, with a screenshot if a page looks different.
   GitHub runs the tests; a pull request needs them passing and one approving review.

Small and focused pull requests are reviewed faster than large ones.

## Rules

- **Never commit real data.** No real user names, real IPs or host names, real job data, passwords, keys or tokens,
  and no screenshots of the real cluster. Use the fake Slurm's made-up data (alice, bob, carol; node1 to node3;
  `example.org`; `192.0.2.x`) for tests, examples and screenshots. A site's own values go in its config file, never
  in the code.
- **Standard library only, Python 3.9.** The cluster runs the system Python of RHEL/Rocky 9; no `pip install`.
- **Pages must work without JavaScript.** JavaScript may only add to a page (live refresh, copy buttons, charts).
- **Everything runs as the signed-in user.** Don't add anything that needs more rights; work that needs root belongs
  in the root helpers, which are reviewed separately.
- **Escape everything you put in a page** with `h()` from `app/cbbl/util.py`. Forms must be POST (the portal checks
  their `Origin`).
- Match the surrounding code: its naming, its comments, lines up to about 120 characters.

## Licence

By contributing you agree that your contribution is licensed under the Apache License 2.0, like the rest of the
project (see `LICENSE`, section 5).
