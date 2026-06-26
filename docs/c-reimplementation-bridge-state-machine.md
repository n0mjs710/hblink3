# Conference Bridge State Machine for C Reimplementation

## Overview

Each conference bridge is a named group of system members. Each member independently
tracks whether it is ACTIVE or INACTIVE. When a member is ACTIVE, traffic received on
its configured system, timeslot, and TGID is eligible to be forwarded to all other ACTIVE
members of the same bridge.

The state machine described here governs how individual members transition between ACTIVE
and INACTIVE, and how timers interact with those transitions.

---

## Data model

A bridge member holds the following state:

- **SYSTEM**: the name of the HBP system this member belongs to
- **TS**: the timeslot (1 or 2) on that system
- **TGID**: the talkgroup ID that traffic on this member carries
- **ACTIVE**: boolean, whether this member is currently passing traffic
- **TO_TYPE**: one of ON, OFF, or NONE — controls timer behavior (see below)
- **TIMEOUT**: integer seconds — the duration of the timer when TO_TYPE is ON
- **TIMER**: absolute Unix epoch — the time at which this member expires
- **ON**: list of TGIDs — receiving any of these on the matching TS activates this member
- **OFF**: list of TGIDs — receiving any of these on the matching TS deactivates this member
- **RESET**: list of TGIDs — receiving any of these on the matching TS extends the timer
  when ACTIVE and TO_TYPE is ON

---

## ACTIVE state transitions

### Activation — fires at call START (new stream, voice header)

When a new stream is detected on a system (a stream_id not previously seen on that
system/slot combination), the incoming TGID is checked against the ON and RESET lists of
every bridge member associated with that system.

If the incoming TGID is in a member's ON list AND the incoming slot matches the member's TS:

- If the member is currently INACTIVE: set ACTIVE to True
  - If TO_TYPE is ON: set TIMER to pkt_time + TIMEOUT (starts countdown from call start)
  - If TO_TYPE is OFF: set TIMER to pkt_time (expires immediately; traffic routes for the
    duration of the triggering call only, then the rule timer loop will deactivate it)
  - If TO_TYPE is NONE: TIMER is irrelevant; member stays ACTIVE indefinitely

- If the member is currently ACTIVE and TO_TYPE is ON: reset TIMER to pkt_time + TIMEOUT
  (extend the countdown; each new triggering call refreshes the timeout)

If the incoming TGID is in a member's RESET list AND the member is ACTIVE AND TO_TYPE is ON:
  - Reset TIMER to pkt_time + TIMEOUT (same as the active-and-already-on case above)

If any member's ACTIVE state changed during this step, emit an immediate bridges snapshot.

**Why activation fires at call START**: the voice header frame must route through to target
systems. If activation were deferred to call END, the header and all voice frames would be
lost and target radios would never hear the call. Activating at the first packet of a new
stream allows the header to be forwarded in the same pass.

### Deactivation — fires at call END (voice terminator received)

When a voice terminator is received for an active stream on a system, the incoming TGID is
checked against the OFF list of every bridge member associated with that system.

If the incoming TGID is in a member's OFF list AND the incoming slot matches the member's TS
AND the member is ACTIVE: set ACTIVE to False.

If any member's ACTIVE state changed, emit an immediate bridges snapshot.

**Why deactivation fires at call END**: the terminator frame must also route to target
systems so they can cleanly close the call. Deactivating at the terminator (not the header)
ensures the full call — header through terminator — reaches all targets.

### Timer expiry — fires in the rule timer loop

The rule timer loop runs every 10 seconds. For each bridge member where TO_TYPE is ON or
OFF and ACTIVE is True:

- If TIMER is less than the current time: set ACTIVE to False

If any member changed state, emit a bridges snapshot. The loop does not emit a snapshot if
no state changed.

Note: TO_TYPE OFF causes TIMER to be set to pkt_time at activation, which is already in the
past by the time the rule timer loop next runs. This means a TO_TYPE OFF member becomes
INACTIVE on the first rule timer loop iteration after the activating call ends. The member
stays ACTIVE while a call is in progress only because the terminator's deactivation check
handles the OFF list separately (see above); the timer is a backstop.

---

## Timer behavior summary

TO_TYPE ON: member activates when an ON TGID is received, stays ACTIVE until TIMEOUT seconds
after the last ON or RESET TGID was received, then deactivates automatically.

TO_TYPE OFF: member activates when an ON TGID is received and deactivates when an OFF TGID
is received or when the call that triggered activation ends, whichever comes first.

TO_TYPE NONE: member activates when an ON TGID is received and only deactivates when an
OFF TGID is received. No automatic timeout.

---

## Routing when ACTIVE

A packet received on a system/slot/TGID that matches an ACTIVE bridge member is forwarded
to every other ACTIVE member of the same bridge, subject to the packet routing rules (see
the routing flow document). The source system is never a forwarding target for its own
traffic.

If a bridge has only one ACTIVE member, there are no targets and the packet is not forwarded
anywhere (but it is still received and processed normally by the ingress system).

---

## Reporting bridge state

The bridge state is reported as a snapshot (the full state of all bridges) rather than as
individual transitions. This means:

- Consumers always have a consistent view — they cannot observe a partial update
- The core does not need to track which clients have seen which transitions
- On new client connect, the current snapshot is sent immediately

Immediate snapshots on ACTIVE state change are important for PTT systems: a user keys up
expecting to see the bridge connect, and a 10-second lag would be confusing. The immediate
snapshot is surgical — it only fires when ACTIVE actually changed, not on every packet.
