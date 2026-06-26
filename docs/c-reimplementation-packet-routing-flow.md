# Packet Routing Flow for C Reimplementation

## Overview

This document describes the path a DMRD (DMR data) packet travels from arrival at a UDP
socket through the routing decision to forwarding on one or more outbound UDP sockets. It
also covers the LC rewriting policy and why it is designed the way it is.

---

## DMRD packet structure

All field offsets are zero-indexed bytes within the UDP payload.

- Bytes 0-3: command identifier, the ASCII string "DMRD"
- Byte 4: sequence number (wraps 0-255)
- Bytes 5-7: rf_src, the radio ID of the subscriber (transmitting radio), 3 bytes big-endian
- Bytes 8-10: dst_id, the destination TGID for group calls or subscriber ID for unit calls,
  3 bytes big-endian
- Bytes 11-14: peer_id, the radio ID of the repeater or hotspot that received the call,
  4 bytes big-endian
- Byte 15: flags byte. Bit 7 (0x80): timeslot (0 = TS1, 1 = TS2). Bits 6-4: frame type.
  Bits 3-0: dtype_vseq, encodes the voice frame sequence position within a superframe.
- Bytes 16-19: stream_id, a 4-byte identifier that is constant for the duration of one call
  and unique per call. Used to detect new streams and track active calls.
- Bytes 20-52: 33 bytes of DMR payload (the actual over-the-air frame content, including
  the embedded LC or SYNC pattern depending on frame type)
- Bytes 53-54: BER (bit error rate) and RSSI values from the repeater

Total: 55 bytes minimum. Some implementations may include additional trailing data.

---

## Arrival and validation

When a DMRD packet arrives on a SERVER system's UDP socket:

1. Confirm the command bytes (0-3) are "DMRD".
2. Extract peer_id from bytes 11-14. Look up the peer_id in the system's repeater table.
3. Validate that the repeater exists, its CONNECTION state is "YES" (fully registered), and
   its stored socket address matches the packet's source address. Drop the packet silently
   if any check fails. This prevents spoofed or stale packets from being routed.
4. Extract rf_src (bytes 5-7), dst_id (bytes 8-10), the timeslot from bit 7 of byte 15,
   dtype_vseq from bits 3-0 of byte 15, and stream_id (bytes 16-19).
5. Determine call type (group or unit) from the flags byte.

For OUTBOUND (peer) systems, the peer_id in the packet is the remote master's ID, not the
local repeater's. Validation is against the outbound system's configured server address and
connection state.

---

## New stream detection

Before the routing loop, check whether this stream_id has been seen before on this
system/slot combination. The active stream table maps (system, slot) to the current
stream_id.

If the stream_id is new (not in the active stream table):

1. Record the new stream_id in the active stream table for this system/slot.
2. Run the ACTIVATION check: consult the bridge membership table for this system and slot.
   For each bridge member this system/slot belongs to, check whether dst_id is in the
   member's ON list. If it is, apply the activation logic described in the bridge state
   machine document. Do this before the routing loop so the triggering header frame itself
   is forwarded to newly activated targets.
3. If any bridge member's ACTIVE state changed, emit an immediate bridges snapshot via the
   reporting feed.

---

## Routing loop

For each bridge that this system/slot/TGID combination belongs to (looked up via a
precomputed BRIDGE_BY_SYSTEM index), and for each other ACTIVE member of that bridge
(excluding the source system):

