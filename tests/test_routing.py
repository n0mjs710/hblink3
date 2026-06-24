#!/usr/bin/env python
#
# Regression tests for the bridge routing indexes (index_bridges) added in the
# 2026 cleanup. They prove the O(1) indexed lookup selects exactly the same
# bridges/sources/targets as the original O(bridges x members) nested scan, for
# randomized rule sets -- the transformation that replaced the per-frame scan in
# routerOBP/routerHBP.group_received and the in-band signalling pass.
#
# Run from the repo root:   venv/bin/python -m unittest discover -s tests

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bridge
from dmr_utils3.utils import bytes_3

SYSTEMS = ['ALPHA', 'BRAVO', 'CHARLIE', 'DELTA']
TGIDS = [bytes_3(t) for t in (1, 9, 3100, 3129, 31665)]
SLOTS = [1, 2]


def _random_bridges(rng):
    bridges = {}
    for b in range(rng.randint(1, 6)):
        members = []
        for _ in range(rng.randint(0, 5)):
            members.append({
                'SYSTEM': rng.choice(SYSTEMS),
                'TS': rng.choice(SLOTS),
                'TGID': rng.choice(TGIDS),
                'ACTIVE': rng.random() < 0.7,
            })
        bridges['BRIDGE{}'.format(b)] = members
    return bridges


# Reference: the ORIGINAL nested-scan selection logic from group_received.
def _ref_targets(bridges, system, slot, dst):
    out = []
    for _bridge in bridges:
        for _src in bridges[_bridge]:
            if (_src['SYSTEM'] == system and _src['TGID'] == dst
                    and _src['TS'] == slot and _src['ACTIVE'] == True):
                for _tgt in bridges[_bridge]:
                    if _tgt['SYSTEM'] != system and _tgt['ACTIVE']:
                        out.append((_bridge, id(_src), id(_tgt)))
    return out


def _idx_targets(src_index, system, slot, dst):
    out = []
    for _bridge, _src, _members in src_index.get((system, slot, dst), ()):
        if _src['ACTIVE'] == True:
            for _tgt in _members:
                if _tgt['SYSTEM'] != system and _tgt['ACTIVE']:
                    out.append((_bridge, id(_src), id(_tgt)))
    return out


# Reference: the ORIGINAL nested-scan from the in-band signalling pass.
def _ref_by_system(bridges, system):
    out = []
    for _bridge in bridges:
        for _sys in bridges[_bridge]:
            if _sys['SYSTEM'] == system:
                out.append((_bridge, id(_sys)))
    return out


def _idx_by_system(by_system, system):
    return [(_bridge, id(_sys)) for _bridge, _sys in by_system.get(system, ())]


class TestRoutingIndexEquivalence(unittest.TestCase):
    def test_source_selection_matches_bruteforce(self):
        rng = random.Random(2026)
        for _ in range(300):
            bridges = _random_bridges(rng)
            src_index, _ = bridge.index_bridges(bridges)
            for system in SYSTEMS:
                for slot in SLOTS:
                    for dst in TGIDS:
                        self.assertEqual(
                            _idx_targets(src_index, system, slot, dst),
                            _ref_targets(bridges, system, slot, dst),
                            'mismatch for {} ts{} {!r}'.format(system, slot, dst))

    def test_by_system_selection_matches_bruteforce(self):
        rng = random.Random(7)
        for _ in range(300):
            bridges = _random_bridges(rng)
            _, by_system = bridge.index_bridges(bridges)
            for system in SYSTEMS:
                self.assertEqual(_idx_by_system(by_system, system),
                                 _ref_by_system(bridges, system))

    def test_missing_key_yields_no_targets(self):
        bridges = {'B': [{'SYSTEM': 'ALPHA', 'TS': 1, 'TGID': bytes_3(9), 'ACTIVE': True}]}
        src_index, by_system = bridge.index_bridges(bridges)
        self.assertEqual(src_index.get(('NOPE', 1, bytes_3(9)), []), [])
        self.assertEqual(by_system.get('NOPE', []), [])

    def test_index_uses_live_active_flag(self):
        # The index references the live member dicts, so flipping ACTIVE after
        # building must take effect without rebuilding.
        member = {'SYSTEM': 'ALPHA', 'TS': 1, 'TGID': bytes_3(9), 'ACTIVE': True}
        target = {'SYSTEM': 'BRAVO', 'TS': 1, 'TGID': bytes_3(9), 'ACTIVE': True}
        bridges = {'B': [member, target]}
        src_index, _ = bridge.index_bridges(bridges)
        self.assertEqual(len(_idx_targets(src_index, 'ALPHA', 1, bytes_3(9))), 1)
        target['ACTIVE'] = False
        self.assertEqual(len(_idx_targets(src_index, 'ALPHA', 1, bytes_3(9))), 0)


if __name__ == '__main__':
    unittest.main()
