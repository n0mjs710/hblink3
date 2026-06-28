#!/usr/bin/env python
#
###############################################################################
#   Copyright (C) 2016-2026  Cortney T. Buffington, N0MJS <n0mjs@me.com>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

'''
HBlink3 dashboard backend.

Connects to HBlink3's NDJSON reporting feed (one JSON object per line), keeps the
authoritative display state, and serves a single-page UI that receives live JSON
deltas over a WebSocket. Run: python server.py  (or via run_dashboard.py).
'''

import asyncio
import json
import logging
import os
import ssl
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from urllib.request import urlopen

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import ijson
import uvicorn

from dmr_utils3.utils import mk_id_dict, get_alias

# Ensure THIS directory's config.py wins over HBlink3's top-level config.py.
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, 'static')
sys.path.insert(0, HERE)

# ---- configuration -----------------------------------------------------------
try:
    from config import (REPORT_NAME, HBLINK_IP, HBLINK_PORT, WEB_HOST, WEB_PORT,
                         LOG_LINES, PATH, PEER_FILE, SUBSCRIBER_FILE, TGID_FILE,
                         LOCAL_SUB_FILE, LOCAL_PEER_FILE)
except ImportError:
    sys.exit('No config.py found -- copy config_sample.py to config.py and edit it.')

try:
    from config import TRY_DOWNLOAD, PEER_URL, SUBSCRIBER_URL, STALE_DAYS
except ImportError:
    TRY_DOWNLOAD = False
    PEER_URL = 'https://www.radioid.net/static/rptrs.json'
    SUBSCRIBER_URL = 'https://www.radioid.net/static/users.json'
    STALE_DAYS = 7

try:
    from config import FILTER_COUNTRIES
except ImportError:
    FILTER_COUNTRIES = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('hbdash')

# Drop an "active" stream this many seconds after its START if no END arrives.
# Real calls end well within this; it clears phantoms left by interrupted calls
# or a lost/late terminator. (HBlink3's own stream trimmer times out at ~5s.)
STREAM_STALE = 30


# ---- alias resolution --------------------------------------------------------
def _abs(p):
    return p if os.path.isabs(p) else os.path.join(HERE, p)

def _stream_id_file(url, path, json_key, countries, stale_secs):
    now = time.time()
    if os.path.isfile(path) and (os.path.getmtime(path) + stale_secs) >= now:
        logger.info('ID ALIAS MAPPER: %s is current, not downloaded', os.path.basename(path))
        return
    no_verify = ssl._create_unverified_context()
    tmp = path + '.tmp'
    try:
        with urlopen(url, context=no_verify) as response, \
             open(tmp, 'w', encoding='utf-8') as out:
            out.write('{"%s":[' % json_key)
            first = True
            for record in ijson.items(response, json_key + '.item'):
                if not countries or record.get('country') in countries:
                    if not first:
                        out.write(',')
                    json.dump({'id': record['id'], 'callsign': record['callsign']}, out)
                    first = False
            out.write(']}')
        os.replace(tmp, path)
        label = ', '.join(sorted(countries)) if countries else 'all countries'
        logger.info('ID ALIAS MAPPER: %s downloaded (%s)', os.path.basename(path), label)
    except IOError as e:
        logger.error('ID ALIAS MAPPER: download of %s failed: %s', os.path.basename(path), e)
        if os.path.exists(tmp):
            os.remove(tmp)

def _download_aliases():
    if not TRY_DOWNLOAD:
        return
    base = _abs(PATH)
    stale_secs = int(STALE_DAYS) * 86400
    countries = set(FILTER_COUNTRIES) if FILTER_COUNTRIES else None
    _stream_id_file(PEER_URL,       base + PEER_FILE,       'rptrs', countries, stale_secs)
    _stream_id_file(SUBSCRIBER_URL, base + SUBSCRIBER_FILE, 'users', countries, stale_secs)

