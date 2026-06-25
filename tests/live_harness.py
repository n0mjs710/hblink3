#!/usr/bin/env python
#
# Live UDP traffic harness: logs into a running HBlink3 MASTER as a HomeBrew
# peer and injects DMRD voice streams over a real socket. Unlike tests/harness.py
# (which drives dmrd_received in-process), this exercises the full server path --
# socket -> master handshake -> ACL -> bridge routing -> contention -> reporting --
# so it can drive the dashboard with realistic traffic.
#
# It is deliberately CONTROLLABLE so we can tell expected contention from a bug:
#
#   default            one clean, fully-terminated call at a time -> expect ZERO
#                      collisions in the bridge log.
#   --concurrent K     fire K overlapping streams (distinct stream-ids) on the
#                      same TGID/slot at once -> SHOULD collide (correct DMR).
#   --no-term          omit the voice terminator (simulate a dropped tail) ->
#                      leaves the target slot "busy"; the NEXT call should then
#                      collide until the stream times out. This is the stuck-
#                      stream condition the supersession / 2s-timeout fix targets.
#
# Usage (against the sample hblink.cfg MASTER-1):
#   venv/bin/python tests/live_harness.py --calls 5
#   venv/bin/python tests/live_harness.py --concurrent 3 --calls 3
#   venv/bin/python tests/live_harness.py --no-term --calls 3 --gap 1

import argparse
import random
import socket
import sys
import time
from binascii import a2b_hex as bhex
from hashlib import sha256

# --- wire constants (mirror const.py) ---
DMRD    = b'DMRD'
RPTL    = b'RPTL'
RPTK    = b'RPTK'
RPTC    = b'RPTC'
RPTACK  = b'RPTACK'
MSTNAK  = b'MSTNAK'
RPTPING = b'RPTPING'

_FT_VOICE = 0       # HBPF_VOICE
_FT_DATA_SYNC = 2   # HBPF_DATA_SYNC
_VHEAD = 1          # HBPF_SLT_VHEAD
_VTERM = 2          # HBPF_SLT_VTERM


def mk_dmrd(seq, rf_src, dst, peer, slot, call_type, frame_type, dtype_vseq, stream_id):
    """Assemble a 55-byte DMRD packet (33-byte payload + 2 BER/RSSI bytes)."""
    dmrpkt = bytes((i * 5 + 1) & 0xFF for i in range(33))
    bits = 0
    if slot == 2:
        bits |= 0x80
    if call_type == 'unit':
        bits |= 0x40
    bits |= (frame_type & 0x3) << 4
    bits |= (dtype_vseq & 0xF)
    return b''.join([DMRD, bytes([seq & 0xFF]), rf_src, dst, peer,
                     bytes([bits]), stream_id, dmrpkt, b'\x00\x00'])


def b3(n): return n.to_bytes(3, 'big')
def b4(n): return n.to_bytes(4, 'big')


def login(sock, addr, peer_id, passphrase):
    """Run the HBP peer->master handshake. Returns when CONNECTED or raises."""
    def expect(prefix):
        sock.settimeout(5.0)
        data, _ = sock.recvfrom(1024)
        if data[:len(MSTNAK)] == MSTNAK:
            raise RuntimeError('master sent MSTNAK (login refused) -- check ACL / peer id')
        if data[:len(prefix)] != prefix:
            raise RuntimeError('expected %r, got %r' % (prefix, data[:10]))
        return data

    sock.sendto(RPTL + b4(peer_id), addr)
    ack = expect(RPTACK)
    salt = ack[6:10]
    h = bhex(sha256(salt + passphrase).hexdigest())
    sock.sendto(RPTK + b4(peer_id) + h, addr)
    expect(RPTACK)

    # Repeater config blob (302 bytes total incl. RPTC + peer_id).
    cfg = b''.join([
        b'HRNS    '[:8],            # callsign  [8:16]
        b'449000000',               # rx_freq   [16:25]
        b'444000000',               # tx_freq   [25:34]
        b'25',                      # tx_power  [34:36]
        b'01',                      # colorcode [36:38]
        b'38.00000',                # lat       [38:46]
        b'-095.0000',               # lon       [46:55]
        b'075',                     # height    [55:58]
        b'Test Harness        '[:20],   # location    [58:78]
        b'live_harness.py    '[:19],    # description [78:97]
        b'1',                       # slots     [97:98]
        b' ' * 124,                 # url       [98:222]
        b'hblink-harness'.ljust(40),    # software_id [222:262]
        b''.ljust(40),                  # package_id  [262:302]
    ])
    sock.sendto(RPTC + b4(peer_id) + cfg, addr)
    expect(RPTACK)
    print('[harness] logged in as peer %d' % peer_id, flush=True)


