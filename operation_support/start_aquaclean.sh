#!/usr/bin/env bash

# for what reason ever cronolog, not logrotate
/usr/bin/python /absolute/path/to/aquaclean_console_app/main.py  2>&1 | /usr/bin/cronolog /absolute/path/to/logs/aquaclean_%Y.%m.%d.log
