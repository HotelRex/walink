"""Filesystem locations, defined once.

logs/   per-run logs, failure screenshots, the remote audit log. Sensitive; safe to purge.
state/  the phone lock. Never purge while walink can run: deleting a held lock lets a second run drive the phone.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.yaml"
LOGS = ROOT / "logs"
STATE = ROOT / "state"
