# Configuring HBlink3

HBlink3's conference bridge (`bridge.py`) is driven by two files:

| File | What it defines |
|---|---|
| `hblink.cfg` | The program itself: global timers, reporting, logging, alias lookups, and one stanza per **system** (a Server you host, a Client you dial out as, or an OpenBridge link). |
| `rules.py` | The **routing**: which systems talk to which, on what timeslot/talkgroup, and the talkgroup triggers/timers that turn bridges on and off. |

This document is the full field reference. The sample files (`hblink-SAMPLE.cfg`, `rules_SAMPLE.py`) carry only terse inline notes and point back here.

Both are read at startup; there is no live reload — change a file, restart the service.

---

# `hblink.cfg`

INI format (`configparser`). Section names in `[BRACKETS]`; `KEY: value` pairs. The four fixed sections (`[GLOBAL]`, `[REPORTS]`, `[LOGGER]`, `[ALIASES]`) must each appear once. Every **other** section is a *system*, named by you and identified by its `MODE`.

## `[GLOBAL]`

| Field | Meaning |
|---|---|
| `PATH` | Working directory for runtime files. `./` (the program's own directory) is normal. |
| `PING_TIME` | Seconds between keepalive pings on Server/Client connections. |
| `MAX_MISSED` | Missed pings before a connection is declared dead and torn down. |
| `USE_ACL` | `True`/`False` top-level on/off switch for the **global** ACLs below. System-level ACLs are independent of this. |
| `REG_ACL` | Registration ACL — which Client **radio IDs** may log in to any Server you host. ACL syntax below. |
| `SUB_ACL` | Subscriber ACL — which **subscriber IDs** (the person keying up) may pass traffic, globally. |
| `TGID_TS1_ACL` / `TGID_TS2_ACL` | Talkgroup ACLs applied to timeslot 1 / timeslot 2 traffic, globally. |

### ACL syntax

Every ACL is one string: `ACTION:list`, where `ACTION` is `PERMIT` or `DENY` and `list` is comma-separated IDs and ranges, or the word `ALL`.

```
PERMIT:ALL              # allow everything
DENY:1                  # block ID 1, allow the rest
DENY:1-5,3120101        # block the range 1–5 and the single ID 3120101
PERMIT:3100-3199,9      # allow only this range and ID 9; deny everything else
```

A global ACL and a system ACL both apply — traffic must pass **both**. Registration ACLs are only meaningful for `SERVER` systems (a Client or OpenBridge link never registers). See **[ACLS.md](ACLS.md)** for the full ACL model — PERMIT/DENY precedence, how ranges are matched, and worked examples.

## `[REPORTS]`

Feeds the real-time dashboard. See [`dashboard/`](dashboard/).

| Field | Meaning |
|---|---|
| `REPORT` | `True`/`False` — emit the event stream at all. |
| `REPORT_INTERVAL` | Seconds between periodic status reports. |
| `REPORT_PORT` | TCP port the report service listens on. |
| `REPORT_CLIENTS` | Comma-separated client IPs allowed to connect (e.g. `127.0.0.1`). |

## `[LOGGER]`

| Field | Meaning |
|---|---|
| `LOG_FILE` | Path to the log file. Empty ⇒ `/dev/null`. |
| `LOG_HANDLERS` | Handler set, e.g. `console-timed`, `file-timed`, or both comma-joined. |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `LOG_NAME` | Name tag prefixed to log lines. |

> Running under **systemd**? You can skip the log file entirely and rely on the journal:
> set `LOG_FILE` to `/dev/null` (or leave it empty) and use a `console` handler — systemd
> captures stdout into the journal (`journalctl -u hblink3`). Most installs are perfectly
> fine doing exactly this.

## `[ALIASES]`

Maps DMR IDs to callsigns/names so logs read as `N0MJS` instead of `3120001`. The files come from radioid.net.

| Field | Meaning |
|---|---|
| `USE_ALIASES` | `True`/`False`. When `False`, no alias files are loaded and logs show raw numeric IDs. Default `False`. |
| `TRY_DOWNLOAD` | `True` fetches fresh files from the URLs below on startup and refreshes them past `STALE_DAYS`. `False` uses whatever is already on disk. |
| `PATH` | Directory holding the alias JSON files. Must end in `/`. Point this at the dashboard's directory to share one copy. |
| `PEER_FILE` / `SUBSCRIBER_FILE` / `TGID_FILE` | Filenames for the peer, subscriber, and talkgroup maps. `TGID_FILE` is optional. |
| `PEER_URL` / `SUBSCRIBER_URL` | radioid.net download URLs used when `TRY_DOWNLOAD` is `True`. |
| `STALE_DAYS` | Re-download after this many days. |

> ⚠️ **Memory warning — read before enabling aliases here.**
> The full radioid.net subscriber database, expanded into HBlink's in-memory Python
> dictionary, balloons to roughly **500 MB of RAM**. `bridge.py` (and `dmrlink`) *can*
> load these files just to print callsigns in the log — but if you also run the
> **dashboard** with aliases, you load the whole thing **twice: over 1 GB of RAM, purely
> for prettier logs.**
>
> **Recommendation:** enable aliases in the **dashboard only**, and leave
> `USE_ALIASES: False` here (or omit the files) for `bridge.py` / `dmrlink`. Turn them on
> in the main program **only** if the subscriber file has been **heavily filtered** to a
> much smaller size first — use the dashboard's `FILTER_COUNTRIES` (in `dashboard/config.py`),
> which writes only the selected countries to disk; both programs then load that smaller
> file. Filtered to a country or two, the footprint drops to roughly **150 MB**; it's the
> *unfiltered* global load in two processes that costs the gigabyte and is almost never
> worth it.

## System stanzas

Any section that isn't one of the four above is a *system*. It is loaded only if `ENABLED: True`, and its `MODE` selects one of three types:

| `MODE` | Role |
|---|---|
| `SERVER` | You are the **Server**: Clients — repeaters, hotspots, or another HBlink acting as a Client — connect *in* to you. (Older DMR docs call a Server a "master"; same thing — a master **is** a Server.) |
| `OUTBOUND` | You are a **Client**: HBlink masquerades as a repeater/hotspot and dials *out* to someone else's Server. |
| `OPENBRIDGE` | A **Server-to-Server** OpenBridge link — no Client role, no login handshake. |

> 🛑 **The section name is load-bearing — get it exactly right.** The name you give a
> system (e.g. `[SERVER-1]`) is the exact string `rules.py` references in its `SYSTEM`
> field. **If any `SYSTEM` in `rules.py` does not match a system name in `hblink.cfg`,
> `bridge.py` will not start at all.** Keep the two files in sync, character for character.

### `MODE: SERVER` (be a Server)

You are the Server; Clients (repeaters, hotspots, or another HBlink acting as a Client) connect in.

| Field | Meaning |
|---|---|
| `ENABLED` | `False` ⇒ the stanza is skipped entirely. |
| `REPEAT` | `True` repeats traffic between Clients connected to *this* Server (normal). `False` makes it a pure bridge feed. |
| `MAX_REPEATERS` | Cap on simultaneously logged-in Clients. |
| `IP` | Local bind address (`0.0.0.0` = all interfaces). |
| `PORT` | Local UDP port Clients connect to. |
| `PASSPHRASE` | Shared login secret Clients must present. |
| `GROUP_HANGTIME` | Seconds a slot stays reserved to the current talkgroup after a transmission, so quick replies aren't lost to contention. |
| `USE_ACL` | Enable this system's own ACLs. |
| `REG_ACL` | Which radio IDs may register to this Server. |
| `SUB_ACL` | Subscriber ACL for this system. |
| `TGID_TS1_ACL` / `TGID_TS2_ACL` | Per-timeslot talkgroup ACLs for this system. |

### `MODE: OUTBOUND` (be a Client)

HBlink masquerades as a repeater/hotspot and dials out to another Server. Most fields are the homebrew configuration/login packet this Client presents to the far Server — cosmetic to HBlink, but visible on the far end.

> 🛑 **BrandMeister will not accept this.** BrandMeister prohibits inbound Client
> (`OUTBOUND`) connections for bridging and **requires OpenBridge** instead. To link
> HBlink to BrandMeister, use `MODE: OPENBRIDGE`, not `OUTBOUND`.

| Field | Meaning |
|---|---|
| `ENABLED` | `False` ⇒ skipped. |
| `LOOSE` | `True` relaxes inbound validation: frames are accepted even if the source radio ID in the header doesn't match this connection's `RADIO_ID`. Needed for some Servers; leave `False` when the far end is strict. |
| `IP` / `PORT` | Our own local bind address/port. |
| `SERVER_IP` / `SERVER_PORT` | The far Server's address/port we dial. |
| `PASSPHRASE` | Login secret for the far Server. |
| `RADIO_ID` | The DMR ID we log in as. |
| `CALLSIGN` | Our callsign (8 chars, space-padded). |
| `RX_FREQ` / `TX_FREQ` | Reported RX/TX frequencies (Hz, 9 digits). |
| `TX_POWER` | Reported TX power. |
| `COLORCODE` | Reported DMR color code. |
| `LATITUDE` / `LONGITUDE` / `HEIGHT` / `LOCATION` / `DESCRIPTION` / `URL` | Reported location/metadata. |
| `SLOTS` | Slot capability reported to the Server. |
| `SOFTWARE_ID` / `PACKAGE_ID` | Reported software/package identifiers. |
| `OPTIONS` | Free-form options string some Servers parse for per-connection settings. Leave empty if unused. |
| `GROUP_HANGTIME` | As above. |
| `USE_ACL` / `SUB_ACL` / `TGID_TS1_ACL` / `TGID_TS2_ACL` | This system's ACLs. (No `REG_ACL` — a Client doesn't accept registrations.) |

