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
    print(UNIT)