1. Determine the target: the other member's SYSTEM, TS, and TGID.
2. Build the outbound packet by copying the inbound packet and rewriting:
   - Bytes 8-10 (dst_id): replace with the target member's TGID
   - Byte 15 (flags): set or clear bit 7 to reflect the target's TS
   - Bytes 11-14 (peer_id): replace with the target system's own configured radio ID
     (the remote master expects its own ID as the peer_id from a peer, and repeaters
     expect the master's ID as the peer_id from a server)
3. Rewrite the LC word in the DMR payload (see LC rewriting section below).
4. Transmit the modified packet to the target system's registered socket address.

If the source system is a SERVER, the packet goes to each target repeater's socket address
on the target server's UDP port. If the source is an OUTBOUND peer, the packet goes to
the remote master's address.

---

## LC rewriting

The Link Control (LC) word encodes the call parameters — specifically the TGID, the
rf_src, and the call type flag. Every forwarded frame must carry an LC word that matches
the target's TGID and the original subscriber's rf_src. If this is not done, radios that
join the call after it has started (late entry) will decode the wrong TGID from the
embedded LC and may route or display the call incorrectly, or route traffic to the wrong
destination.

LC rewriting is applied to every forwarded frame, not just headers:

**Voice header frame**: the LC word occupies a known position in the 33-byte DMR payload
and is protected by a full rate BPTC 19,96 code. Decode the existing LC, replace the FLCO
(Full LC Opcode), rf_src, and dst_id fields with the correct values for this forwarding
path, and re-encode with BPTC 19,96.

**Voice burst frames A-F (dtype_vseq 0-5)**: each voice burst carries an embedded LC
fragment and a Golay-protected SYNC pattern. For bursts B through E (dtype_vseq 1 through
4), bits 116 through 147 of the 264-bit over-the-air representation carry an embedded LC
chunk. These bits are overwritten with the translated LC for this forwarding path.

**Voice terminator frame**: the LC word is present in the same position as the header and
is rewritten identically.

---

## Why talker alias is destroyed during bridging

Talker Alias (TA) is an extended LC feature (FLCO values 0x04 through 0x07) that occupies
exactly the same embedded LC slots in voice bursts B through E that the bridge uses to
carry the translated call LC. These two uses of the same bits are mutually exclusive.

If the bridge were to preserve the talker alias embedded LC in bursts B through E, the
embedded LC in those frames would describe a different FLCO than the header LC, which
carries the standard call LC (FLCO 0x00). A radio performing late entry would read the
burst embedded LC, expect a standard call LC, and instead find talker alias data. Depending
on the radio's implementation, this results in incorrect TGID display, incorrect routing,
or audio artifacts.

The only consistent behavior is to overwrite the burst embedded LC with the translated call
LC on every forwarded frame. This destroys any talker alias that was in those bits.

This is a deliberate design decision, not an oversight. Radios that resolve talker alias
from an external database (RadioID.net or a locally loaded DMR ID file) are unaffected
because they do not depend on the embedded TA LC at all.

When using an IPSC-to-HBP adapter (such as ipsc2hbp), the situation is compounded: IPSC
carries talker alias through a completely different mechanism than DMR over-the-air. Frames
reconstructed from IPSC data cannot reliably contain valid DMR embedded LC in the TA FLCO
format, so preserving it would be actively harmful.

---

## Stream end handling

When a voice terminator frame is received for the current active stream on a system/slot:

1. Forward the terminator to all routing targets as described above (the terminator must
   reach target radios so they can cleanly close the call).
2. Run the DEACTIVATION check: for each bridge member this system/slot belongs to, check
   whether dst_id is in the member's OFF list. If it is, apply the deactivation logic from
   the bridge state machine document.
3. If any bridge member's ACTIVE state changed, emit an immediate bridges snapshot.
4. Remove the stream_id from the active stream table.

---

## Stale stream cleanup

If a call is interrupted (repeater loses power, network drop, late terminator) the
stream_id may remain in the active stream table indefinitely. A background cleanup pass
(run every few seconds) removes stream entries older than a configured stale threshold
(30 seconds is conservative; real calls end within a few seconds of the last voice frame).
This prevents a lost terminator from leaving a phantom active stream that blocks new calls
on the same system/slot from being detected as new streams.

---

## BRIDGE_BY_SYSTEM index

The routing loop above requires efficiently finding all bridges a given system/slot/TGID
belongs to. Iterating every bridge and every member on every packet is too slow.

At startup (and on every rules reload), build a secondary index: a mapping from
(system_name, slot) to a list of (bridge_pointer, member_pointer) pairs. When a packet
arrives, look up (system, slot) in the index to get the candidate bridge members, then
check whether dst_id is in each member's TGID. Because TGID lists per member are small,
this inner scan is fast.

This index is rebuilt from scratch on every rules reload. The old index is replaced
atomically alongside the bridge table.

---

## Unit call routing

Private (unit) calls between two subscribers are routed differently from group calls. Rather
than consulting the bridge table, the router consults a learned subscriber map: a table that
records which system last heard a given subscriber ID transmit. When a unit call arrives
addressed to subscriber X, the router looks up X in the subscriber map and forwards the
call to the system where X was last heard.

The subscriber map is updated on every received DMRD packet: the rf_src ID is recorded as
having been last seen on the ingress system. This requires no explicit configuration; the
map builds itself from observed traffic.

Unit call routing is a secondary feature. The subscriber map is lossy by design (last-seen
wins) and does not handle the case where a subscriber is reachable on multiple systems. It
is sufficient for the common case of routing a private reply back to the originating repeater.
