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
- **TIMEOUT**: integer minutes — the duration of the timer, as configured. Converted to
  seconds internally for comparison against the running timer.
- **TIMER**: an absolute time value in the same units as the application's time source.
  Represents the moment at which the timer expires. The C implementation may use any
  consistent time source (Unix epoch via time(), monotonic clock, or other); what matters
  is that TIMER and the current time are always compared using the same source. When
  emitted in the telemetry feed, TIMER is expressed as a Unix epoch value so that external
  consumers can compute a countdown without needing to know the application's internal time
  reference.
- **ON**: list of TGIDs — receiving any of these on the matching TS triggers the ON action
  for this member (see TO_TYPE behavior below)
- **OFF**: list of TGIDs — receiving any of these on the matching TS triggers the OFF action
  for this member (see TO_TYPE behavior below)
- **RESET**: list of TGIDs — receiving any of these on the matching TS resets the timer
  for this member regardless of TO_TYPE

---

## TO_TYPE semantics

TO_TYPE controls the default state of a bridge member and what the timer expiry does.
TO_TYPE ON and TO_TYPE OFF are logical inverses of each other.

**TO_TYPE ON**: the member is INACTIVE by default. Receiving an ON TGID activates it and
starts the timer. The member stays ACTIVE until the timer expires, at which point it returns
to INACTIVE. This is the "turn on for N minutes" behavior.

**TO_TYPE OFF**: the member is ACTIVE by default. Receiving an OFF TGID deactivates it and
starts the timer. The member stays INACTIVE until the timer expires, at which point it
returns to ACTIVE. This is the "turn off for N minutes" behavior — the logical inverse of
TO_TYPE ON.

**TO_TYPE NONE**: no automatic timer. The member only changes state in response to ON and
OFF TGID triggers. It stays in whatever state it is in indefinitely until a trigger arrives.

RESET always extends the timer by setting it to current_time + TIMEOUT_seconds regardless
of TO_TYPE. For TO_TYPE ON this extends the active period. For TO_TYPE OFF this extends the
deactivation period — keeping the member INACTIVE longer. This is valid behavior even if
its usefulness in the OFF case is limited.

---

## State transitions

### ON trigger — fires at call START (new stream, voice header)

When a new stream is detected on a system (a stream_id not previously seen on that
system/slot combination), the incoming TGID is checked against the ON list of every bridge
member associated with that system and slot.

If the incoming TGID is in a member's ON list and the slot matches:

- For TO_TYPE ON: if INACTIVE, set ACTIVE to True and set TIMER to
  current_time + (TIMEOUT * 60). If already ACTIVE, reset TIMER to extend the countdown.
- For TO_TYPE OFF: no effect from ON list while ACTIVE (already active). If somehow
  INACTIVE when an ON TGID arrives, treat it as a reset — set ACTIVE to True. The ON list
  is not the primary trigger for TO_TYPE OFF members.
- For TO_TYPE NONE: set ACTIVE to True. No timer is set.

If the incoming TGID is in a member's RESET list and the slot matches: set TIMER to
current_time + (TIMEOUT * 60) regardless of current ACTIVE state or TO_TYPE.

If any member's ACTIVE state changed, emit an immediate bridges snapshot.

**Why the ON trigger fires at call START, not call END**: the voice header frame must route
through to target systems before any voice frames arrive. If the trigger were deferred to
call END, the header and all voice frames would be lost and target radios would never hear
the call. Activating at the first packet of a new stream allows the header and all
subsequent frames to be forwarded in the same pass as the triggering call.

### OFF trigger — fires at call END (voice terminator received)

When a voice terminator is received for an active stream on a system, the incoming TGID is
checked against the OFF list of every bridge member associated with that system and slot.

If the incoming TGID is in a member's OFF list and the slot matches:

- For TO_TYPE OFF: if ACTIVE, set ACTIVE to False and set TIMER to
  current_time + (TIMEOUT * 60). This begins the deactivation period.
- For TO_TYPE ON: set ACTIVE to False immediately. No timer restart.
- For TO_TYPE NONE: set ACTIVE to False. No timer.

If any member's ACTIVE state changed, emit an immediate bridges snapshot.

**Why the OFF trigger fires at call END**: the terminator frame must also route to target
systems so they can cleanly close the call. Deactivating at the terminator ensures the full
call — header through terminator — reaches all targets before routing stops.

### Timer expiry — fires in the rule timer loop

The rule timer loop runs every 10 seconds. The 10-second interval is sufficient because
TIMEOUT is configured in whole minutes, making 10-second resolution more than adequate for
correct expiry detection. The loop does not need sub-minute precision.

For each bridge member where TO_TYPE is ON or OFF:

- If TO_TYPE is ON and ACTIVE is True and TIMER is less than current_time: set ACTIVE to
  False. The member has exceeded its active timeout.
- If TO_TYPE is OFF and ACTIVE is False and TIMER is less than current_time: set ACTIVE to
  True. The deactivation period has ended; the member returns to its default active state.

If any member changed state, emit a bridges snapshot. The loop does not emit a snapshot if
no state changed, avoiding unnecessary traffic to reporting clients.

---

## Timer behavior summary

TO_TYPE ON: default INACTIVE. ON TGID activates, timer runs, expiry deactivates. RESET
extends active period. Logical reading: "this bridge is on for N minutes after the last
activity."

TO_TYPE OFF: default ACTIVE. OFF TGID deactivates, timer runs, expiry reactivates. RESET
extends deactivation period. Logical reading: "this bridge is off for N minutes after the
blocking call, then comes back."

TO_TYPE NONE: no timer. ON TGID activates, OFF TGID deactivates. Stays in either state
indefinitely until triggered.

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
