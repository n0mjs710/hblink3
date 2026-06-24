#!/usr/bin/env python
#
# Regression tests for the Link Control helpers extracted from bridge.py during
# the 2026 cleanup: gen_lcs() and embed_lc(). These pin the behavior that all
# four group-routing paths depend on, since live OBP/HBP audio cannot be
# reproduced in a test environment.
#
# Run from the repo root:   venv/bin/python -m unittest discover -s tests

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bitarray import bitarray
from dmr_utils3 import bptc

import bridge
from const import (
    HBPF_VOICE, HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM, LC_OPT,
)


def _bits(_b):
    ba = bitarray(endian='big')
    ba.frombytes(_b)
    return ba


# A representative destination LC: 3 option bytes + 3 TGID bytes + 3 source bytes
DST_LC = LC_OPT + b'\x00\x00\x09' + b'\x30\x40\x50'
# A fixed, non-trivial 33-byte DMR payload (264 bits)
DMRPKT = bytes((i * 7 + 3) & 0xFF for i in range(33))


class TestGenLcs(unittest.TestCase):
    def test_returns_header_terminator_embedded_in_order(self):
        h, t, e = bridge.gen_lcs(DST_LC)
        self.assertEqual(h, bptc.encode_header_lc(DST_LC))
        self.assertEqual(t, bptc.encode_terminator_lc(DST_LC))
        self.assertEqual(e, bptc.encode_emblc(DST_LC))

    def test_embedded_lc_has_bursts_1_through_4(self):
        _, _, e = bridge.gen_lcs(DST_LC)
        for burst in (1, 2, 3, 4):
            self.assertEqual(len(e[burst]), 32,
                             'embedded LC burst {} must be 32 bits'.format(burst))


class TestEmbedLc(unittest.TestCase):
    def setUp(self):
        self.h, self.t, self.e = bridge.gen_lcs(DST_LC)

    def test_voice_header_injects_full_lc_and_preserves_voice(self):
        out = _bits(bridge.embed_lc(DMRPKT, HBPF_DATA_SYNC, HBPF_SLT_VHEAD,
                                    self.h, self.t, self.e))
        # Full LC replaces the outer bits; the middle voice/sync bits survive
        self.assertEqual(out[0:98], self.h[0:98])
        self.assertEqual(out[98:166], _bits(DMRPKT)[98:166])
        self.assertEqual(out[166:], self.h[98:197])

    def test_voice_terminator_injects_full_lc_and_preserves_voice(self):
        out = _bits(bridge.embed_lc(DMRPKT, HBPF_DATA_SYNC, HBPF_SLT_VTERM,
                                    self.h, self.t, self.e))
        self.assertEqual(out[0:98], self.t[0:98])
        self.assertEqual(out[98:166], _bits(DMRPKT)[98:166])
        self.assertEqual(out[166:], self.t[98:197])

    def test_burst_injects_embedded_lc_and_preserves_voice(self):
        # dtype 1-4 with a non-DATA_SYNC frame type takes the embedded-LC path
        for burst in (1, 2, 3, 4):
            out = _bits(bridge.embed_lc(DMRPKT, HBPF_VOICE, burst,
                                        self.h, self.t, self.e))
            self.assertEqual(out[0:116], _bits(DMRPKT)[0:116],
                             'burst {}: leading voice bits changed'.format(burst))
            self.assertEqual(out[116:148], self.e[burst],
                             'burst {}: embedded LC not injected'.format(burst))
            self.assertEqual(out[148:264], _bits(DMRPKT)[148:264],
                             'burst {}: trailing voice bits changed'.format(burst))

    def test_other_frames_pass_through_unchanged(self):
        # dtype 0 (and 5) are neither full-LC nor embedded-LC frames
        for dtype in (0, 5):
            self.assertEqual(
                bridge.embed_lc(DMRPKT, HBPF_VOICE, dtype, self.h, self.t, self.e),
                DMRPKT,
                'dtype {} should pass through unchanged'.format(dtype))

    def test_is_pure_does_not_mutate_input(self):
        src = bytearray(DMRPKT)
        bridge.embed_lc(bytes(src), HBPF_DATA_SYNC, HBPF_SLT_VHEAD,
                        self.h, self.t, self.e)
        self.assertEqual(bytes(src), DMRPKT)


if __name__ == '__main__':
    unittest.main()
