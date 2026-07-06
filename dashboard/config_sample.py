###############################################################################
#   Copyright (C) 2016-2026  Cortney T. Buffington, N0MJS <n0mjs@me.com>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

# Copy this file to config.py and edit for your install.

# Display / branding
REPORT_NAME     = 'My HBlink3 System'   # Shown in the dashboard header
LOGO_FILE       = ''                    # Path to a logo image (png/jpg/svg/gif); '' = no logo

# Connection to HBlink3's reporting feed (the [REPORTS] section of hblink.cfg)
HBLINK_IP       = '127.0.0.1'           # HBlink3 reporting host
HBLINK_PORT     = 4321                  # HBlink3 reporting TCP port (REPORT_PORT)

# Web server
WEB_HOST        = '0.0.0.0'             # Interface to bind the dashboard web server
WEB_PORT        = 8080                  # Port (must be > 1024 if not running as root)

# Call log
LOG_LINES       = 300                   # Number of recent call-log entries to retain

# Last Heard table — most-recent unique callers, shown at the top of the dashboard.
LAST_HEARD       = 'open'               # 'open' | 'closed' | 'off'  (default state; 'off' hides it entirely)
LAST_HEARD_COUNT = 10                   # Number of recent callers to list

# Server systems — default state for each server system's collapsible repeater list.
# Users can toggle each system open/closed in their own browser (remembered locally).
# When collapsed, system-level TS1/TS2 activity pills replace the repeater table.
SERVER_REPEATERS = 'open'               # 'open' | 'closed'  ('closed' gives a compact view for many-repeater servers)

# Alias files — the dashboard owns downloading, storing, and refreshing these.
# Files live in the dashboard's own subdirectory (PATH = './') by default.
# Set TRY_DOWNLOAD = True to fetch fresh files from radioid.net on startup and
# refresh them daily. Set False to manage files manually or skip alias lookups.
#
# NOTE: HBlink3/bridge.py can also load these files for callsign display in
# logs, but doing so costs RAM — roughly 150 MB with the country filter below,
# ~500 MB unfiltered, and that again in the dashboard process if it loads them
# too — so it is STRONGLY DISCOURAGED. If you want log callsigns, point
# bridge.py's [ALIASES] PATH at this directory and keep TRY_DOWNLOAD: False
# there — the dashboard handles the downloads.
TRY_DOWNLOAD    = True
PATH            = './'                  # MUST END IN '/' — dashboard's own directory
PEER_FILE       = 'peer_ids.json'
SUBSCRIBER_FILE = 'subscriber_ids.json'
TGID_FILE       = 'talkgroup_ids.json'  # optional {id: name}; ok if missing
LOCAL_SUB_FILE  = ''                    # optional local subscriber override, '' to disable
LOCAL_PEER_FILE = ''                    # optional local peer override, '' to disable
PEER_URL        = 'https://www.radioid.net/static/rptrs.json'
SUBSCRIBER_URL  = 'https://www.radioid.net/static/users.json'
STALE_DAYS      = 7                     # re-download after this many days

# Country filter for subscriber downloads. Only records matching these country
# names (as they appear in the radioid.net data) are written to disk, which
# keeps memory use and load time low. Set to [] or None to load all countries.
FILTER_COUNTRIES = ['United States', 'France']
