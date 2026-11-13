"""Small, persistent connection limiter shared by forked websockify workers."""
from contextlib import closing
from pathlib import Path
import sqlite3
import time


class AttemptLimiter:
    def __init__(self, path, per_ip=5, total=30, window=60):
        self.path = str(path)
        self.per_ip = per_ip
        self.total = total
        self.window = window
        Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=3)) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS attempts (ip TEXT NOT NULL, at REAL NOT NULL)')
            connection.execute('CREATE INDEX IF NOT EXISTS attempts_time ON attempts(at)')
            connection.commit()
        Path(path).chmod(0o600)

    def admit(self, ip, now=None):
        """Count every admitted handshake, including ones that later authenticate.

        Disconnection must not reset counters: otherwise repeated wrong passwords
        could bypass the limiter. Counters expire after one minute.
        """
        now = time.time() if now is None else now
        with closing(sqlite3.connect(self.path, timeout=3)) as connection:
            try:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute('DELETE FROM attempts WHERE at <= ?', (now - self.window,))
                total = connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
                count = connection.execute('SELECT COUNT(*) FROM attempts WHERE ip = ?', (ip,)).fetchone()[0]
                admitted = total < self.total and count < self.per_ip
                if admitted:
                    connection.execute('INSERT INTO attempts(ip, at) VALUES (?, ?)', (ip, now))
                connection.commit()
                return admitted
            except Exception:
                connection.rollback()
                raise


def allowed_origin(headers, origins):
    # Forwarded headers are deliberately not trusted. Origin is a browser defense,
    # not authentication: a non-browser attacker can forge it.
    return headers.get('Origin') in origins
