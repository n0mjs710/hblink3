#!/usr/bin/env python
#
# Regression tests for call-END emission on HBP (server/outbound) systems.
#
# Background: a slot's "stream has ended" state used to be inferred from
# RX_TYPE/TX_TYPE == HBPF_SLT_VTERM. But those fields hold the last frame's
# _dtype_vseq, which for voice bursts cycles 0..5 and so collides with
# HBPF_SLT_VTERM (== 2). Any stream whose last received burst was vseq 2 looked
# "already terminated", so its END was never emitted -- not on a clean
# terminator, not by the stream-timeout trimmer, not by new-stream supersession.
# The fix replaces that inference with explicit RX_TERMINATED / TX_TERMINATED
# flags. These tests pin the behavior, with the vseq-2 case as the key guard.

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
    """Captures the newline-delimited bridge events a system would emit."""
    def __init__(self):
        self.events = []

    def send_bridge_event(self, data):
        self.events.append(data.decode())


def _world():
    """A two-HBP-system world with reporting enabled and captured."""
    w = harness.World({'B': [_member('SERVER-1', 1, 3100), _member('REPEATER-1', 1, 3100)]})
    w.CONFIG['REPORTS']['REPORT'] = True
    rpt = _Report()
    for sysobj in bridge.systems.values():
        sysobj._report = rpt
    return w, rpt


def _ends(rpt, ct='GROUP VOICE', direction='RX'):
    prefix = '{},END,{},'.format(ct, direction)
    return [e for e in rpt.events if e.startswith(prefix)]


# Common call parameters for SERVER-1 sourcing on TS1/TGID3100.
_KW = dict(rf_src=bytes_3(312000), dst=bytes_3(3100), peer=bytes_4(312000), slot=1)


class TestGroupEnd(unittest.TestCase):

    def test_clean_call_emits_end(self):
        """A properly terminated call (ends on a full superframe) emits an END."""
        w, rpt = _world()
        sid = b'\x00\x00\x00\x01'
        w.feed_group_header('SERVER-1', stream_id=sid, **_KW)
        for burst in range(6):            # full superframe, last burst vseq 5
            w.feed_group_burst('SERVER-1', burst, stream_id=sid, **_KW)
        w.feed_group_terminator('SERVER-1', stream_id=sid, **_KW)
        self.assertEqual(len(_ends(rpt)), 1)

    def test_lost_tail_vseq2_times_out_with_end(self):
        """The regression: a dropped-RF call whose last burst is vseq 2 (== VTERM)
        must still get an END from the stream-timeout trimmer."""
        w, rpt = _world()
        sid = b'\x00\x00\x00\x02'
        w.feed_group_header('SERVER-1', stream_id=sid, **_KW)
        for burst in (0, 1, 2):           # lost tail, last burst vseq 2, no VTERM
            w.feed_group_burst('SERVER-1', burst, stream_id=sid, **_KW)
        self.assertEqual(_ends(rpt), [])              # nothing yet -- call still "open"
        w.clock.tick(bridge.STREAM_TIMEOUT + 1)       # past the no-audio timeout
        bridge.stream_trimmer_loop()
        self.assertEqual(len(_ends(rpt)), 1)

    def test_lost_tail_vseq5_times_out_with_end(self):
        """Control: a lost tail ending on vseq 5 also times out with an END."""
        w, rpt = _world()
        sid = b'\x00\x00\x00\x03'
        w.feed_group_header('SERVER-1', stream_id=sid, **_KW)
        for burst in (0, 1, 2, 3, 4, 5):
            w.feed_group_burst('SERVER-1', burst, stream_id=sid, **_KW)
        w.clock.tick(bridge.STREAM_TIMEOUT + 1)
        bridge.stream_trimmer_loop()
        self.assertEqual(len(_ends(rpt)), 1)

    def test_supersession_emits_end_for_stuck_vseq2_stream(self):
        """A new stream taking the slot while a vseq-2 lost tail is stuck must
        synthesize the prior call's END before starting the new one."""
        w, rpt = _world()
        old = b'\x00\x00\x00\x04'
        w.feed_group_header('SERVER-1', stream_id=old, **_KW)
        for burst in (0, 1, 2):           # stuck lost tail, last burst vseq 2
            w.feed_group_burst('SERVER-1', burst, stream_id=old, **_KW)
        self.assertEqual(_ends(rpt), [])
        # A different subscriber keys up on the same slot -> supersession.
        new = b'\x00\x00\x00\x05'
        kw2 = dict(_KW, rf_src=bytes_3(312999))
        w.clock.tick(bridge.STREAM_TO + 0.1)          # past contention window
        w.feed_group_header('SERVER-1', stream_id=new, **kw2)
        self.assertEqual(len(_ends(rpt)), 1)          # the stuck call was ended

    def test_no_duplicate_end_on_clean_call(self):
        """A cleanly terminated call must not also get a timeout/supersession END."""
        w, rpt = _world()
        sid = b'\x00\x00\x00\x06'
        w.feed_group_header('SERVER-1', stream_id=sid, **_KW)
        for burst in range(6):
            w.feed_group_burst('SERVER-1', burst, stream_id=sid, **_KW)
        w.feed_group_terminator('SERVER-1', stream_id=sid, **_KW)
        w.clock.tick(bridge.STREAM_TIMEOUT + 1)
        bridge.stream_trimmer_loop()
        self.assertEqual(len(_ends(rpt)), 1)          # exactly one, not two


class TestCollisionLogging(unittest.TestCase):

    def test_collision_logged_once_per_stream(self):
        """A contending stream is reported once, not once per colliding frame."""
        w, _ = _world()
        owner = b'\x00\x00\x00\x10'
        w.feed_group_header('SERVER-1', stream_id=owner, **_KW)
        w.feed_group_burst('SERVER-1', 1, stream_id=owner, **_KW)
        collider = dict(_KW, rf_src=bytes_3(312999))   # different subscriber
        cid = b'\x00\x00\x00\x11'
        with self.assertLogs('bridge', level='WARNING') as cm:
            w.feed_group_header('SERVER-1', stream_id=cid, **collider)
            w.feed_group_burst('SERVER-1', 1, stream_id=cid, **collider)
            w.feed_group_burst('SERVER-1', 2, stream_id=cid, **collider)
        hits = [m for m in cm.output if 'collided with existing call' in m]
        self.assertEqual(len(hits), 1)


if __name__ == '__main__':
    unittest.main()
