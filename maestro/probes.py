from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from .security import Denied, repo_name, unit_name
from .inventory import cert_expiry

SUITE = (
    ("gate", "ripcars-gate", "gate", "Ripcars-Gate"),
    ("crew", "ripcars-crew", "crew", "Ripcars-Crew"),
    ("raffle", "ripcars-raffle", "raffle", "Ripcars-Raffle"),
    ("verifier", "ripcars-verifier", "verifier", "Verify-bot"),
)


def suite_targets():
    return [{"id": key, "unit": service + ".service", "enabled": False, "expected_running": False,
             "auto_restart": False, "database": f"/var/lib/{service}/{db}.sqlite3", "install": f"/opt/{service}/current",
             "env_file": f"/etc/{service}.env", "backup_dir": f"/var/lib/{service}/backups", "backup_max_hours": 36,
             "url": "", "expected_json_key": "", "repo": "sobix13/" + repo, "revision": 0} for key, service, db, repo in SUITE]


def validate_target(value):
    if not isinstance(value, dict):
        raise Denied("Target must be a JSON object.")
    known = {"id", "unit", "enabled", "expected_running", "auto_restart", "auto_recipe", "database", "install", "env_file", "backup_dir", "backup_max_hours", "url", "expected_json_key", "repo", "revision", "driver", "container", "adapter", "worker_mode", "recovery_owner", "heartbeat_file", "heartbeat_max_seconds", "certificate_file", "cert_warn_days"}
    if set(value) - known or not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", str(value.get("id", ""))):
        raise Denied("Invalid target fields or ID.")
    unit_name(value["unit"])
    if value.get('driver', 'systemd') not in ('systemd', 'docker') or value.get('adapter', 'generic') not in ('generic', 'futarchist'):
        raise Denied('Choose a supported driver and health adapter.')
    if value.get('driver') == 'docker' and not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value.get('container', '')):
        raise Denied('Use an exact container ID or name.')
    if value.get('worker_mode', 'bot') not in ('bot', 'web', 'all') or value.get('recovery_owner', 'assistant') not in ('assistant', 'external'):
        raise Denied('Invalid worker mode or recovery ownership.')
    if value.get('auto_recipe') and not re.fullmatch('[a-z][a-z0-9_-]{0,40}', value['auto_recipe']):
        raise Denied('Choose an exact locally enrolled automatic recipe ID.')
    if 'cert_warn_days' in value and (isinstance(value['cert_warn_days'], bool) or not isinstance(value['cert_warn_days'], int) or not 1 <= value['cert_warn_days'] <= 60):
        raise Denied('Certificate warning must be between one and sixty days.')
    if not isinstance(value.get('heartbeat_max_seconds', 180), int) or not 30 <= value.get('heartbeat_max_seconds', 180) <= 604800:
        raise Denied('Heartbeat age must be between thirty seconds and seven days.')
    for key in ("enabled", "expected_running", "auto_restart"):
        if not isinstance(value.get(key, False), bool):
            raise Denied("Target flags must be true or false.")
    for key in ("database", "install", "env_file", "backup_dir", 'heartbeat_file', 'certificate_file'):
        text = value.get(key, "")
        if not isinstance(text, str) or (text and (not Path(text).is_absolute() or "\x00" in text or len(text) > 500)):
            raise Denied("Target file locations must be absolute paths.")
    if value.get("url"):
        parts = urlsplit(value["url"])
        if parts.username or parts.password or parts.fragment or parts.scheme not in ("http", "https"):
            raise Denied("Use a credential-free HTTP/HTTPS health URL.")
        if parts.scheme == "http" and parts.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise Denied("Plain HTTP health probes are limited to loopback.")
    if value.get("repo"):
        repo_name(value["repo"])
    if not 1 <= int(value.get("backup_max_hours", 36)) <= 168:
        raise Denied("Backup age must be between 1 and 168 hours.")
    if not isinstance(value.get("revision", 0), int) or isinstance(value.get("revision", 0), bool) or value.get("revision", 0) < 0:
        raise Denied("Target revision must be a nonnegative integer.")
    value = {**value, "backup_max_hours": int(value.get("backup_max_hours", 36))}
    return {"enabled": False, "expected_running": False, "auto_restart": False, "database": "", "install": "", "env_file": "",
            "backup_dir": "", "backup_max_hours": 36, "url": "", "expected_json_key": "", "repo": "", "revision": 0,
            'driver': 'systemd', 'adapter': 'generic', 'worker_mode': 'bot', 'recovery_owner': 'assistant',
            'heartbeat_file': '', 'heartbeat_max_seconds': 180, 'certificate_file': '', 'auto_recipe': '', **value}


