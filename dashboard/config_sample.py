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

# Alias files (map DMR IDs to callsigns/talkgroup names). Same files HBlink3 uses;
# point PATH at HBlink3's directory to share them, or keep your own copies.
PATH            = '../'                 # MUST END IN '/'
PEER_FILE       = 'peer_ids.json'
SUBSCRIBER_FILE = 'subscriber_ids.json'
TGID_FILE       = 'talkgroup_ids.json'  # optional, {id: name}; ok if missing
LOCAL_SUB_FILE  = ''                    # optional local override, '' to disable
LOCAL_PEER_FILE = ''                    # optional local override, '' to disable
