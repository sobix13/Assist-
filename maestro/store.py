from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

DEFAULTS = {"enabled": False, "hourly_seconds": 3600, "daily_hour": 18, "timezone": "Asia/Tehran",
            "auto_recovery": False, "maintenance_until": 0, "report_channel": 0, "job_timeout": 120,
            "retention_days": 7, "disk_min_percent": 5, "memory_min_percent": 5,
            "inventory_daily": True, "telegram_reports": True, "output_limit_mib": 32, "cert_warn_days": 14, 'daily_checkpoint': True}


class Store:
    def __init__(self, path, recover=True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at REAL,actor TEXT,action TEXT,detail TEXT);
CREATE TABLE IF NOT EXISTS targets(id TEXT PRIMARY KEY,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pending(id TEXT PRIMARY KEY,actor TEXT,kind TEXT,payload TEXT,digest TEXT,token_hash TEXT,expires REAL,used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,actor TEXT,kind TEXT,state TEXT,payload TEXT,created REAL,finished REAL,exit_code INTEGER,output TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY,at REAL,kind TEXT,target TEXT,data TEXT);
CREATE TABLE IF NOT EXISTS incidents(key TEXT PRIMARY KEY,target TEXT,code TEXT,first_seen REAL,last_seen REAL,count INTEGER,state TEXT,detail TEXT);
CREATE TABLE IF NOT EXISTS repairs(id INTEGER PRIMARY KEY,at REAL,target TEXT,action TEXT,result TEXT);
CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY,at REAL,kind TEXT,payload TEXT,delivered INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,actor TEXT,at REAL,data TEXT);
CREATE TABLE IF NOT EXISTS deliveries(event_id INTEGER,recipient TEXT,delivered INTEGER DEFAULT 0,PRIMARY KEY(event_id,recipient));
CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,at REAL,actor TEXT,kind TEXT,state TEXT,data TEXT);
CREATE TABLE IF NOT EXISTS backup_runs(id TEXT PRIMARY KEY,at REAL,reason TEXT,state TEXT,data TEXT);
""")
        if recover:
            self.db.execute("UPDATE jobs SET state='interrupted',finished=? WHERE state IN ('queued','running')", (time.time(),))
            self.db.execute("UPDATE pending SET used=1")
            self.db.execute("UPDATE operations SET state='interrupted' WHERE state IN ('preparing','backing_up','running','verifying','restoring')")
            self.db.execute("UPDATE backup_runs SET state='interrupted' WHERE state='running'")
        self.db.commit()
        for private in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            if private.exists():
                private.chmod(0o600)
        if self.get("settings") is None:
            self.put("settings", {**DEFAULTS, "revision": 0})

    def run(self, sql, params=()):
        with self.lock:
            c = self.db.execute(sql, params)
            self.db.commit()
            return c.lastrowid

    def rows(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.rows(sql, params)
        return rows[0] if rows else None

    def get(self, key, default=None):
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def put(self, key, value):
        self.run("INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def settings(self):
        return {**DEFAULTS, **self.get("settings")}

    def save_settings(self, value, revision):
        with self.lock:
            old = self.settings()
            if old["revision"] != revision:
                raise ValueError("Settings changed. Open a new panel before saving.")
            self.put("settings", {**value, "revision": revision + 1})

    def audit(self, actor, action, detail):
        self.run("INSERT INTO audit(at,actor,action,detail) VALUES(?,?,?,?)", (time.time(), actor, action, detail))

    def targets(self):
        return [json.loads(r["data"]) for r in self.rows("SELECT data FROM targets ORDER BY id")]

    def target(self, key):
        row = self.one("SELECT data FROM targets WHERE id=?", (key,))
        return json.loads(row["data"]) if row else None

    def save_target(self, value):
        self.run("INSERT INTO targets VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (value["id"], json.dumps(value)))

    def event(self, kind, payload, recipients=()):
        with self.lock:
            key = self.run("INSERT INTO outbox(at,kind,payload) VALUES(?,?,?)", (time.time(), kind, json.dumps(payload)))
            for recipient in recipients:
                self.run("INSERT OR IGNORE INTO deliveries(event_id,recipient) VALUES(?,?)", (key, recipient))
            return key

    def bind_recipients(self, recipients):
        for row in self.rows("SELECT id FROM outbox WHERE delivered=0"):
            for recipient in recipients:
                self.run("INSERT OR IGNORE INTO deliveries(event_id,recipient) VALUES(?,?)", (row['id'], recipient))

    def events_for(self, recipient):
        return [{**r, 'payload': json.loads(r['payload'])} for r in self.rows(
            "SELECT o.* FROM outbox o JOIN deliveries d ON d.event_id=o.id WHERE d.recipient=? AND d.delivered=0 ORDER BY o.id LIMIT 10", (recipient,))]

    def acknowledge(self, key, recipient):
        self.run("UPDATE deliveries SET delivered=1 WHERE event_id=? AND recipient=?", (key, recipient))
        if not self.one("SELECT 1 FROM deliveries WHERE event_id=? AND delivered=0", (key,)):
            self.run("UPDATE outbox SET delivered=1 WHERE id=?", (key,))

    def operation(self, key, actor, kind, state, data):
        self.run("INSERT INTO operations VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,data=excluded.data",
                 (key, time.time(), actor, kind, state, json.dumps(data)))

    def prune(self, days):
        cutoff = time.time() - days * 86400
        for table, field, extra in (("snapshots", "at", ""), ("audit", "at", ""), ("repairs", "at", ""),
                                    ("outbox", "at", " AND delivered=1"), ("jobs", "finished", "")):
            self.run(f"DELETE FROM {table} WHERE {field}<?{extra}", (cutoff,))
        self.run("DELETE FROM pending WHERE expires<?", (time.time() - 86400,))
        # Keep undelivered incidents, but cap the outbox to prevent a long outage filling disk.
        self.run("DELETE FROM outbox WHERE id NOT IN (SELECT id FROM outbox ORDER BY id DESC LIMIT 2000)")
        self.run("DELETE FROM deliveries WHERE event_id NOT IN (SELECT id FROM outbox)")

    def backup(self, destination):
        with self.lock:
            out = sqlite3.connect(destination)
            try:
                self.db.backup(out)
                if out.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("Backup integrity check failed.")
            finally:
                out.close()

    def close(self):
        self.db.close()