def duration_seconds(value):
    if str(value).isdigit():
        return int(value) / 1_000_000
    units = {"us": .000001, "ms": .001, "s": 1, "min": 60, "h": 3600, "d": 86400}
    return sum(float(n) * units[u] for n, u in re.findall(r"([0-9.]+)(us|ms|min|s|h|d)", str(value)))


def database_check(path):
    target = Path(path)
    if not target.is_file():
        return {"ok": False, "detail": "Configured database is missing; no file was created."}
    db = sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    deadline = time.monotonic() + 3
    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        rows = [r[0] for r in db.execute("PRAGMA quick_check(1)")]
        return {"ok": rows == ["ok"], "detail": "; ".join(rows), "bytes": target.stat().st_size,
                "wal_bytes": Path(str(target) + "-wal").stat().st_size if Path(str(target) + "-wal").exists() else 0}
    finally:
        db.close()


def backup_check(path, hours):
    folder = Path(path)
    if not folder.is_dir():
        return {"ok": False, "detail": "Configured backup directory is missing."}
    files = [p for p in folder.iterdir() if p.is_file() and not p.is_symlink() and not p.name.endswith(("-wal", "-shm"))]
    newest = max(files, key=lambda p: p.stat().st_mtime, default=None)
    age = (time.time() - newest.stat().st_mtime) / 3600 if newest else None
    return {"ok": age is not None and age <= hours, "detail": "No backup found" if newest is None else newest.name,
            "age_hours": round(age, 2) if age is not None else None, "count": len(files)}


def host_check(settings, paths=('/',)):
    disk = shutil.disk_usage("/")
    free = disk.free * 100 / disk.total
    mem = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, value = line.partition(":")
            mem[key] = int(value.strip().split()[0])
    except (OSError, ValueError):
        pass
    available = mem.get("MemAvailable", 0) * 100 / mem.get("MemTotal", 1)
    fs = os.statvfs("/")
    inodes = fs.f_favail * 100 / fs.f_files if fs.f_files else 100
    issues = []
    if free < settings["disk_min_percent"]:
        issues.append("Root filesystem free space is below the configured threshold.")
    if inodes < 5:
        issues.append("Root filesystem free inodes are below 5 percent.")
    if mem and available < settings["memory_min_percent"]:
        issues.append("Available memory is below the configured threshold.")
    volumes, devices = [], set()
    for path in paths[:50]:
        try:
            device = os.stat(path).st_dev
            if device in devices:
                continue
            devices.add(device)
            usage, info = shutil.disk_usage(path), os.statvfs(path)
            percent = usage.free*100/usage.total
            inode = info.f_favail*100/info.f_files if info.f_files else 100
            volumes.append({'path':str(path), 'free_percent':round(percent,1), 'free_bytes':usage.free, 'inode_free_percent':round(inode,1)})
            if path != '/' and (percent < settings['disk_min_percent'] or inode < 5):
                issues.append('Configured filesystem has low free space/inodes: ' + str(path))
        except OSError:
            issues.append('Configured persistent filesystem is unreadable: ' + str(path))
    return {"ok": not issues, "issues": issues, "disk_free_percent": round(free, 1), "inode_free_percent": round(inodes, 1), 'volumes':volumes,
            "memory_available_percent": round(available, 1) if mem else None, "load": os.getloadavg(), "cpus": os.cpu_count()}


