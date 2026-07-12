#!/usr/bin/env python
#
# Regression tests for the asyncio migration's protocol mechanics -- the parts
# the routing harness does not exercise: the server login handshake state
# machine, the reporting server's netstring framing, and that the UDP send paths
# use transport.sendto(). Driven against mock transports (no real sockets).
#
# Run from the repo root:   venv/bin/python -m unittest discover -s tests

import asyncio
import json
import logging
import os
import sys
import unittest
from hashlib import sha256
from binascii import a2b_hex as bhex

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import config
import hblink

# Quiet the protocol's INFO chatter (assertLogs elsewhere still works, since it
# temporarily lowers the level within its own context).
logging.getLogger('hblink').setLevel(logging.CRITICAL)
from dmr_utils3.utils import bytes_3, bytes_4
from const import RPTL, RPTK, RPTC, RPTACK, MSTNAK, DMRD, HBPF_SLT_VHEAD, HBPF_SLT_VTERM

CFG = config.build_config(os.path.join(_HERE, 'harness.cfg'))


class MockDatagramTransport:
    """Captures sendto(data, addr) like an asyncio DatagramTransport."""
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr=None):
        self.sent.append((bytes(data), addr))


class MockStreamTransport:
    """Captures write(data) like an asyncio (TCP) Transport."""
    def __init__(self, peername=('127.0.0.1', 50001)):
        self.written = bytearray()
        self._peername = peername
        self.closed = False

    def write(self, data):
        self.written += data

    def get_extra_info(self, key):
        return self._peername if key == 'peername' else None

    def close(self):
        self.closed = True


class TestServerHandshake(unittest.TestCase):
    """Drive a SERVER through RPTL -> RPTK -> RPTC over datagram_received and
    assert the state machine and responses, exercising the asyncio rename
    (datagram_received) and transport.sendto path."""

    def setUp(self):
        self.server = hblink.HBSYSTEM('SERVER-1', CFG, None)
        self.server.transport = MockDatagramTransport()   # bypass connection_made's task
        self.peer_id = bytes_4(312000)
        self.addr = ('127.0.0.1', 50000)
        self.passphrase = CFG['SYSTEMS']['SERVER-1']['PASSPHRASE']

    def test_full_login_exchange(self):
        m = self.server
        # 1) Login request -> challenge
        m.datagram_received(RPTL + self.peer_id, self.addr)
        self.assertIn(self.peer_id, m._repeaters)
        self.assertEqual(m._repeaters[self.peer_id]['CONNECTION'], 'CHALLENGE_SENT')
        self.assertEqual(m.transport.sent[-1][0][:len(RPTACK)], RPTACK)

        # 2) Answer the challenge with the correct hash -> WAITING_CONFIG
        salt = bytes_4(m._repeaters[self.peer_id]['SALT'])
        calc = bhex(sha256(salt + self.passphrase).hexdigest())
        m.datagram_received(RPTK + self.peer_id + calc, self.addr)
        self.assertEqual(m._repeaters[self.peer_id]['CONNECTION'], 'WAITING_CONFIG')
        self.assertEqual(m.transport.sent[-1][0], RPTACK + self.peer_id)

        # 3) Send configuration -> connected
        rptc = RPTC + self.peer_id + b'TEST    ' + b'\x00' * 300
        m.datagram_received(rptc, self.addr)
        self.assertEqual(m._repeaters[self.peer_id]['CONNECTION'], 'YES')
        self.assertEqual(m.transport.sent[-1][0], RPTACK + self.peer_id)

    def test_wrong_passphrase_is_rejected(self):
        m = self.server
        m.datagram_received(RPTL + self.peer_id, self.addr)
        bad = bhex(sha256(b'\x00\x00\x00\x00' + b'wrong').hexdigest())
        m.datagram_received(RPTK + self.peer_id + bad, self.addr)
        # Peer removed and a NAK sent
        self.assertNotIn(self.peer_id, m._repeaters)
        self.assertEqual(m.transport.sent[-1][0][:6], MSTNAK)