### `MODE: OPENBRIDGE` (server-to-server link)

OpenBridge is a Server-to-Server (both ends equal), always-TS1 link authenticated by a shared HMAC key and the source socket — no login/registration handshake.

| Field | Meaning |
|---|---|
| `ENABLED` | `False` ⇒ skipped. |
| `IP` / `PORT` | Our own local bind address/port. |
| `TARGET_IP` / `TARGET_PORT` | The far OpenBridge server's address/port. |
| `PASSPHRASE` | Shared HMAC-SHA1 key. **Must match exactly on both ends** — this plus the source socket *is* the authentication. |
| `NETWORK_ID` | A DMR-ID-shaped number identifying this server. By convention it is stamped into every outgoing frame's "Repeater ID" field. |
| `PRESERVE_SOURCE_PEER` | `True` forwards the **originating** peer ID in that Repeater-ID field instead of overwriting it with `NETWORK_ID`. The field is unvalidated (auth is the HMAC + source socket) and used only for logging/reporting, so this simply preserves a call's true source across the link. Default `False` (spec-conventional). Most useful when **both** ends enable it. |
| `BOTH_SLOTS` | `True` lets unit (private) calls use both slots; group traffic is always TS1. **🛑 Only HBlink is known to accept this. No other OpenBridge server (BrandMeister, DMR+, etc.) accepts both-slots traffic — set `True` only on HBlink-to-HBlink links, and leave it `False` everywhere else.** |
| `USE_ACL` / `SUB_ACL` | This link's subscriber ACL. |
| `TGID_ACL` | Talkgroup ACL (TS1 only — note the single-slot name, unlike the `SERVER`/`OUTBOUND` `TGID_TS1_ACL`/`TGID_TS2_ACL`). |

