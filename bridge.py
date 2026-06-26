#!/usr/bin/env python
#
###############################################################################
#   Copyright (C) 2016-2019 Cortney T. Buffington, N0MJS <n0mjs@me.com>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software Foundation,
#   Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
###############################################################################

'''
This application, in conjuction with it's rule file (rules.py) will
work like a "conference bridge". This is similar to what most hams think of as a
reflector. You define conference bridges and any system joined to that conference
bridge will both receive traffic from, and send traffic to any other system
joined to the same conference bridge. It does not provide end-to-end connectivity
as each end system must individually be joined to a conference bridge (a name
you create in the configuraiton file) to pass traffic.

This program currently only works with group voice calls.
'''

# Python modules we need
import sys
from bitarray import bitarray
from time import time
import importlib.util
import asyncio

# Things we import from the main hblink module
from hblink import HBSYSTEM, OPENBRIDGE, systems, hblink_handler, reportFactory, REPORT_OPCODES, mk_aliases, run_periodic
from dmr_utils3.utils import bytes_3, int_id, get_alias
from dmr_utils3 import decode, bptc, const
import config
import log
from const import *
# The module needs logging, but handlers, etc. are controlled by the parent
import logging
logger = logging.getLogger(__name__)


# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Cortney T. Buffington, N0MJS'
__copyright__  = 'Copyright (c) 2016-2019 Cortney T. Buffington, N0MJS and the K0USY Group'
__credits__    = 'Colin Durbridge, G4EML, Steve Zingman, N4IRS; Mike Zingman, N4IRR; Jonathan Naylor, G4KLX; Hans Barthen, DL5DI; Torsten Shultze, DG1HT'
__license__    = 'GNU GPLv3'
__maintainer__ = 'Cort Buffington, N0MJS'
__email__      = 'n0mjs@me.com'

# Module gobal varaibles

# Dictionary for dynamically mapping unit (subscriber) to a system.
# This is for pruning unit-to-uint calls to not broadcast once the
# target system for a unit is identified
# format 'unit_id': ('SYSTEM', time)
UNIT_MAP = {}

# Routing indexes derived from BRIDGES once at startup by index_bridges(). They
# turn the per-frame "which bridges does this source feed?" question into an O(1)
# dict lookup instead of a full scan of every bridge and member on every packet.
# Keys (system, ts, tgid / system) are immutable per member; ACTIVE is read live
# at packet time, so the indexes only need rebuilding if bridge MEMBERSHIP changes.
BRIDGE_SRC_INDEX = {}
BRIDGE_BY_SYSTEM = {}

# The reporting server manager; assigned in async_main when REPORT is enabled.
# Module-global so the rule-timer and stream-trimmer loops can reach it.
report_server = None


# Generate the full (header & terminator) and embedded Link Control for a
# re-targeted group stream. Returns (H_LC, T_LC, EMB_LC). Pure -- no side effects.
def gen_lcs(_dst_lc):
    return bptc.encode_header_lc(_dst_lc), bptc.encode_terminator_lc(_dst_lc), bptc.encode_emblc(_dst_lc)


# Rewrite the DMR payload's embedded/full Link Control for a re-targeted group
# stream and return the new 33-byte payload. The middle (voice/sync) bits are
# preserved from the source packet; only the LC portions are replaced. Pure.
def embed_lc(_dmrpkt, _frame_type, _dtype_vseq, _h_lc, _t_lc, _emb_lc):
    dmrbits = bitarray(endian='big')
    dmrbits.frombytes(_dmrpkt)
    # Create a voice header packet (FULL LC)
    if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
        dmrbits = _h_lc[0:98] + dmrbits[98:166] + _h_lc[98:197]
    # Create a voice terminator packet (FULL LC)
    elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
        dmrbits = _t_lc[0:98] + dmrbits[98:166] + _t_lc[98:197]
    # Create a Burst B-E packet (Embedded LC)
    elif _dtype_vseq in [1,2,3,4]:
        dmrbits = dmrbits[0:116] + _emb_lc[_dtype_vseq] + dmrbits[148:264]
    return dmrbits.tobytes()


# Timed loop used for reporting HBP status
#
# REPORT BASED ON THE TYPE SELECTED IN THE MAIN CONFIG FILE
def config_reports(_config, _factory):
    def reporting_loop(logger, _server):
        logger.debug('(REPORT) Periodic reporting loop started')
        _server.send_config()
        _server.send_bridge()

    logger.info('(REPORT) HBlink TCP reporting server configured')

    report_server = _factory(_config)
    report_server.clients = []
    loop = asyncio.get_event_loop()
    loop.create_task(report_server.start(_config['REPORTS']['REPORT_PORT']))
    loop.create_task(run_periodic(_config['REPORTS']['REPORT_INTERVAL'],
                                  reporting_loop, '(REPORT) reporting', logger, report_server))

    return report_server


# Import Bridging rules
# Note: A stanza *must* exist for any SERVER or OUTBOUND system configured in
# the main configuration file and listed as "active". It can be empty,
# but it has to exist.
def make_bridges(_rules):
    # Convert integer GROUP ID numbers from the config into hex strings
    # we need to send in the actual data packets.
    for _bridge in _rules:
        for _system in _rules[_bridge]:
            if _system['SYSTEM'] not in CONFIG['SYSTEMS']:
                sys.exit('ERROR: Conference bridge "{}" references a system named "{}" that is not enabled in the main configuration'.format(_bridge, _system['SYSTEM']))

            _system['TGID']       = bytes_3(_system['TGID'])
            for i, e in enumerate(_system['ON']):
                _system['ON'][i]  = bytes_3(_system['ON'][i])
            for i, e in enumerate(_system['OFF']):
                _system['OFF'][i] = bytes_3(_system['OFF'][i])
            _system['TIMEOUT']    = _system['TIMEOUT']*60
            if _system['ACTIVE'] == True:
                _system['TIMER']  = time() + _system['TIMEOUT']
            else:
                _system['TIMER']  = time()
    return _rules


# Build the routing indexes from the processed BRIDGES structure. Call once after
# make_bridges() (and again only if bridge membership is ever rebuilt at runtime).
# src_index: (system, ts, tgid) -> [(bridge_name, source_member, sibling_members), ...]
# by_system: system -> [(bridge_name, member), ...]   (for the in-band signalling pass)
def index_bridges(_bridges):
    src_index = {}
    by_system = {}
    for _bridge in _bridges:
        _members = _bridges[_bridge]
        for _member in _members:
            src_index.setdefault((_member['SYSTEM'], _member['TS'], _member['TGID']), []).append((_bridge, _member, _members))
            by_system.setdefault(_member['SYSTEM'], []).append((_bridge, _member))
    return src_index, by_system


