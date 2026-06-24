#!/usr/bin/env python
#
# Golden-master test: replays the fixed scenarios in baseline_scenarios.py
# through the current routing core and asserts the emitted packets match the
# frozen baseline in baseline_emissions.json byte-for-byte.
#
# This is the safety net for the routing refactor: regenerate the baseline ONLY
# from known-good code (python tests/baseline_scenarios.py shows current output;
# the file was frozen from the pre-refactor code). After the forward_group
# extraction and the later unification, this test must still pass unchanged.
#
# Run from the repo root:   venv/bin/python -m unittest discover -s tests

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import baseline_scenarios

BASELINE = os.path.join(_HERE, 'baseline_emissions.json')


class TestRoutingBaseline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(BASELINE) as f:
            cls.expected = json.load(f)

    def test_scenarios_match_frozen_baseline(self):
        # Same set of scenarios as when the baseline was frozen.
        self.assertEqual(set(baseline_scenarios.SCENARIOS),
                         set(self.expected),
                         'scenario set drifted from the frozen baseline')

        for name in sorted(baseline_scenarios.SCENARIOS):
            with self.subTest(scenario=name):
                got = baseline_scenarios.SCENARIOS[name]()
                # JSON round-trips tuples to lists; normalize for comparison.
                got = [[s, h] for (s, h) in got]
                self.assertEqual(got, self.expected[name],
                                 'emissions changed for scenario %r' % name)


if __name__ == '__main__':
    unittest.main()
