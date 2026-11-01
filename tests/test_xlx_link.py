#!/usr/bin/env python
#
# Verify the XLX link packet HBlink builds is accepted by xlxd.
#
# Every gate below is transcribed from xlxd's own parser
# (src/cdmrmmdvmprotocol.cpp:636-655, CDmrmmdvmProtocol::IsValidDvHeaderPacket).
# A packet failing any of them is dropped with no response whatsoever -- xlxd never
# acknowledges a link and no frame on the wire carries a module identity -- so these
# checks are the only pre-flight signal available.
#
# The strongest check here is test_matches_2019_reference: the burst builder is run
# against the source and destination IDs decoded out of the hardcoded payload that
# Andy Taylor's 2019 implementation has been sending in the field since then, and
# must reproduce it byte for byte.

# Run from the repo root:   venv/bin/python -m unittest discover -s tests

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import hblink
from const import XLX_UNLINK, XLX_TG_BASE

# xlxd src/cdmrmmdvmprotocol.cpp:51-53
G_DMR_SYNC_BS_DATA = [0x0D, 0xFF, 0x57, 0xD7, 0x5D, 0xF5, 0xD0]
G_DMR_SYNC_MS_DATA = [0x0D, 0x5D, 0x7F, 0x77, 0xFD, 0x75, 0x70]

# The canned bursts in hblink-org/hblink.py:301,305, still deployed everywhere the
# 2019 code runs. Their embedded LC decodes to a private call, 2353150 -> 4005, at
# colour code 3.
REF_HEADER = bytes.fromhex('4f2e00b501ae3a001c40a0c1cc7dff57d75df5d5065026f82880bd616f13f185890000')
REF_TERM   = bytes.fromhex('4f410061011e3a781c30a061ccbdff57d75df5d2534425c02fe0b1216713e885ba0000')
REF_SRC, REF_DST, REF_CC = 2353150, 4005, 3


def make_system(_radio_id=3120101, _colorcode=1, _module='D'):
    """An HBSYSTEM with just enough config to build link packets, capturing sends."""
    _sys = hblink.HBSYSTEM.__new__(hblink.HBSYSTEM)
    _sys._system = 'XLX-TEST'
    _sys._config = {
        'RADIO_ID': _radio_id.to_bytes(4, 'big'),
        'COLORCODE': str(_colorcode),
        'XLX_MODULE': _module,
        'MODE': 'OUTBOUND',
    }
    _sys.sent = []
    _sys.send_server = _sys.sent.append
    return _sys


