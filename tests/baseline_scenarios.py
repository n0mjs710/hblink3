#!/usr/bin/env python
#
# Deterministic routing scenarios used to build a byte-level golden baseline of
# the current routing core, so the planned forward_group extraction (and later
# the full unification) can be proven to emit identical packets.
#
# Each scenario builds its own World, replays a fixed frame sequence, and returns
# the captured emissions as a list of [system, hex]. Emissions are deterministic:
# packet bytes depend only on the input frames and the (fixed) BPTC-encoded LCs,
# never on wall-clock time.

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from dmr_utils3.utils import bytes_3, bytes_4
import harness


def _member(system, ts, tgid):
    return {'SYSTEM': system, 'TS': ts, 'TGID': tgid, 'ACTIVE': True,
            'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []}


def _full_call(w, src, *, rf_src, dst, peer, slot, stream_id):
    """A complete group call: voice header, bursts B-E, voice terminator."""
    w.feed_group_header(src, rf_src=rf_src, dst=dst, peer=peer, slot=slot, stream_id=stream_id)
    for burst in (1, 2, 3, 4):
        w.feed_group_burst(src, burst, rf_src=rf_src, dst=dst, peer=peer, slot=slot, stream_id=stream_id)
    w.feed_group_terminator(src, rf_src=rf_src, dst=dst, peer=peer, slot=slot, stream_id=stream_id)


def _full_unit_call(w, src, *, rf_src, dst, peer, slot, stream_id):
    w.feed_unit_header(src, rf_src=rf_src, dst=dst, peer=peer, slot=slot, stream_id=stream_id)
    for burst in (1, 2, 3, 4):
        w.feed_unit_burst(src, burst, rf_src=rf_src, dst=dst, peer=peer, slot=slot, stream_id=stream_id)
    w.feed_unit_terminator(src, rf_src=rf_src, dst=dst, peer=peer, slot=slot, stream_id=stream_id)


def _hex(captures):
    return [[name, pkt.hex()] for (name, pkt) in captures]


# ---- scenarios ----

def hbp_to_hbp_same_slot():
    w = harness.World({'B': [_member('SERVER-1', 1, 3100), _member('REPEATER-1', 1, 3100)]})
    _full_call(w, 'SERVER-1', rf_src=bytes_3(312000), dst=bytes_3(3100),
               peer=bytes_4(312000), slot=1, stream_id=b'\x00\x00\x00\x11')
    return _hex(w.captures)


def hbp_to_hbp_cross_slot():
    # Source on TS1, target subscribed on TS2 -> exercises the TS-bit flip.
    w = harness.World({'B': [_member('SERVER-1', 1, 3100), _member('REPEATER-1', 2, 3100)]})
    _full_call(w, 'SERVER-1', rf_src=bytes_3(312000), dst=bytes_3(3100),
               peer=bytes_4(312000), slot=1, stream_id=b'\x00\x00\x00\x12')
    return _hex(w.captures)


def hbp_to_hbp_tgid_rewrite():
    # Target subscribed on a different TGID -> destination TGID is rewritten.
    w = harness.World({'B': [_member('SERVER-1', 1, 3100), _member('REPEATER-1', 1, 31665)]})
    _full_call(w, 'SERVER-1', rf_src=bytes_3(312000), dst=bytes_3(3100),
               peer=bytes_4(312000), slot=1, stream_id=b'\x00\x00\x00\x13')
    return _hex(w.captures)


def hbp_to_obp():
    w = harness.World({'B': [_member('SERVER-1', 1, 3100), _member('OBP-1', 1, 3100)]})
    _full_call(w, 'SERVER-1', rf_src=bytes_3(312000), dst=bytes_3(3100),
               peer=bytes_4(312000), slot=1, stream_id=b'\x00\x00\x00\x14')
    return _hex(w.captures)


def obp_to_hbp():
    w = harness.World({'B': [_member('OBP-1', 1, 3100), _member('SERVER-1', 1, 3100)]})
    _full_call(w, 'OBP-1', rf_src=bytes_3(1234), dst=bytes_3(3100),
               peer=bytes_4(3129100), slot=1, stream_id=b'\x00\x00\x00\x15')
    return _hex(w.captures)


def obp_to_obp():
    w = harness.World({'B': [_member('OBP-1', 1, 3100), _member('OBP-2', 1, 3100)]})
    _full_call(w, 'OBP-1', rf_src=bytes_3(1234), dst=bytes_3(3100),
               peer=bytes_4(3129100), slot=1, stream_id=b'\x00\x00\x00\x16')
    return _hex(w.captures)


def hbp_multi_target():
    # One source bridged to an HBP outbound system, a second HBP server, and an OBP.
    w = harness.World({'B': [_member('SERVER-1', 1, 3100), _member('REPEATER-1', 1, 3100),
                             _member('SERVER-2', 1, 3100), _member('OBP-1', 1, 3100)]})
    _full_call(w, 'SERVER-1', rf_src=bytes_3(312000), dst=bytes_3(3100),
               peer=bytes_4(312000), slot=1, stream_id=b'\x00\x00\x00\x17')
    return _hex(w.captures)


def hbp_to_hbp_no_voice_header():
    # First frame is a burst (no voice header) -> the LC is synthesized from the
    # header fields rather than decoded, exercising the other src_lc path.
    w = harness.World({'B': [_member('SERVER-1', 1, 3100), _member('REPEATER-1', 1, 3100)]})
    kw = dict(rf_src=bytes_3(312000), dst=bytes_3(3100), peer=bytes_4(312000),
              slot=1, stream_id=b'\x00\x00\x00\x1a')
    w.feed_group_burst('SERVER-1', 1, **kw)
    w.feed_group_burst('SERVER-1', 2, **kw)
    w.feed_group_terminator('SERVER-1', **kw)
    return _hex(w.captures)


def obp_to_obp_no_voice_header():
    w = harness.World({'B': [_member('OBP-1', 1, 3100), _member('OBP-2', 1, 3100)]})
    kw = dict(rf_src=bytes_3(1234), dst=bytes_3(3100), peer=bytes_4(3129100),
              slot=1, stream_id=b'\x00\x00\x00\x1b')
    w.feed_group_burst('OBP-1', 1, **kw)
    w.feed_group_terminator('OBP-1', **kw)
    return _hex(w.captures)


def hbp_contention_second_stream_blocked():
    # Two streams on the same TGID/slot; the second must not produce a duplicate
    # forwarded call while the first is active.
    w = harness.World({'B': [_member('SERVER-1', 1, 3100), _member('REPEATER-1', 1, 3100)]})
    a = dict(rf_src=bytes_3(312000), dst=bytes_3(3100), peer=bytes_4(312000),
             slot=1, stream_id=b'\x00\x00\x00\x18')
    b = dict(rf_src=bytes_3(312999), dst=bytes_3(3100), peer=bytes_4(312000),
             slot=1, stream_id=b'\x00\x00\x00\x19')
    w.feed_group_header('SERVER-1', **a)
    w.feed_group_burst('SERVER-1', 1, **a)
    w.feed_group_header('SERVER-1', **b)   # contends with the active call
    w.feed_group_burst('SERVER-1', 1, **b)
    return _hex(w.captures)


def unit_hbp_to_hbp():
    w = harness.World({}, unit_systems=['SERVER-1'], extra_systems=['REPEATER-1'])
    w.seed_unit_map(bytes_3(2080), 'REPEATER-1')
    _full_unit_call(w, 'SERVER-1', rf_src=bytes_3(312000), dst=bytes_3(2080),
                    peer=bytes_4(312000), slot=1, stream_id=b'\x00\x00\x00\x21')
    return _hex(w.captures)


def unit_hbp_to_obp():
    w = harness.World({}, unit_systems=['SERVER-1'], extra_systems=['OBP-1'])
    w.seed_unit_map(bytes_3(2080), 'OBP-1')
    _full_unit_call(w, 'SERVER-1', rf_src=bytes_3(312000), dst=bytes_3(2080),
                    peer=bytes_4(312000), slot=1, stream_id=b'\x00\x00\x00\x22')
    return _hex(w.captures)


def unit_hbp_to_obp_ts2():
    # Unit on TS2 to an OBP (BOTH_SLOTS False) -> slot bit cleared on egress.
    w = harness.World({}, unit_systems=['SERVER-1'], extra_systems=['OBP-1'])
    w.seed_unit_map(bytes_3(2080), 'OBP-1')
    _full_unit_call(w, 'SERVER-1', rf_src=bytes_3(312000), dst=bytes_3(2080),
                    peer=bytes_4(312000), slot=2, stream_id=b'\x00\x00\x00\x23')
    return _hex(w.captures)


def unit_obp_to_hbp():
    w = harness.World({}, unit_systems=[], extra_systems=['OBP-1', 'SERVER-1'])
    w.seed_unit_map(bytes_3(2080), 'SERVER-1')
    _full_unit_call(w, 'OBP-1', rf_src=bytes_3(1234), dst=bytes_3(2080),
                    peer=bytes_4(3129100), slot=1, stream_id=b'\x00\x00\x00\x24')
    return _hex(w.captures)


def unit_obp_to_obp():
    w = harness.World({}, unit_systems=[], extra_systems=['OBP-1', 'OBP-2'])
    w.seed_unit_map(bytes_3(2080), 'OBP-2')
    _full_unit_call(w, 'OBP-1', rf_src=bytes_3(1234), dst=bytes_3(2080),
                    peer=bytes_4(3129100), slot=1, stream_id=b'\x00\x00\x00\x25')
    return _hex(w.captures)


SCENARIOS = {
    'hbp_to_hbp_same_slot': hbp_to_hbp_same_slot,
    'hbp_to_hbp_cross_slot': hbp_to_hbp_cross_slot,
    'hbp_to_hbp_tgid_rewrite': hbp_to_hbp_tgid_rewrite,
    'hbp_to_obp': hbp_to_obp,
    'obp_to_hbp': obp_to_hbp,
    'obp_to_obp': obp_to_obp,
    'hbp_to_hbp_no_voice_header': hbp_to_hbp_no_voice_header,
    'obp_to_obp_no_voice_header': obp_to_obp_no_voice_header,
    'hbp_multi_target': hbp_multi_target,
    'hbp_contention_second_stream_blocked': hbp_contention_second_stream_blocked,
    'unit_hbp_to_hbp': unit_hbp_to_hbp,
    'unit_hbp_to_obp': unit_hbp_to_obp,
    'unit_hbp_to_obp_ts2': unit_hbp_to_obp_ts2,
    'unit_obp_to_hbp': unit_obp_to_hbp,
    'unit_obp_to_obp': unit_obp_to_obp,
}


def run_all():
    return {name: fn() for name, fn in sorted(SCENARIOS.items())}


if __name__ == '__main__':
    import json
    print(json.dumps(run_all(), indent=2))
