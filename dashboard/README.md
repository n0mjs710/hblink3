# HBlink3 Dashboard

A real-time web dashboard for HBlink3, in the spirit of the old HBmonitor (same
tables and information) but with a modern dark UI and a far more efficient data
path. It is a **separate program** from the HBlink3 core.

It connects to HBlink3's reporting feed, keeps the display state, and pushes
live **JSON deltas** to the browser over a WebSocket. The browser renders the
tables and ticks the duration/uptime counters itself — no full-page or
full-table refreshes, and no pickle on the wire.

## What it shows

- **Master Systems** — each master and its connected repeaters (ID, callsign,
  location, IP, uptime, frequency, colorcode, slots) with the live call on each
  timeslot (source → destination, color-coded by direction).
- **Peer Systems** — outbound peer connections with health (connection state,
  pings sent/ackd/lost) and live per-timeslot calls.
- **OpenBridge Systems** — network ID, target, and active streams.
- **Conference Bridges** — each bridge's members: timeslot, talkgroup,
  connected/disconnected state, timeout countdown, and on/off trigger talkgroups.
- **Call Log** — a running log of call starts and ends.

## Install

```bash
cd dashboard
python3 -m venv venv && source venv/bin/activate      # or reuse the HBlink3 venv
pip install -r requirements.txt
cp config_sample.py config.py        # then edit config.py
python server.py                     # or, from the repo root: python run_dashboard.py
```

Open `http://<host>:8080`.

## Configuration (`config.py`)

- `HBLINK_IP` / `HBLINK_PORT` — HBlink3's reporting feed (the `[REPORTS]` section
  of `hblink.cfg`: `REPORT_PORT`, default 4321).
- `WEB_HOST` / `WEB_PORT` — where the dashboard listens.
- `PATH` / `*_FILE` — alias files mapping DMR IDs to callsigns/talkgroup names
  (the same files HBlink3 uses; point `PATH` at HBlink3's directory to share them).
- `REPORT_NAME`, `LOG_LINES` — branding and call-log length.

## HBlink3 side

In `hblink.cfg`, under `[REPORTS]`:

- `REPORT: True`
- `REPORT_PORT` must match the dashboard's `HBLINK_PORT`
- `REPORT_CLIENTS` must include the dashboard host's IP (or `*`)
- `REPORT_INTERVAL` sets how often the systems/bridge snapshot is refreshed.
  **Live calls are pushed instantly**; connection/peer-state changes appear on the
  next snapshot, so `10` is a good value for dashboard use.

## Notes

- This replaces the old **HBmonitor**. HBlink3's reporting feed is now
  newline-delimited JSON, which HBmonitor does not understand.
- The dashboard depends on FastAPI + Uvicorn; the HBlink3 core itself remains
  stdlib-only.