def send_stream(sock, addr, peer_id, src, dst, slot, call_type, stream_id,
                superframes, frame_ms, terminate, tail_vseq=None):
    """Send one DMR call and return the vseq of its last voice burst.

    A *properly terminated* call always finishes a complete superframe (voice
    bursts vseq 0..5) and then sends the terminator, so the last voice burst
    before VTERM is always F (vseq 5) -- this is how real radios end a call.

    A *lost tail* (terminate=False) stops mid-superframe with no terminator,
    leaving the last burst at `tail_vseq` (random 0..5 if None). This models an
    RF drop and is the case the stream-timeout / supersession END logic must
    still catch -- including when the last burst lands on vseq 2 (== VTERM's
    numeric value), which historically defeated the "already terminated" check.
    """
    seq = [0]

    def send(ft, vseq):
        sock.sendto(mk_dmrd(seq[0], b3(src), b3(dst), b4(peer_id), slot, call_type,
                            ft, vseq, stream_id), addr)
        seq[0] = (seq[0] + 1) & 0xFF

    send(_FT_DATA_SYNC, _VHEAD)
    if terminate:
        for _ in range(superframes):
            for vseq in range(6):
                send(_FT_VOICE, vseq)
                time.sleep(frame_ms / 1000.0)
        send(_FT_DATA_SYNC, _VTERM)
        return 5

    # Lost tail: whole superframes, then a partial one ending at tail_vseq.
    if tail_vseq is None:
        tail_vseq = random.randint(0, 5)
    for _ in range(max(0, superframes - 1)):
        for vseq in range(6):
            send(_FT_VOICE, vseq)
            time.sleep(frame_ms / 1000.0)
    for vseq in range(tail_vseq + 1):
        send(_FT_VOICE, vseq)
        time.sleep(frame_ms / 1000.0)
    return tail_vseq


def main():
    ap = argparse.ArgumentParser(description='Live DMRD traffic harness for HBlink3')
    ap.add_argument('--master-ip', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=54000)
    ap.add_argument('--passphrase', default='s3cr37w0rd')
    ap.add_argument('--peer-id', type=int, default=312100)
    ap.add_argument('--src', type=int, default=3121234, help='RF source (subscriber) id')
    ap.add_argument('--tgid', type=int, default=1)
    ap.add_argument('--slot', type=int, default=1, choices=(1, 2))
    ap.add_argument('--call-type', default='group', choices=('group', 'unit'))
    ap.add_argument('--calls', type=int, default=3, help='number of calls to place')
    ap.add_argument('--duration', type=float, default=2.0, help='seconds of audio per call')
    ap.add_argument('--gap', type=float, default=2.0, help='idle seconds between calls')
    ap.add_argument('--frame-ms', type=float, default=60.0, help='inter-frame spacing (DMR=60ms)')
    ap.add_argument('--concurrent', type=int, default=1,
                    help='overlapping streams per call on the same TGID/slot (collision test)')
    ap.add_argument('--no-term', action='store_true',
                    help='lost tail: stop mid-superframe with no terminator (dropped-RF test)')
    ap.add_argument('--tail-vseq', type=int, default=None, choices=range(6),
                    help='with --no-term, force the last burst vseq (e.g. 2 to reproduce the END-loss bug); random if unset')
    args = ap.parse_args()

    addr = (args.master_ip, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    login(sock, addr, args.peer_id, args.passphrase.encode())

    # A superframe is 6 voice bursts (~360 ms). Round the requested audio
    # duration to whole superframes so a clean call always ends on a full one.
    superframes = max(1, round(args.duration / (6 * args.frame_ms / 1000.0)))
    terminate = not args.no_term
    # Random per-process stream-id base so concurrent harness instances (each a
    # distinct simulated repeater) don't emit colliding stream-ids -- real radios
    # pick random stream-ids, and identical ids would alias into one call.
    sid = random.randint(0x10000000, 0xF0000000)
    for c in range(args.calls):
        if args.concurrent <= 1:
            sid += 1
            tail = send_stream(sock, addr, args.peer_id, args.src, args.tgid, args.slot,
                               args.call_type, b4(sid), superframes, args.frame_ms,
                               terminate, args.tail_vseq)
            print('[harness] call %d/%d: src=%d tgid=%d ts=%d sid=%08x %s'
                  % (c + 1, args.calls, args.src, args.tgid, args.slot, sid,
                     'TERMINATED' if terminate else ('LOST TAIL, last burst vseq=%d' % tail)),
                  flush=True)
        else:
            # Interleave K distinct streams superframe-by-superframe so they
            # genuinely overlap and contend for the one slot.
            streams = []
            for k in range(args.concurrent):
                sid += 1
                streams.append((b4(sid), args.src + k))
            print('[harness] call %d/%d: %d OVERLAPPING streams on tgid=%d ts=%d %s'
                  % (c + 1, args.calls, args.concurrent, args.tgid, args.slot,
                     'TERMINATED' if terminate else 'LOST TAIL'), flush=True)
            tails = {sidb: (args.tail_vseq if args.tail_vseq is not None else random.randint(0, 5))
                     for sidb, _ in streams}
            seqs = {sidb: 0 for sidb, _ in streams}

            def tx(sidb, s, ft, vseq):
                sock.sendto(mk_dmrd(seqs[sidb], b3(s), b3(args.tgid), b4(args.peer_id),
                                    args.slot, args.call_type, ft, vseq, sidb), addr)
                seqs[sidb] = (seqs[sidb] + 1) & 0xFF

            for sidb, s in streams:
                tx(sidb, s, _FT_DATA_SYNC, _VHEAD)
            for sf in range(superframes):
                for vseq in range(6):
                    for sidb, s in streams:
                        # On a lost tail, a stream goes silent past its tail burst.
                        if terminate or sf < superframes - 1 or vseq <= tails[sidb]:
                            tx(sidb, s, _FT_VOICE, vseq)
                    time.sleep(args.frame_ms / 1000.0)
            if terminate:
                for sidb, s in streams:
                    tx(sidb, s, _FT_DATA_SYNC, _VTERM)
        if c + 1 < args.calls:
            # keep the link alive across the idle gap
            sock.sendto(RPTPING + b4(args.peer_id), addr)
            time.sleep(args.gap)
    print('[harness] done', flush=True)


if __name__ == '__main__':
    main()
