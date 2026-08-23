#!/usr/bin/env python3
"""
generate_uuid.py - creates (or reads) a persistent UUID file identifying this
Pi's fan actuator to the backend.

The backend finds-or-creates an Actuator row by deviceUUID (see
/api/registerActuator in server.js). This file must survive reboots and
script restarts - regenerating it on every run would make the backend think
a brand new fan showed up each time, instead of recognizing the same one.

Usage (standalone):
    python3 generate_uuid.py                  # creates/reads fan_uuid.txt in cwd
    python3 generate_uuid.py /path/to/file.txt

Usage (import, e.g. from fantest.py):
    from generate_uuid import get_or_create_uuid
    device_uuid = get_or_create_uuid("fan_uuid.txt")
"""

import sys
import uuid
from pathlib import Path


def get_or_create_uuid(path):
    """Return the UUID stored at `path`, creating the file with a fresh one
    if it doesn't exist (or is empty/unreadable)."""
    p = Path(path)

    if p.exists():
        existing = p.read_text().strip()
        if existing:
            return existing
        # File exists but is empty - fall through and regenerate.

    new_uuid = str(uuid.uuid4())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(new_uuid + "\n")
    return new_uuid


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "device_uuid.txt"
    result = get_or_create_uuid(target)
    print(f"UUID ({target}): {result}")