class TestPeerEvents(unittest.TestCase):
    """The reconciliation sweep emits granular 'peer' connected/disconnected
    events so the dashboard reflects repeater changes without the full push."""

    def _server(self):
        captured = []

        class Rep:
            def send_peer(self, system, radio_id, action, info=None):
                captured.append((system, radio_id, action, info))

        srv = hblink.HBSYSTEM('SERVER-1', CFG, Rep())
        srv.transport = MockDatagramTransport()
        return srv, captured

    def _login(self, srv, radio_id=312000):
        pid = bytes_4(radio_id)
        addr = ('127.0.0.1', 50000)
        passphrase = CFG['SYSTEMS']['SERVER-1']['PASSPHRASE']
        srv.datagram_received(RPTL + pid, addr)
        salt = bytes_4(srv._repeaters[pid]['SALT'])
        calc = bhex(sha256(salt + passphrase).hexdigest())
        srv.datagram_received(RPTK + pid + calc, addr)
        srv.datagram_received(RPTC + pid + b'TEST    ' + b'\x00' * 300, addr)
        return pid

    def test_connected_event_on_login(self):
        srv, cap = self._server()
        self._login(srv)
        conn = [c for c in cap if c[2] == 'connected']
        self.assertEqual(len(conn), 1)
        self.assertEqual(conn[0][0], 'SERVER-1')      # system
        self.assertEqual(conn[0][1], 312000)          # radio_id
        self.assertIsNotNone(conn[0][3])              # info payload present
        self.assertEqual(conn[0][3]['RADIO_ID'], 312000)

    def test_no_duplicate_connected_event(self):
        srv, cap = self._server()
        self._login(srv)
        srv.report_peer_deltas()                      # a later sweep, nothing changed
        self.assertEqual(len([c for c in cap if c[2] == 'connected']), 1)

    def test_disconnected_event_on_timeout(self):
        srv, cap = self._server()
        pid = self._login(srv)
        srv._repeaters[pid]['LAST_PING'] = 0          # force the ping timeout
        srv.server_maintenance_loop()
        disc = [c for c in cap if c[2] == 'disconnected']
        self.assertEqual(len(disc), 1)
        self.assertEqual(disc[0][1], 312000)


class TestPingQuality(unittest.TestCase):
    """Per-repeater ping-loss %: self-calibrated cadence, decaying, cadence-agnostic."""

    def _server(self):
        srv = hblink.HBSYSTEM('SERVER-1', CFG, None)
        srv.transport = MockDatagramTransport()
        return srv

    def _feed_pings(self, srv, pid, times):
        # Drive _note_ping with an explicit sequence of arrival timestamps.
        prev = times[0]
        srv._note_ping(pid, times[0], 0)              # first ping, seeds
        for t in times[1:]:
            srv._note_ping(pid, t, prev)
            prev = t

    def test_clean_pings_zero_loss(self):
        srv = self._server()
        pid = bytes_4(312000)
        self._feed_pings(srv, pid, [i * 10.0 for i in range(30)])   # every 10s, no gaps
        self.assertEqual(srv._ping_loss_pct(pid, 300.0), 0)

    def test_slower_cadence_is_not_loss(self):
        srv = self._server()
        pid = bytes_4(312000)
        # Pings every 30s (much slower than GLOBAL PING_TIME) must NOT read as loss.
        self._feed_pings(srv, pid, [i * 30.0 for i in range(20)])
        self.assertEqual(srv._ping_loss_pct(pid, 600.0), 0)

    def test_dropped_pings_register_loss(self):
        srv = self._server()
        pid = bytes_4(312000)
        # Establish a 10s cadence, then drop roughly 1 of every 3 pings.
        times = [i * 10.0 for i in range(10)]         # clean baseline
        t = times[-1]
        for k in range(20):
            t += 30.0 if k % 3 == 0 else 10.0         # every 3rd interval misses 2 pings
            times.append(t)
        self._feed_pings(srv, pid, times)
        loss = srv._ping_loss_pct(pid, times[-1])
        self.assertGreater(loss, 10)                  # clearly lossy
        self.assertLess(loss, 60)

    def test_loss_ages_out_of_window(self):
        srv = self._server()
        pid = bytes_4(312000)
        win = CFG['GLOBAL']['PING_LOSS_WINDOW'] * 60   # seconds
        # A burst of loss early, then a long stretch of clean pings that fills the
        # whole window -- the old loss must fall out and the figure return to 0.
        times = [0.0, 10.0, 40.0]                     # 40 = a 30s gap = lost pings
        t = times[-1]
        while t < times[0] + win + 120:               # clean 10s pings well past the window
            t += 10.0
            times.append(t)
        self._feed_pings(srv, pid, times)
        self.assertEqual(srv._ping_loss_pct(pid, times[-1]), 0)


class MockWriteTransport:
    """Mimics enough of an asyncio Transport for ReportServer._send_json."""
    def __init__(self, buffer_size=0):
        self._buffer_size = buffer_size

    def get_write_buffer_size(self):
        return self._buffer_size


