# HBlink3

HBlink3 is an open-source implementation of the DMR **HomeBrew Repeater Protocol (HBP)** in Python 3. It acts as an HBP master and/or peer and routes calls between MMDVM-based DMR systems, working as a **transit / conference-bridge router** — traffic is selectively routed from ingress systems to egress systems according to a rules file. It can also link to **Brandmeister** and **DMR+ (IPSC2)** via **OpenBridge**.

> **Which HBlink?** HBlink3 is system-oriented and built for **transit routing and conference bridging** between networks. Its companion, **HBlink4** (by the same author), is a repeater-oriented **endpoint server** for running a single regional network. Pick the one that matches your role.

## Applications

| Program | Purpose |
|---|---|
| `bridge.py` | The main application: a configurable conference-bridge call router with dynamic on/off triggering, timeouts, and private (unit) call routing. |
| `bridge_all.py` | A simple proxy that forwards all traffic between every configured system — makes several repeaters appear as one. |
| `hblink.py` | The protocol core. Runs standalone as a master/peer for testing, and is the module the applications are built on. |

## Features

- HBP **master** and **peer** modes for MMDVM repeaters and hotspots
- **OpenBridge** connectivity to Brandmeister / DMR+ (IPSC2)
- Rules-based **conference-bridge routing** with dynamic ON/OFF/RESET talkgroup triggers and timeouts
- **Private (unit) call** routing using a learned subscriber-to-system map
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

- **`hblink.cfg`** — global settings, reporting, logging, alias downloads, and one stanza per system (`MASTER`, `PEER`, or `OPENBRIDGE`). Start from `hblink-SAMPLE.cfg`.
- **`rules.py`** — (`bridge.py` only) defines the conference bridges, the systems/talkgroups/timeslots that belong to each, and the systems permitted to pass unit calls. Start from `rules_SAMPLE.py`.

Your `hblink.cfg` and `rules.py` are git-ignored, so `git pull` won't overwrite them.

## Dashboard

A real-time web dashboard lives in [`dashboard/`](dashboard/) — a separate program
with a modern dark UI showing master/peer/OpenBridge systems, conference-bridge
state, and a live call log. Enable HBlink3's reporting feed (`[REPORTS]` in
`hblink.cfg`) and see [dashboard/README.md](dashboard/README.md) to run it.

## Known Limitations

### DMR Talker Alias is not preserved across bridges

When `bridge.py` routes a call to a system with a different TGID or timeslot, it
rewrites the Link Control (LC) word in every forwarded DMR frame — voice header,
voice terminator, and the embedded LC carried in voice bursts B–E. This is
necessary and correct: the translated TGID and source subscriber ID must be
consistent in the header LC (call setup and late entry), the terminator LC, and
the embedded LC in intermediate bursts. A mismatch between the header LC and the
burst-embedded LC would cause late-joining radios to decode the wrong TGID and
potentially receive or route traffic incorrectly.

The consequence is that **DMR Talker Alias data embedded in voice bursts is
destroyed during bridging.** Talker Alias occupies the same embedded LC slots
(FLCO 0x04–0x07 in bursts B–E) that the bridge overwrites with the translated
call LC. There is no way to preserve both simultaneously.

This is worse when using an IPSC2/ipsc2hbp adapter. IPSC carries LC completely
differently from DMR over-the-air; the adapter must reconstruct HBP frames from
IPSC data, and any talker-alias LC embedded in those reconstructed frames cannot
be assumed to match the DMR embedded-LC format that HBlink3 expects.

**Workaround:** None at this time. Radios that look up talker alias via the
RadioID.net database or a local DMR ID file are unaffected.

## Requirements

- Python **3.8+** (Linux recommended)
- `bitarray`, `dmr_utils3` — see `requirements.txt`

## License

Copyright (C) 2016-2026 Cortney T. Buffington, N0MJS — n0mjs@me.com

Licensed under the **GNU GPLv3**; see [LICENSE.txt](LICENSE.txt). You may use this software freely, but please credit the project somewhere public (club, organization, or project site) so we can see where it's in use.

## Support & contributing

Maintained by one person with limited resources. Genuine bug reports are welcome; please don't open issues for configuration help or feature requests. Discuss new features first and submit pull requests on a feature branch.

## Acknowledgments

The HomeBrew Repeater Protocol is the work of Jonathan Naylor (G4KLX), Hans Barthen (DL5DI), and Torsten Schultze (DG1HT). This project is the author's clean-room interpretation of that protocol.
