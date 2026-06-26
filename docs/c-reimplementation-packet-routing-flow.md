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

This is a deliberate design decision driven by protocol interoperability, not a limitation
of the LC handling logic. Preserving talker alias embedded LC in voice bursts B through E
is technically possible in a pure HBP-to-HBP bridging path. The reason it is not done is
that the bridge must operate correctly across multiple protocols — HBP, IPSC via an adapter
such as ipsc2hbp, OpenBridge, and potentially future protocols — each of which carries
talker alias through a different mechanism. IPSC, for example, carries TA entirely outside
the DMR frame structure. Frames reconstructed by an IPSC-to-HBP adapter carry no valid
embedded TA LC. Other future protocol adapters may be similarly constrained.

Attempting to selectively preserve TA LC only on HBP-to-HBP paths while overwriting it on
all other paths introduces conditional logic that must be maintained as new protocol adapters
are added. Overwriting with the call LC on every forwarded frame is the safe, uniform, and
most interoperable choice across the full range of protocols the bridge may interface with.

Radios that resolve talker alias from an external database (RadioID.net or a locally loaded
DMR ID file) are unaffected because they do not depend on the embedded TA LC at all.

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

If a call is interrupted (repeater loses power, network drop, lost terminator) the
stream_id may remain in the active stream table indefinitely. A background cleanup pass
removes stream entries older than a stale threshold and runs at a periodic interval.

A DMR voice superframe is approximately 360 milliseconds. A call that ends without a
terminator becomes detectable within one or two superframes of the last received voice
frame — roughly 1 to 2 seconds. Given the timescales of human PTT interaction, a cleanup
threshold of 1 to 2 seconds is fast enough to prevent phantom streams from blocking new
calls without requiring any sub-second precision in the cleanup interval. The cleanup pass
itself does not need to run faster than every few seconds; it is a backstop for an
uncommon failure case, not a timing-critical path.

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
than consulting the bridge table, the router consults a learned subscriber location cache: a
table that records which system last heard a given subscriber ID transmit. When a unit call
arrives addressed to subscriber X, the router looks up X in the cache and forwards the call
to the system where X was last heard.

The conceptual model is analogous to PIM Sparse Mode for IPv4 multicast: the router does
not know where every subscriber is at all times, but it learns subscriber location from
observed traffic and uses that information to make a forwarding decision. HBlink4's
implementation of this mechanism is the reference for detailed behavior.

The cache is updated once per call stream, on the voice header frame (the first DMRD packet
of a new stream_id). The update is unconditional: the rf_src ID is recorded as having been
last seen on the ingress system, and that entry's expiry timestamp is reset to
current_time + TIMEOUT. If an entry for that rf_src already exists pointing to a different
system, it is overwritten with the new system. This handles subscriber roaming — a unit
that moves from system A to system B between calls will have its cache entry updated to
system B on its next transmission, regardless of whether the old entry has expired.
Updating on subsequent frames of the same call is redundant — a subscriber's location
cannot change mid-call — and would add unnecessary work to every packet on the hot path.
This requires no explicit configuration; the cache builds itself from observed traffic.

The cache must be pruned periodically or it will grow without bound as subscribers come and
go. A pruning pass runs at a configured interval measured in minutes (not seconds), removing
entries whose expiry timestamp has passed. The interval does not need to be fine-grained
because subscriber location changes on human timescales, not millisecond timescales. Each
time a subscriber is heard, its entry is refreshed, so active subscribers are never pruned.

When a unit call arrives and the destination subscriber is not in the cache (a cache miss),
the call must be forwarded to all systems rather than to a single known destination. This is
the equivalent of a PIM Sparse Mode register/flood before the join tree is established. In
practice this is a small volume of additional traffic, as cache misses occur only for
subscribers not recently heard. Each system has a per-system configuration flag that
specifies whether it accepts unit call flooding (forwarding of unit calls destined for
unknown subscribers). Systems that do not permit flooding are skipped during a cache miss
forward. This allows operators to isolate systems that should only receive unit calls with
a confirmed destination.

The subscriber cache is lossy by design: last-seen wins. It does not handle the case where
a subscriber is simultaneously reachable on multiple systems. It is the correct and
sufficient mechanism for the common case of routing a private reply back to the originating
repeater.