class MockAsyncWriter:
    """Mimics enough of an asyncio StreamWriter for ReportServer."""
    def __init__(self, peername=('127.0.0.1', 51000), closing=False, buffer_size=0):
        self.written = bytearray()
        self._peername = peername
        self._closing = closing
        self.closed = False
        self.transport = MockWriteTransport(buffer_size)

    def write(self, data):
        self.written += data

    def is_closing(self):
        return self._closing

    def close(self):
        self.closed = True
        self._closing = True

    def get_extra_info(self, key):
        # 'socket' returns None so keepalive tuning is skipped in tests.
        return {'peername': self._peername, 'socket': None}.get(key)


class MockAsyncReader:
    """Async reader that yields the given chunks then EOF (b'')."""
    def __init__(self, chunks=()):
        self._chunks = list(chunks)

    async def read(self, _n):
        return self._chunks.pop(0) if self._chunks else b''


class TestReportingNDJSON(unittest.TestCase):
    # Exercises the asyncio ReportServer: the client ACL, and the write path's
    # shedding of dead/unresponsive clients (is_closing() or an over-limit write
    # buffer). Uses mock reader/writer objects -- no real sockets or config.
    def _server(self):
        return hblink.ReportServer({})

    def test_send_json_emits_one_terminated_line(self):
        srv = self._server()
        w = MockAsyncWriter()
        srv.clients.append(w)
        srv._send_json({'type': 'ping', 'n': 1})
        data = bytes(w.written)
        self.assertTrue(data.endswith(b'\n'))
        self.assertEqual(json.loads(data), {'type': 'ping', 'n': 1})
        self.assertEqual(data.count(b'\n'), 1)

    def test_send_json_sheds_closing_writer(self):
        srv = self._server()
        alive, dead = MockAsyncWriter(), MockAsyncWriter(closing=True)
        srv.clients.extend([alive, dead])
        srv._send_json({'type': 'ping'})
        self.assertIn(alive, srv.clients)
        self.assertNotIn(dead, srv.clients)      # is_closing() -> shed
        self.assertEqual(bytes(dead.written), b'')

    def test_send_json_sheds_unresponsive_writer(self):
        srv = self._server()
        stuck = MockAsyncWriter(buffer_size=(2 << 20))   # over the 1 MiB limit
        srv.clients.append(stuck)
        srv._send_json({'type': 'ping'})
        self.assertNotIn(stuck, srv.clients)     # not reading -> shed and closed
        self.assertTrue(stuck.closed)

    def test_disallowed_client_is_closed(self):
        srv = self._server()
        w = MockAsyncWriter(peername=('10.9.9.9', 1234))   # not in allowed list
        asyncio.run(srv._client_connected(MockAsyncReader(), w, allowed=['127.0.0.1']))
        self.assertTrue(w.closed)
        self.assertNotIn(w, srv.clients)

    def test_json_systems_omits_secrets(self):
        view = hblink.json_systems(CFG['SYSTEMS'])
        for sysview in view.values():
            self.assertNotIn('PASSPHRASE', sysview)
            self.assertNotIn('SUB_ACL', sysview)


class TestBridgeStreamEvents(unittest.TestCase):
    # BridgeReportServer.send_bridge_event() turns the CSV strings the routing
    # code emits into JSON stream events. Capture _send_json to inspect them.
    def _server(self):
        import bridge
        srv = bridge.BridgeReportServer({})
        captured = []
        srv._send_json = captured.append
        return srv, captured

    def test_start_csv_becomes_json(self):
        srv, cap = self._server()
        srv.send_bridge_event(b'GROUP VOICE,START,RX,SERVER-1,123,312000,3120001,1,3100')
        self.assertEqual(cap[0], {
            'type': 'stream', 'call_type': 'GROUP VOICE', 'action': 'START',
            'trx': 'RX', 'system': 'SERVER-1', 'stream_id': 123, 'peer': 312000,
            'src': 3120001, 'slot': 1, 'dst': 3100})

    def test_end_csv_includes_duration(self):
        srv, cap = self._server()
        srv.send_bridge_event('GROUP VOICE,END,TX,SERVER-1,123,312000,3120001,2,3100,4.20')
        self.assertEqual(cap[0]['action'], 'END')
        self.assertEqual(cap[0]['slot'], 2)
        self.assertEqual(cap[0]['duration'], 4.20)


