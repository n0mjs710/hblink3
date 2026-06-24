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