---

# `rules.py`

Defines the conference bridges — the actual routing. Think "bridge groups" or "reflectors": every system marked active on a bridge exchanges traffic with every other active system on that same bridge. It is **not** end-to-end; each system is activated on each bridge independently.

## `BRIDGES`

A dict. Each **key** is an arbitrary bridge name (`'WORLDWIDE'`, `'STATEWIDE'`, …). Each **value** is a list of system entries — one per participating system — each a dict of:

| Key | Meaning |
|---|---|
| `SYSTEM` | The system's section name from `hblink.cfg`. **Must match exactly.** |
| `TS` | Timeslot (`1` or `2`) this rule matches. (XLX links should always use TS 2.) |
| `TGID` | Talkgroup ID this rule matches. (XLX links should always use TG 9.) |
| `ACTIVE` | `True`/`False` — is this system currently passing traffic on this bridge? (Triggers below can flip it at runtime.) |
| `TO_TYPE` | Timeout behavior: `'ON'` = auto-turn-**off** after `TIMEOUT`; `'OFF'` = auto-turn-**on** after `TIMEOUT`; anything else (use `'NONE'`) = no timer. |
| `TIMEOUT` | Timer length in **minutes**. (Minutes only — timers cost performance.) |
| `ON` | List of talkgroup IDs that, when keyed, **activate** this system on the bridge. Always a list, even for one (`[2]`); use `[]` for none. |
| `OFF` | List of talkgroup IDs that **deactivate** it. |
| `RESET` | List of talkgroup IDs that reset a running timer without otherwise changing state. Only needed when your voice TGID differs from your trigger TGIDs; otherwise `[]`. |

### Example

```python
BRIDGES = {
    'WORLDWIDE': [
        {'SYSTEM': 'SERVER-1', 'TS': 1, 'TGID': 1,    'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'ON',   'ON': [2], 'OFF': [9, 10], 'RESET': []},
        {'SYSTEM': 'CLIENT-1', 'TS': 1, 'TGID': 3100, 'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'ON',   'ON': [2], 'OFF': [9, 10], 'RESET': []},
    ],
    'STATEWIDE': [
        {'SYSTEM': 'SERVER-1', 'TS': 2, 'TGID': 3129, 'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [4], 'OFF': [7, 10], 'RESET': []},
        {'SYSTEM': 'CLIENT-2', 'TS': 2, 'TGID': 3129, 'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [4], 'OFF': [7, 10], 'RESET': []},
    ],
}
```

Here anything on `SERVER-1` TS1/TG1 reaches `CLIENT-1` TS1/TG3100 and vice-versa (talkgroups are rewritten per entry). On `WORLDWIDE`, keying TG2 turns a system on for 2 minutes; TG9 or TG10 turns it off. `STATEWIDE` has no timers.

## `UNIT`

```python
UNIT = ['SERVER-1', 'CLIENT-2']
```

A flat list of system names that should bridge **unit-to-unit (private) calls** to each other. Systems not listed here pass group calls per `BRIDGES` but ignore private calls.

## Validating

`rules.py` is executable Python. Run it directly to syntax-check before restarting the service:

```
python3 rules.py
```

It pretty-prints `BRIDGES` and `UNIT` if the file parses; a traceback means a syntax error to fix.