# Run this every minute for rule timer updates
def rule_timer_loop():
    global UNIT_MAP
    logger.debug('(ROUTER) routerHBP Rule timer loop started')
    _now = time()

    for _bridge in BRIDGES:
        for _system in BRIDGES[_bridge]:
            if _system['TO_TYPE'] == 'ON':
                if _system['ACTIVE'] == True:
                    if _system['TIMER'] < _now:
                        sys_obj = systems.get(_system['SYSTEM'])
                        _st = sys_obj.STATUS[_system['TS']] if sys_obj else None
                        if _st and not _st['RX_TERMINATED'] and _st['RX_TGID'] == _system['TGID']:
                            _system['TIMER'] = _now + _system['TIMEOUT']
                            logger.info('(ROUTER) Conference Bridge TIMEOUT deferred (call in progress): System: %s, Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
                        else:
                            _system['ACTIVE'] = False
                            logger.info('(ROUTER) Conference Bridge TIMEOUT: DEACTIVATE System: %s, Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
                    else:
                        timeout_in = _system['TIMER'] - _now
                        logger.info('(ROUTER) Conference Bridge ACTIVE (ON timer running): System: %s Bridge: %s, TS: %s, TGID: %s, Timeout in: %.2fs,', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']),  timeout_in)
                elif _system['ACTIVE'] == False:
                    logger.debug('(ROUTER) Conference Bridge INACTIVE (no change): System: %s Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
            elif _system['TO_TYPE'] == 'OFF':
                if _system['ACTIVE'] == False:
                    if _system['TIMER'] < _now:
                        _system['ACTIVE'] = True
                        logger.info('(ROUTER) Conference Bridge TIMEOUT: ACTIVATE System: %s, Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
                    else:
                        timeout_in = _system['TIMER'] - _now
                        logger.info('(ROUTER) Conference Bridge INACTIVE (OFF timer running): System: %s Bridge: %s, TS: %s, TGID: %s, Timeout in: %.2fs,', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']),  timeout_in)
                elif _system['ACTIVE'] == True:
                    logger.debug('(ROUTER) Conference Bridge ACTIVE (no change): System: %s Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
            else:
                logger.debug('(ROUTER) Conference Bridge NO ACTION: System: %s, Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))

    _then = _now - 60
    remove_list = []
    for unit in UNIT_MAP:
        if UNIT_MAP[unit][1] < (_then):
            remove_list.append(unit)

    for unit in remove_list:
        del UNIT_MAP[unit]

    logger.debug('Removed unit(s) %s from UNIT_MAP', remove_list)

    # Push refreshed bridge state to consumers after a rule-timer pass
    if CONFIG['REPORTS']['REPORT'] and report_server:
        report_server.send_bridge()


# run this every 10 seconds to trim orphaned stream ids
def stream_trimmer_loop():
    # Runs once a second -- intentionally silent per-pass; it only logs when it
    # actually times out a stream.
    _now = time()

    for system in systems:
        # HBP systems, server and outbound
        if CONFIG['SYSTEMS'][system]['MODE'] != 'OPENBRIDGE':
            for slot in range(1,3):
                _slot  = systems[system].STATUS[slot]

                # RX slot check -- time out a stream with no audio for STREAM_TIMEOUT
                if not _slot['RX_TERMINATED'] and _slot['RX_TIME'] <  _now - STREAM_TIMEOUT:
                    logger.info('(%s) *TIME OUT*  RX STREAM ID: %s SUB: %s TGID %s, TS %s, Duration: %.2f', \
                        system, int_id(_slot['RX_STREAM_ID']), int_id(_slot['RX_RFS']), int_id(_slot['RX_TGID']), slot, _slot['RX_TIME'] - _slot['RX_START'])
                    systems[system]._end_slot_stream(slot)

                # TX slot check
                if not _slot['TX_TERMINATED'] and _slot['TX_TIME'] <  _now - STREAM_TIMEOUT:
                    _slot['TX_TERMINATED'] = True
                    logger.info('(%s) *TIME OUT*  TX STREAM ID: %s SUB: %s TGID %s, TS %s, Duration: %.2f', \
                        system, int_id(_slot['TX_STREAM_ID']), int_id(_slot['TX_RFS']), int_id(_slot['TX_TGID']), slot, _slot['TX_TIME'] - _slot['TX_START'])
                    if CONFIG['REPORTS']['REPORT']:
                        systems[system]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(system, int_id(_slot['TX_STREAM_ID']), int_id(_slot['TX_PEER']), int_id(_slot['TX_RFS']), slot, int_id(_slot['TX_TGID']), _slot['TX_TIME'] - _slot['TX_START']).encode(encoding='utf-8', errors='ignore'))

        # OBP systems
        # We can't delete items from a dicationry that's being iterated, so we have to make a temporarly list of entrys to remove later
        if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE':
            remove_list = []
            for stream_id in systems[system].STATUS:
                if systems[system].STATUS[stream_id]['LAST'] < _now - STREAM_TIMEOUT:
                    remove_list.append(stream_id)
            for stream_id in remove_list:
                if stream_id in systems[system].STATUS:
                    _stream = systems[system].STATUS[stream_id]
                    _sysconfig = CONFIG['SYSTEMS'][system]
                    if systems[system].STATUS[stream_id]['ACTIVE']:
                        logger.info('(%s) *TIME OUT*   STREAM ID: %s SUB: %s PEER: %s TYPE: %s DST ID: %s TS 1 Duration: %.2f', \
                        system, int_id(stream_id), get_alias(int_id(_stream['RFS']), subscriber_ids), get_alias(int_id(_sysconfig['NETWORK_ID']), peer_ids), _stream['TYPE'], get_alias(int_id(_stream['DST']), talkgroup_ids), _stream['LAST'] - _stream['START'])
                    if CONFIG['REPORTS']['REPORT']:
                            if _stream['TYPE'] == 'GROUP':
                                systems[system]._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(system, int_id(stream_id), int_id(_sysconfig['NETWORK_ID']), int_id(_stream['RFS']), 1, int_id(_stream['DST']), _stream['LAST'] - _stream['START']).encode(encoding='utf-8', errors='ignore'))
                            elif _stream['TYPE'] == 'UNIT':
                                systems[system]._report.send_bridgeEvent('UNIT VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(system, int_id(stream_id), int_id(_sysconfig['NETWORK_ID']), int_id(_stream['RFS']), 1, int_id(_stream['DST']), _stream['LAST'] - _stream['START']).encode(encoding='utf-8', errors='ignore'))
                    removed = systems[system].STATUS.pop(stream_id)
                else:
                    logger.error('(%s) Attemped to remove OpenBridge Stream ID %s not in the Stream ID list: %s', system, int_id(stream_id), [id for id in systems[system].STATUS])

class routerOBP(OPENBRIDGE):

    def __init__(self, _name, _config, _report):
        OPENBRIDGE.__init__(self, _name, _config, _report)
        self.name = _name
        self.STATUS = {}
        
        # list of self._targets for unit (subscriber, private) calls
        self._targets = []

    def group_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _frame_type, _dtype_vseq, _stream_id, _data):
        pkt_time = time()
        dmrpkt = _data[20:53]
        _bits = _data[15]
        
        # Is this a new call stream?
        if (_stream_id not in self.STATUS):
            # This is a new call stream
            self.STATUS[_stream_id] = {
                'START':     pkt_time,
                'CONTENTION':False,
                'RFS':       _rf_src,
                'TYPE':      'GROUP',
                'DST':       _dst_id,
                'ACTIVE':    True
            }

            # If we can, use the LC from the voice header as to keep all options intact
            if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                decoded = decode.voice_head_term(dmrpkt)
                self.STATUS[_stream_id]['LC'] = decoded['LC']

            # If we don't have a voice header then don't wait to decode the Embedded LC
            # just make a new one from the HBP header. This is good enough, and it saves lots of time
            else:
                self.STATUS[_stream_id]['LC'] = LC_OPT + _dst_id + _rf_src


            logger.info('(%s) *GROUP CALL START* OBP STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s', \
                    self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
            if CONFIG['REPORTS']['REPORT']:
                self._report.send_bridgeEvent('GROUP VOICE,START,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))

        self.STATUS[_stream_id]['LAST'] = pkt_time


        # Hand each active target the frame; the target system applies its own
        # admission/contention policy and egress framing (see bridge_group).
        _src_lc = self.STATUS[_stream_id]['LC']
        for _bridge, _system, _members in BRIDGE_SRC_INDEX.get((self._system, _slot, _dst_id), ()):
            if _system['ACTIVE'] == True:
                for _target in _members:
                    if (_target['SYSTEM'] != self._system) and (_target['ACTIVE']):
                        systems[_target['SYSTEM']].bridge_group(
                            self, _bridge, _target, _system['TS'], _src_lc, b'\x00\x00',
                            _peer_id, _rf_src, _dst_id, _stream_id, _slot,
                            _frame_type, _dtype_vseq, _data, pkt_time)


        # Final actions - Is this a voice terminator?
        if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM):
            call_duration = pkt_time - self.STATUS[_stream_id]['START']
            logger.info('(%s) *GROUP CALL END*   STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s, Duration: %.2f', \
                    self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, call_duration)
            if CONFIG['REPORTS']['REPORT']:
               self._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id), call_duration).encode(encoding='utf-8', errors='ignore'))
            self.STATUS[_stream_id]['ACTIVE'] = False
            logger.debug('(%s) OpenBridge sourced call stream end, remove terminated Stream ID: %s', self._system, int_id(_stream_id))


    # Contention-log gate (source side): log a target-contention message once per
    # source stream. Called by an HBP target's bridge_group.
    def should_log_contention(self, _stream_id, _slot, _frame_type, _dtype_vseq):
        if self.STATUS[_stream_id]['CONTENTION'] == False:
            self.STATUS[_stream_id]['CONTENTION'] = True
            return True
        return False

    # Forward a bridged group frame INTO this OpenBridge (this system is the
    # target). OpenBridge carries unlimited concurrent streams (keyed by stream
    # id), is effectively TS1, and carries no BER/RSSI trailer.
    def bridge_group(self, _src, _bridge, _target, _src_ts, _src_lc, _ber_rssi,
                     _peer_id, _rf_src, _dst_id, _stream_id, _slot,
                     _frame_type, _dtype_vseq, _data, _pkt_time):
        _bits = _data[15]
        _dmrpkt = _data[20:53]
        _target_status = self.STATUS
        # Is this a new call stream on the target?
        if (_stream_id not in _target_status):
            _target_status[_stream_id] = {
                'START':     _pkt_time,
                'CONTENTION':False,
                'RFS':       _rf_src,
                'TYPE':      'GROUP',
                'DST':       _dst_id,
                'ACTIVE':    True
            }
            # Generate LCs (full and EMB) for the TX stream
            dst_lc = b''.join([_src_lc[0:3], _target['TGID'], _rf_src])
            _target_status[_stream_id]['H_LC'], _target_status[_stream_id]['T_LC'], _target_status[_stream_id]['EMB_LC'] = gen_lcs(dst_lc)
            logger.info('(%s) Conference Bridge: %s, Call Bridged to OBP System: %s TS: %s, TGID: %s', _src._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
            if CONFIG['REPORTS']['REPORT']:
                self._report.send_bridgeEvent('GROUP VOICE,START,TX,{},{},{},{},{},{}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID'])).encode(encoding='utf-8', errors='ignore'))

        # Record the time of this packet so we can later identify a stale stream
        _target_status[_stream_id]['LAST'] = _pkt_time
        # Clear the TS bit -- all OpenBridge streams are effectively on TS1
        _tmp_bits = _bits & ~(1 << 7)
        _tmp_data = b''.join([_data[:8], _target['TGID'], _data[11:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])
        _dmrpkt = embed_lc(_dmrpkt, _frame_type, _dtype_vseq, _target_status[_stream_id]['H_LC'], _target_status[_stream_id]['T_LC'], _target_status[_stream_id]['EMB_LC'])
        # On the voice terminator, finalize the target stream and report the call end
        if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM and CONFIG['REPORTS']['REPORT']:
            call_duration = _pkt_time - _target_status[_stream_id]['START']
            _target_status[_stream_id]['ACTIVE'] = False
            self._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
        _tmp_data = b''.join([_tmp_data, _dmrpkt])
        self.send_system(_tmp_data)
        # Drop the target stream on the terminator (the trimmer also cleans up)
        if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM and _stream_id in _target_status:
            _target_status.pop(_stream_id)

    def unit_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _frame_type, _dtype_vseq, _stream_id, _data):
        global UNIT_MAP
        pkt_time = time()
        dmrpkt = _data[20:53]
        _bits = _data[15]
 
        # Make/update this unit in the UNIT_MAP cache
        UNIT_MAP[_rf_src] = (self.name, pkt_time)
        
        
        # Is this a new call stream?
        if (_stream_id not in self.STATUS):
            # This is a new call stream
            self.STATUS[_stream_id] = {
                'START':     pkt_time,
                'CONTENTION':False,
                'RFS':       _rf_src,
                'TYPE':      'UNIT',
                'DST':       _dst_id,
                'ACTIVE':    True
            }
                
            # Create a destination list for the call:                
            if _dst_id in UNIT_MAP:
                if UNIT_MAP[_dst_id][0] != self._system:
                    self._targets = [UNIT_MAP[_dst_id][0]]
                else:
                    self._targets = []
                    logger.error('UNIT call to a subscriber on the same system, send nothing')
            else:
                self._targets = list(UNIT)
                self._targets.remove(self._system)
            
            
            # This is a new call stream, so log & report
            logger.info('(%s) *UNIT CALL START* STREAM ID: %s SUB: %s (%s) PEER: %s (%s) UNIT: %s (%s), TS: %s, FORWARD: %s', \
                    self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, self._targets)
            if CONFIG['REPORTS']['REPORT']:
                self._report.send_bridgeEvent('UNIT VOICE,START,RX,{},{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id), self._targets).encode(encoding='utf-8', errors='ignore'))

        # Record the time of this packet so we can later identify a stale stream
        self.STATUS[_stream_id]['LAST'] = pkt_time

        for _target in self._targets:
            systems[_target].bridge_unit(
                self, _peer_id, _rf_src, _dst_id, _stream_id, _slot,
                _frame_type, _dtype_vseq, _data, pkt_time)


        # Final actions - Is this a voice terminator?
        if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM):
            self._targets = []
            call_duration = pkt_time - self.STATUS[_stream_id]['START']
            logger.info('(%s) *UNIT CALL END*   STREAM ID: %s SUB: %s (%s) PEER: %s (%s) UNIT %s (%s), TS %s, Duration: %.2f', \
                    self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, call_duration)
            if CONFIG['REPORTS']['REPORT']:
               self._report.send_bridgeEvent('UNIT VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id), call_duration).encode(encoding='utf-8', errors='ignore'))


    # Forward a bridged unit (private) call INTO this OpenBridge (this system is
    # the target). Unit calls are not TGID/LC rewritten; the slot bit is cleared
    # unless BOTH_SLOTS is set, and there is no BER/RSSI trailer.
    def bridge_unit(self, _src, _peer_id, _rf_src, _dst_id, _stream_id, _slot,
                    _frame_type, _dtype_vseq, _data, _pkt_time):
        _bits = _data[15]
        _dmrpkt = _data[20:53]
        _target_status = self.STATUS
        if (_stream_id not in _target_status):
            _target_status[_stream_id] = {
                'START':     _pkt_time,
                'CONTENTION':False,
                'RFS':       _rf_src,
                'TYPE':      'UNIT',
                'DST':       _dst_id,
                'ACTIVE':    True
            }
            logger.info('(%s) Unit call bridged to OBP System: %s TS: %s, UNIT: %s', _src._system, self._system, _slot if self._config['BOTH_SLOTS'] else 1, int_id(_dst_id))
            if CONFIG['REPORTS']['REPORT']:
                self._report.send_bridgeEvent('UNIT VOICE,START,TX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))

        # Record the time of this packet so we can later identify a stale stream
        _target_status[_stream_id]['LAST'] = _pkt_time
        # Clear the TS bit and follow proper OBP definition, unless BOTH_SLOTS is set
        if self._config['BOTH_SLOTS']:
            _tmp_bits = _bits
        else:
            _tmp_bits = _bits & ~(1 << 7)
        _out = b''.join([_data[:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20], _dmrpkt])
        if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM):
            _target_status[_stream_id]['ACTIVE'] = False
        self.send_system(_out)
        # Drop the target stream on the terminator (the trimmer also cleans up)
        if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM) and _stream_id in _target_status:
            _target_status.pop(_stream_id)

    def dmrd_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data):

        if _call_type == 'group':
            self.group_received(_peer_id, _rf_src, _dst_id, _seq, _slot, _frame_type, _dtype_vseq, _stream_id, _data)
        elif _call_type == 'unit':
            self.unit_received(_peer_id, _rf_src, _dst_id, _seq, _slot, _frame_type, _dtype_vseq, _stream_id, _data)
        elif _call_type == 'vcsbk':
            logger.debug('CSBK recieved, but HBlink does not process them currently')
        else:
            logger.error('Unknown call type recieved -- not processed')


class routerHBP(HBSYSTEM):

    def __init__(self, _name, _config, _report):
        HBSYSTEM.__init__(self, _name, _config, _report)
        self.name = _name

        # list of self._targets for unit (subscriber, private) calls
        self._targets = []

        # Status information for the system, TS1 & TS2
        # 1 & 2 are "timeslot"
        # In TX_EMB_LC, 2-5 are burst B-E
        self.STATUS = {
            1: {
                'RX_START':     time(),
                'TX_START':     time(),
                'RX_SEQ':       0,
                'RX_RFS':       b'\x00',
                'TX_RFS':       b'\x00',
                'RX_PEER':      b'\x00',
                'TX_PEER':      b'\x00',
                'RX_STREAM_ID': b'\x00',
                'TX_STREAM_ID': b'\x00',
                'RX_TGID':      b'\x00\x00\x00',
                'TX_TGID':      b'\x00\x00\x00',
                'RX_TIME':      time(),
                'TX_TIME':      time(),
                'RX_TYPE':      HBPF_SLT_VTERM,
                'TX_TYPE':      HBPF_SLT_VTERM,
                # Explicit "stream has ended" flags. RX_/TX_TYPE hold the last
                # frame's _dtype_vseq, which for voice bursts cycles 0..5 and so
                # collides with HBPF_SLT_VTERM (==2) -- using it as the terminated
                # sentinel drops the END of any stream whose last burst was vseq 2.
                'RX_TERMINATED': True,
                'TX_TERMINATED': True,
                # Last stream-id we logged a slot collision for, so a contending
                # stream is reported once -- not once per colliding frame.
                'RX_COLLISION_SID': b'\x00',
                'RX_CT':        'GROUP VOICE',
                'RX_LC':        b'\x00',
                'TX_H_LC':      b'\x00',
                'TX_T_LC':      b'\x00',
                'TX_EMB_LC': {
                    1: b'\x00',
                    2: b'\x00',
                    3: b'\x00',
                    4: b'\x00',
                    }
                },
            2: {
                'RX_START':     time(),
                'TX_START':     time(),
                'RX_SEQ':       0,
                'RX_RFS':       b'\x00',
                'TX_RFS':       b'\x00',
                'RX_PEER':      b'\x00',
                'TX_PEER':      b'\x00',
                'RX_STREAM_ID': b'\x00',
                'TX_STREAM_ID': b'\x00',
                'RX_TGID':      b'\x00\x00\x00',
                'TX_TGID':      b'\x00\x00\x00',
                'RX_TIME':      time(),
                'TX_TIME':      time(),
                'RX_TYPE':      HBPF_SLT_VTERM,
                'TX_TYPE':      HBPF_SLT_VTERM,
                # Explicit "stream has ended" flags. RX_/TX_TYPE hold the last
                # frame's _dtype_vseq, which for voice bursts cycles 0..5 and so
                # collides with HBPF_SLT_VTERM (==2) -- using it as the terminated
                # sentinel drops the END of any stream whose last burst was vseq 2.
                'RX_TERMINATED': True,
                'TX_TERMINATED': True,
                # Last stream-id we logged a slot collision for, so a contending
                # stream is reported once -- not once per colliding frame.
                'RX_COLLISION_SID': b'\x00',
                'RX_CT':        'GROUP VOICE',
                'RX_LC':        b'\x00',
                'TX_H_LC':      b'\x00',
                'TX_T_LC':      b'\x00',
                'TX_EMB_LC': {
                    1: b'\x00',
                    2: b'\x00',
                    3: b'\x00',
                    4: b'\x00',
                    }
                }
            }


    # If a timeslot is holding a stream that never sent a terminator (it was
    # superseded by a new stream, or it timed out on the trimmer), declare it
    # ended and emit a matching END so consumers don't leak it. No-op if the slot
    # already ended cleanly. Used by both the new-stream path and the trimmer.
    def _end_slot_stream(self, _slot):
        st = self.STATUS[_slot]
        if st['RX_TERMINATED']:
            return
        st['RX_TERMINATED'] = True
        if CONFIG['REPORTS']['REPORT'] and self._report:
            duration = st['RX_TIME'] - st['RX_START']
            self._report.send_bridgeEvent('{},END,RX,{},{},{},{},{},{},{:.2f}'.format(
                st.get('RX_CT', 'GROUP VOICE'), self._system, int_id(st['RX_STREAM_ID']),
                int_id(st['RX_PEER']), int_id(st['RX_RFS']), _slot, int_id(st['RX_TGID']),
                duration).encode(encoding='utf-8', errors='ignore'))


    def group_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _frame_type, _dtype_vseq, _stream_id, _data):
        global UNIT_MAP
        pkt_time = time()
        dmrpkt = _data[20:53]
        _bits = _data[15]
        
        # Make/update an entry in the UNIT_MAP for this subscriber
        UNIT_MAP[_rf_src] = (self.name, pkt_time)

        # Is this a new call stream?
        if (_stream_id != self.STATUS[_slot]['RX_STREAM_ID']):
            if (not self.STATUS[_slot]['RX_TERMINATED']) and (pkt_time < (self.STATUS[_slot]['RX_TIME'] + STREAM_TO)) and (_rf_src != self.STATUS[_slot]['RX_RFS']):
                if self.STATUS[_slot]['RX_COLLISION_SID'] != _stream_id:
                    self.STATUS[_slot]['RX_COLLISION_SID'] = _stream_id
                    logger.warning('(%s) Packet received with STREAM ID: %s <FROM> SUB: %s PEER: %s <TO> TGID %s, SLOT %s collided with existing call', self._system, int_id(_stream_id), int_id(_rf_src), int_id(_peer_id), int_id(_dst_id), _slot)
                return

            # A new stream is taking the slot: end any prior un-terminated stream first
            self._end_slot_stream(_slot)

            # This is a new call stream
            self.STATUS[_slot]['RX_START'] = pkt_time
            self.STATUS[_slot]['RX_TERMINATED'] = False
            self.STATUS[_slot]['RX_CT'] = 'GROUP VOICE'
            logger.info('(%s) *GROUP CALL START* STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s', \
                    self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
            if CONFIG['REPORTS']['REPORT']:
                self._report.send_bridgeEvent('GROUP VOICE,START,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))

            # If we can, use the LC from the voice header as to keep all options intact
            if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                decoded = decode.voice_head_term(dmrpkt)
                self.STATUS[_slot]['RX_LC'] = decoded['LC']

            # If we don't have a voice header then don't wait to decode it from the Embedded LC
            # just make a new one from the HBP header. This is good enough, and it saves lots of time
            else:
                self.STATUS[_slot]['RX_LC'] = LC_OPT + _dst_id + _rf_src

            # In-band signalling on call START: fire ACTIVATION triggers now so the
            # header frame and all subsequent frames route to newly-connected targets.
            _bridge_state_changed = False
            for _bridge, _system in BRIDGE_BY_SYSTEM.get(self._system, ()):
                if (_dst_id in _system['ON'] or _dst_id in _system['RESET']) and _slot == _system['TS']:
                    if _dst_id in _system['ON']:
                        if _system['ACTIVE'] == False:
                            _system['ACTIVE'] = True
                            _bridge_state_changed = True
                            _system['TIMER'] = pkt_time + _system['TIMEOUT']
                            logger.info('(%s) Bridge: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                            if _system['TO_TYPE'] == 'OFF':
                                _system['TIMER'] = pkt_time
                                logger.info('(%s) Bridge: %s set to "OFF" with an on timer rule: timeout timer cancelled', self._system, _bridge)
                    if _system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON':
                        _system['TIMER'] = pkt_time + _system['TIMEOUT']
                        logger.info('(%s) Bridge: %s, timeout timer reset to: %s', self._system, _bridge, _system['TIMER'] - pkt_time)
            if _bridge_state_changed and CONFIG['REPORTS']['REPORT'] and report_server:
                report_server.send_bridge()

        # Hand each active target the frame; the target system applies its own
        # admission/contention policy and egress framing (see bridge_group).
        _src_lc = self.STATUS[_slot]['RX_LC']
        for _bridge, _system, _members in BRIDGE_SRC_INDEX.get((self._system, _slot, _dst_id), ()):
            if _system['ACTIVE'] == True:
                for _target in _members:
                    if _target['SYSTEM'] != self._system and _target['ACTIVE']:
                        systems[_target['SYSTEM']].bridge_group(
                            self, _bridge, _target, _system['TS'], _src_lc, _data[53:55],
                            _peer_id, _rf_src, _dst_id, _stream_id, _slot,
                            _frame_type, _dtype_vseq, _data, pkt_time)


        # Final actions - Is this a voice terminator?
        if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM) and (not self.STATUS[_slot]['RX_TERMINATED']):
            self.STATUS[_slot]['RX_TERMINATED'] = True
            call_duration = pkt_time - self.STATUS[_slot]['RX_START']
            logger.info('(%s) *GROUP CALL END*   STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s, Duration: %.2f', \
                    self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, call_duration)
            if CONFIG['REPORTS']['REPORT']:
               self._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id), call_duration).encode(encoding='utf-8', errors='ignore'))

            #
            # Begin in-band signalling for call end. This has nothign to do with routing traffic directly.
            #

            # Iterate the rules dictionary

            # Indexed lookup of this system's bridge memberships for in-band signalling.
            _bridge_state_changed = False
            for _bridge, _system in BRIDGE_BY_SYSTEM.get(self._system, ()):

                # TGID matches a rule source, reset its timer
                if _slot == _system['TS'] and _dst_id == _system['TGID'] and ((_system['TO_TYPE'] == 'ON' and (_system['ACTIVE'] == True)) or (_system['TO_TYPE'] == 'OFF' and _system['ACTIVE'] == False)):
                    _system['TIMER'] = pkt_time + _system['TIMEOUT']
                    logger.info('(%s) Transmission match for Bridge: %s. Reset timeout to %s', self._system, _bridge, _system['TIMER'])

                # TGID matches an DE-ACTIVATION trigger
                if (_dst_id in _system['OFF']  or _dst_id in _system['RESET']) and _slot == _system['TS']:
                    # Set the matching rule as ACTIVE
                    if _dst_id in _system['OFF']:
                        if _system['ACTIVE'] == True:
                            _system['ACTIVE'] = False
                            _bridge_state_changed = True
                            logger.info('(%s) Bridge: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                            # Cancel the timer if we've enabled an "ON" type timeout
                            if _system['TO_TYPE'] == 'ON':
                                _system['TIMER'] = pkt_time
                                logger.info('(%s) Bridge: %s set to ON with and "OFF" timer rule: timeout timer cancelled', self._system, _bridge)
                    # Reset the timer for the rule
                    if _system['ACTIVE'] == False and _system['TO_TYPE'] == 'OFF':
                        _system['TIMER'] = pkt_time + _system['TIMEOUT']
                        logger.info('(%s) Bridge: %s, timeout timer reset to: %s', self._system, _bridge, _system['TIMER'] - pkt_time)
                    # Cancel the timer if we've enabled an "ON" type timeout
                    if _system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON' and _dst_id in _system['OFF']:
                        _system['TIMER'] = pkt_time
                        logger.info('(%s) Bridge: %s set to ON with and "OFF" timer rule: timeout timer cancelled', self._system, _bridge)

            if _bridge_state_changed and CONFIG['REPORTS']['REPORT'] and report_server:
                report_server.send_bridge()

        #
        # END IN-BAND SIGNALLING
        #
        # Mark status variables for use later
        self.STATUS[_slot]['RX_PEER']      = _peer_id
        self.STATUS[_slot]['RX_SEQ']       = _seq
        self.STATUS[_slot]['RX_RFS']       = _rf_src
        self.STATUS[_slot]['RX_TYPE']      = _dtype_vseq
        self.STATUS[_slot]['RX_TGID']      = _dst_id
        self.STATUS[_slot]['RX_TIME']      = pkt_time
        self.STATUS[_slot]['RX_STREAM_ID'] = _stream_id


    # Contention-log gate (source side): log once on the new stream's voice header.
    def should_log_contention(self, _stream_id, _slot, _frame_type, _dtype_vseq):
        return _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD and self.STATUS[_slot]['RX_STREAM_ID'] != _stream_id

    # Forward a bridged group frame INTO this HBP repeater system (this system is
    # the target). HBP timeslots carry one stream at a time, so this applies the
    # contention/group-hangtime policy and preserves the BER/RSSI trailer.
    def bridge_group(self, _src, _bridge, _target, _src_ts, _src_lc, _ber_rssi,
                     _peer_id, _rf_src, _dst_id, _stream_id, _slot,
                     _frame_type, _dtype_vseq, _data, _pkt_time):
        # If this target is an OUTBOUND client not logged into its upstream server,
        # don't forward -- the frames would just be dropped there, and it would
        # falsely show as an active call/forward on the dashboard.
        if not self.egress_ready():
            return
        _bits = _data[15]
        _dmrpkt = _data[20:53]
        _target_status = self.STATUS
        _ts = _target['TS']
        # BEGIN STANDARD CONTENTION HANDLING -- drop the frame if the target slot
        # is busy with, or in group hangtime for, a different call.
        if ((_target['TGID'] != _target_status[_ts]['RX_TGID']) and ((_pkt_time - _target_status[_ts]['RX_TIME']) < self._config['GROUP_HANGTIME'])):
            if _src.should_log_contention(_stream_id, _slot, _frame_type, _dtype_vseq):
                logger.info('(%s) Call not routed to TGID %s, target active or in group hangtime: HBSystem: %s, TS: %s, TGID: %s', _src._system, int_id(_target['TGID']), _target['SYSTEM'], _ts, int_id(_target_status[_ts]['RX_TGID']))
            return
        if ((_target['TGID'] != _target_status[_ts]['TX_TGID']) and ((_pkt_time - _target_status[_ts]['TX_TIME']) < self._config['GROUP_HANGTIME'])):
            if _src.should_log_contention(_stream_id, _slot, _frame_type, _dtype_vseq):
                logger.info('(%s) Call not routed to TGID%s, target in group hangtime: HBSystem: %s, TS: %s, TGID: %s', _src._system, int_id(_target['TGID']), _target['SYSTEM'], _ts, int_id(_target_status[_ts]['TX_TGID']))
            return
        if (_target['TGID'] == _target_status[_ts]['RX_TGID']) and ((_pkt_time - _target_status[_ts]['RX_TIME']) < STREAM_TO):
            if _src.should_log_contention(_stream_id, _slot, _frame_type, _dtype_vseq):
                logger.info('(%s) Call not routed to TGID%s, matching call already active on target: HBSystem: %s, TS: %s, TGID: %s', _src._system, int_id(_target['TGID']), _target['SYSTEM'], _ts, int_id(_target_status[_ts]['RX_TGID']))
            return
        if (_target['TGID'] == _target_status[_ts]['TX_TGID']) and (_rf_src != _target_status[_ts]['TX_RFS']) and ((_pkt_time - _target_status[_ts]['TX_TIME']) < STREAM_TO):
            if _src.should_log_contention(_stream_id, _slot, _frame_type, _dtype_vseq):
                logger.info('(%s) Call not routed for subscriber %s, call route in progress on target: HBSystem: %s, TS: %s, TGID: %s, SUB: %s', _src._system, int_id(_rf_src), _target['SYSTEM'], _ts, int_id(_target_status[_ts]['TX_TGID']), int_id(_target_status[_ts]['TX_RFS']))
            return

        # Is this a new call stream on the target slot?
        if (_target_status[_ts]['TX_STREAM_ID'] != _stream_id):
            _target_status[_ts]['TX_START'] = _pkt_time
            _target_status[_ts]['TX_TERMINATED'] = False
            _target_status[_ts]['TX_TGID'] = _target['TGID']
            _target_status[_ts]['TX_STREAM_ID'] = _stream_id
            _target_status[_ts]['TX_RFS'] = _rf_src
            _target_status[_ts]['TX_PEER'] = _peer_id
            # Generate LCs (full and EMB) for the TX stream
            dst_lc = b''.join([_src_lc[0:3], _target['TGID'], _rf_src])
            _target_status[_ts]['TX_H_LC'], _target_status[_ts]['TX_T_LC'], _target_status[_ts]['TX_EMB_LC'] = gen_lcs(dst_lc)
            logger.debug('(%s) Generating TX FULL and EMB LCs for HomeBrew destination: System: %s, TS: %s, TGID: %s', _src._system, _target['SYSTEM'], _ts, int_id(_target['TGID']))
            logger.info('(%s) Conference Bridge: %s, Call Bridged to HBP System: %s TS: %s, TGID: %s', _src._system, _bridge, _target['SYSTEM'], _ts, int_id(_target['TGID']))
            if CONFIG['REPORTS']['REPORT']:
                self._report.send_bridgeEvent('GROUP VOICE,START,TX,{},{},{},{},{},{}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _ts, int_id(_target['TGID'])).encode(encoding='utf-8', errors='ignore'))

        # Set values for the contention handler to test on the next frame
        _target_status[_ts]['TX_TIME'] = _pkt_time
        _target_status[_ts]['TX_TYPE'] = _dtype_vseq

        # Flip the TS bit if the source and target timeslots differ
        if _src_ts != _ts:
            _tmp_bits = _bits ^ 1 << 7
        else:
            _tmp_bits = _bits
        _tmp_data = b''.join([_data[:8], _target['TGID'], _data[11:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])
        _dmrpkt = embed_lc(_dmrpkt, _frame_type, _dtype_vseq, _target_status[_ts]['TX_H_LC'], _target_status[_ts]['TX_T_LC'], _target_status[_ts]['TX_EMB_LC'])
        # On the voice terminator, mark the TX stream ended and report the call end
        if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM and not _target_status[_ts]['TX_TERMINATED']:
            _target_status[_ts]['TX_TERMINATED'] = True
            if CONFIG['REPORTS']['REPORT']:
                call_duration = _pkt_time - _target_status[_ts]['TX_START']
                self._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _ts, int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
        _tmp_data = b''.join([_tmp_data, _dmrpkt, _ber_rssi])
        self.send_system(_tmp_data)

    def unit_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _frame_type, _dtype_vseq, _stream_id, _data):
        global UNIT_MAP
        pkt_time = time()
        dmrpkt = _data[20:53]
        _bits = _data[15]
 
        # Make/update this unit in the UNIT_MAP cache
        UNIT_MAP[_rf_src] = (self.name, pkt_time)
        
        
        # Is this a new call stream?
        if (_stream_id != self.STATUS[_slot]['RX_STREAM_ID']):
            
            # Collision in progress, bail out!
            if (not self.STATUS[_slot]['RX_TERMINATED']) and (pkt_time < (self.STATUS[_slot]['RX_TIME'] + STREAM_TO)) and (_rf_src != self.STATUS[_slot]['RX_RFS']):
                if self.STATUS[_slot]['RX_COLLISION_SID'] != _stream_id:
                    self.STATUS[_slot]['RX_COLLISION_SID'] = _stream_id
                    logger.warning('(%s) Packet received with STREAM ID: %s <FROM> SUB: %s PEER: %s <TO> UNIT %s, SLOT %s collided with existing call', self._system, int_id(_stream_id), int_id(_rf_src), int_id(_peer_id), int_id(_dst_id), _slot)
                return
                
            # A new stream is taking the slot: end any prior un-terminated stream first
            self._end_slot_stream(_slot)

            # Create a destination list for the call:
            if _dst_id in UNIT_MAP:
                if UNIT_MAP[_dst_id][0] != self._system:
                    self._targets = [UNIT_MAP[_dst_id][0]]
                else:
                    self._targets = []
                    logger.error('UNIT call to a subscriber on the same system, send nothing')
            else:
                self._targets = list(UNIT)
                self._targets.remove(self._system)
            
            # This is a new call stream, so log & report
            self.STATUS[_slot]['RX_START'] = pkt_time
            self.STATUS[_slot]['RX_TERMINATED'] = False
            self.STATUS[_slot]['RX_CT'] = 'UNIT VOICE'
            logger.info('(%s) *UNIT CALL START* STREAM ID: %s SUB: %s (%s) PEER: %s (%s) UNIT: %s (%s), TS: %s, FORWARD: %s', \
                    self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, self._targets)
            if CONFIG['REPORTS']['REPORT']:
                self._report.send_bridgeEvent('UNIT VOICE,START,RX,{},{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id), self._targets).encode(encoding='utf-8', errors='ignore'))

        for _target in self._targets:
            systems[_target].bridge_unit(
                self, _peer_id, _rf_src, _dst_id, _stream_id, _slot,
                _frame_type, _dtype_vseq, _data, pkt_time)


        # Final actions - Is this a voice terminator?
        if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM) and (not self.STATUS[_slot]['RX_TERMINATED']):
            self.STATUS[_slot]['RX_TERMINATED'] = True
            self._targets = []
            call_duration = pkt_time - self.STATUS[_slot]['RX_START']
            logger.info('(%s) *UNIT CALL END*   STREAM ID: %s SUB: %s (%s) PEER: %s (%s) UNIT %s (%s), TS %s, Duration: %.2f', \
                    self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, call_duration)
            if CONFIG['REPORTS']['REPORT']:
               self._report.send_bridgeEvent('UNIT VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id), call_duration).encode(encoding='utf-8', errors='ignore'))

        # Mark status variables for use later
        self.STATUS[_slot]['RX_PEER']      = _peer_id
        self.STATUS[_slot]['RX_SEQ']       = _seq
        self.STATUS[_slot]['RX_RFS']       = _rf_src
        self.STATUS[_slot]['RX_TYPE']      = _dtype_vseq
        self.STATUS[_slot]['RX_TGID']      = _dst_id
        self.STATUS[_slot]['RX_TIME']      = pkt_time
        self.STATUS[_slot]['RX_STREAM_ID'] = _stream_id


    # Forward a bridged unit (private) call INTO this HBP repeater system (this
    # system is the target). Unit calls are sent as-is (no TGID/LC rewrite); the
    # timeslot's contention policy still applies. Only the two stream-timeout
    # checks are active for unit calls (the group-hangtime checks are disabled).
    def bridge_unit(self, _src, _peer_id, _rf_src, _dst_id, _stream_id, _slot,
                    _frame_type, _dtype_vseq, _data, _pkt_time):
        # Don't forward to an OUTBOUND client that isn't logged into its upstream server.
        if not self.egress_ready():
            return
        _target_status = self.STATUS
        if (_dst_id == _target_status[_slot]['RX_TGID']) and ((_pkt_time - _target_status[_slot]['RX_TIME']) < STREAM_TO):
            if _src.should_log_contention(_stream_id, _slot, _frame_type, _dtype_vseq):
                logger.info('(%s) Call not routed to destination %s, matching call already active on target: HBSystem: %s, TS: %s, DEST: %s', _src._system, int_id(_dst_id), self._system, _slot, int_id(_target_status[_slot]['RX_TGID']))
            return
        if (_dst_id == _target_status[_slot]['TX_TGID']) and (_rf_src != _target_status[_slot]['TX_RFS']) and ((_pkt_time - _target_status[_slot]['TX_TIME']) < STREAM_TO):
            if _src.should_log_contention(_stream_id, _slot, _frame_type, _dtype_vseq):
                logger.info('(%s) Call not routed for subscriber %s, call route in progress on target: HBSystem: %s, TS: %s, DEST: %s, SUB: %s', _src._system, int_id(_rf_src), self._system, _slot, int_id(_target_status[_slot]['TX_TGID']), int_id(_target_status[_slot]['TX_RFS']))
            return

        # Record target information if this is a new call stream on the slot
        if (_target_status[_slot]['TX_STREAM_ID'] != _stream_id):
            _target_status[_slot]['TX_START'] = _pkt_time
            _target_status[_slot]['TX_TERMINATED'] = False
            _target_status[_slot]['TX_TGID'] = _dst_id
            _target_status[_slot]['TX_STREAM_ID'] = _stream_id
            _target_status[_slot]['TX_RFS'] = _rf_src
            _target_status[_slot]['TX_PEER'] = _peer_id
            logger.info('(%s) Unit call bridged to HBP System: %s TS: %s, UNIT: %s', _src._system, self._system, _slot, int_id(_dst_id))
            if CONFIG['REPORTS']['REPORT']:
                self._report.send_bridgeEvent('UNIT VOICE,START,TX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))

        # On the voice terminator, mark the TX stream ended so the stream-trimmer
        # doesn't later synthesize a duplicate timeout END for a cleanly-ended call.
        if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
            _target_status[_slot]['TX_TERMINATED'] = True

        # Set values for the contention handler to test on the next frame
        _target_status[_slot]['TX_TIME'] = _pkt_time
        _target_status[_slot]['TX_TYPE'] = _dtype_vseq
        self.send_system(_data)

    def dmrd_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data):
        if _call_type == 'group':
            self.group_received(_peer_id, _rf_src, _dst_id, _seq, _slot, _frame_type, _dtype_vseq, _stream_id, _data)
        elif _call_type == 'unit':
            if self._system not in UNIT:
                logger.error('(%s) *UNIT CALL NOT FORWARDED* UNIT calling is disabled for this system (INGRESS)', self._system)
            else:
                self.unit_received(_peer_id, _rf_src, _dst_id, _seq, _slot, _frame_type, _dtype_vseq, _stream_id, _data)
        elif _call_type == 'vcsbk':
            logger.debug('CSBK recieved, but HBlink does not process them currently')
        else:
            logger.error('Unknown call type recieved -- not processed')

#
# Socket-based reporting section (newline-delimited JSON; see hblink.reportFactory)
#

# Build a JSON-serializable view of the BRIDGES (conference bridge) structure.
def json_bridges(_bridges):
    out = {}
    for bridge, members in _bridges.items():
        out[bridge] = [{
            'SYSTEM':  m['SYSTEM'],
            'TS':      m['TS'],
            'TGID':    int_id(m['TGID']),
            'ACTIVE':  m['ACTIVE'],
            'TO_TYPE': m['TO_TYPE'],
            'TIMER':   m['TIMER'],
            'TIMEOUT': m['TIMEOUT'],
            'ON':      [int_id(t) for t in m['ON']],
            'OFF':     [int_id(t) for t in m['OFF']],
        } for m in members]
    return out


class bridgeReportFactory(reportFactory):

    def send_bridge(self):
        self.send_clients({'type': 'bridges', 'bridges': json_bridges(BRIDGES)})

    def send_initial(self, _client):
        # A new consumer gets both the systems config and the bridge state.
        self.send_to(_client, self.config_event())
        self.send_to(_client, {'type': 'bridges', 'bridges': json_bridges(BRIDGES)})

    def send_bridgeEvent(self, _data):
        # Call sites pass a CSV string (kept to avoid churning the routing code);
        # convert it to a JSON stream event. CSV fields are:
        #   call_type, action, trx, system, stream_id, peer, src, slot, dst[, duration]
        if isinstance(_data, (bytes, bytearray)):
            _data = _data.decode('utf-8', errors='ignore')
        p = _data.split(',')
        try:
            event = {
                'type':      'stream',
                'call_type': p[0],
                'action':    p[1],
                'trx':       p[2],
                'system':    p[3],
                'stream_id': int(p[4]),
                'peer':      int(p[5]),
                'src':       int(p[6]),
                'slot':      int(p[7]),
                'dst':       int(p[8]),
            }
            if len(p) > 9:
                event['duration'] = float(p[9])
        except (IndexError, ValueError):
            logger.error('(REPORT) malformed bridge event: %s', _data)
            return
        self.send_clients(event)


#************************************************
#      MAIN PROGRAM LOOP STARTS HERE
#************************************************

if __name__ == '__main__':

    import argparse
    import sys
    import os
    import signal

    # Change the current directory to the location of the application
    os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))

    # CLI argument parser - handles picking up the config file from the command line, and sending a "help" message
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', action='store', dest='CONFIG_FILE', help='/full/path/to/config.file (usually hblink.cfg)')
    parser.add_argument('-r', '--rules', action='store', dest='RULES_FILE', help='/full/path/to/rules.file (usually rules.py)')
    parser.add_argument('-l', '--logging', action='store', dest='LOG_LEVEL', help='Override config file logging level.')
    cli_args = parser.parse_args()

    # Ensure we have a path for the config file, if one wasn't specified, then use the default (top of file)
    if not cli_args.CONFIG_FILE:
        cli_args.CONFIG_FILE = os.path.dirname(os.path.abspath(__file__))+'/hblink.cfg'

    # Call the external routine to build the configuration dictionary
    CONFIG = config.build_config(cli_args.CONFIG_FILE)

    # Ensure we have a path for the rules file, if one wasn't specified, then use the default (top of file)
    if not cli_args.RULES_FILE:
        cli_args.RULES_FILE = os.path.dirname(os.path.abspath(__file__))+'/rules.py'

    # Start the system logger
    if cli_args.LOG_LEVEL:
        CONFIG['LOGGER']['LOG_LEVEL'] = cli_args.LOG_LEVEL
    logger = log.config_logging(CONFIG['LOGGER'])
    logger.info('\n\nCopyright (c) 2013, 2014, 2015, 2016, 2018, 2019, 2020, 2021, 2026\n\tThe Regents of the K0USY Group. All rights reserved.\n')
    logger.debug('(GLOBAL) Logging system started, anything from here on gets logged')

    # Create the name-number mapping dictionaries
    peer_ids, subscriber_ids, talkgroup_ids = mk_aliases(CONFIG)
    
    # Import the ruiles file as a module, and create BRIDGES from it
    spec = importlib.util.spec_from_file_location("module.name", cli_args.RULES_FILE)
    rules_module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(rules_module)
        logger.info('(ROUTER) Routing bridges file found and bridges imported: %s', cli_args.RULES_FILE)
    except (ImportError, FileNotFoundError):
        sys.exit('(ROUTER) TERMINATING: Routing bridges file not found or invalid: {}'.format(cli_args.RULES_FILE))

    # Build the routing rules file
    BRIDGES = make_bridges(rules_module.BRIDGES)

    # Build the per-frame routing lookup indexes from the rules
    BRIDGE_SRC_INDEX, BRIDGE_BY_SYSTEM = index_bridges(BRIDGES)

    # Get rule parameter for private calls
    UNIT = rules_module.UNIT

    # The asyncio entry point: signal handling, reporting, a UDP endpoint for each
    # enabled system, and the rule-timer / stream-trimmer periodic tasks.
    async def async_main():
        global report_server
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def shutdown(signum):
            logger.info('(GLOBAL) SHUTDOWN: CONFBRIDGE IS TERMINATING WITH SIGNAL %s', signum)
            hblink_handler(signum, None)
            logger.info('(GLOBAL) SHUTDOWN: ALL SYSTEM HANDLERS EXECUTED - STOPPING')
            stop_event.set()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, shutdown, sig)

        # INITIALIZE THE REPORTING LOOP
        if CONFIG['REPORTS']['REPORT']:
            report_server = config_reports(CONFIG, bridgeReportFactory)
        else:
            report_server = None
            logger.info('(REPORT) TCP Socket reporting not configured')

        # HBlink instance creation
        logger.info('(GLOBAL) HBlink \'bridge.py\' -- SYSTEM STARTING...')
        for system in CONFIG['SYSTEMS']:
            if CONFIG['SYSTEMS'][system]['ENABLED']:
                if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE':
                    systems[system] = routerOBP(system, CONFIG, report_server)
                else:
                    systems[system] = routerHBP(system, CONFIG, report_server)
                await loop.create_datagram_endpoint(
                    lambda s=systems[system]: s,
                    local_addr=(CONFIG['SYSTEMS'][system]['IP'], CONFIG['SYSTEMS'][system]['PORT']))
                logger.debug('(GLOBAL) %s instance created: %s, %s', CONFIG['SYSTEMS'][system]['MODE'], system, systems[system])

        # Initialize the rule timer (user-activated stuff) and the stream trimmer
        loop.create_task(run_periodic(60, rule_timer_loop, '(ROUTER) rule timer'))
        loop.create_task(run_periodic(1, stream_trimmer_loop, '(ROUTER) stream trimmer'))

        await stop_event.wait()

    asyncio.run(async_main())
