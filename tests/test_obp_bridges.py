#!/usr/bin/env python
#
# Tests for expand_obp_bridges(): the per-OBP TGID<->bridge table (rules.OBP_BRIDGES)
# that replaces inline OpenBridge bridge membership. Covers expansion into synthetic
# members, the TS default + override, and the load-time validation (ingress-fork
# ERROR, inter-OBP renumber WARNING, inline-OBP-member ERROR, non-OBP system ERROR).
#
# Run from the repo root:   venv/bin/python -m unittest discover -s tests

import copy
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, 'tools'))

import bridge
import migrate_obp_rules


def _member(system, ts, tgid):
    # A plain inline (non-OBP) bridge member, pre-make_bridges (raw int TGID).
    return {'SYSTEM': system, 'TS': ts, 'TGID': tgid, 'ACTIVE': True,
            'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []}


class TestExpandOBPBridges(unittest.TestCase):
    def setUp(self):
        bridge.CONFIG = {'SYSTEMS': {
            'VESTA_OBP': {'MODE': 'OPENBRIDGE'},
            'CC_OBP':    {'MODE': 'OPENBRIDGE'},
            'NEIGHBOR':  {'MODE': 'OPENBRIDGE'},
            'REPEATERS': {'MODE': 'SERVER'},
        }}

    def test_noop_without_obp_table(self):
        bridges = {'B1': [_member('REPEATERS', 1, 2)]}
        out = bridge.expand_obp_bridges(bridges, {})
        self.assertEqual(len(out['B1']), 1)
        self.assertEqual(out['B1'][0]['SYSTEM'], 'REPEATERS')

    def test_expands_and_appends_synthetic_member(self):
        bridges = {'B1': [_member('REPEATERS', 1, 2)]}
        bridge.expand_obp_bridges(bridges, {'VESTA_OBP': {'B1': 2}})
        self.assertEqual(len(bridges['B1']), 2)                 # appended alongside REPEATERS
        obp = bridges['B1'][1]
        self.assertEqual(obp['SYSTEM'], 'VESTA_OBP')
        self.assertEqual(obp['TGID'], 2)
        self.assertEqual(obp['TS'], 1)                          # default
        self.assertTrue(obp['ACTIVE'])
        self.assertEqual(obp['TO_TYPE'], 'NONE')               # triggers hard-wired inert
        self.assertEqual((obp['ON'], obp['OFF'], obp['RESET']), ([], [], []))

    def test_ts_override_tuple(self):
        bridges = {}
        bridge.expand_obp_bridges(bridges, {'VESTA_OBP': {'KS-STATEWIDE': (3120, 2)}})
        m = bridges['KS-STATEWIDE'][0]
        self.assertEqual((m['TGID'], m['TS']), (3120, 2))

    def test_creates_obp_only_bridge(self):
        bridges = {}
        bridge.expand_obp_bridges(bridges, {'VESTA_OBP': {'TRANSIT': 31}})
        self.assertIn('TRANSIT', bridges)
        self.assertEqual(bridges['TRANSIT'][0]['SYSTEM'], 'VESTA_OBP')

    def test_ingress_fork_is_error(self):
        # One TGID mapped to two bridges on the SAME OBP -> stream duplication.
        with self.assertRaises(SystemExit):
            bridge.expand_obp_bridges({}, {'VESTA_OBP': {'B1': 2, 'B2': 2}})

    def test_unknown_obp_system_is_error(self):
        with self.assertRaises(SystemExit):
            bridge.expand_obp_bridges({}, {'NOPE': {'B1': 2}})

    def test_non_openbridge_system_is_error(self):
        # REPEATERS exists but is a SERVER, not an OPENBRIDGE.
        with self.assertRaises(SystemExit):
            bridge.expand_obp_bridges({}, {'REPEATERS': {'B1': 2}})

    def test_inline_obp_member_is_error(self):
        # Old-style: an OBP system left as an inline BRIDGES member.
        bridges = {'B1': [_member('VESTA_OBP', 1, 2)]}
        with self.assertRaises(SystemExit):
            bridge.expand_obp_bridges(bridges, {})

    def test_same_tgid_across_obps_no_warning(self):
        # Two OBPs agree on the number -> no renumber, no warning.
        bridges = {}
        with self.assertNoLogs(bridge.logger, level='WARNING'):
            bridge.expand_obp_bridges(bridges, {
                'VESTA_OBP': {'B1': 2},
                'CC_OBP':    {'B1': 2},
            })
        self.assertEqual(len(bridges['B1']), 2)

    def test_renumber_across_obps_warns_but_starts(self):
        # A bridge carrying different TGIDs on two OBPs: WARNING, not ERROR.
        bridges = {}
        with self.assertLogs(bridge.logger, level='WARNING') as cm:
            bridge.expand_obp_bridges(bridges, {
                'VESTA_OBP': {'KS-STATEWIDE': 3120},
                'NEIGHBOR':  {'KS-STATEWIDE': 8},
            })
        self.assertTrue(any('renumbers TGID' in line for line in cm.output))
        self.assertEqual(len(bridges['KS-STATEWIDE']), 2)      # both still added


class TestMigrateOBPRules(unittest.TestCase):
    """The migration tool (tools/migrate_obp_rules.py) must move inline OBP members
    into OBP_BRIDGES while preserving the exact routing."""

    OBP_SYSTEMS = {'VESTA_OBP', 'CC_OBP'}

    def setUp(self):
        bridge.CONFIG = {'SYSTEMS': {
            'VESTA_OBP': {'MODE': 'OPENBRIDGE'},
            'CC_OBP':    {'MODE': 'OPENBRIDGE'},
            'REPEATERS': {'MODE': 'SERVER'},
            'IPSC':      {'MODE': 'SERVER'},
        }}

    def _old_bridges(self):
        # Old inline form: OBP systems mixed in as ordinary members.
        return {
            'B2':  [_member('REPEATERS', 1, 2), _member('IPSC', 1, 2), _member('VESTA_OBP', 1, 2)],
            'B9':  [_member('REPEATERS', 1, 9), _member('VESTA_OBP', 1, 9), _member('CC_OBP', 1, 9)],
            'B8':  [_member('CC_OBP', 1, 8)],                       # OBP-only -> should drop from BRIDGES
            'BSW': [_member('REPEATERS', 2, 3120), _member('VESTA_OBP', 1, 3120)],
        }

    def _routing_projection(self, bridges, obp_bridges=None):
        # {(system, ts, tgid_bytes): sorted[bridge names]} -- identity-independent.
        # obp_bridges=None => old inline path (no expand); a dict => new expanded path.
        b = copy.deepcopy(bridges)
        if obp_bridges is not None:
            b = bridge.expand_obp_bridges(b, obp_bridges)
        src, _ = bridge.index_bridges(bridge.make_bridges(b))
        return {k: sorted(hit[0] for hit in v) for k, v in src.items()}

    def test_roundtrip_preserves_routing(self):
        old = self._old_bridges()
        # OLD routing: inline OBP members, no OBP_BRIDGES table.
        want = self._routing_projection(old)

        new_bridges, obp_bridges, moved, errors, warnings = migrate_obp_rules.migrate(
            copy.deepcopy(old), self.OBP_SYSTEMS)
        self.assertFalse(errors)
        # NEW routing: trimmed BRIDGES + expanded OBP_BRIDGES.
        got = self._routing_projection(new_bridges, obp_bridges)
        self.assertEqual(got, want)

    def test_moves_and_drops_obp_only_bridge(self):
        new_bridges, obp_bridges, moved, errors, warnings = migrate_obp_rules.migrate(
            copy.deepcopy(self._old_bridges()), self.OBP_SYSTEMS)
        self.assertEqual(len(moved), 5)                         # VESTA_OBP x3 + CC_OBP x2
        self.assertNotIn('B8', new_bridges)                    # OBP-only bridge dropped from BRIDGES
        self.assertEqual(obp_bridges['CC_OBP']['B8'], 8)       # but present in the table
        self.assertEqual(obp_bridges['VESTA_OBP']['B2'], 2)

    def test_ts_override_becomes_tuple(self):
        old = {'BSW': [_member('VESTA_OBP', 2, 3120)]}          # TS 2 (non-default)
        _, obp_bridges, _, _, _ = migrate_obp_rules.migrate(old, {'VESTA_OBP'})
        self.assertEqual(obp_bridges['VESTA_OBP']['BSW'], (3120, 2))

    def test_detects_ingress_fork(self):
        old = {'B1': [_member('VESTA_OBP', 1, 2)], 'B2': [_member('VESTA_OBP', 1, 2)]}
        _, _, _, errors, _ = migrate_obp_rules.migrate(old, {'VESTA_OBP'})
        self.assertTrue(errors)

    def test_detects_renumber(self):
        old = {'BSW': [_member('VESTA_OBP', 1, 3120), _member('CC_OBP', 1, 8)]}
        _, _, _, _, warnings = migrate_obp_rules.migrate(old, {'VESTA_OBP', 'CC_OBP'})
        self.assertTrue(warnings)


if __name__ == '__main__':
    unittest.main()