def _reload_aliases():
    global PEER_IDS, SUBSCRIBER_IDS, TALKGROUP_IDS
    base = _abs(PATH)
    PEER_IDS       = mk_id_dict(base, PEER_FILE)
    SUBSCRIBER_IDS = mk_id_dict(base, SUBSCRIBER_FILE)
    TALKGROUP_IDS  = mk_id_dict(base, TGID_FILE)
    if LOCAL_PEER_FILE:
        PEER_IDS.update(mk_id_dict(base, LOCAL_PEER_FILE))
    if LOCAL_SUB_FILE:
        SUBSCRIBER_IDS.update(mk_id_dict(base, LOCAL_SUB_FILE))
    logger.info('aliases loaded: %d peers, %d subscribers, %d talkgroups',
                len(PEER_IDS), len(SUBSCRIBER_IDS), len(TALKGROUP_IDS))

PEER_IDS = {}
SUBSCRIBER_IDS = {}
TALKGROUP_IDS = {}

async def _alias_refresh_loop():
    while True:
        await asyncio.sleep(86400)
        logger.info('aliases: starting daily refresh')
        await asyncio.to_thread(_download_aliases)
        _reload_aliases()

# Logo
try:
    from config import LOGO_FILE
except ImportError:
    LOGO_FILE = ''
_logo_path = _abs(LOGO_FILE) if LOGO_FILE else ''
_logo_exists = bool(_logo_path and os.path.isfile(_logo_path))
_logo_media_type = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.svg': 'image/svg+xml', '.webp': 'image/webp',
}.get(os.path.splitext(_logo_path)[1].lower(), 'image/png') if _logo_exists else 'image/png'
LOGO_HTML = '<img src="/logo" alt="" class="logo">' if _logo_exists else ''

def alias(_id, _dict):
    a = get_alias(_id, _dict)
    return None if a == _id else a


# ---- shared state ------------------------------------------------------------
class State:
    def __init__(self):
        self.systems = {}                 # last 'config' event payload
        self.bridges = {}                 # last 'bridges' event payload (enriched)
        self.streams = {}                 # key -> last START stream event (active calls)
        self.log = deque(maxlen=LOG_LINES)
        self.hblink = False
        self.clients = set()              # connected dashboard WebSockets
        self.ping_time = 5                # PING_TIME from hblink global config
        self.max_missed = 3               # MAX_MISSED from hblink global config

STATE = State()


def stream_key(evt):
    return '{}|{}|{}'.format(evt['system'], evt['slot'], evt['stream_id'])

def enrich_stream(evt):
    evt['src_alias'] = alias(evt['src'], SUBSCRIBER_IDS)
    evt['peer_alias'] = alias(evt['peer'], PEER_IDS)
    # A unit call's destination is a subscriber; a group call's is a talkgroup.
    if evt.get('call_type') == 'UNIT VOICE':
        evt['dst_alias'] = alias(evt['dst'], SUBSCRIBER_IDS)
    else:
        evt['dst_alias'] = alias(evt['dst'], TALKGROUP_IDS)
    return evt

# Convert server-side absolute epochs to "seconds since/until" deltas so the
# browser can tick live counters without depending on the client's clock.
def enrich_config(systems):
    now = time.time()
    for sysview in systems.values():
        if sysview['MODE'] == 'SERVER':
            for p in sysview.get('REPEATERS', {}).values():
                p['connected_secs'] = int(max(0, now - p.get('CONNECTED', now)))
                last_ping = p.get('LAST_PING', 0)
                p['last_ping_secs'] = int(max(0, now - last_ping)) if last_ping else None
        elif sysview['MODE'] == 'OUTBOUND':
            c = sysview.get('STATS', {}).get('CONNECTED')
            sysview['STATS']['connected_secs'] = int(max(0, now - c)) if c else None
    return systems

def enrich_bridges(bridges):
    now = time.time()
    for members in bridges.values():
        for m in members:
            m['TGID_NAME'] = alias(m['TGID'], TALKGROUP_IDS)
            if m['TO_TYPE'] in ('ON', 'OFF'):
                m['remaining'] = int(m['TIMER'] - now)
            else:
                m['remaining'] = None
    return bridges


async def broadcast(obj):
    dead = set()
    for ws in STATE.clients:
        try:
            await ws.send_json(obj)
        except Exception:
            dead.add(ws)
    STATE.clients -= dead


