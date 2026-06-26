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
This program does very little on its own. It is intended to be used as a module
to build applications on top of the HomeBrew Repeater Protocol. By itself, it
will only act as a server or outbound client for the systems specified in its
configuration file (usually hblink.cfg). It is ALWAYS best practice to ensure
that this program works stand-alone before troubleshooting any applications that
use it. It has sufficient logging to be used standalone as a troubleshooting
application.
'''

# Specifig functions from modules we need
from binascii import b2a_hex as ahex
from binascii import a2b_hex as bhex
from random import randint
from hashlib import sha256, sha1
from hmac import new as hmac_new, compare_digest
from time import time
from collections import deque
import asyncio
from bisect import bisect_right

# Other files we pull from -- this is mostly for readability and segmentation
import log
import config
from const import *
from dmr_utils3.utils import int_id, bytes_4, try_download, mk_id_dict

# Imports for the reporting server
import json
from reporting_const import *

# The module needs logging logging, but handlers, etc. are controlled by the parent
import logging
logger = logging.getLogger(__name__)

# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Cortney T. Buffington, N0MJS'
__copyright__  = 'Copyright (c) 2016-2019 Cortney T. Buffington, N0MJS and the K0USY Group'
__credits__    = 'Colin Durbridge, G4EML, Steve Zingman, N4IRS; Mike Zingman, N4IRR; Jonathan Naylor, G4KLX; Hans Barthen, DL5DI; Torsten Shultze, DG1HT'
__license__    = 'GNU GPLv3'
__maintainer__ = 'Cort Buffington, N0MJS'
__email__      = 'n0mjs@me.com'

# Global variables used whether we are a module or __main__
systems = {}

# Generic periodic-task runner replacing twisted's task.LoopingCall. Runs _func
# every _interval seconds. Unlike a bare LoopingCall, a raised exception is logged
# and the loop continues, so a transient error can't silently kill the timer.
async def run_periodic(_interval, _func, _name, *_args):
    try:
        while True:
            await asyncio.sleep(_interval)
            try:
                _func(*_args)
            except Exception:
                logger.error('(GLOBAL) Error in periodic task %s', _name, exc_info=True)
    except asyncio.CancelledError:
        raise

# Set up the TCP reporting server and its periodic config push. Must be called
# from within the running asyncio event loop (e.g. from async_main).
def config_reports(_config, _factory):
    def reporting_loop(_logger, _server):
        _logger.debug('(GLOBAL) Periodic reporting loop started')
        _server.send_config()

    logger.info('(GLOBAL) HBlink TCP reporting server configured')

    report_server = _factory(_config)
    report_server.clients = []
    loop = asyncio.get_event_loop()
    loop.create_task(report_server.start(_config['REPORTS']['REPORT_PORT']))
    loop.create_task(run_periodic(_config['REPORTS']['REPORT_INTERVAL'],
                                  reporting_loop, '(GLOBAL) reporting', logger, report_server))

    return report_server


# Shut ourselves down gracefully by disconnecting from servers and repeaters.
def hblink_handler(_signal, _frame):
    for system in systems:
        logger.info('(GLOBAL) SHUTDOWN: DE-REGISTER SYSTEM: %s', system)
        systems[system].dereg()

# Check a supplied ID against the ACL provided. Returns action (True|False) based
# on matching and the action specified. The ACL is the structure produced by
# config.acl_build: (action, singles_frozenset, range_starts, range_ends), where
# the ranges are sorted and disjoint so a single bisect locates any match.
def acl_check(_id, _acl):
    id = int_id(_id)
    action, singles, starts, ends = _acl
    if id in singles:
        return action
    i = bisect_right(starts, id) - 1
    if i >= 0 and id <= ends[i]:
        return action
    return not action


#************************************************
#    OPENBRIDGE CLASS
#************************************************

class OPENBRIDGE(asyncio.DatagramProtocol):
    def __init__(self, _name, _config, _report):
        # Define a few shortcuts to make the rest of the class more readable
        self._CONFIG = _config
        self._system = _name
        self._report = _report
        self._config = self._CONFIG['SYSTEMS'][self._system]
        self._laststrid = deque([], 20)

    def connection_made(self, transport):
        self.transport = transport

    def dereg(self):
        logger.info('(%s) is mode OPENBRIDGE. No De-Registration required, continuing shutdown', self._system)

    def send_system(self, _packet):
        if _packet[:4] == DMRD:
            #_packet = _packet[:11] + self._config['NETWORK_ID'] + _packet[15:]
            _packet = b''.join([_packet[:11], self._config['NETWORK_ID'], _packet[15:]])
            #_packet += hmac_new(self._config['PASSPHRASE'],_packet,sha1).digest()
            _packet = b''.join([_packet, (hmac_new(self._config['PASSPHRASE'],_packet,sha1).digest())])
            self.transport.sendto(_packet, (self._config['TARGET_IP'], self._config['TARGET_PORT']))
            # KEEP THE FOLLOWING COMMENTED OUT UNLESS YOU'RE DEBUGGING DEEPLY!!!!
            # logger.debug('(%s) TX Packet to OpenBridge %s:%s -- %s', self._system, self._config['TARGET_IP'], self._config['TARGET_PORT'], ahex(_packet))
        else:
            logger.error('(%s) OpenBridge system was asked to send non DMRD packet: %s', self._system, _packet)

    def dmrd_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data):
        pass
        #print(int_id(_peer_id), int_id(_rf_src), int_id(_dst_id), int_id(_seq), _slot, _call_type, _frame_type, repr(_dtype_vseq), int_id(_stream_id))

    def datagram_received(self, _packet, _sockaddr):
        # Keep This Line Commented Unless HEAVILY Debugging!
        #logger.debug('(%s) RX packet from %s -- %s', self._system, _sockaddr, ahex(_packet))

        if _packet[:4] == DMRD:    # DMRData -- encapsulated DMR data frame
            _data = _packet[:53]
            _hash = _packet[53:]
            _ckhs = hmac_new(self._config['PASSPHRASE'],_data,sha1).digest()

            if compare_digest(_hash, _ckhs) and _sockaddr == self._config['TARGET_SOCK']:
                _peer_id = _data[11:15]
                _seq = _data[4]
                _rf_src = _data[5:8]
                _dst_id = _data[8:11]
                _bits = _data[15]
                _slot = 2 if (_bits & 0x80) else 1
                #_call_type = 'unit' if (_bits & 0x40) else 'group'
                if _bits & 0x40:
                    _call_type = 'unit'
                elif (_bits & 0x23) == 0x23:
                    _call_type = 'vcsbk'
                else:
                    _call_type = 'group'
                _frame_type = (_bits & 0x30) >> 4
                _dtype_vseq = (_bits & 0xF) # data, 1=voice header, 2=voice terminator; voice, 0=burst A ... 5=burst F
                _stream_id = _data[16:20]
                #logger.debug('(%s) DMRD - Seqence: %s, RF Source: %s, Destination ID: %s', self._system, int_id(_seq), int_id(_rf_src), int_id(_dst_id))

                # Sanity check for OpenBridge -- all calls must be on Slot 1 for Brandmeister or DMR+. Other HBlinks can process timeslot on OPB if the flag is set
                if _slot != 1 and not self._config['BOTH_SLOTS'] and not _call_type == 'unit':
                    logger.error('(%s) OpenBridge packet discarded because it was not received on slot 1. SID: %s, TGID %s', self._system, int_id(_rf_src), int_id(_dst_id))
                    return

                # ACL Processing
                if self._CONFIG['GLOBAL']['USE_ACL']:
                    if not acl_check(_rf_src, self._CONFIG['GLOBAL']['SUB_ACL']):
                        if _stream_id not in self._laststrid:
                            logger.info('(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY GLOBAL ACL', self._system, int_id(_stream_id), int_id(_rf_src))
                            self._laststrid.append(_stream_id)
                        return
                    if _slot == 1 and not acl_check(_dst_id, self._CONFIG['GLOBAL']['TG1_ACL']):
                        if _stream_id not in self._laststrid:
                            logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TS1 ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                            self._laststrid.append(_stream_id)
                        return
                if self._config['USE_ACL']:
                    if not acl_check(_rf_src, self._config['SUB_ACL']):
                        if _stream_id not in self._laststrid:
                            logger.info('(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY SYSTEM ACL', self._system, int_id(_stream_id), int_id(_rf_src))
                            self._laststrid.append(_stream_id)
                        return
                    if not acl_check(_dst_id, self._config['TG1_ACL']):
                        if _stream_id not in self._laststrid:
                            logger.info('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY SYSTEM ACL', self._system, int_id(_stream_id), int_id(_dst_id))
                            self._laststrid.append(_stream_id)
                        return

                # Userland actions -- typically this is the function you subclass for an application
                self.dmrd_received(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data)
            else:
                logger.info('(%s) OpenBridge HMAC failed, packet discarded - OPCODE: %s DATA: %s HMAC LENGTH: %s HMAC: %s', self._system, _packet[:4], repr(_packet[:53]), len(_packet[53:]), repr(_packet[53:])) 


#************************************************
#     HB SERVER CLASS
#************************************************

class HBSYSTEM(asyncio.DatagramProtocol):
    def __init__(self, _name, _config, _report):
        # Define a few shortcuts to make the rest of the class more readable
        self._CONFIG = _config
        self._system = _name
        self._report = _report
        self._config = self._CONFIG['SYSTEMS'][self._system]
        self._laststrid = {1: b'', 2: b''}

        # Define shortcuts and generic function names based on the type of system we are
        if self._config['MODE'] == 'SERVER':
            self._repeaters = self._CONFIG['SYSTEMS'][self._system]['REPEATERS']
            self.send_system = self.send_repeaters
            self.maintenance_loop = self.server_maintenance_loop
            self.datagram_received = self.server_datagram_received
            self.dereg = self.server_dereg

        elif self._config['MODE'] == 'OUTBOUND':
            self._stats = self._config['STATS']
            self.send_system = self.send_server
            self.maintenance_loop = self.outbound_maintenance_loop
            self.datagram_received = self.outbound_datagram_received
            self.dereg = self.outbound_dereg

        self._maintenance_task = None

    def connection_made(self, transport):
        self.transport = transport
        # Set up periodic loop for tracking pings from peers. Run every 'PING_TIME' seconds
        self._maintenance_task = asyncio.create_task(
            run_periodic(self._CONFIG['GLOBAL']['PING_TIME'], self.maintenance_loop,
                         '(%s) maintenance loop' % self._system))

    def connection_lost(self, exc):
        if self._maintenance_task:
            self._maintenance_task.cancel()

    # Aliased in __init__ to maintenance_loop if system is a server
    def server_maintenance_loop(self):
        logger.debug('(%s) Server maintenance loop started', self._system)
        remove_list = []
        for repeater in self._repeaters:
            _this_repeater = self._repeaters[repeater]
            # Check to see if any of the repeaters have been quiet (no ping) longer than allowed
            if _this_repeater['LAST_PING']+(self._CONFIG['GLOBAL']['PING_TIME']*self._CONFIG['GLOBAL']['MAX_MISSED']) < time():
                remove_list.append(repeater)
        for repeater in remove_list:
            logger.info('(%s) Repeater %s (%s) has timed out and is being removed', self._system, self._repeaters[repeater]['CALLSIGN'], self._repeaters[repeater]['RADIO_ID'])
            # Remove any timed out repeaters from the configuration
            del self._CONFIG['SYSTEMS'][self._system]['REPEATERS'][repeater]

    # Aliased in __init__ to maintenance_loop if system is an outbound client
    def outbound_maintenance_loop(self):
        logger.debug('(%s) Outbound maintenance loop started', self._system)
        if self._stats['PING_OUTSTANDING']:
            self._stats['NUM_OUTSTANDING'] += 1
        # If we're not connected, zero out the stats and send a login request RPTL
        if self._stats['CONNECTION'] != 'YES' or self._stats['NUM_OUTSTANDING'] >= self._CONFIG['GLOBAL']['MAX_MISSED']:
            self._stats['PINGS_SENT'] = 0
            self._stats['PINGS_ACKD'] = 0
            self._stats['NUM_OUTSTANDING'] = 0
            self._stats['PING_OUTSTANDING'] = False
            self._stats['CONNECTION'] = 'RPTL_SENT'
            self.send_server(b''.join([RPTL, self._config['RADIO_ID']]))
            logger.info('(%s) Sending login request to server %s:%s', self._system, self._config['SERVER_IP'], self._config['SERVER_PORT'])
        # If we are connected, send a ping to the server and increment the counter
        if self._stats['CONNECTION'] == 'YES':
            self.send_server(b''.join([RPTPING, self._config['RADIO_ID']]))
            logger.debug('(%s) RPTPING Sent to Server. Total Sent: %s, Total Missed: %s, Currently Outstanding: %s', self._system, self._stats['PINGS_SENT'], self._stats['PINGS_SENT'] - self._stats['PINGS_ACKD'], self._stats['NUM_OUTSTANDING'])
            self._stats['PINGS_SENT'] += 1
            self._stats['PING_OUTSTANDING'] = True

    def send_repeaters(self, _packet):
        for _repeater in self._repeaters:
            self.send_repeater(_repeater, _packet)
            #logger.debug('(%s) Packet sent to repeater %s', self._system, self._repeaters[_repeater]['RADIO_ID'])

    def send_repeater(self, _repeater, _packet):
        if _packet[:4] == DMRD:
            _packet = b''.join([_packet[:11], _repeater, _packet[15:]])
        self.transport.sendto(_packet, self._repeaters[_repeater]['SOCKADDR'])
        # KEEP THE FOLLOWING COMMENTED OUT UNLESS YOU'RE DEBUGGING DEEPLY!!!!
        #logger.debug('(%s) TX Packet to %s on port %s: %s', self._repeaters[_repeater]['RADIO_ID'], self._repeaters[_repeater]['IP'], self._repeaters[_repeater]['PORT'], ahex(_packet))

    # Whether this system can currently deliver bridged call traffic. An OUTBOUND
    # client can only forward to its upstream server once it has finished logging
    # in; servers and OpenBridges are always ready (their own send paths fan out
    # to whatever is actually connected). Used by the router to skip dead targets.
    def egress_ready(self):
        if self._config['MODE'] == 'OUTBOUND':
            return self._stats['CONNECTION'] == 'YES'
        return True

    def send_server(self, _packet):
        if _packet[:4] == DMRD:
            # Don't push call traffic to the upstream server until we're logged
            # in -- it would only be dropped. Login/keepalive packets still flow.
            if self._stats['CONNECTION'] != 'YES':
                return
            _packet = b''.join([_packet[:11], self._config['RADIO_ID'], _packet[15:]])
        self.transport.sendto(_packet, self._config['SERVER_SOCKADDR'])
        # KEEP THE FOLLOWING COMMENTED OUT UNLESS YOU'RE DEBUGGING DEEPLY!!!!
        # logger.debug('(%s) TX Packet to %s:%s -- %s', self._system, self._config['SERVER_IP'], self._config['SERVER_PORT'], ahex(_packet))

    def dmrd_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data):
        pass

    # Apply GLOBAL then SYSTEM ACLs to an inbound DMRD frame. Returns True if the
    # call must be dropped (logging each dropped stream only once per slot), else False.
    # Used by both the server and outbound receive paths, which share identical ACL logic.
    def dmrd_acl_check(self, _rf_src, _dst_id, _slot, _stream_id):
        def _drop(_msg, _id):
            if self._laststrid[_slot] != _stream_id:
                logger.info(_msg, self._system, int_id(_stream_id), int_id(_id))
                self._laststrid[_slot] = _stream_id
            return True

        if self._CONFIG['GLOBAL']['USE_ACL']:
            if not acl_check(_rf_src, self._CONFIG['GLOBAL']['SUB_ACL']):
                return _drop('(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY GLOBAL ACL', _rf_src)
            if _slot == 1 and not acl_check(_dst_id, self._CONFIG['GLOBAL']['TG1_ACL']):
                return _drop('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TS1 ACL', _dst_id)
            if _slot == 2 and not acl_check(_dst_id, self._CONFIG['GLOBAL']['TG2_ACL']):
                return _drop('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TS2 ACL', _dst_id)
        if self._config['USE_ACL']:
            if not acl_check(_rf_src, self._config['SUB_ACL']):
                return _drop('(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY SYSTEM ACL', _rf_src)
            if _slot == 1 and not acl_check(_dst_id, self._config['TG1_ACL']):
                return _drop('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY SYSTEM TS1 ACL', _dst_id)
            if _slot == 2 and not acl_check(_dst_id, self._config['TG2_ACL']):
                return _drop('(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY SYSTEM TS2 ACL', _dst_id)
        return False

    def server_dereg(self):
        for _repeater in self._repeaters:
            self.send_repeater(_repeater, MSTCL + _repeater)
            logger.info('(%s) De-Registration sent to Repeater: %s (%s)', self._system, self._repeaters[_repeater]['CALLSIGN'], self._repeaters[_repeater]['RADIO_ID'])

    def outbound_dereg(self):
        self.send_server(RPTCL + self._config['RADIO_ID'])
        logger.info('(%s) De-Registration sent to Server: %s:%s', self._system, self._config['SERVER_SOCKADDR'][0], self._config['SERVER_SOCKADDR'][1])

    # Aliased in __init__ to datagram_received if system is a server
    def server_datagram_received(self, _data, _sockaddr):
        # Keep This Line Commented Unless HEAVILY Debugging!
        # logger.debug('(%s) RX packet from %s -- %s', self._system, _sockaddr, ahex(_data))

        # Extract the command, which is various length, all but one 4 significant characters -- RPTCL
        _command = _data[:4]

        if _command == DMRD:    # DMRData -- encapsulated DMR data frame
            _peer_id = _data[11:15]
            if _peer_id in self._repeaters \
                        and self._repeaters[_peer_id]['CONNECTION'] == 'YES' \
                        and self._repeaters[_peer_id]['SOCKADDR'] == _sockaddr:
                _seq = _data[4]
                _rf_src = _data[5:8]
                _dst_id = _data[8:11]
                _bits = _data[15]
                _slot = 2 if (_bits & 0x80) else 1
                #_call_type = 'unit' if (_bits & 0x40) else 'group'
                if _bits & 0x40:
                    _call_type = 'unit'
                elif (_bits & 0x23) == 0x23:
                    _call_type = 'vcsbk'
                else:
                    _call_type = 'group'
                _frame_type = (_bits & 0x30) >> 4
                _dtype_vseq = (_bits & 0xF) # data, 1=voice header, 2=voice terminator; voice, 0=burst A ... 5=burst F
                _stream_id = _data[16:20]
                #logger.debug('(%s) DMRD - Seqence: %s, RF Source: %s, Destination ID: %s', self._system, _seq, int_id(_rf_src), int_id(_dst_id))
                # ACL Processing
                if self.dmrd_acl_check(_rf_src, _dst_id, _slot, _stream_id):
                    return

                # The basic purpose of a server is to repeat to the repeaters
                if self._config['REPEAT'] == True:
                    pkt = [_data[:11], '', _data[15:]]
                    for _repeater in self._repeaters:
                        if _repeater != _peer_id:
                            pkt[1] = _repeater
                            self.transport.sendto(b''.join(pkt), self._repeaters[_repeater]['SOCKADDR'])
                            #logger.debug('(%s) Packet on TS%s from %s (%s) for destination ID %s repeated to repeater: %s (%s) [Stream ID: %s]', self._system, _slot, self._repeaters[_peer_id]['CALLSIGN'], int_id(_peer_id), int_id(_dst_id), self._repeaters[_repeater]['CALLSIGN'], int_id(_repeater), int_id(_stream_id))


                # Userland actions -- typically this is the function you subclass for an application
                self.dmrd_received(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data)

        elif _command == RPTL:    # RPTLogin -- a repeater wants to login
            _peer_id = _data[4:8]
            # Check to see if we've reached the maximum number of allowed repeaters
            if len(self._repeaters) < self._config['MAX_REPEATERS']:
                # Check for valid Radio ID
                if acl_check(_peer_id, self._CONFIG['GLOBAL']['REG_ACL']) and acl_check(_peer_id, self._config['REG_ACL']):
                    # Build the configuration data structure for the repeater
                    self._repeaters.update({_peer_id: {
                        'CONNECTION': 'RPTL-RECEIVED',
                        'CONNECTED': time(),
                        'PINGS_RECEIVED': 0,
                        'LAST_PING': time(),
                        'SOCKADDR': _sockaddr,
                        'IP': _sockaddr[0],
                        'PORT': _sockaddr[1],
                        'SALT': randint(0,0xFFFFFFFF),
                        'RADIO_ID': str(int(ahex(_peer_id), 16)),
                        'CALLSIGN': '',
                        'RX_FREQ': '',
                        'TX_FREQ': '',
                        'TX_POWER': '',
                        'COLORCODE': '',
                        'LATITUDE': '',
                        'LONGITUDE': '',
                        'HEIGHT': '',
                        'LOCATION': '',
                        'DESCRIPTION': '',
                        'SLOTS': '',
                        'URL': '',
                        'SOFTWARE_ID': '',
                        'PACKAGE_ID': '',
                    }})
                    logger.info('(%s) Repeater Logging in with Radio ID: %s, %s:%s', self._system, int_id(_peer_id), _sockaddr[0], _sockaddr[1])
                    _salt_str = bytes_4(self._repeaters[_peer_id]['SALT'])
                    self.send_repeater(_peer_id, b''.join([RPTACK, _salt_str]))
                    self._repeaters[_peer_id]['CONNECTION'] = 'CHALLENGE_SENT'
                    logger.info('(%s) Sent Challenge Response to %s for login: %s', self._system, int_id(_peer_id), self._repeaters[_peer_id]['SALT'])
                else:
                    self.transport.sendto(b''.join([MSTNAK, _peer_id]), _sockaddr)
                    logger.warning('(%s) Invalid Login from %s Radio ID: %s Denied by Registation ACL', self._system, _sockaddr[0], int_id(_peer_id))
            else:
                self.transport.sendto(b''.join([MSTNAK, _peer_id]), _sockaddr)
                logger.warning('(%s) Registration denied from Radio ID: %s Maximum number of repeaters exceeded', self._system, int_id(_peer_id))

        elif _command == RPTK:    # Repeater has answered our login challenge
            _peer_id = _data[4:8]
            if _peer_id in self._repeaters \
                        and self._repeaters[_peer_id]['CONNECTION'] == 'CHALLENGE_SENT' \
                        and self._repeaters[_peer_id]['SOCKADDR'] == _sockaddr:
                _this_repeater = self._repeaters[_peer_id]
                _this_repeater['LAST_PING'] = time()
                _sent_hash = _data[8:]
                _salt_str = bytes_4(_this_repeater['SALT'])
                _calc_hash = bhex(sha256(_salt_str+self._config['PASSPHRASE']).hexdigest())
                if _sent_hash == _calc_hash:
                    _this_repeater['CONNECTION'] = 'WAITING_CONFIG'
                    self.send_repeater(_peer_id, b''.join([RPTACK, _peer_id]))
                    logger.info('(%s) Repeater %s has completed the login exchange successfully', self._system, _this_repeater['RADIO_ID'])
                else:
                    logger.info('(%s) Repeater %s has FAILED the login exchange successfully', self._system, _this_repeater['RADIO_ID'])
                    self.transport.sendto(b''.join([MSTNAK, _peer_id]), _sockaddr)
                    del self._repeaters[_peer_id]
            else:
                self.transport.sendto(b''.join([MSTNAK, _peer_id]), _sockaddr)
                logger.warning('(%s) Login challenge from Radio ID that has not logged in: %s', self._system, int_id(_peer_id))

        elif _command == RPTC:    # Repeater is sending it's configuraiton OR disconnecting
            if _data[:5] == RPTCL:    # Disconnect command
                _peer_id = _data[5:9]
                if _peer_id in self._repeaters \
                            and self._repeaters[_peer_id]['CONNECTION'] == 'YES' \
                            and self._repeaters[_peer_id]['SOCKADDR'] == _sockaddr:
                    logger.info('(%s) Repeater is closing down: %s (%s)', self._system, self._repeaters[_peer_id]['CALLSIGN'], int_id(_peer_id))
                    self.transport.sendto(b''.join([MSTNAK, _peer_id]), _sockaddr)
                    del self._repeaters[_peer_id]

            else:
                _peer_id = _data[4:8]      # Configure Command
                if _peer_id in self._repeaters \
                            and self._repeaters[_peer_id]['CONNECTION'] == 'WAITING_CONFIG' \
                            and self._repeaters[_peer_id]['SOCKADDR'] == _sockaddr:
                    _this_repeater = self._repeaters[_peer_id]
                    _this_repeater['CONNECTION'] = 'YES'
                    _this_repeater['CONNECTED'] = time()
                    _this_repeater['LAST_PING'] = time()
                    _this_repeater['CALLSIGN'] = _data[8:16]
                    _this_repeater['RX_FREQ'] = _data[16:25]
                    _this_repeater['TX_FREQ'] =  _data[25:34]
                    _this_repeater['TX_POWER'] = _data[34:36]
                    _this_repeater['COLORCODE'] = _data[36:38]
                    _this_repeater['LATITUDE'] = _data[38:46]
                    _this_repeater['LONGITUDE'] = _data[46:55]
                    _this_repeater['HEIGHT'] = _data[55:58]
                    _this_repeater['LOCATION'] = _data[58:78]
                    _this_repeater['DESCRIPTION'] = _data[78:97]
                    _this_repeater['SLOTS'] = _data[97:98]
                    _this_repeater['URL'] = _data[98:222]
                    _this_repeater['SOFTWARE_ID'] = _data[222:262]
                    _this_repeater['PACKAGE_ID'] = _data[262:302]

                    self.send_repeater(_peer_id, b''.join([RPTACK, _peer_id]))
                    logger.info('(%s) Repeater %s (%s) has sent repeater configuration', self._system, _this_repeater['CALLSIGN'], _this_repeater['RADIO_ID'])
                else:
                    self.transport.sendto(b''.join([MSTNAK, _peer_id]), _sockaddr)
                    logger.warning('(%s) Repeater info from Radio ID that has not logged in: %s', self._system, int_id(_peer_id))

        elif _command == DMRC:
            _peer_id = _data[4:8]
            # Check to see if we've reached the maximum number of allowed repeaters
            if len(self._repeaters) < self._config['MAX_REPEATERS']:
                # Check for valid Radio ID
                if acl_check(_peer_id, self._CONFIG['GLOBAL']['REG_ACL']) and acl_check(_peer_id, self._config['REG_ACL']):
                    if _peer_id in self._repeaters:
                        #    and self._repeaters[_peer_id][CONNECTED] == 'YES' \
                        #    and self._repeaters[_peer_id][SOCKADDR] == _sockaddr:
                        self._repeaters[_peer_id]['PINGS_RECEIVED'] += 1
                        self._repeaters[_peer_id]['LAST_PING'] = time()
                        logger.debug('(%s) Received DMRC update from repeater %s (%s)', self._system, self._repeaters[_peer_id]['CALLSIGN'], int_id(_peer_id))
                    else:
                        # Build the configuration data structure for the repeater
                        self._repeaters.update({_peer_id: {
                            'CONNECTION': 'YES',
                            'CONNECTED': time(),
                            'PINGS_RECEIVED': 0,
                            'LAST_PING': time(),
                            'SOCKADDR': _sockaddr,
                            'IP': _sockaddr[0],
                            'PORT': _sockaddr[1],
                            'SALT': 0,
                            'RADIO_ID': str(int(ahex(_peer_id), 16)),
                            'CALLSIGN': _data[8:16],
                            'RX_FREQ': _data[16:25],
                            'TX_FREQ': _data[25:34],
                            'TX_POWER': _data[34:36],
                            'COLORCODE': _data[36:38],
                            'LATITUDE': b'',
                            'LONGITUDE': b'',
                            'HEIGHT': b'',
                            'LOCATION': b'',
                            'DESCRIPTION': '',
                            'SLOTS': bytes([_data[38]]),
                            'URL': b'',
                            'SOFTWARE_ID': _data[39:79],
                            'PACKAGE_ID': _data[79:119],
                        }})

                        logger.info('(%s) DMRC login from %s. DMRC HBP PDU: %s', self._system, int_id(_peer_id), ahex(_data))
                else:
                    self.transport.sendto(b''.join([MSTNAK, _peer_id]), _sockaddr)
                    logger.warning('(%s) Invalid DMRC Login or Update from %s Radio ID: %s Denied by Registation ACL', self._system, _sockaddr[0], int_id(_peer_id))
            else:
                self.transport.sendto(b''.join([MSTNAK, _peer_id]), _sockaddr)
                logger.warning('(%s) Invalid DMRC Login from %s Radio ID: %s Denied, Maximum number of repeaters exceeded', self._system, _sockaddr[0], int_id(_peer_id))

        elif _command == RPTP:    # RPTPing -- repeater is pinging us
                _peer_id = _data[7:11]
                if _peer_id in self._repeaters \
                            and self._repeaters[_peer_id]['CONNECTION'] == "YES" \
                            and self._repeaters[_peer_id]['SOCKADDR'] == _sockaddr:
                    self._repeaters[_peer_id]['PINGS_RECEIVED'] += 1
                    self._repeaters[_peer_id]['LAST_PING'] = time()
                    self.send_repeater(_peer_id, b''.join([MSTPONG, _peer_id]))
                    logger.debug('(%s) Received and answered RPTPING from repeater %s (%s)', self._system, self._repeaters[_peer_id]['CALLSIGN'], int_id(_peer_id))
                else:
                    self.transport.sendto(b''.join([MSTNAK, _peer_id]), _sockaddr)
                    logger.warning('(%s) Ping from Radio ID that is not logged in: %s', self._system, int_id(_peer_id))

        elif _command == RPTO:
            _peer_id = _data[4:8]
            if _peer_id in self._repeaters \
                        and self._repeaters[_peer_id]['CONNECTION'] == 'YES' \
                        and self._repeaters[_peer_id]['SOCKADDR'] == _sockaddr:
                logger.info('(%s) Repeater %s (%s) has sent options: %s', self._system, self._repeaters[_peer_id]['CALLSIGN'], int_id(_peer_id), _data[8:])
                self.transport.sendto(b''.join([RPTACK, _peer_id]), _sockaddr)

        elif _command == DMRA:
            _peer_id = _data[4:8]
            logger.info('(%s) Recieved DMR Talker Alias from repeater %s, subscriber %s', self._system, self._repeaters[_peer_id]['CALLSIGN'], int_id(_rf_src))

        else:
            logger.error('(%s) Unrecognized command. Raw HBP PDU: %s', self._system, ahex(_data))

    # Aliased in __init__ to datagram_received if system is an outbound client
    def outbound_datagram_received(self, _data, _sockaddr):
        # Keep This Line Commented Unless HEAVILY Debugging!
        # logger.debug('(%s) RX packet from %s -- %s', self._system, _sockaddr, ahex(_data))

        # Validate that we receveived this packet from the server - security check!
        if self._config['SERVER_SOCKADDR'] == _sockaddr:
            # Extract the command, which is various length, but only 4 significant characters
            _command = _data[:4]
            if   _command == DMRD:    # DMRData -- encapsulated DMR data frame

                _peer_id = _data[11:15]
                if self._config['LOOSE'] or _peer_id == self._config['RADIO_ID']: # Validate the Radio_ID unless using loose validation
                    _seq = _data[4:5]
                    _rf_src = _data[5:8]
                    _dst_id = _data[8:11]
                    _bits = _data[15]
                    _slot = 2 if (_bits & 0x80) else 1
                    #_call_type = 'unit' if (_bits & 0x40) else 'group'
                    if _bits & 0x40:
                        _call_type = 'unit'
                    elif (_bits & 0x23) == 0x23:
                        _call_type = 'vcsbk'
                    else:
                        _call_type = 'group'
                    _frame_type = (_bits & 0x30) >> 4
                    _dtype_vseq = (_bits & 0xF) # data, 1=voice header, 2=voice terminator; voice, 0=burst A ... 5=burst F
                    _stream_id = _data[16:20]
                    #logger.debug('(%s) DMRD - Sequence: %s, RF Source: %s, Destination ID: %s', self._system, int_id(_seq), int_id(_rf_src), int_id(_dst_id))

                    # ACL Processing
                    if self.dmrd_acl_check(_rf_src, _dst_id, _slot, _stream_id):
                        return


                    # Userland actions -- typically this is the function you subclass for an application
                    self.dmrd_received(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data)

            elif _command == MSTN:    # Actually MSTNAK -- a NACK from the server
                _peer_id = _data[6:10]
                if self._config['LOOSE'] or _peer_id == self._config['RADIO_ID']: # Validate the Radio_ID unless using loose validation
                    logger.warning('(%s) MSTNAK Received. Resetting connection to the Server.', self._system)
                    self._stats['CONNECTION'] = 'NO' # Disconnect ourselves and re-register
                    self._stats['CONNECTED'] = time()

            elif _command == RPTA:    # Actually RPTACK -- an ACK from the server
                # Depending on the state, an RPTACK means different things, in each clause, we check and/or set the state
                if self._stats['CONNECTION'] == 'RPTL_SENT': # If we've sent a login request...
                    _login_int32 = _data[6:10]
                    logger.info('(%s) Repeater Login ACK Received with 32bit ID: %s', self._system, int_id(_login_int32))
                    _pass_hash = sha256(b''.join([_login_int32, self._config['PASSPHRASE']])).hexdigest()
                    _pass_hash = bhex(_pass_hash)
                    self.send_server(b''.join([RPTK, self._config['RADIO_ID'], _pass_hash]))
                    self._stats['CONNECTION'] = 'AUTHENTICATED'

                elif self._stats['CONNECTION'] == 'AUTHENTICATED': # If we've sent the login challenge...
                    _peer_id = _data[6:10]
                    if self._config['LOOSE'] or _peer_id == self._config['RADIO_ID']: # Validate the Radio_ID unless using loose validation
                        logger.info('(%s) Repeater Authentication Accepted', self._system)
                        _config_packet =  b''.join([\
                                              self._config['RADIO_ID'],\
                                              self._config['CALLSIGN'],\
                                              self._config['RX_FREQ'],\
                                              self._config['TX_FREQ'],\
                                              self._config['TX_POWER'],\
                                              self._config['COLORCODE'],\
                                              self._config['LATITUDE'],\
                                              self._config['LONGITUDE'],\
                                              self._config['HEIGHT'],\
                                              self._config['LOCATION'],\
                                              self._config['DESCRIPTION'],\
                                              self._config['SLOTS'],\
                                              self._config['URL'],\
                                              self._config['SOFTWARE_ID'],\
                                              self._config['PACKAGE_ID']\
                                          ])

                        self.send_server(b''.join([RPTC, _config_packet]))
                        self._stats['CONNECTION'] = 'CONFIG-SENT'
                        logger.info('(%s) Repeater Configuration Sent', self._system)
                    else:
                        self._stats['CONNECTION'] = 'NO'
                        logger.error('(%s) Server ACK Contained wrong ID - Connection Reset', self._system)

                elif self._stats['CONNECTION'] == 'CONFIG-SENT': # If we've sent out configuration to the server
                    _peer_id = _data[6:10]
                    if self._config['LOOSE'] or _peer_id == self._config['RADIO_ID']: # Validate the Radio_ID unless using loose validation
                        logger.info('(%s) Repeater Configuration Accepted', self._system)
                        if self._config['OPTIONS']:
                            self.send_server(b''.join([RPTO, self._config['RADIO_ID'], self._config['OPTIONS']]))
                            self._stats['CONNECTION'] = 'OPTIONS-SENT'
                            logger.info('(%s) Sent options: (%s)', self._system, self._config['OPTIONS'])
                        else:
                            self._stats['CONNECTION'] = 'YES'
                            self._stats['CONNECTED'] = time()
                            logger.info('(%s) Connection to Server Completed', self._system)

                    else:
                        self._stats['CONNECTION'] = 'NO'
                        logger.error('(%s) Server ACK Contained wrong ID - Connection Reset', self._system)

                elif self._stats['CONNECTION'] == 'OPTIONS-SENT': # If we've sent out options to the server
                    _peer_id = _data[6:10]
                    if self._config['LOOSE'] or _peer_id == self._config['RADIO_ID']: # Validate the Radio_ID unless using loose validation
                        logger.info('(%s) Repeater Options Accepted', self._system)
                        self._stats['CONNECTION'] = 'YES'
                        self._stats['CONNECTED'] = time()
                        logger.info('(%s) Connection to Server Completed with options', self._system)
                    else:
                        self._stats['CONNECTION'] = 'NO'
                        logger.error('(%s) Server ACK Contained wrong ID - Connection Reset', self._system)

            elif _command == MSTP:    # Actually MSTPONG -- a reply to RPTPING (sent by outbound client)
                _peer_id = _data[7:11]
                if self._config['LOOSE'] or _peer_id == self._config['RADIO_ID']: # Validate the Radio_ID unless using loose validation
                    self._stats['PING_OUTSTANDING'] = False
                    self._stats['NUM_OUTSTANDING'] = 0
                    self._stats['PINGS_ACKD'] += 1
                    logger.debug('(%s) MSTPONG Received. Pongs Since Connected: %s', self._system, self._stats['PINGS_ACKD'])

            elif _command == MSTC:    # Actually MSTCL -- notify us the server is closing down
                _peer_id = _data[5:9]
                if self._config['LOOSE'] or _peer_id == self._config['RADIO_ID']: # Validate the Radio_ID unless using loose validation
                    self._stats['CONNECTION'] = 'NO'
                    logger.info('(%s) MSTCL Recieved', self._system)

            else:
                logger.error('(%s) Received an invalid command in packet: %s', self._system, ahex(_data))

#
# Socket-based reporting section
#
# Per-connection reporting protocol. The wire format is newline-delimited JSON
# (NDJSON): each message is one json.dumps(...) object terminated by '\n'. A
# consumer (such as the dashboard) connects and passively receives a config
# snapshot followed by a live stream of events.
class report(asyncio.Protocol):
    def __init__(self, factory):
        self._factory = factory
        self.transport = None

    def connection_made(self, transport):
        peername = transport.get_extra_info('peername')
        host = peername[0] if peername else ''
        clients = self._factory._config['REPORTS']['REPORT_CLIENTS']
        if host in clients or '*' in clients:
            self.transport = transport
            self._factory.clients.append(self)
            logger.info('(REPORT) reporting client connected: %s', peername)
            self._factory.send_initial(self)
        else:
            logger.error('(REPORT) Invalid report client connection attempt from: %s', peername)
            transport.close()

    def connection_lost(self, exc):
        if self in self._factory.clients:
            self._factory.clients.remove(self)
        logger.info('(REPORT) reporting client disconnected')

    def data_received(self, data):
        pass    # consumers are passive; nothing to read from them

    def send_raw(self, _line):
        self.transport.write(_line)


# Build a JSON-serializable view of the SYSTEMS config for consumers. Bytes
# fields are decoded and DMR IDs converted to integers; secrets (passphrases) and
# internal ACL structures are intentionally omitted.
def json_systems(_systems):
    def s(_v):
        return _v.decode('utf-8', errors='ignore').strip() if isinstance(_v, (bytes, bytearray)) else _v

    out = {}
    for name, c in _systems.items():
        mode = c['MODE']
        view = {'MODE': mode, 'ENABLED': c['ENABLED']}
        if mode == 'SERVER':
            view['REPEAT'] = c.get('REPEAT', False)
            view['MAX_REPEATERS'] = c.get('MAX_REPEATERS')
            repeaters = {}
            for pid, p in c['REPEATERS'].items():
                repeaters[str(int_id(pid))] = {
                    'RADIO_ID':   int_id(pid),
                    'CALLSIGN':   s(p.get('CALLSIGN', '')),
                    'LOCATION':   s(p.get('LOCATION', '')),
                    'IP':         p.get('IP', ''),
                    'PORT':       p.get('PORT', ''),
                    'CONNECTED':  p.get('CONNECTED', 0),
                    'CONNECTION': p.get('CONNECTION', ''),
                    'LAST_PING':  p.get('LAST_PING', 0),
                    'RX_FREQ':    s(p.get('RX_FREQ', '')),
                    'TX_FREQ':    s(p.get('TX_FREQ', '')),
                    'SLOTS':      s(p.get('SLOTS', '')),
                }
            view['REPEATERS'] = repeaters
        elif mode == 'OUTBOUND':
            stats = c.get('STATS', {})
            view.update({
                'RADIO_ID':    int_id(c['RADIO_ID']),
                'CALLSIGN':     s(c['CALLSIGN']),
                'LOCATION':     s(c['LOCATION']),
                'SERVER_IP':    c['SERVER_IP'],
                'SERVER_PORT':  c['SERVER_PORT'],
                'SLOTS':        s(c['SLOTS']),
                'STATS': {
                    'CONNECTION':      stats.get('CONNECTION', ''),
                    'CONNECTED':       stats.get('CONNECTED', 0),
                    'NUM_OUTSTANDING': stats.get('NUM_OUTSTANDING', 0),
                },
            })
        elif mode == 'OPENBRIDGE':
            view.update({
                'NETWORK_ID':  int_id(c['NETWORK_ID']),
                'TARGET_IP':   c['TARGET_IP'],
                'TARGET_PORT': c['TARGET_PORT'],
            })
        out[name] = view
    return out


# Manages the set of connected reporting clients. Doubles as the asyncio protocol
# factory (it is callable) passed to loop.create_server().
class reportFactory:
    def __init__(self, config):
        self._config = config
        self.clients = []

    def __call__(self):
        return report(self)

    async def start(self, port):
        loop = asyncio.get_running_loop()
        self._server = await loop.create_server(self, None, port)
        logger.info('(REPORT) HBlink reporting server listening on port %s', port)

    def send_clients(self, _obj):
        line = (json.dumps(_obj) + '\n').encode('utf-8')
        for client in self.clients:
            client.send_raw(line)

    def send_to(self, _client, _obj):
        _client.send_raw((json.dumps(_obj) + '\n').encode('utf-8'))

    def config_event(self):
        return {
            'type': 'config',
            'systems': json_systems(self._config['SYSTEMS']),
            'ping_time': self._config['GLOBAL'].get('PING_TIME', 5),
            'max_missed': self._config['GLOBAL'].get('MAX_MISSED', 3),
        }

    def send_config(self):
        self.send_clients(self.config_event())

    # Sent to a single client immediately after it connects so it has full state.
    def send_initial(self, _client):
        self.send_to(_client, self.config_event())


# ID ALIAS CREATION
# Download
def mk_aliases(_config):
    if _config['ALIASES']['TRY_DOWNLOAD'] == True:
        # Try updating peer aliases file
        result = try_download(_config['ALIASES']['PATH'], _config['ALIASES']['PEER_FILE'], _config['ALIASES']['PEER_URL'], _config['ALIASES']['STALE_TIME'])
        logger.info('(GLOBAL) %s', result)
        # Try updating subscriber aliases file
        result = try_download(_config['ALIASES']['PATH'], _config['ALIASES']['SUBSCRIBER_FILE'], _config['ALIASES']['SUBSCRIBER_URL'], _config['ALIASES']['STALE_TIME'])
        logger.info('(GLOBAL) %s', result)

    # Make Dictionaries
    peer_ids = mk_id_dict(_config['ALIASES']['PATH'], _config['ALIASES']['PEER_FILE'])
    if peer_ids:
        logger.info('(GLOBAL) ID ALIAS MAPPER: peer_ids dictionary is available')

    subscriber_ids = mk_id_dict(_config['ALIASES']['PATH'], _config['ALIASES']['SUBSCRIBER_FILE'])
    if subscriber_ids:
        logger.info('(GLOBAL) ID ALIAS MAPPER: subscriber_ids dictionary is available')

    talkgroup_ids = mk_id_dict(_config['ALIASES']['PATH'], _config['ALIASES']['TGID_FILE'])
    if talkgroup_ids:
        logger.info('(GLOBAL) ID ALIAS MAPPER: talkgroup_ids dictionary is available')

    return peer_ids, subscriber_ids, talkgroup_ids

#************************************************
#      MAIN PROGRAM LOOP STARTS HERE
#************************************************

if __name__ == '__main__':
    # Python modules we need
    import argparse
    import sys
    import os
    import signal

    # Change the current directory to the location of the application
    os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))

    # CLI argument parser - handles picking up the config file from the command line, and sending a "help" message
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', action='store', dest='CONFIG_FILE', help='/full/path/to/config.file (usually hblink.cfg)')
    parser.add_argument('-l', '--logging', action='store', dest='LOG_LEVEL', help='Override config file logging level.')
    cli_args = parser.parse_args()

    # Ensure we have a path for the config file, if one wasn't specified, then use the execution directory
    if not cli_args.CONFIG_FILE:
        cli_args.CONFIG_FILE = os.path.dirname(os.path.abspath(__file__))+'/hblink.cfg'

    # Call the external routine to build the configuration dictionary
    CONFIG = config.build_config(cli_args.CONFIG_FILE)

    # Call the external routing to start the system logger
    if cli_args.LOG_LEVEL:
        CONFIG['LOGGER']['LOG_LEVEL'] = cli_args.LOG_LEVEL
    logger = log.config_logging(CONFIG['LOGGER'])
    logger.info('\n\nCopyright (c) 2013, 2014, 2015, 2016, 2018, 2019, 2020, 2021, 2026\n\tThe Regents of the K0USY Group. All rights reserved.\n')
    logger.debug('(GLOBAL) Logging system started, anything from here on gets logged')

    peer_ids, subscriber_ids, talkgroup_ids = mk_aliases(CONFIG)

    # The asyncio entry point: install signal handlers, start the reporting
    # server, bind a UDP endpoint for each enabled system, then wait for shutdown.
    async def async_main():
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def shutdown(signum):
            logger.info('(GLOBAL) SHUTDOWN: HBLINK IS TERMINATING WITH SIGNAL %s', signum)
            hblink_handler(signum, None)
            logger.info('(GLOBAL) SHUTDOWN: ALL SYSTEM HANDLERS EXECUTED - STOPPING')
            stop_event.set()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown, sig)

        # INITIALIZE THE REPORTING LOOP
        if CONFIG['REPORTS']['REPORT']:
            report_server = config_reports(CONFIG, reportFactory)
        else:
            report_server = None
            logger.info('(REPORT) TCP Socket reporting not configured')

        # HBlink instance creation
        logger.info('(GLOBAL) HBlink \'HBlink.py\' -- SYSTEM STARTING...')
        for system in CONFIG['SYSTEMS']:
            if CONFIG['SYSTEMS'][system]['ENABLED']:
                if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE':
                    systems[system] = OPENBRIDGE(system, CONFIG, report_server)
                else:
                    systems[system] = HBSYSTEM(system, CONFIG, report_server)
                await loop.create_datagram_endpoint(
                    lambda s=systems[system]: s,
                    local_addr=(CONFIG['SYSTEMS'][system]['IP'], CONFIG['SYSTEMS'][system]['PORT']))
                logger.debug('(GLOBAL) %s instance created: %s, %s', CONFIG['SYSTEMS'][system]['MODE'], system, systems[system])

        await stop_event.wait()

    asyncio.run(async_main())
