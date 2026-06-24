#!/usr/bin/env python
#
# Regression tests for the asyncio migration's protocol mechanics -- the parts
# the routing harness does not exercise: the master login handshake state
# machine, the reporting server's netstring framing, and that the UDP send paths
# use transport.sendto(). Driven against mock transports (no real sockets).
#
# Run from the repo root:   venv/bin/python -m unittest discover -s tests

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
from dmr_utils3.utils import bytes_4
from const import RPTL, RPTK, RPTC, RPTACK, MSTNAK, DMRD

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


class TestMasterHandshake(unittest.TestCase):
    """Drive a MASTER through RPTL -> RPTK -> RPTC over datagram_received and
    assert the state machine and responses, exercising the asyncio rename
    (datagram_received) and transport.sendto path."""

    def setUp(self):
        self.master = hblink.HBSYSTEM('MASTER-1', CFG, None)
        self.master.transport = MockDatagramTransport()   # bypass connection_made's task
        self.peer_id = bytes_4(312000)
        self.addr = ('127.0.0.1', 50000)
        self.passphrase = CFG['SYSTEMS']['MASTER-1']['PASSPHRASE']

    def test_full_login_exchange(self):
        m = self.master
        # 1) Login request -> challenge
        m.datagram_received(RPTL + self.peer_id, self.addr)
        self.assertIn(self.peer_id, m._peers)
        self.assertEqual(m._peers[self.peer_id]['CONNECTION'], 'CHALLENGE_SENT')
        self.assertEqual(m.transport.sent[-1][0][:len(RPTACK)], RPTACK)

        # 2) Answer the challenge with the correct hash -> WAITING_CONFIG
        salt = bytes_4(m._peers[self.peer_id]['SALT'])
        calc = bhex(sha256(salt + self.passphrase).hexdigest())
        m.datagram_received(RPTK + self.peer_id + calc, self.addr)
        self.assertEqual(m._peers[self.peer_id]['CONNECTION'], 'WAITING_CONFIG')
        self.assertEqual(m.transport.sent[-1][0], RPTACK + self.peer_id)

        # 3) Send configuration -> connected
        rptc = RPTC + self.peer_id + b'TEST    ' + b'\x00' * 300
        m.datagram_received(rptc, self.addr)
        self.assertEqual(m._peers[self.peer_id]['CONNECTION'], 'YES')
        self.assertEqual(m.transport.sent[-1][0], RPTACK + self.peer_id)

    def test_wrong_passphrase_is_rejected(self):
        m = self.master
        m.datagram_received(RPTL + self.peer_id, self.addr)
        bad = bhex(sha256(b'\x00\x00\x00\x00' + b'wrong').hexdigest())
        m.datagram_received(RPTK + self.peer_id + bad, self.addr)
        # Peer removed and a NAK sent
        self.assertNotIn(self.peer_id, m._peers)
        self.assertEqual(m.transport.sent[-1][0][:6], MSTNAK)


class TestReportingNDJSON(unittest.TestCase):
    def _client(self, peer=('127.0.0.1', 51000)):
        factory = hblink.reportFactory(CFG)
        proto = factory()                      # factory is callable -> report instance
        proto.connection_made(MockStreamTransport(peer))
        return factory, proto

    def test_disallowed_client_is_closed(self):
        factory = hblink.reportFactory(CFG)
        proto = factory()
        t = MockStreamTransport(('10.9.9.9', 1234))   # not in REPORT_CLIENTS
        proto.connection_made(t)
        self.assertTrue(t.closed)
        self.assertNotIn(proto, factory.clients)

    def test_connect_pushes_config_snapshot(self):
        _, proto = self._client()
        first = bytes(proto.transport.written).split(b'\n')[0]
        evt = json.loads(first)
        self.assertEqual(evt['type'], 'config')
        self.assertIn('MASTER-1', evt['systems'])
        self.assertEqual(evt['systems']['OBP-1']['MODE'], 'OPENBRIDGE')

    def test_send_clients_emits_one_json_line(self):
        factory, proto = self._client()
        proto.transport.written.clear()
        factory.send_clients({'type': 'ping', 'n': 1})
        data = bytes(proto.transport.written)
        self.assertTrue(data.endswith(b'\n'))
        self.assertEqual(json.loads(data), {'type': 'ping', 'n': 1})

    def test_json_systems_omits_secrets(self):
        view = hblink.json_systems(CFG['SYSTEMS'])
        for sysview in view.values():
            self.assertNotIn('PASSPHRASE', sysview)
            self.assertNotIn('SUB_ACL', sysview)


class TestBridgeStreamEvents(unittest.TestCase):
    def _factory(self):
        import bridge
        f = bridge.bridgeReportFactory(CFG)
        captured = []
        f.send_clients = captured.append
        return f, captured

    def test_start_csv_becomes_json(self):
        f, cap = self._factory()
        f.send_bridgeEvent(b'GROUP VOICE,START,RX,MASTER-1,123,312000,3120001,1,3100')
        self.assertEqual(cap[0], {
            'type': 'stream', 'call_type': 'GROUP VOICE', 'action': 'START',
            'trx': 'RX', 'system': 'MASTER-1', 'stream_id': 123, 'peer': 312000,
            'src': 3120001, 'slot': 1, 'dst': 3100})

    def test_end_csv_includes_duration(self):
        f, cap = self._factory()
        f.send_bridgeEvent('GROUP VOICE,END,TX,MASTER-1,123,312000,3120001,2,3100,4.20')
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

    def test_peer_send_master_uses_sendto(self):
        peer = hblink.HBSYSTEM('REPEATER-1', CFG, None)
        peer.transport = MockDatagramTransport()
        pkt = DMRD + b'\x00' + b'\x11\x22\x33' + b'\x00\x00\x09' + b'\xaa\xbb\xcc\xdd' + b'\x00' + b'\x00\x00\x00\x01' + b'\x00' * 33
        peer.send_master(pkt)
        self.assertEqual(len(peer.transport.sent), 1)
        _, addr = peer.transport.sent[0]
        self.assertEqual(addr, CFG['SYSTEMS']['REPEATER-1']['MASTER_SOCKADDR'])


if __name__ == '__main__':
    unittest.main()
