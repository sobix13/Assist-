"""Bounded read-only host discovery. Finding a service never takes ownership."""
from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path

from .security import unit_name

CATALOG = [
    {'id': 'futarchist-bot', 'unit': 'futarchist-bot.service', 'adapter': 'futarchist', 'worker_mode': 'bot',
     'database': '/srv/futarchist/data/futarchist.sqlite3', 'env_file': '/srv/futarchist/.env', 'repo': 'sobix13/FUTARCHIST'},
    {'id': 'futarchist-web', 'unit': 'futarchist-web.service', 'adapter': 'futarchist', 'worker_mode': 'web',
     'database': '/srv/futarchist/data/futarchist.sqlite3', 'env_file': '/srv/futarchist/.env',
     'url': 'http://127.0.0.1:8765/ready', 'expected_json_key': 'ok', 'repo': 'sobix13/FUTARCHIST'},
    {'id': 'melee-zone', 'unit': 'melee-zone.service', 'adapter': 'generic', 'url': 'http://127.0.0.1:3020/ready',
     'expected_json_key': 'ready', 'recovery_owner': 'external', 'repo': 'sobix13/zone'},
    {'id': 'melee-recovery', 'unit': 'melee-zone-recovery.service', 'adapter': 'generic',
     'url': 'http://127.0.0.1:3021/health', 'recovery_owner': 'external', 'repo': 'sobix13/zone'},
    {'id': 'nginx', 'unit': 'nginx.service'},
]


def safe_remote(value):
    value = re.sub(r'(https?://)[^/@\s]+@', r'\1[REDACTED]@', value)
    return re.sub(r'([?&](?:token|key|password)=)[^&\s]+', r'\1[REDACTED]', value, flags=re.I)[:250]


def scan_projects(roots, limit=3000, seconds=8):
    found, errors, examined = [], [], 0
    deadline = time.monotonic() + seconds
    skip = {'.git', '.venv', 'node_modules', '__pycache__', 'backups', 'releases', 'outputs', 'uploads', 'jobs', '.cache', '.codex', '.local', '.npm'}
    for root in roots:
        base = Path(root)
        try:
            if not base.is_dir() or base.is_symlink():
                continue
        except OSError:
            errors.append(str(base) + ': unreadable root')
            continue
        for parent, directories, files in os.walk(base, followlinks=False, onerror=lambda e: errors.append(str(e.filename) + ': unreadable directory')):
            examined += len(files) + len(directories) + 1
            here = Path(parent)
            depth = len(here.relative_to(base).parts)
            directories[:] = [d for d in directories if d not in skip and not (here/d).is_symlink()] if depth < 3 else []
            if examined > limit or time.monotonic() > deadline:
                return {'projects': found, 'complete': False, 'detail': 'Discovery limit reached. Add specific roots and scan again.', 'examined': examined}
            markers = [x for x in ('package.json', 'requirements.txt', 'pyproject.toml', 'docker-compose.yml', 'compose.yaml', 'VERSION') if x in files]
            if not markers and not (here/'.git').is_dir():
                continue
            item = {'path': str(here), 'markers': markers, 'environment_files': [x for x in files if x == '.env' or x.endswith('.env')], 'remotes': []}
            try:
                config = here/'.git/config'
                if config.is_file() and not config.is_symlink() and config.stat().st_size < 65536:
                    item['remotes'] = [safe_remote(x.strip()) for x in re.findall(r'^\s*url\s*=\s*(.+)$', config.read_text(errors='replace'), re.M)]
                if (here/'VERSION').is_file() and not (here/'VERSION').is_symlink() and (here/'VERSION').stat().st_size < 1000:
                    item['version'] = (here/'VERSION').read_text(errors='replace').strip()[:100]
            except OSError:
                errors.append(str(here) + ': unreadable metadata')
            # This identifies PM2 state without invoking PM2 and accidentally starting a daemon.
            found.append(item)
    return {'projects': found, 'complete': not errors, 'errors': errors, 'examined': examined}


def scan_databases(roots, limit=4000, seconds=5):
    found=[];seen=set();examined=0;deadline=time.monotonic()+seconds;errors=[]
    for root in roots:
        base=Path(root)
        if not base.is_dir() or base.is_symlink():continue
        for here,dirs,names in os.walk(base,followlinks=False,onerror=lambda e:errors.append(str(e.filename)+': unreadable directory')):
            if time.monotonic()>deadline:
                return {'databases':found,'complete':False,'roots':list(roots),'errors':errors[:10],'detail':'Scan deadline reached. Add specific roots for remaining databases.'}
            depth=len(Path(here).relative_to(base).parts)
            dirs[:]=[d for d in dirs if d not in {'.git','.venv','node_modules','__pycache__','.cache','.codex','backups','releases','checkpoints','packages','restic-cache'} and not (Path(here)/d).is_symlink()] if depth<5 else []
            for name in names:
                examined+=1
                if examined>limit or time.monotonic()>deadline or len(found)>=100:return {'databases':found,'complete':False,'roots':list(roots),'errors':errors[:10],'detail':'Scan limit reached. Add specific roots for remaining databases.'}
                p=Path(here)/name
                if p.suffix.lower() not in {'.sqlite3','.sqlite','.db'} or p.is_symlink():continue
                try:
                    with p.open('rb') as file:header=file.read(16)
                    key=str(p.resolve())
                    if header==b'SQLite format 3\x00' and key not in seen:
                        seen.add(key);found.append({'path':key,'bytes':p.stat().st_size,'kind':'sqlite'})
                except OSError:errors.append(str(p)+': unreadable database candidate')
    return {'databases':found,'complete':not errors,'roots':list(roots),'errors':errors[:10]}