async def handle_event(evt):
    t = evt.get('type')
    if t == 'config':
        STATE.ping_time = evt.get('ping_time', STATE.ping_time)
        STATE.max_missed = evt.get('max_missed', STATE.max_missed)
        STATE.systems = enrich_config(evt['systems'])
        await broadcast({'type': 'config', 'systems': STATE.systems,
                         'ping_time': STATE.ping_time, 'max_missed': STATE.max_missed})
    elif t == 'bridges':
        STATE.bridges = enrich_bridges(evt['bridges'])
        await broadcast({'type': 'bridges', 'bridges': STATE.bridges})
    elif t == 'stream':
        enrich_stream(evt)
        key = stream_key(evt)
        if evt['action'] == 'START':
            evt['_seen'] = time.time()
            STATE.streams[key] = evt
        else:
            STATE.streams.pop(key, None)
        # Log ingress (RX) legs only, matching the original monitor's behavior.
        if evt['trx'] == 'RX':
            STATE.log.appendleft(evt)
        await broadcast(evt)
    else:
        logger.debug('ignoring unknown event type: %s', t)


# ---- HBlink3 feed client (with reconnect) ------------------------------------
async def hblink_feed():
    while True:
        try:
            reader, writer = await asyncio.open_connection(HBLINK_IP, HBLINK_PORT)
            logger.info('connected to HBlink3 feed at %s:%s', HBLINK_IP, HBLINK_PORT)
            STATE.hblink = True
            await broadcast({'type': 'hblink', 'connected': True})
            while True:
                line = await reader.readline()
                if not line:
                    break                                  # connection closed
                try:
                    await handle_event(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning('bad JSON line from HBlink3: %r', line[:120])
        except (ConnectionRefusedError, OSError) as e:
            logger.warning('HBlink3 feed unavailable (%s); retrying', e)
        finally:
            if STATE.hblink:
                STATE.hblink = False
                STATE.systems = {}
                STATE.bridges = {}
                STATE.streams = {}
                await broadcast({'type': 'hblink', 'connected': False})
        await asyncio.sleep(3)                              # reconnect delay


async def reap_streams():
    while True:
        await asyncio.sleep(5)
        now = time.time()
        stale = [k for k, e in STATE.streams.items() if now - e.get('_seen', now) > STREAM_STALE]
        for k in stale:
            STATE.streams.pop(k, None)
        if stale:
            logger.debug('reaped %d stale stream(s)', len(stale))


@asynccontextmanager
async def lifespan(app):
    await asyncio.to_thread(_download_aliases)
    _reload_aliases()
    refresher = asyncio.create_task(_alias_refresh_loop())
    feed      = asyncio.create_task(hblink_feed())
    reaper    = asyncio.create_task(reap_streams())
    yield
    feed.cancel()
    reaper.cancel()
    refresher.cancel()

app = FastAPI(lifespan=lifespan)


# ---- HTTP + WebSocket --------------------------------------------------------
@app.get('/', response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC, 'dashboard.html'), encoding='utf-8') as f:
        html = f.read()
    return html.replace('{{REPORT_NAME}}', REPORT_NAME).replace('{{LOGO_HTML}}', LOGO_HTML)

@app.get('/logo')
async def serve_logo():
    if not _logo_exists:
        raise HTTPException(status_code=404)
    return FileResponse(_logo_path, media_type=_logo_media_type)

@app.get('/api/state')
async def api_state():
    return JSONResponse(current_state())

def current_state():
    return {
        'report_name': REPORT_NAME,
        'hblink': STATE.hblink,
        'systems': STATE.systems,
        'bridges': STATE.bridges,
        'streams': list(STATE.streams.values()),
        'log': list(STATE.log),
        'ping_time': STATE.ping_time,
        'max_missed': STATE.max_missed,
    }

@app.websocket('/ws')
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    STATE.clients.add(ws)
    try:
        await ws.send_json({'type': 'initial', **current_state()})
        while True:
            await ws.receive_text()                        # ignore; keep the socket open
    except WebSocketDisconnect:
        pass
    finally:
        STATE.clients.discard(ws)


def main():
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level='warning')

if __name__ == '__main__':
    main()
