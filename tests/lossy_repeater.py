#!/usr/bin/env python
#
# Lossy-repeater simulator. Logs into a running HBlink3 SERVER as a HomeBrew
# repeater and sends keepalive pings with a deliberate drop rate, so you can watch
# the dashboard's per-repeater ping-loss % climb (and the callsign turn gold)
# WITHOUT needing a genuinely marginal RF link.
#
# How it stays honest:
#   * It pings cleanly for a short warmup first, so the server calibrates this
#     repeater's ping cadence before any loss is introduced (otherwise the very
#     first big gap would be mistaken for the normal interval).
#   * It never drops more than --max-consec pings in a row, so it can't trip
#     MAX_MISSED and get disconnected -- it stays up and simply shows loss.
#   * Keep --interval well under (PING_TIME * MAX_MISSED) seconds so even a run
#     of drops stays inside the timeout.
#
# Point --port / --passphrase / --peer-id at a real SERVER the repeater may join
# (--peer-id just has to pass that server's REG_ACL). Use a peer-id that isn't one
# of your real repeaters so it shows up as its own row.
#
#   venv/bin/python tests/lossy_repeater.py --port 54000 --passphrase s3cr37w0rd \
#       --peer-id 3129999 --interval 5 --loss 0.25
#
# Ctrl-C to stop; the simulated repeater then times out and drops off on its own.

import argparse
import os
import random
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live_harness import login, RPTPING, b4


def main():
    ap = argparse.ArgumentParser(description='Simulate a lossy repeater (drops keepalive pings)')
    ap.add_argument('--server-ip', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=54000, help="the SERVER system's port")
    ap.add_argument('--passphrase', default='s3cr37w0rd')
    ap.add_argument('--peer-id', type=int, default=3129999, help='repeater radio id (must pass REG_ACL)')
    ap.add_argument('--interval', type=float, default=5.0,
                    help='seconds between pings; keep well under PING_TIME*MAX_MISSED')
    ap.add_argument('--loss', type=float, default=0.25, help='fraction of pings to drop (0..0.9)')
    ap.add_argument('--warmup', type=int, default=6,
                    help='clean pings first so the server calibrates the cadence')
    ap.add_argument('--max-consec', type=int, default=2,
                    help='never drop more than this many in a row (avoids the MAX_MISSED timeout)')
    args = ap.parse_args()

    loss = max(0.0, min(0.9, args.loss))
    addr = (args.server_ip, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    login(sock, addr, args.peer_id, args.passphrase.encode())
    sock.setblocking(False)

    ping = RPTPING + b4(args.peer_id)
    sent = dropped = consec = n = 0
    print('[lossy] peer %d pinging every %.1fs, offering ~%d%% loss (%d clean warmup pings). Ctrl-C to stop.'
          % (args.peer_id, args.interval, int(loss * 100), args.warmup), flush=True)
    try:
        while True:
            try:                                   # drain MSTPONG replies
                while True:
                    sock.recvfrom(1024)
            except BlockingIOError:
                pass
            n += 1
            drop = (n > args.warmup) and (consec < args.max_consec) and (random.random() < loss)
            if drop:
                dropped += 1
                consec += 1
            else:
                sock.sendto(ping, addr)
                sent += 1
                consec = 0
            if n % 6 == 0:
                tot = sent + dropped
                print('[lossy] sent=%d dropped=%d  (~%d%% offered loss)'
                      % (sent, dropped, round(100 * dropped / tot) if tot else 0), flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\n[lossy] stopping; the simulated repeater will time out and drop off shortly.', flush=True)


if __name__ == '__main__':
    main()
