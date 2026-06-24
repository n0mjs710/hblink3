#!/usr/bin/env python
#
# Regression tests for HBSYSTEM.dmrd_acl_check(), the ACL helper extracted from
# the master and peer receive paths in hblink.py during the 2026 cleanup.
# Pins both the PERMIT/DENY + per-slot matching contract and the "log each
# dropped stream only once per slot" dedup behavior.
#
# Run from the repo root:   venv/bin/python -m unittest discover -s tests

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random

import const
import config
import hblink
from dmr_utils3.utils import bytes_3, bytes_4


# Reference implementation of the ORIGINAL linear ACL semantics, used to prove
# the bisect-based acl_check is behavior-identical.
def _ref_build(_acl, _max):
    if not _acl:
        return (True, [(const.ID_MIN, _max)])
    action = (_acl.split(':')[0] == 'PERMIT')
    ranges = []
    for entry in _acl.split(':')[1].split(','):
        if entry == 'ALL':
            return (action, [(const.ID_MIN, _max)])
        elif '-' in entry:
            s, e = entry.split('-')
            ranges.append((int(s), int(e)))
        else:
            ranges.append((int(entry), int(entry)))
    return (action, ranges)


def _ref_check(_id, _ref):
    action, ranges = _ref
    for s, e in ranges:
        if s <= _id <= e:
            return action
    return not action


def _acl(_str):
    return config.acl_build(_str, const.ID_MAX)


def _aclset(use, sub='PERMIT:ALL', tg1='PERMIT:ALL', tg2='PERMIT:ALL'):
    return {'USE_ACL': use, 'SUB_ACL': _acl(sub),
            'TG1_ACL': _acl(tg1), 'TG2_ACL': _acl(tg2)}


class _FakeSystem:
    # Borrow the real method under test, unbound, onto a minimal host object so
    # we don't have to stand up a full Twisted DatagramProtocol.
    dmrd_acl_check = hblink.HBSYSTEM.dmrd_acl_check

    def __init__(self, global_acl, system_acl):
        self._CONFIG = {'GLOBAL': global_acl}
        self._config = system_acl
        self._system = 'TEST'
        self._laststrid = {1: b'', 2: b''}


SID = b'\x11\x22\x33\x44'
SID2 = b'\xaa\xbb\xcc\xdd'


class TestAclMatching(unittest.TestCase):
    def test_permit_all_allows_both_slots(self):
        sysobj = _FakeSystem(_aclset(True), _aclset(True))
        self.assertFalse(sysobj.dmrd_acl_check(bytes_3(1234), bytes_3(9), 1, SID))
        self.assertFalse(sysobj.dmrd_acl_check(bytes_3(1234), bytes_3(9), 2, SID))

    def test_global_subscriber_deny_drops_only_matching_sub(self):
        sysobj = _FakeSystem(_aclset(True, sub='DENY:1'), _aclset(True))
        self.assertTrue(sysobj.dmrd_acl_check(bytes_3(1), bytes_3(9), 1, SID))
        self.assertFalse(sysobj.dmrd_acl_check(bytes_3(2), bytes_3(9), 1, SID))

    def test_global_tg1_acl_is_slot1_only(self):
        sysobj = _FakeSystem(_aclset(True, tg1='DENY:9'), _aclset(True))
        # Denied on TS1...
        self.assertTrue(sysobj.dmrd_acl_check(bytes_3(1234), bytes_3(9), 1, SID))
        # ...but TS2 is governed by TG2_ACL (PERMIT:ALL here), so it passes
        self.assertFalse(sysobj.dmrd_acl_check(bytes_3(1234), bytes_3(9), 2, SID))

    def test_global_tg2_acl_is_slot2_only(self):
        sysobj = _FakeSystem(_aclset(True, tg2='DENY:9'), _aclset(True))
        self.assertTrue(sysobj.dmrd_acl_check(bytes_3(1234), bytes_3(9), 2, SID))
        self.assertFalse(sysobj.dmrd_acl_check(bytes_3(1234), bytes_3(9), 1, SID))

    def test_system_acl_applies_when_global_disabled(self):
        sysobj = _FakeSystem(_aclset(False, sub='DENY:1'),
                             _aclset(True, sub='DENY:5'))
        # Global is off so its DENY:1 is ignored; system DENY:5 still applies
        self.assertFalse(sysobj.dmrd_acl_check(bytes_3(1), bytes_3(9), 1, SID))
        self.assertTrue(sysobj.dmrd_acl_check(bytes_3(5), bytes_3(9), 1, SID))

    def test_both_disabled_allows_everything(self):
        sysobj = _FakeSystem(_aclset(False, sub='DENY:1'),
                             _aclset(False, sub='DENY:1'))
        self.assertFalse(sysobj.dmrd_acl_check(bytes_3(1), bytes_3(9), 1, SID))


