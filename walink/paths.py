"""Filesystem locations, defined once.

logs/   per-run logs, failure screenshots, the remote audit log. Sensitive; safe to purge.
state/  the phone lock and the remote rate-limit state. Never purge while walink can run:
        deleting the lock breaks mutual exclusion, deleting the rate file resets the limits.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.yaml"
LOGS = ROOT / "logs"
STATE = ROOT / "state"
