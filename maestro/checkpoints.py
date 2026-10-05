"""Consistent database exports and verified Restic checkpoints before execution.

Credentials and coverage are enrolled locally. A partial Restic backup is a failed
checkpoint. Restores are staged offline and never overwrite a production database.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .security import Denied, isolated_env, read_env, unit_name


def protected_json(path):
    path = Path(path)
    stat = path.stat()
    if path.is_symlink() or not path.is_file() or stat.st_uid != 0 or stat.st_mode & 0o077:
        raise Denied('Backup configuration must be a root-owned regular file with mode 0600.')
    return json.loads(path.read_text())


def sqlite_snapshot(source, destination, timeout=60):
    path = Path(source).resolve()
    if not path.is_file():
        raise Denied('A declared SQLite database is missing.')
    deadline = time.monotonic() + timeout
    src = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)
    dst = sqlite3.connect(destination)
    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise TimeoutError('SQLite checkpoint deadline reached.')
    try:
        src.backup(dst, pages=128, sleep=0.05, progress=progress)
        dst.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise Denied('SQLite checkpoint integrity check failed.')
        dst.commit()
    finally:
        dst.close()
        src.close()
    Path(destination).chmod(0o600)


def validate_config(value):
    if not isinstance(value, dict) or value.get('coverage_reviewed') is not True:
        raise Denied('Review backup coverage on the VPS before enabling execution.')
    paths = value.get('paths', [])
    if not isinstance(paths, list) or not paths or len(paths) > 50:
        raise Denied('Set one to fifty persistent filesystem backup roots.')
    for path in paths:
        if not isinstance(path, str) or not Path(path).is_absolute() or any(c in path for c in '\x00\n\r') or not Path(path).exists():
            raise Denied('A configured backup root is invalid or missing.')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', value.get('env_file', '')):
        raise Denied('Set an absolute protected Restic environment file.')
    databases = value.get('databases', [])
    if not isinstance(databases, list) or len(databases) > 100:
        raise Denied('Database coverage is limited to one hundred declared exports.')
    names = set()
    for item in databases:
        if not isinstance(item, dict) or not re.fullmatch('[a-z][a-z0-9_-]{0,40}', item.get('name', '')) or item['name'] in names:
            raise Denied('Each database export needs a unique simple name.')
        names.add(item['name'])
        if item.get('kind') == 'sqlite':
            if not isinstance(item.get('path'), str) or not Path(item['path']).is_absolute():
                raise Denied('SQLite sources require an absolute file path.')
        elif item.get('kind') == 'export':
            argv = item.get('argv')
            if not isinstance(argv, list) or not argv or len(argv) > 50 or not Path(argv[0]).is_absolute() or any(not isinstance(x, str) or '\x00' in x or len(x) > 1000 for x in argv):
                raise Denied('Export hooks require a locally reviewed absolute executable and argument array.')
        else:
            raise Denied('Choose sqlite or a locally reviewed export hook.')
    units = value.get('quiesce_units', [])
    if not isinstance(units, list) or len(units) > 100:
        raise Denied('Set a bounded list of writer services to pause.')
    for unit in units:
        unit_name(unit)
        if unit.startswith('maestro'):
            raise Denied('A checkpoint cannot stop its own agent or interfaces.')
    for key, low, high, default in [('timeout_seconds', 30, 7200, 1800), ('export_limit_mib', 1, 4096, 512)]:
        item = value.get(key, default)
        if isinstance(item, bool) or not isinstance(item, int) or not low <= item <= high:
            raise Denied('Invalid checkpoint limit: ' + key)
    excludes = value.get('exclude', [])
    if not isinstance(excludes, list) or len(excludes) > 100 or any(not isinstance(x, str) or '\x00' in x or '\n' in x for x in excludes):
        raise Denied('Invalid backup exclusions.')
    return value


class Checkpoints:
    def __init__(self, policy, root, store, redactor):
        self.policy, self.store, self.redactor = policy, store, redactor
        self.root = Path(root) / 'checkpoints'
        self.root.mkdir(exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.lock = asyncio.Lock()
        self.last_tick = 0
        self.busy = False
        self.restic = policy.data.get('restic_binary', 'restic')
        if self.restic != 'restic' and not Path(self.restic).is_absolute():
            raise Denied('Restic must use its installed command or a root-enrolled absolute executable.')

    def config(self):
        path = self.policy.data.get('backup_config', '/etc/maestro/backup.json')
        return validate_config(protected_json(path))

    def status(self):
        try:
            cfg = self.config()
            self.environment(cfg)
            ready = bool(shutil.which(self.restic))
            return {'required': self.policy.backup_required, 'configured': ready, 'paths': cfg['paths'],
                    'database_exports': [x['name'] for x in cfg.get('databases', [])],
                    'quiesce_units': cfg.get('quiesce_units', []), 'busy': self.busy,
                    'detail': 'Ready for a checkpoint attempt' if ready else 'Install Restic on the VPS.'}
        except (OSError, ValueError, KeyError) as exc:
            return {'required': self.policy.backup_required, 'configured': False, 'busy': self.busy,
                    'detail': self.redactor.clean(exc)[:250]}

    def environment(self, cfg):
        path = Path(cfg['env_file'])
        stat = path.stat()
        if not path.is_file() or path.is_symlink() or stat.st_uid != 0 or stat.st_mode & 0o077:
            raise Denied('Restic credentials must be root-owned mode 0600.')
        values = read_env(path)
        if not values.get('RESTIC_REPOSITORY') or not (values.get('RESTIC_PASSWORD') or values.get('RESTIC_PASSWORD_FILE')):
            raise Denied('Configure the Restic repository and protected password.')
        self.redactor.load_env(path)
        for item in values.values():
            if len(item) >= 8:
                self.redactor.values.add(item)
        return {**isolated_env(self.root), **values}

    @contextmanager
    def process_lock(self):
        with (self.root/'repository.lock').open('a') as file:
            os.chmod(file.name, 0o600)
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Denied('Another process is using the checkpoint repository. Try after it finishes.') from None
            yield

    async def command(self, argv, env, timeout, output_file=None, max_bytes=512*1024*1024):
        if argv[0] == 'restic':
            argv = [self.restic, *argv[1:]]
        proc = await asyncio.create_subprocess_exec(*argv, env=env, cwd=self.root,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True)
        buffers = {'stdout': bytearray(), 'stderr': bytearray()}
        total = 0
        limited = False
        file = Path(output_file).open('xb') if output_file else None
        async def drain(stream, label):
            nonlocal total, limited
            try:
                while block := await stream.read(65536):
                    if label == 'stdout' and file:
                        total += len(block)
                        if total > max_bytes:
                            limited = True
                            try:
                                os.killpg(proc.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            continue
                        file.write(block)
                    else:
                        buffers[label].extend(block)
                        if len(buffers[label]) > 128*1024:
                            del buffers[label][:-128*1024]
            except BaseException:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                raise
        tasks = [asyncio.create_task(drain(proc.stdout, 'stdout')), asyncio.create_task(drain(proc.stderr, 'stderr'))]
        async def tick():
            while proc.returncode is None:
                self.last_tick = time.monotonic()
                await asyncio.sleep(10)
        beat = asyncio.create_task(tick())
        try:
            await asyncio.wait_for(proc.wait(), timeout)
            await asyncio.wait_for(asyncio.gather(*tasks), 10)
            if limited:
                raise Denied('Database export exceeded the configured size limit.')
            return proc.returncode, (buffers['stdout'] + b'\n' + buffers['stderr']).decode('utf-8', 'replace')
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
            beat.cancel()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(beat, *tasks, return_exceptions=True)
            if file:
                try:
                    file.flush()
                    os.fsync(file.fileno())
                finally:
                    file.close()
                Path(output_file).chmod(0o600)

    async def create(self, reason, targets=()):
        if self.lock.locked():
            raise Denied('A checkpoint or restore is already running.')
        with self.process_lock():
            return await self._create(reason, targets)

    async def _create(self, reason, targets):
        async with self.lock:
            self.busy, self.last_tick = True, time.monotonic()
            key = secrets.token_hex(12)
            stage = self.root / ('checkpoint-' + key)
            stage.mkdir(mode=0o700)
            record = {'reason': reason, 'stage': str(stage), 'exports': [], 'resumed': [], 'snapshot_id': ''}
            self.store.run('INSERT INTO backup_runs VALUES(?,?,?,?,?)', (key, time.time(), reason, 'running', json.dumps(record)))
            stopped = []
            try:
                cfg = self.config()
                env = self.environment(cfg)
                dbs = list(cfg.get('databases', []))
                declared = {str(Path(x['path']).resolve()) for x in dbs if x['kind'] == 'sqlite'}
                # Every enrolled local SQLite target is included automatically, even if not
                # yet enabled for monitoring. Missing optional installations are skipped.
                for target in targets:
                    path = target.get('database', '')
                    if path and Path(path).is_file() and str(Path(path).resolve()) not in declared:
                        dbs.append({'name': 'target-' + target['id'], 'kind': 'sqlite', 'path': path})
                        declared.add(str(Path(path).resolve()))
                for unit in cfg.get('quiesce_units', []):
                    code, state = await self.command(['systemctl', 'is-active', unit], env, 10)
                    if code == 0 and state.strip() == 'active':
                        stopped.append(unit)
                        record['stopped_units'] = stopped.copy()
                        self.store.run('UPDATE backup_runs SET data=? WHERE id=?', (json.dumps(record), key))
                        code, text = await self.command(['systemctl', 'stop', unit], env, 90)
                        if code:
                            raise Denied('A configured writer could not be paused: ' + unit)
                for item in dbs:
                    self.last_tick = time.monotonic()
                    dest = stage / (item['name'] + ('.sqlite3' if item['kind'] == 'sqlite' else '.dump'))
                    if item['kind'] == 'sqlite':
                        await asyncio.to_thread(sqlite_snapshot, item['path'], dest)
                    else:
                        code, text = await self.command(item['argv'], env, cfg.get('timeout_seconds', 1800),
                            output_file=dest, max_bytes=cfg.get('export_limit_mib', 512)*1024*1024)
                        if code or dest.stat().st_size == 0:
                            raise Denied('Database export failed: ' + item['name'])
                    record['exports'].append({'name': item['name'], 'kind': item['kind'], 'source': item.get('path', '(export hook)'),
                        'file': str(dest), 'sha256': await asyncio.to_thread(file_hash, dest), 'bytes': dest.stat().st_size})
                (stage / 'manifest.json').write_text(json.dumps(record, indent=2))
                argv = ['restic', 'backup', '--json', '--tag', 'maestro', '--tag', 'checkpoint:' + key]
                # Virtual filesystems, our private staging tree and a local Restic
                # repository are excluded from broad filesystem roots. This checkpoint's
                # explicit export directory is supplied separately, outside these roots.
                exclude = ['/proc', '/sys', '/dev', '/run', '/tmp', '/var/tmp'] + cfg.get('exclude', [])
                exclude += [str(p) for p in self.root.iterdir() if p != stage]
                repository = env['RESTIC_REPOSITORY']
                if repository.startswith('/'):
                    exclude.append(repository)
                for value in exclude:
                    argv += ['--exclude', value]
                if cfg.get('one_file_system', False):
                    argv.append('--one-file-system')
                argv += ['--', *cfg['paths'], str(stage)]
                code, text = await self.command(argv, env, cfg.get('timeout_seconds', 1800))
                summaries = []
                for line in text.splitlines():
                    try:
                        value = json.loads(line)
                        if value.get('message_type') == 'summary':
                            summaries.append(value)
                    except (ValueError, AttributeError):
                        pass
                sid = summaries[-1].get('snapshot_id', '') if summaries else ''
                if code != 0 or not re.fullmatch('[a-f0-9]{8,64}', sid):
                    raise Denied('Restic did not complete a full checkpoint. No command is authorized. ' + self.redactor.clean(text)[-300:])
                record['snapshot_id'] = sid
                code, text = await self.command(['restic', 'check'], env, cfg.get('timeout_seconds', 1800))
                if code:
                    raise Denied('Backup repository integrity check failed. Execution remains blocked.')
                record['repository_check'] = True
                # Prove this checkpoint's exports can be restored, not just that
                # a snapshot ID was returned. Ordinary filesystem data is checked
                # by repository metadata now and rotating data reads daily.
                verify = self.root / ('verify-' + key)
                verify.mkdir(mode=0o700)
                code, text = await self.command(['restic', 'restore', sid, '--verify', '--include', str(stage), '--target', str(verify)],
                    env, cfg.get('timeout_seconds', 1800))
                if code:
                    raise Denied('Checkpoint export restore verification failed. Execution remains blocked.')
                for export in record['exports']:
                    restored = verify / export['file'].lstrip('/')
                    if not restored.is_file() or await asyncio.to_thread(file_hash, restored) != export['sha256']:
                        raise Denied('Checkpoint database export checksum verification failed.')
                manifest = verify / str(stage / 'manifest.json').lstrip('/')
                if not manifest.is_file():
                    raise Denied('Checkpoint manifest could not be restored. Execution remains blocked.')
                record['export_restore_verified'] = True
                shutil.rmtree(verify)
                state = 'completed'
            except BaseException as exc:
                state = 'interrupted' if isinstance(exc, asyncio.CancelledError) else 'failed'
                record['error'] = self.redactor.clean(type(exc).__name__ + ': ' + str(exc))[:500]
                raise
            finally:
                # Resuming configured writers is mandatory even after backup failure.
                for unit in reversed(stopped):
                    try:
                        code, text = await self.command(['systemctl', 'start', unit], env, 90)
                        record['resumed'].append({'unit': unit, 'ok': code == 0})
                        if code:
                            state = 'failed'
                    except BaseException:
                        record['resumed'].append({'unit': unit, 'ok': False})
                        state = 'failed'
                self.store.run('UPDATE backup_runs SET state=?,data=? WHERE id=?', (state, json.dumps(record), key))
                self.busy = False
                if state == 'completed':
                    shutil.rmtree(stage)
            if state != 'completed':
                raise Denied('Backup completed but a paused service did not resume. Execution remains blocked.')
            return {'id': key, **record, 'state': state}

    async def inspect_repository(self, read_data=True):
        if self.lock.locked():
            return {'ok': True, 'deferred': True, 'detail': 'Another checkpoint or restore is active.'}
        with self.process_lock():
            return await self._inspect_locked(read_data)

    async def _inspect_locked(self, read_data):
        async with self.lock:
            self.busy, self.last_tick = True, time.monotonic()
            try:
                return await self._inspect_repository(read_data)
            finally:
                self.busy = False

    async def _inspect_repository(self, read_data):
        cfg = self.config()
        part = datetime_weekday()
        argv = ['restic', 'check'] + (['--read-data-subset=' + str(part) + '/7'] if read_data else [])
        code, text = await self.command(argv, self.environment(cfg), cfg.get('timeout_seconds', 1800))
        return {'ok': code == 0, 'exit_code': code, 'detail': self.redactor.clean(text)[-1000:]}

    async def restore_stage(self, checkpoint_id):
        if self.lock.locked():
            raise Denied('A checkpoint or restore is already running.')
        with self.process_lock():
            return await self._restore_locked(checkpoint_id)

    async def restore_selected(self, checkpoint_id, paths):
        if self.lock.locked() or not paths or len(paths)>1200 or any(not isinstance(p,str) or not Path(p).is_absolute() or '\x00' in p for p in paths):
            raise Denied('Selected restore is busy or its path inventory is invalid.')
        with self.process_lock():
            async with self.lock:
                self.busy,self.last_tick=True,time.monotonic()
                destination=self.root/('package-restore-'+secrets.token_hex(12))
                destination.mkdir(mode=0o700)
                try:
                    row=self.store.one("SELECT data FROM backup_runs WHERE id=? AND state='completed'",(checkpoint_id,))
                    if not row:raise Denied('Select a completed checkpoint.')
                    record=json.loads(row['data']);cfg=self.config()
                    argv=['restic','restore',record['snapshot_id'],'--verify','--target',str(destination)]
                    for path in paths:argv+=['--include',path]
                    code,_=await self.command(argv,self.environment(cfg),cfg.get('timeout_seconds',1800))
                    if code:raise Denied('Selected snapshot restore failed.')
                    return {'destination':str(destination),'verified':True}
                except BaseException:
                    shutil.rmtree(destination);raise
                finally:self.busy=False

    async def _restore_locked(self, checkpoint_id):
        async with self.lock:
            self.busy, self.last_tick = True, time.monotonic()
            try:
                return await self._restore_stage(checkpoint_id)
            finally:
                self.busy = False

    async def _restore_stage(self, checkpoint_id):
        row = self.store.one("SELECT * FROM backup_runs WHERE id=? AND state='completed'", (checkpoint_id,))
        if not row:
            raise Denied('Select a completed checkpoint.')
        record = json.loads(row['data'])
        cfg = self.config()
        destination = self.root / ('restore-' + secrets.token_hex(12))
        destination.mkdir(mode=0o700)
        # --verify checks restored data. A fresh destination never touches production.
        code, text = await self.command(['restic', 'restore', record['snapshot_id'], '--verify', '--target', str(destination)],
            self.environment(cfg), cfg.get('timeout_seconds', 1800))
        if code:
            raise Denied('Offline restore did not complete. Partial files remain private for investigation.')
        for export in record.get('exports', []):
            restored = destination / export['file'].lstrip('/')
            if not restored.is_file() or await asyncio.to_thread(file_hash, restored) != export['sha256']:
                raise Denied('A restored database export failed its recorded checksum.')
        return {'checkpoint': checkpoint_id, 'destination': str(destination), 'verified': True,
                'detail': 'Offline files only. Production data and service configuration were not overwritten.'}


def file_hash(path):
    sha = hashlib.sha256()
    with Path(path).open('rb') as file:
        while block := file.read(1024*1024):
            sha.update(block)
    return sha.hexdigest()


def datetime_weekday():
    return int(time.strftime('%w', time.gmtime())) + 1
