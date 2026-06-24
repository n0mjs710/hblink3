#!/usr/bin/env python
#
# Smoke / characterization tests proving the frame-replay harness actually
# drives the real routing core and captures sensible emissions. These also
# document current behavior that the planned unification must preserve.
#
# Run from the repo root:   venv/bin/python -m unittest discover -s tests

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))   # repo root
sys.path.insert(0, _HERE)                     # tests/ dir, for 'import harness'

from dmr_utils3.utils import bytes_3, bytes_4
import harness


# The sample config defines MASTER-1 (HBP master), REPEATER-1 (HBP peer) and
# OBP-1 (OpenBridge). Build bridges over them on TS1 / TGID 3100.
def _bridge_spec(systems):
    return {
        'TEST': [
            {'SYSTEM': s, 'TS': 1, 'TGID': 3100, 'ACTIVE': True,
             'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []}
            for s in systems
        ]
    }


def _tgid_of(pkt):
    return pkt[8:11]


def _stream_of(pkt):
    return pkt[16:20]


class TestHarnessDrivesRoutingCore(unittest.TestCase):
    def test_group_call_hbp_to_hbp_is_forwarded(self):
        w = harness.World(_bridge_spec(['MASTER-1', 'REPEATER-1']))
        sid = b'\x00\x00\x00\x01'
        kw = dict(rf_src=bytes_3(312000), dst=bytes_3(3100),
                  peer=bytes_4(312000), slot=1, stream_id=sid)
        w.feed_group_header('MASTER-1', **kw)
        w.feed_group_terminator('MASTER-1', **kw)

        # The call should have been bridged to REPEATER-1 (and never echoed back
        # to the source system).
        to_repeater = w.emitted_to('REPEATER-1')
        self.assertTrue(to_repeater, 'call was not bridged to the peer system')
        self.assertEqual(w.emitted_to('MASTER-1'), [])
        # Destination TGID preserved on the bridged copy.
        self.assertEqual(_tgid_of(to_repeater[0]), bytes_3(3100))
        self.assertEqual(_stream_of(to_repeater[0]), sid)

    def test_group_call_hbp_to_obp_is_forwarded(self):
        w = harness.World(_bridge_spec(['MASTER-1', 'OBP-1']))
        sid = b'\x00\x00\x00\x02'
        kw = dict(rf_src=bytes_3(312000), dst=bytes_3(3100),
                  peer=bytes_4(312000), slot=1, stream_id=sid)
        w.feed_group_header('MASTER-1', **kw)
        w.feed_group_terminator('MASTER-1', **kw)
        self.assertTrue(w.emitted_to('OBP-1'), 'call was not bridged to OBP')

    def test_two_concurrent_obp_source_streams_both_forward(self):
        # THE case the naive "OBP-as-TS1" unification would break: two different
        # talkgroups arriving concurrently from an OBP trunk must BOTH bridge out.
        # TGID 3100 routes to MASTER-1, TGID 3200 routes to REPEATER-1, so the two
        # interleaved OBP streams have independent destinations and both forward.
        spec = {
            'B3100': [
                {'SYSTEM': 'OBP-1', 'TS': 1, 'TGID': 3100, 'ACTIVE': True,
                 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []},
                {'SYSTEM': 'MASTER-1', 'TS': 1, 'TGID': 3100, 'ACTIVE': True,
                 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []},
            ],
            'B3200': [
                {'SYSTEM': 'OBP-1', 'TS': 1, 'TGID': 3200, 'ACTIVE': True,
                 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []},
                {'SYSTEM': 'REPEATER-1', 'TS': 1, 'TGID': 3200, 'ACTIVE': True,
                 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []},
            ],
        }
        w = harness.World(spec)
        a = b'\x00\x00\x0A\x0A'
        b = b'\x00\x00\x0B\x0B'
        # Interleave the two streams' headers -- the OBP STATUS dict must hold both.
        w.feed_group_header('OBP-1', rf_src=bytes_3(1111), dst=bytes_3(3100),
                            peer=bytes_4(3129100), slot=1, stream_id=a)
        w.feed_group_header('OBP-1', rf_src=bytes_3(2222), dst=bytes_3(3200),
                            peer=bytes_4(3129100), slot=1, stream_id=b)

        src = bridge_status('OBP-1')
        self.assertIn(a, src, 'first concurrent OBP stream not tracked')
        self.assertIn(b, src, 'second concurrent OBP stream not tracked')
        # Each stream reached its own destination, concurrently.
        self.assertEqual([_stream_of(p) for p in w.emitted_to('MASTER-1')], [a])
        self.assertEqual([_stream_of(p) for p in w.emitted_to('REPEATER-1')], [b])

    def test_clock_is_controllable(self):
        w = harness.World(_bridge_spec(['MASTER-1', 'REPEATER-1']))
        t0 = w.clock()
        w.clock.tick(5.0)
        self.assertEqual(w.clock() - t0, 5.0)


def bridge_status(system):
    import bridge
    return bridge.systems[system].STATUS


if __name__ == '__main__':
    unittest.main()
