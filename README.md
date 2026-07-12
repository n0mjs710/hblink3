# HBlink3

HBlink3 is an open-source implementation of the DMR **HomeBrew Repeater Protocol (HBP)** in Python 3. It acts as an HBP server and/or client and routes calls between MMDVM-based DMR systems, working as a **transit / conference-bridge router** — traffic is selectively routed from ingress systems to egress systems according to a rules file. It can also connect to other HBlink3 and similar systems via **OpenBridge**.

> **Which HBlink?** HBlink3 is "system-oriented" and built for **transit routing and conference bridging** between networks. Its companion, **HBlink4** (by the same author), is a repeater-oriented **endpoint server** for running a single end-point network with no granudlar transit routing. Pick the one that matches your role.

> ⚠️ **Upgrading? Two breaking config changes to know about.**
> 1. **OpenBridge routing moved** — `MODE: OPENBRIDGE` systems are no longer inline `BRIDGES` members in `rules.py`; they now live in a dedicated **`OBP_BRIDGES`** table. Old configs stop with a clear startup error. Run **`python tools/migrate_obp_rules.py`** to convert your `rules.py` automatically, or see [CONFIGURING.md](CONFIGURING.md#obp_bridges).
> 2. **System modes renamed** — `MASTER`→**`SERVER`** and `PEER`/`CLIENT`→**`OUTBOUND`** (`OPENBRIDGE` unchanged). Update the `MODE:` line of each stanza in `hblink.cfg`.
>
> Your `hblink.cfg`/`rules.py` are git-ignored, so a `git pull` won't touch them — but the new code won't accept the old values. Non-OpenBridge configs need only change (2).

## Applications

| Program | Purpose |
|---|---|
| `bridge.py` | The main application: a configurable conference-bridge call router with dynamic on/off triggering, timeouts, and private (unit) call routing. |
| `bridge_all.py` | A simple proxy that forwards all traffic between every configured system — makes several repeaters appear as one. |
| `hblink.py` | The protocol core. Runs standalone as a server/outbound system for testing, and is the module the applications are built on. |

## Features

- HBP **server** and **outbound** (client) modes for MMDVM repeaters and hotspots
- **OpenBridge** connectivity to other transit call routers, including Brandmeister / DMR+ (IPSC2)
- Rules-based **conference-bridge routing** with dynamic ON/OFF/RESET talkgroup triggers and timeouts, including the ability to use TGIDs as in-band signalling to trigger dynamic connections -- sometimes referred to as "dial-a-talkgroup".
- **Private (unit) call** routing using a learned subscriber-to-system map that only floods systems configured for unit calls, and only until the target unit's system is located. It then prunes re-transmission to the system with the target unit.
- Layered **access control lists** — registration, subscriber, and per-timeslot talkgroup
- TCP **reporting feed** for external dashboards
- Built on Python **`asyncio`** — no external networking framework

## Quick start

```bash
git clone https://github.com/n0mjs710/hblink3.git
cd hblink3
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp hblink-SAMPLE.cfg hblink.cfg      # edit for your systems
cp rules_SAMPLE.py  rules.py         # edit your bridges (bridge.py only)
python bridge.py -c hblink.cfg -r rules.py
```

See **[INSTALL.md](INSTALL.md)** for full setup, configuration, and running as a systemd service.

## Configuration

- **`hblink.cfg`** — global settings, reporting, logging, alias downloads, and one stanza per system (`SERVER`, `OUTBOUND`, or `OPENBRIDGE`). Start from `hblink-SAMPLE.cfg`.
- **`rules.py`** — (`bridge.py` only) defines the conference bridges, the systems/talkgroups/timeslots that belong to each, and the systems permitted to pass unit calls. Start from `rules_SAMPLE.py`.

Your `hblink.cfg` and `rules.py` are git-ignored, so `git pull` won't overwrite them.

### Alias files

HBlink3 resolves numeric IDs to human-readable names in logs and in the dashboard. The
`[ALIASES]` section of `hblink.cfg` controls where these files live and how they are obtained.

**Peer and subscriber aliases** (`peer_ids.json`, `subscriber_ids.json`) are downloaded
automatically from RadioID.net when `TRY_DOWNLOAD: True` is set and the files are older
than `STALE_DAYS`. No manual action is needed for these.

**Talkgroup aliases** (`talkgroup_ids.json`) have no automatic download source. This file
must be created and maintained by the operator. Copy `talkgroup_ids_SAMPLE.json` to the
path configured in `[ALIASES]` (default `./`) and rename it to match `TGID_FILE`
(default `talkgroup_ids.json`), then edit it to reflect the talkgroups used on your network.

The format is a JSON object with a single key containing a list of records, each with an
`id` field (integer talkgroup number) and a `callsign` field (display name). The field is
named `callsign` because the same parser in `dmr_utils3` handles peer, subscriber, and
talkgroup files alike — for talkgroups it simply holds the human-readable name. The outer
key name can be anything, but it must be the first and only key in the object — the parser
takes the value of the first key it finds:

```json
{
    "talkgroups": [
        {"id": 3100,    "callsign": "Nationwide"},
        {"id": 3170001, "callsign": "KS Statewide"}
    ]
}
```

The outer key name (`"talkgroups"` above) can be anything — only the list it contains is
used. Talkgroup IDs not present in the file are displayed as their raw numeric value.

## Dashboard

A real-time web dashboard lives in [`dashboard/`](dashboard/) — a separate program
with a modern dark UI showing server/outbound/OpenBridge systems, conference-bridge
state, and a live call log. Enable HBlink3's reporting feed (`[REPORTS]` in
`hblink.cfg`) and see [dashboard/README.md](dashboard/README.md) to run it. the dashboard
is kept separate to keep performance of the forwarding hot path free from unecessary
loads.

