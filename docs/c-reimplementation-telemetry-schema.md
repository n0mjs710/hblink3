# Telemetry Event Schema for C Reimplementation

## Design Philosophy

The reporting feed is a secondary concern. The C core is a DMR call router first. The
telemetry emitter must never slow, stall, or complicate the packet routing path. Concretely:

- All TCP writes to reporting clients are non-blocking. If a client's socket buffer is full,
  the write is dropped for that client. The routing path does not wait.
- Stream events (call START/END) are emitted inline in the routing path because they are
  time-sensitive, but they must be cheap — a single formatted write to each client fd.
- Config and bridges events are periodic snapshots, not live state. They are assembled and
  sent on a timer, not on every state change. Exception: bridge ACTIVE state changes may
  trigger an immediate bridges snapshot because operators observe bridge state directly and
  a 10-second lag is confusing on a PTT system.
- If a reporting client disconnects or is slow, it is removed from the client list and
  forgotten. It does not affect other clients or the routing core.

A C implementer should treat the reporting feed as a UDP-style "best effort" service that
happens to run over TCP for reliability with slow consumers. The core routes calls regardless
of whether any clients are connected.

---

## Transport

The core listens on a configured TCP port (default 4321). Dashboard and admin clients
connect and receive a stream of newline-delimited JSON objects (NDJSON) — one complete JSON
object per line, terminated by `\n`. Clients are read-only; the core ignores any data sent
by clients.

On client connect, the core immediately sends the current `config` event and the current
`bridges` event so the client has a complete picture before any deltas arrive.

---

## Event Types

### `config` — System snapshot

Sent periodically (every `REPORT_INTERVAL` seconds, typically 10). Contains the full state
of all configured systems and their connected repeaters/peers. This is a complete snapshot,
not a delta — receivers replace their entire system state on each `config` event.

```
{
  "type": "config",
  "ping_time": <integer, seconds, from GLOBAL config>,
  "max_missed": <integer, from GLOBAL config>,
  "systems": {
    "<SYSTEM_NAME>": <system object, see below>
  }
}
```

**System object — SERVER mode** (HBP master, repeaters connect to it):
```
{
  "MODE": "SERVER",
  "REPEAT": <boolean>,
  "CALLSIGN": <string>,
  "RADIO_ID": <integer>,
  "SLOTS": <integer, bitmask>,
  "LOCATION": <string>,
  "REPEATERS": {
    "<peer_id_hex>": {
      "CALLSIGN": <string>,
      "RADIO_ID": <integer>,
      "IP": <string>,
      "PORT": <integer>,
      "RX_FREQ": <string, 9-digit>,
      "TX_FREQ": <string, 9-digit>,
      "SLOTS": <integer>,
      "CONNECTION": <string, "YES" | "NO" | handshake state>,
      "CONNECTED": <float, Unix epoch of connection time>,
      "LAST_PING": <float, Unix epoch of last RPTP received from repeater>
    }
  }
}
```

**System object — OUTBOUND mode** (HBP peer, connects to a remote master):
```
{
  "MODE": "OUTBOUND",
  "CALLSIGN": <string>,
  "RADIO_ID": <integer>,
  "SLOTS": <integer>,
  "LOCATION": <string>,
  "SERVER_IP": <string>,
  "SERVER_PORT": <integer>,
  "STATS": {
    "CONNECTION": <string, "YES" | "NO" | handshake state>,
    "CONNECTED": <float, Unix epoch, null if not connected>,
    "NUM_OUTSTANDING": <integer, unacknowledged pings, 0 in normal operation>
  }
}
```

**System object — OPENBRIDGE mode**:
```
{
  "MODE": "OPENBRIDGE",
  "CALLSIGN": <string>,
  "RADIO_ID": <integer>,
  "SLOTS": <integer>,
  "STATS": {
    "CONNECTION": <string>
  }
}
```

**Notes on epoch fields**: `CONNECTED` and `LAST_PING` are Unix epoch floats (seconds since
1970-01-01 UTC) as returned by C's `time()` or Python's `time.time()`. Dashboard consumers
subtract from the current wall clock to get elapsed seconds. The dashboard server enriches
these to `connected_secs` and `last_ping_secs` before forwarding to browser clients, so
browser code never sees raw epochs.

---

### `bridges` — Bridge state snapshot

Sent periodically (every `REPORT_INTERVAL`) and immediately when any bridge member's ACTIVE
state changes. Contains the full state of all configured bridges. Receivers replace their
entire bridge state on each `bridges` event.

```
{
  "type": "bridges",
  "bridges": {
    "<BRIDGE_NAME>": [
      {
        "SYSTEM": <string, system name>,
        "TGID": <integer>,
        "TS": <integer, 1 or 2>,
        "ACTIVE": <boolean>,
        "TO_TYPE": <string, "ON" | "OFF" | "NONE">,
        "TIMEOUT": <integer, seconds>,
        "TIMER": <float, Unix epoch when this member expires, 0 if TO_TYPE is NONE>,
        "ON": [<integer TGID>, ...],
        "OFF": [<integer TGID>, ...],
        "RESET": [<integer TGID>, ...]
      },
      ...
    ]
  }
}
```

`TIMER` is an absolute epoch. Consumers compute `remaining = TIMER - now` to display a
countdown. When `remaining <= 0` the member has expired (ACTIVE will become False on the
next rule timer evaluation). Dashboard consumers must handle the case where `TIMER` is in
the past before the next `bridges` snapshot arrives.

---

### `stream` — Call event

Emitted once at call START (first packet of a new stream) and once at call END (voice
terminator received, or stream reaped as stale). These are the only events emitted in the
routing hot path and must be assembled and written with minimal overhead.

```
{
  "type": "stream",
  "action": "START" | "END",
  "system": <string, ingress system name>,
  "slot": <integer, 1 or 2>,
  "stream_id": <string, 4-byte stream ID as hex>,
  "src": <integer, rf_src subscriber radio ID>,
  "dst": <integer, dst_id TGID for group calls, subscriber ID for unit calls>,
  "peer": <integer, repeater radio ID that originated the call>,
  "trx": "RX" | "TX",
  "call_type": "GROUP VOICE" | "UNIT VOICE",
  "pkt_time": <float, Unix epoch of the packet that triggered this event>
}
```

`trx` is `RX` for the ingress leg (received from a repeater or peer) and `TX` for egress
legs (forwarded to target systems). Dashboard call logs typically show only `RX` legs to
avoid duplicating each call for every forwarded copy.

---

### `hblink` — Feed connectivity

Emitted by the **dashboard server** (Python), not by the C core itself, to notify browser
clients that the connection to the C core's reporting port was lost or restored. The C core
does not emit this event type. It is included here for completeness so dashboard implementers
understand the full event space.

```
{ "type": "hblink", "connected": <boolean> }
```

---

## What is deliberately excluded

The following are not emitted because they would add overhead to the hot path or are
derivable by consumers from the data above:

- Per-packet statistics (byte counts, packet counts beyond what is in STATS)
- Talker alias data (destroyed during LC rewriting; see routing flow document)
- Per-system call history (consumers build this from stream events)
- Internal timing metrics (ping round-trip times, etc.)

The schema is intentionally minimal. Adding fields is cheap; removing them once consumers
depend on them is expensive. When in doubt, leave it out.
