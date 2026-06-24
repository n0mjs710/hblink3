#!/usr/bin/env python
# Convenience launcher for the HBlink3 dashboard. Equivalent to running
# `python dashboard/server.py`.
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard'))
import server

if __name__ == '__main__':
    server.main()