class TestXLXLinkPacket(unittest.TestCase):

    # --- xlxd acceptance gates ---------------------------------------------

    def test_packet_is_exactly_55_bytes(self):
        # Buffer.size() == 55. A bare 53-byte DMRD (20 header + 33 burst) is
        # rejected outright; the two trailing pad bytes are load-bearing.
        _sys = make_system()
        _sys.send_xlx_link(4001)
        self.assertEqual(len(_sys.sent), 5)
        for _pkt in _sys.sent:
            self.assertEqual(len(_pkt), 55)

    def test_frame_type_is_data_sync(self):
        # (data[15] & 0x30) >> 4 == DMRMMDVM_FRAMETYPE_DATASYNC (2)
        _sys = make_system()
        _sys.send_xlx_link(4001)
        for _pkt in _sys.sent:
            self.assertEqual((_pkt[15] & 0x30) >> 4, 2)

    def test_slot_is_ts2(self):
        # uiSlot must equal DMRMMDVM_REFLECTOR_SLOT (DMR_SLOT2)
        _sys = make_system()
        _sys.send_xlx_link(4001)
        for _pkt in _sys.sent:
            self.assertTrue(_pkt[15] & 0x80)

    def test_call_type_is_private(self):
        # Module selection is a private call; group traffic is TS2/TG9 instead.
        _sys = make_system()
        _sys.send_xlx_link(4001)
        for _pkt in _sys.sent:
            self.assertTrue(_pkt[15] & 0x40)

    def test_only_header_frames_carry_the_command(self):
        # data[15] & 0x0F == MMDVM_SLOTTYPE_HEADER (1) is required for xlxd to read
        # a link/unlink command. The two terminators are slot type 2 and are ignored
        # as commands -- they exist to frame the call.
        _sys = make_system()
        _sys.send_xlx_link(4001)
        self.assertEqual([_p[15] & 0x0F for _p in _sys.sent], [1, 1, 1, 2, 2])

    def test_sync_pattern_is_valid_dmr_data_sync(self):
        # dmrsync[0] = data[33] & 0x0F, [1:6] = data[34:39], [6] = data[39] & 0xF0
        _sys = make_system()
        _sys.send_xlx_link(4001)
        for _pkt in _sys.sent:
            _sync = [_pkt[33] & 0x0F] + list(_pkt[34:39]) + [_pkt[39] & 0xF0]
            self.assertIn(_sync, (G_DMR_SYNC_BS_DATA, G_DMR_SYNC_MS_DATA))

    def test_destination_is_readable_from_the_header(self):
        # xlxd takes uiDstId from bytes 8-10 and never decodes the LC.
        _sys = make_system()
        _sys.send_xlx_link(4017)
        for _pkt in _sys.sent:
            self.assertEqual(int.from_bytes(_pkt[8:11], 'big'), 4017)

    # --- packet structure ---------------------------------------------------

    def test_all_five_frames_share_one_stream_id(self):
        _sys = make_system()
        _sys.send_xlx_link(4001)
        self.assertEqual(len({_p[16:20] for _p in _sys.sent}), 1)

    def test_separate_calls_use_different_stream_ids(self):
        _sys = make_system()
        _sys.send_xlx_link(XLX_UNLINK)
        _sys.send_xlx_link(4004)
        self.assertNotEqual(_sys.sent[0][16:20], _sys.sent[5][16:20])

    def test_sequence_numbers_run_0_to_4(self):
        _sys = make_system()
        _sys.send_xlx_link(4001)
        self.assertEqual([_p[4] for _p in _sys.sent], [0, 1, 2, 3, 4])

    def test_source_and_peer_id_are_our_radio_id(self):
        _sys = make_system(_radio_id=3120101)
        _sys.send_xlx_link(4001)
        for _pkt in _sys.sent:
            self.assertEqual(int.from_bytes(_pkt[5:8], 'big'), 3120101)
            self.assertEqual(int.from_bytes(_pkt[11:15], 'big'), 3120101)

    def test_embedded_lc_agrees_with_the_header(self):
        # xlxd ignores the LC, but a packet whose payload contradicts its header is
        # a debugging trap: anything that does decode it reports the wrong call.
        from bitarray import bitarray
        from dmr_utils3 import bptc
        _sys = make_system(_radio_id=3120101)
        _sys.send_xlx_link(4009)
        for _pkt in _sys.sent:
            _ba = bitarray(endian='big')
            _ba.frombytes(_pkt[20:53])
            _lc = bptc.decode_full_lc(_ba[0:98] + _ba[166:264]).tobytes()
            self.assertEqual(_lc[0] & 0x3f, 0x03)                          # FLCO USER_USER
            self.assertEqual(int.from_bytes(_lc[3:6], 'big'), 4009)        # dst == header dst
            self.assertEqual(int.from_bytes(_lc[6:9], 'big'), 3120101)     # src == header src

    # --- the reference check ------------------------------------------------

    def test_matches_2019_reference(self):
        # Reproduce the field-proven canned bursts from their own decoded values.
        _sys = make_system(_radio_id=REF_SRC, _colorcode=REF_CC)
        _sys.send_xlx_link(REF_DST)
        self.assertEqual(_sys.sent[0][20:53], REF_HEADER[:33])
        self.assertEqual(_sys.sent[3][20:53], REF_TERM[:33])

    # --- link sequencing ----------------------------------------------------

    def test_link_sends_unlink_then_module(self):
        # Not defensive: while a client already holds a module, xlxd discards a link
        # naming a different one (cdmrmmdvmprotocol.cpp:297-325), so a reconnect that
        # skipped the unlink would silently stay in the previous room.
        _sys = make_system(_module='D')
        _sys.xlx_link_module()
        self.assertEqual(len(_sys.sent), 10)
        self.assertEqual(int.from_bytes(_sys.sent[0][8:11], 'big'), XLX_UNLINK)
        self.assertEqual(int.from_bytes(_sys.sent[5][8:11], 'big'), XLX_TG_BASE + 4)

    def test_module_letters_map_to_4001_through_4026(self):
        for _letter, _expected in (('A', 4001), ('D', 4004), ('J', 4010), ('Z', 4026)):
            _sys = make_system(_module=_letter)
            _sys.xlx_link_module()
            self.assertEqual(int.from_bytes(_sys.sent[5][8:11], 'big'), _expected)

    def test_non_xlx_system_sends_nothing(self):
        _sys = make_system(_module='')
        _sys.xlx_link_module()
        self.assertEqual(_sys.sent, [])

    def test_colorcode_is_taken_from_config(self):
        # The slot type carries the colour code; xlxd does not check it, but the
        # packet should still describe the system it claims to come from.
        _a = make_system(_colorcode=1)
        _b = make_system(_colorcode=7)
        _a.send_xlx_link(4001)
        _b.send_xlx_link(4001)
        self.assertNotEqual(_a.sent[0][20:53], _b.sent[0][20:53])


