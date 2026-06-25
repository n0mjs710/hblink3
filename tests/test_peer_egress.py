#!/usr/bin/env python
#
# Regression test: the router must not forward bridged call traffic to a PEER
# system that is not currently logged into its upstream master. Doing so would
# fire DMRD at a master that drops it (we're not registered) and falsely show as
# an active forward on the dashboard.

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

_KW = dict(rf_src=bytes_3(312000), dst=bytes_3(3100), peer=bytes_4(312000), slot=1)


class TestPeerEgress(unittest.TestCase):

    def _world(self):
        # MASTER-1 (source) bridged to REPEATER-1, which is MODE PEER in harness.cfg.
        return harness.World({'B': [_member('MASTER-1', 1, 3100),
                                    _member('REPEATER-1', 1, 3100)]})

    def test_connected_peer_receives_forward(self):
        w = self._world()                         # harness marks the peer connected
        self.assertEqual(bridge.systems['REPEATER-1']._stats['CONNECTION'], 'YES')
        w.feed_group_header('MASTER-1', stream_id=b'\x00\x00\x00\x40', **_KW)
        w.feed_group_burst('MASTER-1', 0, stream_id=b'\x00\x00\x00\x40', **_KW)
        self.assertTrue(w.emitted_to('REPEATER-1'))

    def test_disconnected_peer_target_skipped(self):
        w = self._world()
        bridge.systems['REPEATER-1']._stats['CONNECTION'] = 'NO'   # not logged in
        w.feed_group_header('MASTER-1', stream_id=b'\x00\x00\x00\x41', **_KW)
        for burst in (0, 1, 2):
            w.feed_group_burst('MASTER-1', burst, stream_id=b'\x00\x00\x00\x41', **_KW)
        self.assertEqual(w.emitted_to('REPEATER-1'), [])           # nothing forwarded


if __name__ == '__main__':
    unittest.main()
