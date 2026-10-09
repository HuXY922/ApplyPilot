"""Shared read-only policy data; safe in both Web and standalone Radar images."""
import json
from pathlib import Path


def policy():
    return json.loads(Path(__file__).with_name('radar_policy.json').read_text(encoding='utf-8'))
