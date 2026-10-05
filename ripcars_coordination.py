"""Rip Cars coordination protocol 2. Identical in all four repositories.

Both legacy lock tables are mirrored inside a single SQLite transaction.
No private bot data or Discord permissions are managed by this module.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import time
from pathlib import Path

PROTOCOL_VERSION = 2
TABLES = (("leases", "key"), ("locks", "name"))


def initialize(connection, error=ValueError):
    connection.execute("CREATE TABLE IF NOT EXISTS suite_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    row = connection.execute("SELECT value FROM suite_meta WHERE key='protocol_version'").fetchone()
    if row and row[0] != str(PROTOCOL_VERSION):
        raise error("Unsupported coordination protocol. Update the suite together; no ownership migration was applied.")
    connection.execute("CREATE TABLE IF NOT EXISTS leases(guild INTEGER NOT NULL,key TEXT NOT NULL,token TEXT NOT NULL,expires REAL NOT NULL,PRIMARY KEY(guild,key))")
    connection.execute("CREATE TABLE IF NOT EXISTS locks(guild INTEGER NOT NULL,name TEXT NOT NULL,token TEXT NOT NULL,expires REAL NOT NULL,PRIMARY KEY(guild,name))")
    duplicate = connection.execute("SELECT guild,object_id FROM resources GROUP BY guild,object_id HAVING COUNT(*)>1 LIMIT 1").fetchone()
    if duplicate:
        raise error("Duplicate coordination resource IDs require explicit administrator review. No binding was removed.")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS suite_resource_identity ON resources(guild,object_id)")
    connection.execute("INSERT OR IGNORE INTO suite_meta VALUES('protocol_version',?)", (str(PROTOCOL_VERSION),))


def claim(connection, guild, name="server-setup", ttl=180, token=None, error=ValueError):
    if not 0 < ttl <= 3600:
        raise error("Lease duration must be greater than zero and at most 3600 seconds.")
    now = time.time()
    for table, key in TABLES:
        row = connection.execute(f"SELECT token,expires FROM {table} WHERE guild=? AND {key}=?", (guild, name)).fetchone()
        if row and row[1] > now:
            raise error("Another Rip Cars bot holds this setup lease. Retry after it finishes.")
    token = token or secrets.token_hex(16)
    for table, key in TABLES:
        connection.execute(f"INSERT INTO {table} VALUES(?,?,?,?) ON CONFLICT(guild,{key}) DO UPDATE SET token=excluded.token,expires=excluded.expires", (guild, name, token, now + ttl))
    return token


def ensure(connection, guild, name, token, error=ValueError):
    now = time.time()
    for table, key in TABLES:
        row = connection.execute(f"SELECT token,expires FROM {table} WHERE guild=? AND {key}=?", (guild, name)).fetchone()
        if not token or not row or row[0] != token or row[1] <= now:
            raise error("Shared setup lease was lost or expired. The next change was stopped.")


def renew(connection, guild, name, token, ttl=180, error=ValueError):
    if not 0 < ttl <= 3600:
        raise error("Invalid lease duration.")
    ensure(connection, guild, name, token, error)
    expiry = time.time() + ttl
    for table, key in TABLES:
        connection.execute(f"UPDATE {table} SET expires=? WHERE guild=? AND {key}=? AND token=?", (expiry, guild, name, token))


def release(connection, guild, name, token):
    for table, key in TABLES:
        connection.execute(f"DELETE FROM {table} WHERE guild=? AND {key}=? AND token=?", (guild, name, token))


def shared_permissions(path):
    """Preserve file owners and group; fix only files owned by this process."""
    for filename in (str(path), str(path) + "-wal", str(path) + "-shm"):
        target = Path(filename)
        if target.exists() and target.stat().st_uid == os.getuid():
            os.chmod(target, 0o660)


def inspect(path, guild=None):
    """Read-only report. Never creates, repairs, releases, or copies a database."""
    target = Path(path).resolve()
    if not target.is_file():
        return {"ok": False, "protocol": None, "issues": ["Shared coordination database is missing."], "resources": [], "leases": []}
    connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        issues = []
        version = None
        if "suite_meta" in tables:
            row = connection.execute("SELECT value FROM suite_meta WHERE key='protocol_version'").fetchone()
            version = int(row[0]) if row else None
        if version != PROTOCOL_VERSION:
            issues.append("Coordination protocol 2 is not initialized. Install current bot releases.")
        if "resources" not in tables:
            issues.append("Shared resource table is missing.")
            resources = []
        else:
            sql = "SELECT guild,key,object_id,kind,owner,state FROM resources"
            resources = [dict(row) for row in connection.execute(sql + (" WHERE guild=?" if guild is not None else "") + " ORDER BY guild,key", (guild,) if guild is not None else ())]
        identities = set()
        for row in resources:
            identity = (row["guild"], row["object_id"])
            if identity in identities:
                issues.append("Duplicate resource identity: " + str(identity))
            identities.add(identity)
            if row["state"] in ("manual", "missing", "partial", "crew-temporary"):
                issues.append("Resource requires controller review: " + row["key"] + " (" + row["state"] + ")")
        live = {}
        for table, key in TABLES:
            if table not in tables:
                issues.append("Missing lease table: " + table)
                continue
            sql = f"SELECT guild,{key} AS name,token,expires FROM {table} WHERE expires>?"
            args = (time.time(),)
            if guild is not None:
                sql += " AND guild=?"
                args += (guild,)
            for row in connection.execute(sql, args):
                live.setdefault((row["guild"], row["name"]), {})[table] = dict(row)
        leases = []
        for identity, rows in sorted(live.items()):
            matching = len(rows) == 2 and rows["leases"]["token"] == rows["locks"]["token"]
            if not matching:
                issues.append("Legacy or divergent lease requires rollout review: " + str(identity))
            leases.append({"guild": identity[0], "name": identity[1], "mirrored": matching, "expires": min(row["expires"] for row in rows.values())})
        return {"ok": not issues, "protocol": version, "issues": issues, "resources": resources, "leases": leases}
    finally:
        connection.close()
