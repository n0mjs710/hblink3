# HBlink3 Dashboard — Field Reference

What every element on the dashboard means, section by section. The page updates
live: it receives a full snapshot when it connects, then per-call deltas over a
WebSocket, and it ticks all timers (durations, uptimes, countdowns) in the
browser once per second.

A note on **direction**, used throughout: directions are from HBlink3's point of
view. **RX** (green) is traffic coming *into* HBlink3 from a system; **TX** (red)
is traffic HBlink3 is sending *out* to a system. So the radio/peer that keyed up
shows green, and everyone hearing the repeated copy shows red.

---

## Header

| Element | Meaning |
|---|---|
| Logo / title | The HBlink logo and your `REPORT_NAME` (set in `dashboard/config.py`), with the "HBlink3 Monitor" subtitle. |
| Connection badge | The link between **this dashboard and the HBlink3 reporting feed** — *not* the health of any radio system. **connected** (green) = receiving live data; **connecting… / disconnected** (red) = the feed at `HBLINK_IP:HBLINK_PORT` is unreachable (HBlink3 down or `[REPORTS]` not enabled); **dashboard offline** = your browser lost the dashboard itself. |

---

## Master Systems

One block per HBlink3 **MASTER** instance (repeaters/hotspots connect *to* it).

**Block heading**

| Element | Meaning |
|---|---|
| System name | The master's name from `hblink.cfg` (e.g. `MASTER-1`). |
| `repeat` / `isolate` pill | `repeat` if the master forwards traffic between its own peers (`REPEAT: True`), `isolate` if it does not. |
| *N* peer(s) | How many repeaters/hotspots are currently logged in. |

**Per-peer row**

| Column | Meaning |
|---|---|
| Repeater | The peer's callsign and radio ID (DMR ID), with its location underneath. |
| Connection | The peer's source `IP:PORT`, and how long it has been logged in (`up …`). |
| Freq | The peer's RX / TX frequencies in MHz, or `N/A` if it didn't report them. |
| CC / Slots | The peer's color code and the number of timeslots it advertises. |
| TS1 / TS2 | The live call on timeslot 1 / timeslot 2 (see **Call cell** below), or `— idle —`. |

---

## Peer Systems

One row per HBlink3 **PEER** instance (HBlink3 connecting *out* to an upstream
master, e.g. another HBlink or a BrandMeister/IPSC2 server).

| Column | Meaning |
|---|---|
| System | The peer instance name, with its mode (`PEER`) underneath. |
| Identity | The callsign and radio ID HBlink3 logs in with, plus its location and the upstream `master IP:PORT`. |
| Connection | A status dot and the login state — **YES** (green) when fully connected, otherwise the current handshake state (red). `up …` is time since connect. |
| Pings S/A/L | Keepalive pings **S**ent / **A**cknowledged / **L**ost (sent − acked). Growing "Lost" means the link to the master is unhealthy. |
| TS1 / TS2 | The live call on each timeslot (see **Call cell**), or `— idle —`. |

---

## OpenBridge Systems

One row per **OPENBRIDGE** instance. OpenBridge is a trunk between two networks
and is **not timeslot-bound** — it carries any number of concurrent streams at
once, so this section lists them side by side rather than in TS1/TS2 columns.

| Column | Meaning |
|---|---|
| System | The OpenBridge instance name. |
| Network / Target | The local `NETWORK_ID`, and the far end's `TARGET_IP:TARGET_PORT`. |
| Active Streams | A chip per concurrent call: direction (**RX** inbound / **TX** outbound), source, and destination. `— idle —` when none are active. Seeing several chips here at once is normal and expected for OpenBridge. |

---

## Call cell

The colored box shown in the TS1/TS2 columns (and, as chips, under OpenBridge).

| Part | Meaning |
|---|---|
| Top line | Call type (**Group** or **Unit**) · source · destination, using callsigns / talkgroup names where known. For a group call the destination is a talkgroup; for a unit (private) call it's another subscriber. |
| Bottom line | The source's raw radio ID · elapsed call time (ticking live). |
| Color | **Green** = RX (this system/peer is the one transmitting in). **Red** = TX (the call is going out to this system — i.e. it's hearing repeated traffic). |
| `— idle —` | No active call on that slot. |

A call that ends normally clears immediately. If a terminator is lost, the
dashboard drops the stale call after ~30 s so it can't linger forever (HBlink3
itself also times streams out at ~2 s and emits the end — this is just a backstop
for a non-local dashboard or a feed interruption).

---

## Conference Bridges

One table per conference bridge defined in `rules.py`, with a row per member
system. (Empty if you're running `bridge_all` or standalone `hblink.py`, which
have no conference-bridge rules.)

| Column | Meaning |
|---|---|
| System | The member system's name. |
| TS | The timeslot this rule matches on. |
| TGID | The talkgroup this rule matches, with its name if known. |
| Status | **Connected** (green) = this member is currently bridged (`ACTIVE`); **Disconnected** (red) = traffic is not flowing for this member. |
| Timeout | For timed rules (`TO_TYPE` ON/OFF), the live countdown until the state flips; `N/A` for rules without a timer. |
| Action | What the timeout will do: **Disconnect** (an `ON` timer), **Connect** (an `OFF` timer), or **None**. |
| Connect TGIDs | Talkgroup IDs that, when keyed, turn this member **on** (the rule's `ON` list). |
| Disconnect TGIDs | Talkgroup IDs that turn this member **off** (the rule's `OFF` list). |

---

## Call Log

A reverse-chronological feed of call activity. Only **ingress (RX)** legs are
logged — one line when a call starts and one when it ends — matching the classic
HBmonitor behavior, so each conversation appears once regardless of how many
systems it was bridged to.

| Field | Meaning |
|---|---|
| Timestamp | Local time the event was received. |
| Event | Call type plus **START** (blue) or **END** (green). |
| System | The system the call came in on. |
| Source → Dest | Source subscriber (callsign/ID), the timeslot (`TS1`/`TS2`), and the destination talkgroup or subscriber (name/ID). |
| Duration | On an **END** line, how long the call lasted. |

---

## How the data arrives (and update latency)

- **Calls** are event-driven and appear **instantly** as START/END events.
- **System and peer state** (a peer connecting or dropping, ping counts, bridge
  on/off state) comes from a periodic snapshot every `REPORT_INTERVAL` seconds
  (`[REPORTS]` in `hblink.cfg`; ~10 s is a good value for dashboard use). So a
  newly connected peer can take up to one interval to appear.
- All elapsed/uptime/countdown timers advance in the browser; the server sends
  time *deltas*, so the dashboard needs no clock synchronization with the server.
