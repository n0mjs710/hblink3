#!/usr/bin/env python
#
# Received-signal reporting on HBP RX streams: RSSI (DMRD byte 54, -dBm, 0 = no
# reading) and BER (byte 53, corrected AMBE bit errors per voice frame out of 141)
# carried as rssi_dbm / ber_pct on RX stream_start / stream_end and on rate-limited
# stream_update events. A field is omitted when there is no reading, and BER is
# omitted when no errors were counted.

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from dmr_utils3.utils import bytes_3, bytes_4
import bridge
import harness


def _member(system, ts, tgid):
    return {'SYSTEM': system, 'TS': ts, 'TGID': tgid, 'ACTIVE': True,
            'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []}


class _Report:
    def __init__(self):
        self.events = []    # (csv, extra)
        self.updates = []   # fields dicts

    def send_bridge_event(self, data, extra=None):
        self.events.append((data.decode(), extra or {}))

    def send_stream_update(self, system, stream_id, slot, fields):
        self.updates.append(dict(fields, system=system, slot=slot))


def _world():
    w = harness.World({'B': [_member('SERVER-1', 1, 3100), _member('REPEATER-1', 1, 3100)]},
                      unit_systems=['SERVER-1', 'REPEATER-1'])
    w.CONFIG['REPORTS']['REPORT'] = True
    rpt = _Report()
    for sysobj in bridge.systems.values():
        sysobj._report = rpt
    return w, rpt


def _rx(rpt, action, ct='GROUP VOICE'):
    prefix = '{},{},RX,'.format(ct, action)
    return [extra for csv, extra in rpt.events if csv.startswith(prefix)]


_KW = dict(rf_src=bytes_3(312000), dst=bytes_3(3100), peer=bytes_4(312000), slot=1)


class TestStartEnd(unittest.TestCase):

    def test_mmdvm_style_readings(self):
        """Fresh RSSI on every frame, no bit errors: START has the header reading,
        END the last one, and no BER anywhere."""
        w, rpt = _world()
        sid = b'\x00\x00\x00\x01'
        w.feed_group_header('SERVER-1', stream_id=sid, rssi=65, **_KW)
        for burst in range(6):
            w.feed_group_burst('SERVER-1', burst, stream_id=sid, rssi=66, **_KW)
        w.feed_group_terminator('SERVER-1', stream_id=sid, rssi=67, **_KW)
        self.assertEqual(_rx(rpt, 'START'), [{'rssi_dbm': -65}])
        self.assertEqual(_rx(rpt, 'END'), [{'rssi_dbm': -67}])
        self.assertEqual(rpt.updates, [])

    def test_ipsc2hbp_style_readings(self):
        """Readings only on header and terminator (voice bursts carry 0): the held
        value is the last real reading."""
        w, rpt = _world()
        sid = b'\x00\x00\x00\x02'
        w.feed_group_header('SERVER-1', stream_id=sid, rssi=65, **_KW)
        for burst in range(6):
            w.feed_group_burst('SERVER-1', burst, stream_id=sid, **_KW)
        w.feed_group_terminator('SERVER-1', stream_id=sid, rssi=63, **_KW)
        self.assertEqual(_rx(rpt, 'START'), [{'rssi_dbm': -65}])
        self.assertEqual(_rx(rpt, 'END'), [{'rssi_dbm': -63}])

    def test_no_reading_omits_fields(self):
        w, rpt = _world()
        sid = b'\x00\x00\x00\x03'
        w.feed_group_header('SERVER-1', stream_id=sid, **_KW)
        for burst in range(6):
            w.feed_group_burst('SERVER-1', burst, stream_id=sid, **_KW)
        w.feed_group_terminator('SERVER-1', stream_id=sid, **_KW)
        self.assertEqual(_rx(rpt, 'START'), [{}])
        self.assertEqual(_rx(rpt, 'END'), [{}])

    def test_end_ber_is_whole_call(self):
        """7 errors in each of 6 voice frames: 42 * 100 / (141 * 6) = 4.96 -> 5.0.
        The header and terminator are not voice frames and don't count."""
        w, rpt = _world()
        sid = b'\x00\x00\x00\x04'
        w.feed_group_header('SERVER-1', stream_id=sid, rssi=100, **_KW)
        for burst in range(6):
            w.feed_group_burst('SERVER-1', burst, stream_id=sid, ber=7, **_KW)
        w.feed_group_terminator('SERVER-1', stream_id=sid, **_KW)
        self.assertEqual(_rx(rpt, 'END'), [{'rssi_dbm': -100, 'ber_pct': 5.0}])

    def test_tiny_ber_rounds_to_nothing(self):
        """Below 0.05% rounds to 0.0, which is shown as no BER rather than 0.0%."""
        w, rpt = _world()
        sid = b'\x00\x00\x00\x05'
        w.feed_group_header('SERVER-1', stream_id=sid, **_KW)
        w.feed_group_burst('SERVER-1', 0, stream_id=sid, ber=1, **_KW)
        for i in range(29):
            w.feed_group_burst('SERVER-1', (i + 1) % 6, stream_id=sid, **_KW)
        w.feed_group_terminator('SERVER-1', stream_id=sid, **_KW)
        self.assertEqual(_rx(rpt, 'END'), [{}])        # 1 / (141 * 30) = 0.024%

    def test_timeout_end_carries_fields(self):
        w, rpt = _world()
        sid = b'\x00\x00\x00\x06'
        w.feed_group_header('SERVER-1', stream_id=sid, rssi=90, **_KW)
        for burst in range(3):
            w.feed_group_burst('SERVER-1', burst, stream_id=sid, ber=14, **_KW)
        w.clock.tick(bridge.STREAM_TIMEOUT + 1)
        bridge.stream_trimmer_loop()
        self.assertEqual(_rx(rpt, 'END'), [{'rssi_dbm': -90, 'ber_pct': 9.9}])

    def test_unit_call(self):
        w, rpt = _world()
        w.seed_unit_map(bytes_3(3120999), 'REPEATER-1')
        kw = dict(_KW, dst=bytes_3(3120999))
        sid = b'\x00\x00\x00\x07'
        w.feed_unit_header('SERVER-1', stream_id=sid, rssi=65, **kw)
        for burst in range(6):
            w.feed_unit_burst('SERVER-1', burst, stream_id=sid, ber=10, **kw)
        w.feed_unit_terminator('SERVER-1', stream_id=sid, rssi=63, **kw)
        self.assertEqual(_rx(rpt, 'START', 'UNIT VOICE'), [{'rssi_dbm': -65}])
        self.assertEqual(_rx(rpt, 'END', 'UNIT VOICE'), [{'rssi_dbm': -63, 'ber_pct': 7.1}])

    def test_tx_events_carry_no_fields(self):
        w, rpt = _world()
        sid = b'\x00\x00\x00\x08'
        w.feed_group_header('SERVER-1', stream_id=sid, rssi=65, **_KW)
        w.feed_group_terminator('SERVER-1', stream_id=sid, rssi=63, **_KW)
        tx = [extra for csv, extra in rpt.events if ',TX,' in csv]
        self.assertTrue(tx)
        self.assertTrue(all(extra == {} for extra in tx))


class TestStreamUpdate(unittest.TestCase):

    def _start(self, w, sid, rssi=70):
        w.feed_group_header('SERVER-1', stream_id=sid, rssi=rssi, **_KW)

    def test_update_only_after_interval(self):
        w, rpt = _world()
        sid = b'\x00\x00\x00\x10'
        self._start(w, sid)
        w.feed_group_burst('SERVER-1', 0, stream_id=sid, rssi=80, **_KW)
        self.assertEqual(rpt.updates, [])              # interval not yet elapsed
        w.clock.tick(bridge.SIG_UPDATE_INTERVAL)
        w.feed_group_burst('SERVER-1', 1, stream_id=sid, rssi=80, **_KW)
        self.assertEqual(rpt.updates, [{'rssi_dbm': -80, 'system': 'SERVER-1', 'slot': 1}])

    def test_unchanged_values_send_nothing(self):
        w, rpt = _world()
        sid = b'\x00\x00\x00\x11'
        self._start(w, sid)
        for _ in range(3):
            w.clock.tick(bridge.SIG_UPDATE_INTERVAL)
            w.feed_group_burst('SERVER-1', 1, stream_id=sid, rssi=70, **_KW)
        self.assertEqual(rpt.updates, [])

    def test_ber_is_per_interval_and_clears(self):
        """A burst of errors shows in the update for its interval; a clean interval
        after it sends an update without BER, which clears it on the dashboard."""
        w, rpt = _world()
        sid = b'\x00\x00\x00\x12'
        self._start(w, sid)
        w.feed_group_burst('SERVER-1', 1, stream_id=sid, ber=14, **_KW)
        w.clock.tick(bridge.SIG_UPDATE_INTERVAL)
        w.feed_group_burst('SERVER-1', 2, stream_id=sid, ber=0, **_KW)
        w.clock.tick(bridge.SIG_UPDATE_INTERVAL)
        w.feed_group_burst('SERVER-1', 3, stream_id=sid, **_KW)
        w.clock.tick(bridge.SIG_UPDATE_INTERVAL)
        w.feed_group_burst('SERVER-1', 4, stream_id=sid, **_KW)
        fields = [{k: v for k, v in u.items() if k in ('rssi_dbm', 'ber_pct')} for u in rpt.updates]
        self.assertEqual(fields, [{'rssi_dbm': -70, 'ber_pct': 5.0}, {'rssi_dbm': -70}])

    def test_no_update_on_terminator(self):
        w, rpt = _world()
        sid = b'\x00\x00\x00\x13'
        self._start(w, sid)
        w.clock.tick(bridge.SIG_UPDATE_INTERVAL)
        w.feed_group_terminator('SERVER-1', stream_id=sid, rssi=60, **_KW)
        self.assertEqual(rpt.updates, [])
        self.assertEqual(_rx(rpt, 'END'), [{'rssi_dbm': -60}])


class TestReportJson(unittest.TestCase):

    def _server(self):
        srv = bridge.BridgeReportServer({})
        captured = []
        srv._send_json = captured.append
        return srv, captured

    def test_extra_fields_merge_into_stream_event(self):
        srv, cap = self._server()
        srv.send_bridge_event(b'GROUP VOICE,START,RX,SERVER-1,123,312000,3120001,1,3100',
                              {'rssi_dbm': -65})
        self.assertEqual(cap[0]['type'], 'stream_start')
        self.assertEqual(cap[0]['rssi_dbm'], -65)
        self.assertNotIn('ber_pct', cap[0])

    def test_stream_update_shape(self):
        srv, cap = self._server()
        srv.send_stream_update('SERVER-1', 123, 2, {'rssi_dbm': -98, 'ber_pct': 3.4})
        self.assertEqual(cap[0], {'type': 'stream_update', 'system': 'SERVER-1', 'stream_id': 123,
                                  'slot': 2, 'trx': 'RX', 'rssi_dbm': -98, 'ber_pct': 3.4})


if __name__ == '__main__':
    unittest.main()
