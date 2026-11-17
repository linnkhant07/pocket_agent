"""Short-lived hosted-response cache keyed by the full request fingerprint."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class ResponseCache:
    def __init__(self, path: Path, ttl=86400):
        self.path, self.ttl = path, ttl

    def get(self, key):
        if not self.path.exists():
            return None
        try:
            with sqlite3.connect(self.path) as db:
                row = db.execute('SELECT payload, expires FROM responses WHERE key=?', (key,)).fetchone()
            if row and row[1] > time.time():
                return json.loads(row[0])
        except (sqlite3.Error, ValueError):
            pass
        return None

    def put(self, key, value):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Cache may contain applicant text, so keep it private to the user.
            self.path.touch(mode=0o600, exist_ok=True)
            with sqlite3.connect(self.path) as db:
                db.execute('CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, payload TEXT, expires REAL)')
                db.execute('DELETE FROM responses WHERE expires <= ?', (time.time(),))
                db.execute('INSERT OR REPLACE INTO responses VALUES (?, ?, ?)',
                           (key, json.dumps(value), time.time() + self.ttl))
        except (OSError, sqlite3.Error):
            pass  # A cache failure must never block filling.
