# Changelog

Notable changes to HBlink3. This is the first tagged release; it establishes a
baseline of the current state rather than enumerating the project's full history.

## [Unreleased]

### OpenBridge both-slots extension removed — **breaking**
- Group calls on an OpenBridge are **TS1 on the wire by protocol**. The local
  `BOTH_SLOTS` extension that admitted group frames on TS2 is gone: only the receive
  half was ever implemented (group egress has always forced the slot bit to 0), so no
  HBlink3 ever originated TS2 group traffic, and an admitted TS2 frame was discarded
  silently in routing. A group frame on TS2 is now **rejected at ingress with a log
  line** instead of being accepted and dropped without one.
- Unit (private) calls are TS1 on a trunk too, and the **`BOTH_SLOTS` setting is gone
  entirely** — removed from `hblink.cfg` parsing, so an existing key is simply ignored
  and the line can be deleted at your convenience. Unit egress now forces TS1
  unconditionally, and unit frames on TS2 are rejected at ingress alongside group
  frames. The whole slot check is back to what it was before the extension: all
  OpenBridge traffic is Slot 1.
- A `(TGID, TS)` row in `OBP_BRIDGES` is now a **startup ERROR** (TS is injected as
  `1`). Left in place, the field could only disagree with the wire and make the row
  unmatchable, taking a talkgroup off the air with no diagnostic. **Local** timeslot
  assignment is unchanged and lives where it always has: the `TS` on the bridge's
  `REPEATER`/`SERVER` member in `BRIDGES`, which `bridge.py` flips the slot bit to
  match. `tools/migrate_obp_rules.py` drops a moved member's TS with a WARNING.
- `hblink-SAMPLE.cfg` no longer ships a `BOTH_SLOTS` line.

## [3.0.0] — 2026-07-12

First tagged release. Highlights of the current state:

### Reporting & dashboard (event-driven overhaul)
- The daemon→dashboard feed is event-driven newline-delimited JSON. Repeater
  connect/disconnect and live call events are pushed **as they happen**; the full
  config/bridge state is resent only as a slow periodic resync + heartbeat (it used
  to be a full push every interval).
- Per-repeater **ping-loss** quality metric — surfaces a repeater that stays
  connected but drops keepalive pings (a lossy link → choppy audio), self-calibrated
  to each repeater's own ping cadence. Configured with `PING_LOSS_WINDOW` /
  `PING_LOSS_WARN` in `[GLOBAL]`; the dashboard golds a repeater's callsign at/above
  the warn threshold.
- Canonical event vocabulary: `repeater_connected` / `repeater_disconnected`,
  `stream_start` / `stream_end`.
- **Unix-socket transport** for a same-host dashboard (`REPORT_TRANSPORT=unix` +
  `REPORT_SOCKET`; dashboard side `HBLINK_TRANSPORT` / `HBLINK_SOCKET`), which
  retires the silently-severed-link failure class for local dashboards. Remote (TCP)
  dashboards gain TCP keepalive + a feed read-timeout so a dead link is detected.

### OpenBridge configuration model — **breaking**
- OpenBridge systems are configured in a per-OBP table
  `OBP_BRIDGES = { <obp system> : { <bridge> : <TGID> } }` in `rules.py`, **not** as
  inline `BRIDGES` members. OBP entries carry no ON/OFF/TIMEOUT triggers or real
  timeslot (a trunk has no RF user to key them). The table doubles as the
  fail-closed ingress/egress filter; a one-TGID-to-two-bridges fork is a startup
  **ERROR**, a cross-OpenBridge renumber a **WARNING**.
- Migration tool `tools/migrate_obp_rules.py` converts an old inline `rules.py`.

### System-mode terminology — **breaking**
- `MASTER` → **`SERVER`**, `PEER` / `CLIENT` → **`OUTBOUND`** (`OPENBRIDGE`
  unchanged). Update the `MODE:` line of each stanza in `hblink.cfg`.

### Documentation
- README (with an upgrade warning), INSTALL, CONFIGURING, ACLS, and the dashboard
  docs refreshed for all of the above.

[3.0.0]: https://github.com/n0mjs710/hblink3/releases/tag/v3.0.0
