# Tests

Regression tests pinning the behavior of helpers extracted during the 2026
cleanup. They exist because a live OBP/HBP network with real DMR audio cannot be
reproduced in a dev environment, so the routing helpers are validated in
isolation instead.

- `test_acl.py` — `HBSYSTEM.dmrd_acl_check()` (the shared master/peer ACL logic):
  PERMIT/DENY matching, per-timeslot scoping, and the once-per-stream drop logging.
- `test_lc.py` — `gen_lcs()` and `embed_lc()` from `bridge.py`: Link Control
  generation and the DMR payload LC rewrite used by all four group-routing paths.

## Running

From the repo root, using the project virtualenv (which has `twisted`,
`dmr_utils3`, and `bitarray`):

```
venv/bin/python -m unittest discover -s tests -v
```

Pure stdlib `unittest` — no extra dependencies. They also run under `pytest` if
you have it installed (`venv/bin/python -m pytest tests`).