class Probes:
    def __init__(self, runner, redactor, github_token=""):
        self.runner = runner
        self.redactor = redactor
        self.github_token = github_token

    async def service(self, target, daily=False):
        unit = unit_name(target["unit"])
        docker = target.get('driver') == 'docker'
        if docker:
            result = await self.runner.run(['docker', 'inspect', '--format', '{{json .State}}', target['container']], timeout=10)
            try:
                value = json.loads(result.output) if result.code == 0 else {}
            except ValueError:
                value = {}
            fields = {'LoadState': 'loaded' if value else 'not-found', 'ActiveState': 'active' if value.get('Running') else 'failed' if value.get('Status') in ('dead', 'restarting') or value.get('ExitCode', 0) != 0 or value.get('OOMKilled') else 'inactive',
                      'SubState': value.get('Status', 'unknown'), 'MainPID': str(value.get('Pid', 0)), 'WatchdogUSec': '0'}
        else:
            result = await self.runner.run(["systemctl", "show", unit, "--no-pager", "--property=LoadState,ActiveState,SubState,Result,MainPID,ExecMainStatus,NRestarts,WatchdogUSec,WatchdogTimestampMonotonic,UnitFileState,WorkingDirectory,EnvironmentFiles"], timeout=10)
            fields = dict(line.split("=", 1) for line in result.output.splitlines() if "=" in line)
            fields = {key:self.redactor.clean(value) for key,value in fields.items()}
        issues = []
        if result.code or fields.get("LoadState") != "loaded":
            issues.append({"code": "unit_missing", "detail": "Unit is missing or not readable."})
        elif target["expected_running"] and fields.get("ActiveState") != "active":
            issues.append({"code": "unit_down", "detail": "Expected service is not active: " + fields.get("ActiveState", "unknown")})
        elif fields.get("ActiveState") == "active":
            limit = duration_seconds(fields.get("WatchdogUSec", "0"))
            stamp = int(fields.get("WatchdogTimestampMonotonic", "0") or 0) / 1_000_000
            if limit and stamp and time.monotonic() - stamp > max(limit * 2, 120):
                issues.append({"code": "watchdog_stale", "detail": "systemd watchdog heartbeat is stale."})
        report = {"id": target["id"], "unit": unit, "fields": fields, "issues": issues, "checks": {}}
        if docker and value.get('Health', {}).get('Status') == 'unhealthy':
            issues.append({'code': 'endpoint_unhealthy', 'detail': 'Docker health check is unhealthy.'})
        if target.get("env_file"):
            self.redactor.load_env(target["env_file"])
        if target.get("url"):
            endpoint = await self.endpoint(target["url"], target.get("expected_json_key", ""))
            report["checks"]["http"] = endpoint
            if not endpoint["ok"]:
                issues.append({"code": "endpoint_unhealthy", "detail": endpoint["detail"]})
        if target.get('adapter') == 'futarchist' and target['expected_running']:
            checked = await asyncio.to_thread(futarchist_health, target.get('database', ''), target.get('worker_mode', 'bot'))
            report['checks']['workers'] = checked
            if not checked['ok']:
                issues.append({'code': 'worker_unhealthy', 'detail': checked['detail']})
        if target.get('heartbeat_file') and target['expected_running']:
            path = Path(target['heartbeat_file'])
            age = time.time() - path.stat().st_mtime if path.is_file() else None
            good = age is not None and -60 <= age <= target['heartbeat_max_seconds']
            report['checks']['heartbeat'] = {'ok': good, 'age_seconds': age}
            if not good:
                issues.append({'code': 'worker_unhealthy', 'detail': 'Configured successful-work heartbeat is missing or stale.'})
        if daily:
            for key, fn, args in (("database", database_check, (target.get("database"),)),
                                  ("backup", backup_check, (target.get("backup_dir"), target.get("backup_max_hours", 36)))):
                if args[0]:
                    try:
                        checked = await asyncio.to_thread(fn, *args)
                    except (OSError, ValueError, sqlite3.Error) as exc:
                        checked = {"ok": False, "detail": type(exc).__name__ + ": " + self.redactor.clean(exc)}
                    report["checks"][key] = checked
                    if not checked["ok"]:
                        issues.append({"code": key + "_failed", "detail": checked["detail"]})
            if target.get("install"):
                version = Path(target["install"]) / "VERSION"
                report["checks"]["version"] = version.read_text().strip()[:100] if version.is_file() else "VERSION missing"
            if target.get('certificate_file'):
                certificate = await asyncio.to_thread(cert_expiry, target['certificate_file'], target.get('cert_warn_days', 14))
                report['checks']['certificate'] = certificate
                if not certificate['ok']:
                    issues.append({'code': 'certificate_review', 'detail': 'Certificate expiry or decoding requires review.'})
        if issues or daily:
            journal = await self.target_logs(target)
            report["journal"] = journal
        report["ok"] = not issues
        return report

    async def target_logs(self, target):
        if target.get('driver') == 'docker':
            result = await self.runner.run(['docker', 'logs', '--tail', '60', '--since', '1h', target['container']], timeout=10)
            return self.redactor.clean(result.output)[-12000:]
        return await self.logs(target['unit'])

    async def logs(self, unit):
        result = await self.runner.run(["journalctl", "-u", unit_name(unit), "-n", "60", "--since=-1hour", "--no-pager", "-o", "cat"], timeout=10)
        return self.redactor.clean(result.output)[-12000:]

    async def endpoint(self, url, expected_key=""):
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                async with session.get(url, allow_redirects=False) as response:
                    body = await response.content.read(65537)
                    if len(body) > 65536:
                        return {"ok": False, "detail": "Health response exceeds 64 KiB."}
                    good = response.status == 200
                    if expected_key and good:
                        data = json.loads(body)
                        good = isinstance(data, dict) and data.get(expected_key) is True
                    return {"ok": good, "detail": f"HTTP {response.status}; expected health flag {expected_key or '(status only)'}"}
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            return {"ok": False, "detail": type(exc).__name__}

    async def repository(self, name):
        name = repo_name(name)
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Maestro/1.0.0"}
        if self.github_token:
            headers["Authorization"] = "Bearer " + self.github_token
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.get(f"https://api.github.com/repos/{name}/commits?per_page=1", headers=headers, allow_redirects=False) as response:
                if response.status != 200:
                    return {"ok": False, "detail": f"GitHub returned {response.status}; no update was performed."}
                raw = await response.content.read(256001)
                if len(raw) > 256000:
                    return {"ok": False, "detail": "GitHub response too large."}
                rows = json.loads(raw)
                if not isinstance(rows, list) or not rows:
                    return {"ok": False, "detail": "Repository has no readable default-branch commit."}
                row = rows[0]
                return {"ok": True, "sha": row["sha"], "url": row["html_url"], "detail": row["commit"]["message"].splitlines()[0][:160]}


def futarchist_health(path, mode):
    if not Path(path).is_file():
        return {'ok': False, 'detail': 'FUTARCHIST database is missing.'}
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    deadline = time.monotonic() + 3
    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        expected = (['poller', 'inbox', 'outbox', 'maintenance'] if mode in ('bot', 'all') else []) + (['web'] if mode in ('web', 'all') else [])
        rows = {row[0]: (row[1], row[2]) for row in db.execute('SELECT component,last_at,state FROM heartbeats')}
        bad = [name for name in expected if name not in rows or not -60 <= time.time()-rows[name][0] <= 180 or rows[name][1] == 'degraded']
        return {'ok': not bad, 'detail': 'Workers healthy' if not bad else 'Unhealthy workers: ' + ', '.join(bad)}
    finally:
        db.close()
