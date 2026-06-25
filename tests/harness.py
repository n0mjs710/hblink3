#!/usr/bin/env python
#
# Frame-replay test harness for the bridge routing core.
#
# It stands up the REAL routerHBP / routerOBP instances against a small config,
# drives synthetic DMRD frames through their routing entry point (dmrd_received),
# and captures the packets each system would emit (send_system). A controllable
# clock makes contention/hangtime deterministic.
#
# Purpose: characterize the current routing behavior and, during the planned
# unification, run identical frame sequences through the old and new code and
# assert byte-identical emissions -- so OBP multi-stream behavior and HBP slot
# locking can be proven preserved without a live over-the-air network.
#
# This module is scaffolding, not a test itself; see test_harness_smoke.py.

import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bridge
import config

# Keep the routers quiet during replay (they log heavily at INFO). Raise only
# their own logger levels rather than logging.disable() -- a global disable would
# also defeat assertLogs() checks in other test modules. setLevel is fine because
# assertLogs temporarily lowers the level within its own context.
for _name in ('bridge', 'hblink'):
    logging.getLogger(_name).setLevel(logging.CRITICAL)

# Self-contained test topology: two HBP masters, one HBP peer, two OpenBridges.
TEST_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'harness.cfg')

# Bit-field constants for assembling the DMRD flags byte (see datagramReceived).
_FT_VOICE = 0       # HBPF_VOICE
_FT_DATA_SYNC = 2   # HBPF_DATA_SYNC
_VHEAD = 1          # HBPF_SLT_VHEAD
_VTERM = 2          # HBPF_SLT_VTERM


class Clock:
    """A manually advanced stand-in for time.time()."""
    def __init__(self, start=1_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t += seconds
        return self.t


def mk_dmrd(seq, rf_src, dst, peer, slot, call_type, frame_type, dtype_vseq,
            stream_id, dmrpkt=None):
    """Assemble a 55-byte DMRD packet (33-byte payload + 2 BER/RSSI bytes)."""
    if dmrpkt is None:
        dmrpkt = bytes((i * 5 + 1) & 0xFF for i in range(33))
    assert len(rf_src) == 3 and len(dst) == 3 and len(peer) == 4 and len(stream_id) == 4
    assert len(dmrpkt) == 33
    bits = 0
    if slot == 2:
        bits |= 0x80
    if call_type == 'unit':
        bits |= 0x40
    bits |= (frame_type & 0x3) << 4
    bits |= (dtype_vseq & 0xF)
    return b''.join([b'DMRD', bytes([seq]), rf_src, dst, peer,
                     bytes([bits]), stream_id, dmrpkt, b'\x00\x00'])


class World:
    """A small routing world: a set of systems joined to bridges, with a clock
    and an emission-capture log."""

    def __init__(self, bridges, unit_systems=None, extra_systems=None):
        # Fresh, isolated module state for the bridge module.
        self.clock = Clock()
        bridge.time = self.clock  # monkeypatch the clock the routers read

        self.CONFIG = config.build_config(TEST_CFG)
        self.CONFIG['REPORTS']['REPORT'] = False
        bridge.CONFIG = self.CONFIG

        # Aliases used only for logging; empty is fine (get_alias returns the id).
        bridge.subscriber_ids = {}
        bridge.peer_ids = {}
        bridge.talkgroup_ids = {}

        bridge.UNIT_MAP = {}
        bridge.UNIT = list(unit_systems or [])

        # Build the BRIDGES structure and indexes from the supplied spec.
        bridge.BRIDGES = bridge.make_bridges(bridges)
        bridge.BRIDGE_SRC_INDEX, bridge.BRIDGE_BY_SYSTEM = bridge.index_bridges(bridge.BRIDGES)

        # Instantiate the real router objects for every system referenced.
        self.captures = []
        bridge.systems.clear()
        names = set(unit_systems or []) | set(extra_systems or [])
        for b in bridges:
            for member in bridges[b]:
                names.add(member['SYSTEM'])
        for name in names:
            mode = self.CONFIG['SYSTEMS'][name]['MODE']
            if mode == 'OPENBRIDGE':
                obj = bridge.routerOBP(name, self.CONFIG, None)
            else:
                obj = bridge.routerHBP(name, self.CONFIG, None)
                # The router only forwards to a PEER once it has logged into its
                # upstream master; the harness models connected, participating
                # systems, so mark peers as connected.
                if mode == 'PEER':
                    obj._stats['CONNECTION'] = 'YES'
            # Capture what the routing core hands to each system to transmit,
            # instead of writing to a real UDP transport.
            obj.send_system = self._make_capture(name)
            bridge.systems[name] = obj

        # The STATUS tables were initialized with the current clock value. Advance
        # well past any hangtime/stream window so the first real frame isn't seen
        # as colliding with construction-time state (matches "started, then idle").
        self.clock.tick(3600.0)

    def _make_capture(self, name):
        def _capture(_packet):
            self.captures.append((name, bytes(_packet)))
        return _capture

    def feed(self, src, *, rf_src, dst, peer, slot, stream_id, call_type,
             frame_type, dtype_vseq, seq=0):
        """Drive one DMRD frame into src's routing entry point."""
        data = mk_dmrd(seq, rf_src, dst, peer, slot, call_type,
                       frame_type, dtype_vseq, stream_id)
        bridge.systems[src].dmrd_received(
            peer, rf_src, dst, seq, slot, call_type,
            frame_type, dtype_vseq, stream_id, data)

    def feed_group_header(self, src, **kw):
        self.feed(src, call_type='group', frame_type=_FT_DATA_SYNC,
                  dtype_vseq=_VHEAD, **kw)

    def feed_group_terminator(self, src, **kw):
        self.feed(src, call_type='group', frame_type=_FT_DATA_SYNC,
                  dtype_vseq=_VTERM, **kw)

    def feed_group_burst(self, src, burst, **kw):
        self.feed(src, call_type='group', frame_type=_FT_VOICE,
                  dtype_vseq=burst, **kw)

    def feed_unit_header(self, src, **kw):
        self.feed(src, call_type='unit', frame_type=_FT_DATA_SYNC,
                  dtype_vseq=_VHEAD, **kw)

    def feed_unit_terminator(self, src, **kw):
        self.feed(src, call_type='unit', frame_type=_FT_DATA_SYNC,
                  dtype_vseq=_VTERM, **kw)

    def feed_unit_burst(self, src, burst, **kw):
        self.feed(src, call_type='unit', frame_type=_FT_VOICE,
                  dtype_vseq=burst, **kw)

    def seed_unit_map(self, subscriber, system):
        """Register that `subscriber` (3 bytes) was last heard on `system`, so a
        unit call to it routes there."""
        bridge.UNIT_MAP[subscriber] = (system, self.clock())

    def drain(self):
        """Return and clear captured emissions as a list of (system, bytes)."""
        out = self.captures[:]
        self.captures.clear()
        return out

    def emitted_to(self, system):
        return [pkt for (name, pkt) in self.captures if name == system]
