# Installing HBlink3

## Prerequisites

- Python **3.8** or newer
- Linux (developed and run on Linux; other platforms are untested)
- Install and run as the **same user account**

## 1. Clone and create a virtual environment

```bash
git clone https://github.com/n0mjs710/hblink3.git
cd hblink3
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

The optional web [dashboard](dashboard/) can share this same virtualenv — it only
adds FastAPI and Uvicorn on top of the core. To include it:

```bash
pip install -r dashboard/requirements.txt   # or:  pip install -e ".[dashboard]"
```

## 2. Configure

Copy the sample config (the live name is git-ignored, so updates won't overwrite your edits):

```bash
cp hblink-SAMPLE.cfg hblink.cfg
```

Edit `hblink.cfg`:

- **[GLOBAL]** — ping timing and the default access-control lists.
- **[REPORTS]** — enable the TCP feed for a dashboard (listen port and allowed client IPs).
- **[LOGGER]** — log file, handlers, and level.
- **[ALIASES]** — optional download of repeater/subscriber ID files from radioid.net.
- **System stanzas** — one per connection. Set `MODE` to:
  - `MASTER` — accept incoming repeaters/hotspots
  - `PEER` — connect outward to another master
  - `OPENBRIDGE` — link to Brandmeister / DMR+ (IPSC2)

  Duplicate a stanza for each system; the stanza name (e.g. `[MASTER-1]`) must be unique.

If you are running `bridge.py`, also create the rules file:

```bash
cp rules_SAMPLE.py rules.py
```

`rules.py` defines:

- **`BRIDGES`** — each conference bridge and the system / timeslot / talkgroup that belongs to it, with optional ON/OFF/RESET trigger talkgroups and timeouts.
- **`UNIT`** — the systems permitted to exchange private (unit) calls.

Every system named in `rules.py` must exist and be enabled in `hblink.cfg`.

## 3. Run

```bash
source venv/bin/activate
python bridge.py -c hblink.cfg -r rules.py     # conference-bridge router (main app)
# or
python bridge_all.py -c hblink.cfg             # forward-everything proxy
# or
python hblink.py -c hblink.cfg                 # protocol core, standalone master/peer
```

`-c` / `-r` default to `hblink.cfg` and `rules.py` in the program directory. `-l LEVEL` overrides the configured log level.

## 4. Run as a service (systemd)

A sample unit, `hblink3.service`, is provided (it targets `bridge.py`):

```bash
sudo cp hblink3.service /etc/systemd/system/
sudoedit /etc/systemd/system/hblink3.service   # set User/Group and the install paths
sudo systemctl daemon-reload
sudo systemctl enable --now hblink3
journalctl -u hblink3 -f                        # follow the log
```

## Updating

```bash
git pull
source venv/bin/activate
pip install -r requirements.txt    # in case dependencies changed
sudo systemctl restart hblink3
```

`hblink.cfg` and `rules.py` are git-ignored and are not touched by `git pull`.

## Notes

- The legacy voice utilities (`playback.py`, `play_ambe.py`) are **not** migrated to asyncio and will not run against the current core.