def cert_expiry(path, warn_days=14):
    try:
        info = ssl._ssl._test_decode_cert(str(path))
        expires = datetime.strptime(info['notAfter'], '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc)
        days = (expires.timestamp() - time.time()) / 86400
        return {'path': str(path), 'ok': days > warn_days, 'days_remaining': round(days, 1), 'expires': expires.isoformat()}
    except (OSError, ValueError, ssl.SSLError, KeyError) as exc:
        return {'path': str(path), 'ok': False, 'detail': type(exc).__name__}


class Inventory:
    def __init__(self, runner, redactor, roots=('/opt', '/srv', '/root', '/home'), database_roots=None):
        self.runner, self.redactor, self.roots = runner, redactor, list(roots)
        self.database_roots=list(database_roots) if database_roots is not None else list(dict.fromkeys([*self.roots,'/var/lib']))
        self.lock = asyncio.Lock()

    async def collect(self):
        if self.lock.locked():
            raise ValueError('Host discovery is already running.')
        async with self.lock:
            report = {'at': time.time(), 'services': [], 'containers': [], 'timers': [], 'ports': '', 'errors': [], 'pm2': []}
            for key, argv in [('services', ['systemctl', 'list-units', '--all', '--type=service', '--no-pager', '--no-legend', '--plain']),
                              ('timers', ['systemctl', 'list-timers', '--all', '--no-pager', '--no-legend', '--plain']),
                              ('ports', ['ss', '-lntup']), ('failed_units', ['systemctl', '--failed', '--no-pager', '--plain'])]:
                try:
                    result = await self.runner.run(argv, timeout=15)
                    if result.code:
                        report['errors'].append(key + ': command unavailable or failed')
                    if key == 'services':
                        for line in result.output.splitlines()[:500]:
                            words = line.split()
                            if len(words) >= 4:
                                try:
                                    unit = unit_name(words[0])
                                except ValueError:
                                    continue
                                report[key].append({'unit': unit, 'load': words[1], 'active': words[2], 'sub': words[3]})
                    elif key == 'timers':
                        report[key] = [self.redactor.clean(line)[:350] for line in result.output.splitlines()[:200]]
                    else:
                        report[key] = self.redactor.clean(result.output)[:18000]
                except (OSError, ValueError) as exc:
                    report['errors'].append(key + ': ' + type(exc).__name__)
            try:
                result = await self.runner.run(['docker', 'ps', '--all', '--format', '{{json .}}'], timeout=15)
                if not result.code:
                    for line in result.output.splitlines()[:200]:
                        value = json.loads(line)
                        report['containers'].append({key: value.get(key, '') for key in ('ID', 'Names', 'Image', 'Status')})
            except (OSError, ValueError):
                pass
            report['files'] = await asyncio.to_thread(scan_projects, self.roots)
            report['database_inventory'] = await asyncio.to_thread(scan_databases, self.database_roots)
            for home in [Path('/root'), *list(Path('/home').glob('*'))[:50]]:
                state = home/'.pm2/dump.pm2'
                try:
                    if state.is_file() and not state.is_symlink() and state.stat().st_size < 2*1024*1024:
                        for row in json.loads(state.read_text())[:100]:
                            report['pm2'].append({'home': str(home), 'name': row.get('name', ''), 'status': row.get('status', 'unknown'),
                                'detail': 'Saved PM2 declaration. It does not prove the process is running.'})
                except (OSError, ValueError, TypeError, AttributeError):
                    report['errors'].append('Unreadable saved PM2 state: ' + str(home))
            report['cron_files'] = []
            for root in ('/etc/cron.d', '/var/spool/cron/crontabs'):
                try:
                    report['cron_files'] += [str(p) for p in Path(root).glob('*') if p.is_file()][:100]
                except OSError:
                    report['errors'].append('Cron inventory unavailable: ' + root)
            report['mounts'] = []
            try:
                for line in Path('/proc/self/mountinfo').read_text().splitlines()[:300]:
                    left, _, right = line.partition(' - ')
                    fields = left.split()
                    if len(fields) >= 5 and right.split()[0] not in ('proc', 'sysfs', 'tmpfs', 'devtmpfs', 'cgroup2'):
                        report['mounts'].append({'path': fields[4], 'filesystem': right.split()[0]})
            except (OSError, IndexError):
                report['errors'].append('Mount inventory unavailable')
            report['complete'] = not report['errors'] and report['files']['complete']
            return report
