# Changelog

Notable changes to HBlink3. This is the first tagged release; it establishes a
baseline of the current state rather than enumerating the project's full history.

## [Unreleased]

### NUL-padded config fields from a connecting repeater are stripped

The RPTC config blob is a fixed-width ASCII record that clients are expected to
space-pad -- DMRGateway emits the whole thing from one `%-8.8s%09u%09u...`
sprintf, and HBlink3's own peer mode uses `str.ljust()`. Some clients pad with
NUL instead. The login never cared, because the blob is sliced positionally, so
this surfaced only downstream: the reporting feed decoded each field with a bare
`.strip()`, which removes whitespace but **not** NUL, and the padding rode
through into the dashboard JSON as `"W0UK\u0000\u0000\u0000\u0000"` where
every other repeater appeared cleanly stripped. A consumer calling `float()` on
a NUL-padded latitude would also raise `ValueError` on that one peer.

- Reporting now strips NUL as well as whitespace from decoded config fields, so
  a dashboard sees the same clean text regardless of how a client padded.
- The repeater-facing log lines still print the raw bytes on purpose: a client
  padding the wrong way stays visible there for diagnosis.

### OUTBOUND config blob padded exactly as DMRGateway pads it

A system in `OUTBOUND` mode is itself the client sending the RPTC blob, and
DMRGateway builds that record with a single sprintf:

```
"%-8.8s%09u%09u%02u%02u%8.8s%9.9s%03d%-20.20s%-19.19s%c%-124.124s%-40.40s%-40.40s"
```

That is three conventions, not one, and `config.py` now applies all three via
`_pad_text` / `_pad_num` / `_pad_decimal`:

| conversion | fill | fields |
|---|---|---|
| `%-N.Ns` | left-justified, space | `CALLSIGN`, `LOCATION`, `DESCRIPTION`, `URL`, `SOFTWARE_ID`, `PACKAGE_ID` |
| `%0Nu` | right-justified, zero | `RX_FREQ`, `TX_FREQ`, `TX_POWER`, `COLORCODE`, `HEIGHT` |
| `%N.Ns` | right-justified, space | `LATITUDE`, `LONGITUDE` |

`TX_POWER`, `COLORCODE` and `HEIGHT` were already zero-filled. What changed is
the frequencies (previously space-filled on the right, so a value shorter than
nine digits was sent as `4448500` plus two trailing spaces rather than
`004448500`) and latitude and
longitude (previously left-justified). Values of the expected width — which is
every well-formed config — go out byte for byte as before; `tests/test_protocol.py`
now pins the assembled blob against that format string directly.

### OpenBridge both-slots extension removed — **breaking**

HBlink3 carried a local extension to OpenBridge (`BOTH_SLOTS`) that allowed traffic on
TS2, against the protocol's all-traffic-on-TS1 convention. It is gone, and the reason
is simply that **it gained no capability and created ways to cause problems.**

- **No capability gained.** A trunk multiplexes concurrent calls by stream id, not by
  timeslot, so a second slot adds no concurrency — HBlink3 keeps OpenBridge stream
  state keyed by stream id, with no slot dimension to relieve. And the one thing a
  slot could usefully express — *which local timeslot a talkgroup lands on* — was
  never the wire's job in the first place: the receiver decides that from the TGID,
  and in HBlink3 it already does, via the `TS` on a bridge's `REPEATER`/`SERVER`
  member. That path is untouched and always worked.
- **Problems created.** Only the receive half was ever implemented, so the extension
  was never functional end to end — a TS2 group frame was admitted and then discarded
  *silently* in routing. The global `TGID_TS1_ACL` is gated on slot 1, so an admitted
  TS2 frame also skipped it. The `(TGID, TS)` knob in `OBP_BRIDGES` could only make a
  row unmatchable, taking a talkgroup off the air with no diagnostic. And the
  documentation had drifted into describing the resulting gap as intent.
- **Nothing else implements it.** TS1 is forced on transmit by every known
  implementation — BrandMeister, DMR+, DMR Gateway, and HBlink4, which additionally
  *ignores* the received slot bit and derives local timeslot from its own per-OBP
  `talkgroup_slots` map. So the extension had no counterparty either.

Mechanics of the change:

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