class TestExpandXLXBridges(unittest.TestCase):
    """XLX_BRIDGES expansion and the load-time guards."""

    def setUp(self):
        import bridge
        self.bridge = bridge
        bridge.CONFIG = {'SYSTEMS': {
            'XLX950-D':  {'MODE': 'OUTBOUND', 'XLX_MODULE': 'D'},
            'XLX068-A':  {'MODE': 'OUTBOUND', 'XLX_MODULE': 'A'},
            'HOTSPOT':   {'MODE': 'OUTBOUND', 'XLX_MODULE': ''},
            'REPEATERS': {'MODE': 'SERVER'},
            'VESTA_OBP': {'MODE': 'OPENBRIDGE'},
        }}

    @staticmethod
    def _member(system, ts, tgid):
        return {'SYSTEM': system, 'TS': ts, 'TGID': tgid, 'ACTIVE': True,
                'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []}

    def test_noop_without_xlx_table(self):
        bridges = {'B1': [self._member('REPEATERS', 1, 2)]}
        out = self.bridge.expand_xlx_bridges(bridges, {})
        self.assertEqual(len(out['B1']), 1)

    def test_ts_and_tgid_are_forced_to_2_and_9(self):
        # The whole point: these are injected, never read from the rules file.
        bridges = {}
        self.bridge.expand_xlx_bridges(bridges, {'XLX950-D': 'WORLDWIDE'})
        _m = bridges['WORLDWIDE'][0]
        self.assertEqual((_m['TS'], _m['TGID']), (2, 9))

    def test_appends_alongside_existing_members(self):
        bridges = {'WORLDWIDE': [self._member('REPEATERS', 1, 3100)]}
        self.bridge.expand_xlx_bridges(bridges, {'XLX950-D': 'WORLDWIDE'})
        self.assertEqual(len(bridges['WORLDWIDE']), 2)
        self.assertEqual(bridges['WORLDWIDE'][1]['SYSTEM'], 'XLX950-D')

    def test_triggers_and_timers_are_inert(self):
        # No end user on an XLX connection to send ON/OFF/RESET TGIDs.
        bridges = {}
        self.bridge.expand_xlx_bridges(bridges, {'XLX950-D': 'WORLDWIDE'})
        _m = bridges['WORLDWIDE'][0]
        self.assertEqual(_m['TO_TYPE'], 'NONE')
        self.assertEqual((_m['ON'], _m['OFF'], _m['RESET']), ([], [], []))
        self.assertTrue(_m['ACTIVE'])

    def test_two_modules_are_two_systems_on_one_bridge(self):
        bridges = {}
        self.bridge.expand_xlx_bridges(bridges, {'XLX950-D': 'WW', 'XLX068-A': 'WW'})
        self.assertEqual({_m['SYSTEM'] for _m in bridges['WW']}, {'XLX950-D', 'XLX068-A'})

    def test_inline_xlx_member_is_fatal(self):
        bridges = {'WORLDWIDE': [self._member('XLX950-D', 2, 9)]}
        with self.assertRaises(SystemExit):
            self.bridge.expand_xlx_bridges(bridges, {})

    def test_non_xlx_system_in_table_is_fatal(self):
        with self.assertRaises(SystemExit):
            self.bridge.expand_xlx_bridges({}, {'REPEATERS': 'WORLDWIDE'})

    def test_outbound_without_module_is_not_xlx(self):
        with self.assertRaises(SystemExit):
            self.bridge.expand_xlx_bridges({}, {'HOTSPOT': 'WORLDWIDE'})

    def test_unknown_system_is_fatal(self):
        with self.assertRaises(SystemExit):
            self.bridge.expand_xlx_bridges({}, {'NOT-A-SYSTEM': 'WORLDWIDE'})

    def test_tgid_override_attempt_is_rejected(self):
        # An operator reaching for the OBP {bridge: tgid} shape must not silently
        # get a working config with an unusable TGID.
        with self.assertRaises(SystemExit):
            self.bridge.expand_xlx_bridges({}, {'XLX950-D': {'WORLDWIDE': 3100}})

    def test_xlx_in_unit_is_fatal(self):
        # A private call forwarded to the reflector would change its module for
        # every connected user.
        with self.assertRaises(SystemExit):
            self.bridge.validate_xlx_unit(['REPEATERS', 'XLX950-D'])

    def test_unit_without_xlx_is_accepted(self):
        self.bridge.validate_xlx_unit(['REPEATERS', 'HOTSPOT'])


class TestXLXConfigValidation(unittest.TestCase):
    """XLX_MODULE parsing in config.py."""

    def setUp(self):
        import configparser
        import config
        self.config = config
        self.parser = configparser.ConfigParser()
        self.parser.add_section('XLX-1')

    def _parse(self, _value):
        self.parser.set('XLX-1', 'XLX_MODULE', _value)
        return self.config._parse_xlx_module(self.parser, 'XLX-1')

    def test_absent_field_means_not_xlx(self):
        self.assertEqual(self.config._parse_xlx_module(self.parser, 'XLX-1'), '')

    def test_empty_field_means_not_xlx(self):
        self.assertEqual(self._parse(''), '')

    def test_letter_is_accepted_and_uppercased(self):
        self.assertEqual(self._parse('d'), 'D')
        self.assertEqual(self._parse(' A '), 'A')

    def test_module_number_is_rejected(self):
        # Rejecting 4004 outright is deliberate: it is what the 2019 implementation
        # took, so an operator migrating a config would otherwise get a silent
        # mis-parse rather than an error.
        with self.assertRaises(SystemExit):
            self._parse('4004')

    def test_multi_character_is_rejected(self):
        with self.assertRaises(SystemExit):
            self._parse('AB')

    def test_non_letter_is_rejected(self):
        with self.assertRaises(SystemExit):
            self._parse('1')

    # --- settings from the pre-2026 HBlink -----------------------------------

    def _reject(self, **_options):
        self.parser.set('XLX-1', 'MODE', _options.pop('MODE', 'OUTBOUND'))
        for _key, _value in _options.items():
            self.parser.set('XLX-1', _key, _value)
        self.config._reject_legacy_xlx(self.parser, 'XLX-1')

    def test_legacy_key_is_fatal(self):
        # Ignored, it logs in normally and never links: connected with no module.
        with self.assertRaises(SystemExit) as _err:
            self._reject(XLXMODULE='D')
        self.assertIn('XLX_MODULE', str(_err.exception))

    def test_legacy_mode_is_fatal(self):
        with self.assertRaises(SystemExit):
            self._reject(MODE='XLXPEER')

    def test_current_settings_are_not_legacy(self):
        self._reject(XLX_MODULE='D')

    # --- finished system config ----------------------------------------------

    def _validate(self, _radio_id, _module='D', _mode='OUTBOUND'):
        self.config._validate_xlx_systems({'XLX-1': {
            'MODE': _mode,
            'XLX_MODULE': _module,
            'RADIO_ID': _radio_id.to_bytes(4, 'big'),
        }})

    def test_largest_3_byte_radio_id_is_accepted(self):
        self._validate(0xFFFFFF)
        make_system(_radio_id=0xFFFFFF).send_xlx_link(4004)

    def test_radio_id_over_3_bytes_is_fatal(self):
        # send_xlx_link would raise OverflowError after the login had completed.
        with self.assertRaises(SystemExit):
            self._validate(0x1000000)
        with self.assertRaises(SystemExit):
            self._validate(302123401)

    def test_large_radio_id_is_fine_without_xlx(self):
        self._validate(302123401, _module='')

    def test_xlx_module_on_server_is_fatal(self):
        with self.assertRaises(SystemExit):
            self._validate(312000, _mode='SERVER')


if __name__ == '__main__':
    unittest.main(verbosity=2)