## DMR Talker Alias is not preserved across bridges

When `bridge.py` routes a call to a system with a different TGID or timeslot, it
rewrites the Link Control (LC) word in every forwarded DMR frame — voice header,
voice terminator, and the embedded LC carried in voice bursts B–E.

The consequence is that **DMR Talker Alias data embedded in voice bursts is
destroyed during bridging.** Talker Alias occupies the same embedded LC slots
(FLCO 0x04–0x07 in bursts B–E) that the bridge overwrites with the translated
call LC.

This was a consious design decision to ensure every superframe carries full LC
information -- preferencing voice quality and integrity over all else.

**Future:** Currently, most hams appear to prefer/additinoally use a static 
database for ID to callsign mapping. Inclusion of Talker Alias may be 
considered in the future.

## Requirements

- Python, at least, **3.8+** (Linux recommended)
- `bitarray`, `dmr_utils3` — see `requirements.txt`

## License

Copyright (C) 2016-2026 Cortney T. Buffington, N0MJS — n0mjs@me.com

Licensed under the **GNU GPLv3**; see [LICENSE.txt](LICENSE.txt). You may use this software freely, but please credit the project somewhere public (club, organization, or project site) so we can see where it's in use.

### No Support Is Provided

This is not commercial software. It is provided free of charge. The author(s)
received no compensation for creating and maintaining it. Countless hours over
many years have gone into the this. If you have problems, the author will try
to help if possible, please have no expectations for support. There is no online
group, such as DVSwitch or groups.io that is an "official" outlet for information.
The only definitive source of information is me. Beware of others claiming to
be authoritative. User-based mutual support is great, and I'm all for it. But
please understand, this is what they are, and I have not sanctioned anyone to be
the "home" of my software packages.

### GitHub "Issues"

Do not use GitHub issues for support. Genuine bugs are accepted as issues. Before 
opening one, make sure that it is a true problem with the software and not merely
a misconfiguration, or contention around a feature that was not supported. Isssues
should never be used to ask for or recommend features. Issues that do not include
complete details, relevent tracebacks, error messages, configuration snippets, 
operatrional conditions surrounding the event, etc. will be closed without action.

## Acknowledgments

The HomeBrew Repeater Protocol is the work of Jonathan Naylor (G4KLX), Hans Barthen (DL5DI), and Torsten Schultze (DG1HT). This project is the author's clean-room interpretation of that protocol.