class TestTransportSend(unittest.TestCase):
    def test_openbridge_send_uses_sendto_to_target(self):
        obp = hblink.OPENBRIDGE('OBP-1', CFG, None)
        obp.transport = MockDatagramTransport()
        # 20-byte DMRD header + payload; send_system rewrites peer id and appends HMAC
        pkt = DMRD + b'\x00' + b'\x11\x22\x33' + b'\x00\x00\x09' + b'\xaa\xbb\xcc\xdd' + b'\x00' + b'\x00\x00\x00\x01' + b'\x00' * 33
        obp.send_system(pkt)
        self.assertEqual(len(obp.transport.sent), 1)
        data, addr = obp.transport.sent[0]
        self.assertEqual(addr, (CFG['SYSTEMS']['OBP-1']['TARGET_IP'], CFG['SYSTEMS']['OBP-1']['TARGET_PORT']))
        # NETWORK_ID was written into the peer-id slot [11:15]
        self.assertEqual(data[11:15], CFG['SYSTEMS']['OBP-1']['NETWORK_ID'])

    def test_outbound_send_server_uses_sendto(self):
        peer = hblink.HBSYSTEM('REPEATER-1', CFG, None)
        peer.transport = MockDatagramTransport()
        pkt = DMRD + b'\x00' + b'\x11\x22\x33' + b'\x00\x00\x09' + b'\xaa\xbb\xcc\xdd' + b'\x00' + b'\x00\x00\x00\x01' + b'\x00' * 33
        # A logged-in outbound system sends call traffic to its server via sendto.
        peer._stats['CONNECTION'] = 'YES'
        peer.send_server(pkt)
        self.assertEqual(len(peer.transport.sent), 1)
        _, addr = peer.transport.sent[0]
        self.assertEqual(addr, CFG['SYSTEMS']['REPEATER-1']['SERVER_SOCKADDR'])

    def test_outbound_send_server_drops_dmrd_when_not_connected(self):
        peer = hblink.HBSYSTEM('REPEATER-1', CFG, None)
        peer.transport = MockDatagramTransport()
        peer._stats['CONNECTION'] = 'NO'
        pkt = DMRD + b'\x00' + b'\x11\x22\x33' + b'\x00\x00\x09' + b'\xaa\xbb\xcc\xdd' + b'\x00' + b'\x00\x00\x00\x01' + b'\x00' * 33
        peer.send_server(pkt)
        self.assertEqual(peer.transport.sent, [])          # call traffic dropped
        # but login/keepalive packets still go out
        peer.send_server(RPTL + b'\x00\x00\x00\x01')
        self.assertEqual(len(peer.transport.sent), 1)


class TestSupersessionEnd(unittest.TestCase):
    """routerHBP._end_slot_stream emits a matching END for a slot's un-terminated
    stream (superseded by a new stream, or timed out) so consumers don't leak it."""

    def _router(self):
        import bridge
        cfg = {'REPORTS': {'REPORT': True}, 'SYSTEMS': CFG['SYSTEMS']}
        bridge.CONFIG = cfg
        captured = []

        class Rep:
            def send_bridge_event(self, data):
                captured.append(data.decode() if isinstance(data, (bytes, bytearray)) else data)

        return bridge.routerHBP('SERVER-1', cfg, Rep()), captured

    def _activate(self, st, ct='GROUP VOICE'):
        st['RX_TYPE'] = HBPF_SLT_VHEAD          # an active (non-terminated) stream
        st['RX_TERMINATED'] = False
        st['RX_CT'] = ct
        st['RX_STREAM_ID'] = bytes_4(12345)
        st['RX_PEER'] = bytes_4(312100)
        st['RX_RFS'] = bytes_3(3120001)
        st['RX_TGID'] = bytes_3(3100)
        st['RX_START'] = 100.0
        st['RX_TIME'] = 104.2

    def test_emits_end_and_marks_terminated(self):
        r, cap = self._router()
        self._activate(r.STATUS[1])
        r._end_slot_stream(1)
        self.assertEqual(cap, ['GROUP VOICE,END,RX,SERVER-1,12345,312100,3120001,1,3100,4.20'])
        self.assertTrue(r.STATUS[1]['RX_TERMINATED'])
        # idempotent: a second call (already terminated) emits nothing more
        r._end_slot_stream(1)
        self.assertEqual(len(cap), 1)

    def test_unit_call_type_preserved(self):
        r, cap = self._router()
        self._activate(r.STATUS[2], ct='UNIT VOICE')
        r._end_slot_stream(2)
        self.assertTrue(cap[0].startswith('UNIT VOICE,END,RX,SERVER-1,12345,'))

    def test_no_event_for_idle_slot(self):
        r, cap = self._router()
        r._end_slot_stream(1)                   # slot starts terminated (RX_TERMINATED == True)
        self.assertEqual(cap, [])


if __name__ == '__main__':
    unittest.main()
