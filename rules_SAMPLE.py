'''
THIS EXAMPLE WILL NOT WORK AS-IS - YOU MUST SPECIFY YOUR OWN VALUES!
Full reference: CONFIGURING.md (rules.py section).

BRIDGES defines "conference bridges" (think bridge groups / reflectors): every system
marked ACTIVE on a bridge exchanges traffic with every other ACTIVE system on that same
bridge. Not end-to-end -- each system is activated on each bridge independently.

Each bridge key is an arbitrary name; its value is one entry per participating system:
    SYSTEM  - system name from hblink.cfg -- MUST match EXACTLY, or bridge.py won't start
    TS      - timeslot 1 or 2 to match          (XLX: always TS 2)
    TGID    - talkgroup ID to match             (XLX: always TG 9)
    ACTIVE  - True/False: passing traffic now? (ON/OFF triggers below can flip it)
    TO_TYPE - 'ON' = auto-off after TIMEOUT; 'OFF' = auto-on after TIMEOUT; else 'NONE'
    TIMEOUT - timer length in MINUTES (minutes only; timers cost performance)
    ON/OFF  - lists of TGIDs that activate/deactivate this system (always lists; [] = none)
    RESET   - list of TGIDs that reset a running timer (only when voice TGID != trigger TGIDs)
'''

BRIDGES = {
    'WORLDWIDE': [
            {'SYSTEM': 'SERVER-1',    'TS': 1, 'TGID': 1,    'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'ON',  'ON': [2,], 'OFF': [9,10], 'RESET': []},
            {'SYSTEM': 'CLIENT-1',    'TS': 1, 'TGID': 3100, 'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'ON',  'ON': [2,], 'OFF': [9,10], 'RESET': []},
        ],
    'ENGLISH': [
            {'SYSTEM': 'SERVER-1',    'TS': 1, 'TGID': 13,   'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [3,], 'OFF': [8,10], 'RESET': []},
            {'SYSTEM': 'CLIENT-2',    'TS': 1, 'TGID': 13,   'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [3,], 'OFF': [8,10], 'RESET': []},
        ],
    'STATEWIDE': [
            {'SYSTEM': 'SERVER-1',    'TS': 2, 'TGID': 3129, 'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [4,], 'OFF': [7,10], 'RESET': []},
            {'SYSTEM': 'CLIENT-2',    'TS': 2, 'TGID': 3129, 'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [4,], 'OFF': [7,10], 'RESET': []},
        ]
}

'''
OpenBridge (OBP) systems are NOT configured as inline BRIDGES members above.
An OBP is a point-to-point trunk that passes talkgroups by TGID -- it has no RF
user to key ON/OFF timers and no real timeslot -- so it gets its own table:

    OBP_BRIDGES = { <OBP system from hblink.cfg> : { <bridge name> : <TGID> } }

Read a row as "on THIS OpenBridge, this TGID *is* this bridge." That one fact is
the route in BOTH directions and the filter: a TGID not listed for an OBP is
dropped (fail-closed). At startup bridge.py expands each row into a bridge member
for you. Notes:
    * There is no TS column, and that is deliberate -- see below.
    * The OBP system name must be an enabled MODE: OPENBRIDGE system in hblink.cfg.
    * Leaving an OBP system as an inline BRIDGES member above is a startup ERROR.
    * On one OBP, a TGID may map to only one bridge (a fork duplicates the stream)
      -> startup ERROR. A bridge carrying different TGIDs on two OBPs (renumber in
      transit) is allowed but logs a WARNING.
    * OBP_BRIDGES is optional -- omit it or leave it {} if you run no OpenBridges.

No timeslot is configurable here. OpenBridge group traffic is TS1 on the wire by
protocol -- the slot bit is a convention on a trunk that multiplexes streams by
stream-id, so it carries no meaning and every known implementation forces it to 1.
Which local timeslot a talkgroup lands on is decided by the RECEIVER, from the TGID.
In HBlink3 that decision is the TS on the bridge's REPEATER/SERVER member in BRIDGES
above, and bridge.py flips the slot bit to match on the way out -- so "TGID 3129
arrives over the trunk and lands on TS2 locally" is written as TS 2 on that member,
with the OBP row naming the TGID alone. A TS on an OBP row could only ever disagree
with the wire and make the row unmatchable, silently taking a talkgroup off the air,
so it is rejected at startup.
'''

OBP_BRIDGES = {
    # 'BACKBONE-OBP': {
    #     'WORLDWIDE': 1,
    #     'STATEWIDE': 3129,
    #     'ENGLISH':   13,
    # },
}

'''
XLX reflector connections also get their own table. An XLX system is a MODE:
OUTBOUND system in hblink.cfg with an XLX_MODULE letter set; it is nailed up to
exactly one conference bridge:

    XLX_BRIDGES = { <XLX system from hblink.cfg> : <bridge name> }

There is no TS or TGID column, and this is deliberate. Every XLX module presents
as TS2/TG9 on the wire -- there is no module identifier in any frame -- so those
values are protocol constants that bridge.py injects for you. They are not made
configurable because a wrong value produces no visible symptom: the reflector
never acknowledges anything, so the traffic would simply vanish. Notes:
    * The module (A-Z) is set in hblink.cfg as XLX_MODULE, not here. One module
      per connection; to bridge two modules, configure two systems.
    * Leaving an XLX system as an inline BRIDGES member above is a startup ERROR.
    * An XLX system in UNIT (below) is a startup ERROR -- a private call to the
      reflector would change its module for everyone connected to it.
    * No timers or ON/OFF/RESET triggers: there is no end user on the connection.
    * XLX_BRIDGES is optional -- omit it or leave it {} if you run no reflectors.
'''

XLX_BRIDGES = {
    # 'XLX950-D': 'WORLDWIDE',
}

'''
UNIT: system names that should bridge unit-to-unit (individual/private) calls to each other.
'''

UNIT = ['ONE', 'TWO']

'''
This is for testing the syntax of the file. It won't eliminate all errors, but running this file
like it were a Python program itself will tell you if the syntax is correct!
'''

if __name__ == '__main__':
    from pprint import pprint
    pprint(BRIDGES)
    pprint(OBP_BRIDGES)
    pprint(XLX_BRIDGES)
    print(UNIT)
