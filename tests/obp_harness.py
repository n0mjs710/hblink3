#!/usr/bin/env python
#
# Live OpenBridge (OBP) traffic harness for HBlink3.
#
# Unlike the HomeBrew peer harness (tests/live_harness.py), OpenBridge has no
# login handshake: each DMRD frame is a 53-byte DMR payload followed by a 20-byte
# HMAC-SHA1(passphrase, payload), and the receiver only accepts packets whose
# SOURCE address matches its configured TARGET_SOCK. So this harness BINDS the
# local socket to the OBP's TARGET_PORT and sends to the OBP's listen PORT.
#
# OpenBridge is NOT timeslot-bound: it carries an unlimited number of concurrent
# streams keyed by stream-id. --streams N fires N overlapping calls at once so
# you can see several simultaneous OBP activities (and, via bridging, the peer
# OBP sending them all out concurrently) -- in contrast to the slot-bound HBP
# side where only one call per slot survives.
#
# Example (matches the demo wiring this session set up):
#   tests/obp_harness.py --bind-port 62045 --obp-port 62035 --streams 3 --calls 2
#   tests/obp_harness.py --bind-port 62046 --obp-port 62036 --streams 3 --calls 2

import argparse
import random
import socket
import time
from hashlib import sha1
from hmac import new as hmac_new

DMRD = b'DMRD'
_FT_VOICE = 0
_FT_DATA_SYNC = 2
_VHEAD = 1
_VTERM = 2


def b3(n): return n.to_bytes(3, 'big')
def b4(n): return n.to_bytes(4, 'big')


def mk_obp(seq, rf_src, dst, peer, ft, vseq, sid, passphrase):
    """A 73-byte OpenBridge frame: 53-byte DMRD payload + 20-byte HMAC-SHA1.
    OpenBridge calls are always slot 1 (no slot bit)."""
    dmrpkt = bytes((i * 5 + 1) & 0xFF for i in range(33))
    bits = (ft & 0x3) << 4 | (vseq & 0xF)              # group, slot 1
    data = b''.join([DMRD, bytes([seq & 0xFF]), b3(rf_src), b3(dst), b4(peer),
                     bytes([bits]), b4(sid), dmrpkt])  # 53 bytes
    return data + hmac_new(passphrase, data, sha1).digest()


def main():
    ap = argparse.ArgumentParser(description='Live OpenBridge traffic harness for HBlink3')
    ap.add_argument('--server-ip', default='127.0.0.1')
    ap.add_argument('--obp-port', type=int, required=True, help="the OBP system's listen PORT")
    ap.add_argument('--bind-port', type=int, required=True, help="must equal that OBP's TARGET_PORT")
    ap.add_argument('--passphrase', default='password')
    ap.add_argument('--peer', type=int, default=3120, help='source network id (DMRD peer field)')
    ap.add_argument('--src', type=int, default=3120001, help='RF source (subscriber) id')
    ap.add_argument('--tgid', type=int, default=8)
    ap.add_argument('--streams', type=int, default=3, help='concurrent overlapping streams (OBP is not slot-bound)')
    ap.add_argument('--calls', type=int, default=3, help='number of call rounds')
    ap.add_argument('--duration', type=float, default=2.0, help='seconds of audio per call')
    ap.add_argument('--gap', type=float, default=1.5, help='idle seconds between rounds')
    ap.add_argument('--frame-ms', type=float, default=60.0)
    ap.add_argument('--no-term', action='store_true', help='lost tail: stop with no terminator')
    ap.add_argument('--tail-vseq', type=int, default=None, choices=range(6))
    args = ap.parse_args()

    passphrase = args.passphrase.encode()
    dst = (args.server_ip, args.obp_port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('127.0.0.1', args.bind_port))          # source addr must match OBP TARGET_SOCK
    sock.setblocking(False)
    print('[obp] sending to OBP %s from :%d (passphrase %r)' % (dst, args.bind_port, args.passphrase), flush=True)

    superframes = max(1, round(args.duration / (6 * args.frame_ms / 1000.0)))
    terminate = not args.no_term
    egress = [0]

    def drain():
        try:
            while True:
                sock.recv(2048)
                egress[0] += 1
        except BlockingIOError:
            pass

    sid = random.randint(0x10000000, 0xF0000000)
    for c in range(args.calls):
        streams = []
        for k in range(args.streams):
            sid += 1
            tail = args.tail_vseq if args.tail_vseq is not None else random.randint(0, 5)
            streams.append({'sid': sid, 'src': args.src + k, 'seq': 0, 'tail': tail})
        print('[obp] round %d/%d: %d concurrent streams on TGID %d %s'
              % (c + 1, args.calls, args.streams, args.tgid,
                 'TERMINATED' if terminate else 'LOST TAIL'), flush=True)

        def tx(st, ft, vseq):
            sock.sendto(mk_obp(st['seq'], st['src'], args.tgid, args.peer, ft, vseq, st['sid'], passphrase), dst)
            st['seq'] = (st['seq'] + 1) & 0xFF

        for st in streams:
            tx(st, _FT_DATA_SYNC, _VHEAD)
        for sf in range(superframes):
            for vseq in range(6):
                for st in streams:
                    if terminate or sf < superframes - 1 or vseq <= st['tail']:
                        tx(st, _FT_VOICE, vseq)
                time.sleep(args.frame_ms / 1000.0)
                drain()
        if terminate:
            for st in streams:
                tx(st, _FT_DATA_SYNC, _VTERM)
        if c + 1 < args.calls:
            time.sleep(args.gap)
            drain()
    drain()
    print('[obp] done (received %d egress frames back)' % egress[0], flush=True)


if __name__ == '__main__':
    main()