class TestAclDedup(unittest.TestCase):
    def test_allow_does_not_touch_laststrid(self):
        sysobj = _FakeSystem(_aclset(True), _aclset(True))
        sysobj.dmrd_acl_check(bytes_3(1234), bytes_3(9), 1, SID)
        self.assertEqual(sysobj._laststrid[1], b'')

    def test_drop_records_stream_id_for_its_slot(self):
        sysobj = _FakeSystem(_aclset(True, sub='DENY:1'), _aclset(True))
        sysobj.dmrd_acl_check(bytes_3(1), bytes_3(9), 2, SID)
        self.assertEqual(sysobj._laststrid[2], SID)
        self.assertEqual(sysobj._laststrid[1], b'')

    def test_same_stream_logs_once_new_stream_logs_again(self):
        sysobj = _FakeSystem(_aclset(True, sub='DENY:1'), _aclset(True))
        with self.assertLogs('hblink', level='INFO') as cm:
            # Same dropped stream three times -> a single log line
            for _ in range(3):
                self.assertTrue(sysobj.dmrd_acl_check(bytes_3(1), bytes_3(9), 1, SID))
            # A new stream id -> logs again
            self.assertTrue(sysobj.dmrd_acl_check(bytes_3(1), bytes_3(9), 1, SID2))
        drops = [m for m in cm.output if 'CALL DROPPED' in m]
        self.assertEqual(len(drops), 2)


class TestAclCheckEquivalence(unittest.TestCase):
    # The bisect-based acl_check must match the original linear semantics for
    # every ACL shape: permit/deny, singles, ranges, overlapping/adjacent
    # ranges, ALL, and large enumerated lists.
    SPECS = [
        'PERMIT:ALL',
        'DENY:1',
        'DENY:1-100',
        'PERMIT:3100-3200',
        'DENY:9,10,3129',
        'PERMIT:1-5,3-10,100',          # overlapping ranges
        'DENY:1-10,11-20',              # adjacent ranges
        'PERMIT:50,40,30,20,10',        # unsorted singles
        'DENY:5-5',                     # degenerate range == single
    ]

    def test_matches_linear_reference(self):
        probe = [0, 1, 2, 5, 9, 10, 11, 20, 50, 100, 101, 3099, 3129, 3200, 3201,
                 const.ID_MAX, const.ID_MAX + 1]
        for spec in self.SPECS:
            built = config.acl_build(spec, const.ID_MAX)
            ref = _ref_build(spec, const.ID_MAX)
            for i in probe:
                self.assertEqual(
                    hblink.acl_check(bytes_4(i), built),
                    _ref_check(i, ref),
                    'spec={!r} id={}'.format(spec, i))

    def test_large_enumerated_acl_matches_reference(self):
        random.seed(42)
        ids = random.sample(range(1, const.ID_MAX), 800)
        spec = 'DENY:' + ','.join(str(i) for i in ids)
        built = config.acl_build(spec, const.ID_MAX)
        ref = _ref_build(spec, const.ID_MAX)
        denied = set(ids)
        for i in ids[:200] + random.sample(range(1, const.ID_MAX), 200):
            self.assertEqual(hblink.acl_check(bytes_4(i), built), _ref_check(i, ref))
            # spot check the actual intent too: listed ids are denied
            self.assertEqual(hblink.acl_check(bytes_4(i), built), i not in denied)

    def test_ranges_are_merged_and_disjoint(self):
        action, singles, starts, ends = config.acl_build('DENY:1-10,5-20,22', const.ID_MAX)
        self.assertEqual(starts, (1,))
        self.assertEqual(ends, (20,))
        self.assertIn(22, singles)

    def test_blank_acl_permits_all(self):
        built = config.acl_build('', const.ID_MAX)
        self.assertTrue(hblink.acl_check(bytes_4(1), built))
        self.assertTrue(hblink.acl_check(bytes_4(const.ID_MAX), built))


if __name__ == '__main__':
    unittest.main()
