from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import pwd
import re
import secrets
import shutil
import socket
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__
from .files import Files, MAX_UPLOAD
from .process import Runner
from .probes import Probes, host_check, suite_targets, validate_target
from .runbooks import AUTO_BLOCKERS, DIAGNOSTICS, EVIDENCE_DIAGNOSTICS, SERVICE_ACTIONS, classify
from .security import Denied, Redactor, digest, read_env, secret_hash, unit_name
from .store import DEFAULTS, Store
from .checkpoints import Checkpoints
from .inventory import CATALOG, Inventory
from .output import OutputVault
from .recipes import FileRollback, RecipeBook
from .access import Access
from .discovery import reconcile
from .packages import Packages
from .applications import Applications


class Engine:
    def __init__(self, policy, state_dir, runner=None, probes=None):
        self.policy = policy
        self.root = Path(state_dir)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o711)
        self.root.chmod(0o711)
        self.store = Store(self.root / "maestro.sqlite3")
        self.access = Access(policy, self.store)
        policy.delegate_checker = self.access.user
        self.runner = runner or Runner()
        self.redactor = Redactor([f["token"] for f in policy.frontends] + [policy.data.get("github_token", "")])
        for path in policy.data.get('secret_envs', ['/etc/maestro/frontend.env', '/etc/maestro/telegram.env']):
            self.redactor.load_env(path)
        self.probes = probes or Probes(self.runner, self.redactor, policy.data.get("github_token", ""))
        self.applications = Applications(policy, self.store, self.redactor, self.probes, self.runner)
        self.files = Files(self.root / "uploads", self.store, policy.same_owner)
        self.checkpoints = Checkpoints(policy, self.root, self.store, self.redactor)
        self.packages = Packages(self.root/'packages', self.checkpoints, self.store)
        self.recipes = RecipeBook(policy)
        self.inventory = Inventory(self.runner, self.redactor, policy.data.get('inventory_roots', ['/opt', '/srv', '/root', '/home']),policy.data.get('database_roots'))
        self.outputs = OutputVault(self.root/'outputs', self.redactor)
        self.output_lock = asyncio.Lock()
        self.mutation_lock = asyncio.Lock()
        self.store.bind_recipients(policy.owners)
        self.workspaces = self.root / "jobs"
        self.workspaces.mkdir(mode=0o711, exist_ok=True)
        self.workspaces.chmod(0o711)
        self.jobs = {}
        self.monitor_lock = asyncio.Lock()
        self.started = time.monotonic()
        self.scheduler_tick = time.monotonic()
        self.scheduler_error = ""
        if not self.store.targets():
            for target in suite_targets():
                self.store.save_target(target)
            self.store.save_target(validate_target({"id": "maestro", "unit": "maestro-discord.service", "enabled": False,
                                    "expected_running": False, "auto_restart": False, "database": str(self.store.path),
                                    "install": "/opt/maestro/current", "env_file": "/etc/maestro/frontend.env"}))
        for target in CATALOG:
            if not self.store.target(target['id']):
                self.store.save_target(validate_target(target))
        for row in self.store.rows("SELECT id,data FROM backup_runs WHERE state='interrupted' ORDER BY at DESC LIMIT 20"):
            record = json.loads(row['data'])
            if record.get('stopped_units') and not self.store.get('interrupted_backup_reported:' + row['id']):
                self._event('checkpoint_review', {'checkpoint':row['id'], 'stopped_units':record['stopped_units'],
                            'detail':'The agent stopped during a checkpoint. Check whether these writers resumed; use a reviewed service action if needed. No commands were replayed.'})
                self.store.put('interrupted_backup_reported:' + row['id'], True)

    def actor(self, actor):
        self.access.user(actor)

    def _audit(self, actor, action, detail):
        self.store.audit(actor, action, self.redactor.clean(detail))

    def _event(self, kind, payload):
        # Every event is bounded and redacted before it enters the durable delivery queue.
        def clean(value):
            if isinstance(value, str):
                return self.redactor.clean(value)
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()}
            if isinstance(value, (tuple, list)):
                return [clean(v) for v in value]
            return value
        value = clean(payload)
        self.store.event(kind, value, self.policy.owners)

    def status(self):
        return {"version": __version__, "uptime_seconds": int(time.monotonic() - self.started), "settings": self.store.settings(),
                "scheduler_age_seconds": round(time.monotonic() - self.scheduler_tick), "scheduler_error": self.scheduler_error,
                "jobs_active": len(self.jobs), "open_incidents": self.store.rows("SELECT * FROM incidents WHERE state='open' ORDER BY last_seen DESC LIMIT 30"),
                "pending_reports": self.store.one("SELECT COUNT(*) AS n FROM outbox WHERE delivered=0")["n"], "targets": self.store.targets(),
                'checkpoint': self.checkpoints.status(), 'operations': self.store.rows('SELECT id,at,kind,state FROM operations ORDER BY at DESC LIMIT 20')}

    async def dispatch(self, actor, action, params):
        self.actor(actor)
        if not isinstance(params, dict):
            raise Denied("Parameters must be an object.")
        access = self.access.authorize(actor, action, params)
        if action == 'whoami':
            return access
        if action == 'package_part':
            return self.packages.part(actor,params.get('id'),params.get('offset',0),self.policy)
        if action == 'applications':
            return [{'id':v['id'],'kind':v['kind'],'target':v['target']} for v in self.applications.all()]
        if action == 'application':
            return self.applications.read(params.get('id'))
        if action == 'inspect_target':
            target=self.store.target(params.get('target'))
            if not target:raise Denied('Choose an existing target.')
            report=await self.probes.service(target)
            fields=report.get('fields',{})
            if not target.get('install') and fields.get('WorkingDirectory','').startswith('/'):
                target['install']=fields['WorkingDirectory']
            envs=re.findall(r'(/[^\s()]+)\s+\(ignore_errors=(?:yes|no)\)',fields.get('EnvironmentFiles',''))
            if not target.get('env_file') and len(envs)==1 and Path(envs[0]).is_file():target['env_file']=envs[0]
            if target.get('env_file') and not target.get('database'):
                values=read_env(target['env_file']);database=values.get('DATABASE_PATH') or values.get('DB_PATH')
                if database:
                    p=Path(database) if Path(database).is_absolute() else Path(target.get('install') or '/')/database
                    if p.is_file() and not p.is_symlink():
                        with p.open('rb') as f:header=f.read(16)
                        if header==b'SQLite format 3\x00':target['database']=str(p.resolve())
            target['revision']+=1;target=validate_target(target);self.store.save_target(target)
            return target
        if action == 'access_list':
            return self.access.all(actor)
        if action == 'access_grant':
            return self.access.grant(actor, params)
        if action == 'access_revoke':
            value = self.access.revoke(actor, params.get('actor'))
            for row in self.store.rows("SELECT id FROM jobs WHERE actor=? AND state IN ('queued','running')", (params.get('actor'),)):
                if row['id'] in self.jobs:
                    self.jobs[row['id']].cancel()
            return value
        if access['role'] != 'superadmin' and action == 'status':
            return {'version': __version__, 'role': access['role'], 'targets': [t for t in self.store.targets() if t['id'] in access['targets']],
                    'incidents': [r for r in self.store.rows("SELECT * FROM incidents WHERE state='open'") if r['target'] in access['targets']]}
        if action == "status":
            return self.status()
        if action == "settings":
            return self.store.settings()
        if action == "save_settings":
            return self.save_settings(actor, params)
        if action == "targets":
            return [t for t in self.store.targets() if access['role'] == 'superadmin' or t['id'] in access['targets']]
        if action == 'inventory':
            result = await self.inventory.collect()
            self.store.put('inventory', result)
            result['new_targets'] = reconcile(self.store, result)
            self._audit(actor, 'inventory', 'Read-only host inventory refreshed')
            return result
        if action == 'checkpoint_status':
            return self.checkpoints.status()
        if action == 'checkpoints':
            return [{**r, 'data': json.loads(r['data'])} for r in self.store.rows('SELECT * FROM backup_runs ORDER BY at DESC LIMIT 30')]
        if action == 'recipes':
            return self.recipes.all()
        if action == 'job_output':
            self.job(actor, params.get('id'))
            return self.outputs.part(params['id'], params.get('offset', 0), params.get('compressed', False))
        if action == "discover":
            found = []
            for target in self.store.targets():
                report = await self.probes.service({**target, "expected_running": False}, daily=False)
                if report["fields"].get("LoadState") == "loaded":
                    target["enabled"] = True
                    target["expected_running"] = report["fields"].get("ActiveState") == "active"
                    working = report['fields'].get('WorkingDirectory', '')
                    if working.startswith('/') and Path(working).is_dir():
                        target['install'] = working
                    environment_files = re.findall(r'(/[^\s()]+)\s+\(ignore_errors=(?:yes|no)\)', report['fields'].get('EnvironmentFiles', ''))
                    if len(environment_files) == 1 and Path(environment_files[0]).is_file():
                        target['env_file'] = environment_files[0]
                    if target.get("env_file") and Path(target["env_file"]).is_file():
                        env = read_env(target["env_file"])
                        database = env.get("DATABASE_PATH") or env.get("DB_PATH")
                        if database and not Path(database).is_absolute() and target.get('install'):
                            database = str(Path(target['install']) / database)
                        if database and Path(database).is_absolute():
                            target["database"] = database
                            target["backup_dir"] = env.get("BACKUP_PATH") or str(Path(database).parent / "backups")
                    self.store.save_target(validate_target(target))
                    target["revision"] = target.get("revision", 0) + 1
                    self.store.save_target(target)
                    found.append(target["id"])
            self._audit(actor, "discover", ",".join(found))
            return {"found": found, "detail": "Installed units enrolled. Stopped services remain intentionally stopped. Automatic restart flags were preserved."}
        if action == "save_target":
            target = validate_target(params)
            old = self.store.target(target["id"])
            if old and target["revision"] != old.get("revision", 0):
                raise Denied("Target changed. Open a new panel before saving.")
            target["revision"] += 1
            duplicate = next((t for t in self.store.targets() if t['id'] != target['id'] and
                              t.get('driver', 'systemd') == target.get('driver', 'systemd') and
                              (t.get('container') == target.get('container') if target.get('driver') == 'docker' else t['unit'] == target['unit'])), None)
            if duplicate:
                raise Denied('This service/container is already enrolled as ' + duplicate['id'] + '. Edit that target instead.')
            if target["unit"] == "maestro-agent.service" and (target["auto_restart"] or target.get('auto_recipe')):
                raise Denied("The backend cannot automatically restart itself. Its systemd watchdog supervises it.")
            self.store.save_target(target)
            self._audit(actor, "target_saved", json.dumps(target))
            return target
        if action == "check":
            kind = params.get("kind", "hourly")
            if kind not in ("watch", "hourly", "daily"):
                raise Denied("Choose watch, hourly or daily.")
            result = await self.check_cycle(kind, params.get("target"), manual=True)
            return result if access['role'] == 'superadmin' else {'kind': kind, 'targets': result['targets']}
        if action == "diagnostic":
            name = params.get("name")
            if name not in DIAGNOSTICS:
                raise Denied("Unknown diagnostic template.")
            result = await self.runner.run(DIAGNOSTICS[name], timeout=15)
            self._audit(actor, "diagnostic", name)
            return {"code": result.code, "output": self.redactor.clean(result.output)}
        if action == "logs":
            target = self.store.target(params.get("target"))
            if not target:
                raise Denied("Choose a configured target.")
            return {"output": await self.probes.target_logs(target)}
        if action == "prepare":
            if params.get('kind')=='application':
                plan=self.applications.plan(params.get('payload',{}))
                validation=await self.applications.preflight(plan)
                params={**params,'payload':{**params.get('payload',{}),'validation':validation}}
            return self.prepare(actor, params)
        if action == "confirm":
            return self.confirm(actor, params)
        if action == "discard":
            row = self.store.one("SELECT * FROM pending WHERE id=? AND actor=?", (params.get("id"), actor))
            if not row or row["used"] or not secrets.compare_digest(row["token_hash"], secret_hash(str(params.get("token", "")))):
                raise Denied("Review is missing, already submitted or already discarded. Check job history.")
            self.store.run("UPDATE pending SET used=1 WHERE id=?", (row["id"],))
            return {"discarded": True}
        if action == "job":
            return self.job(actor, params.get("id"))
        if action == "jobs":
            return [r for r in self.store.rows('SELECT id,actor,kind,state,created,finished,exit_code FROM jobs ORDER BY created DESC LIMIT 100') if self.policy.same_owner(actor, r['actor'])][:20]
        if action == "cancel":
            key = params.get("id")
            self.job(actor, key)
            if key in self.jobs:
                self.jobs[key].cancel()
                self._audit(actor, "cancel", key)
                return {"cancel_requested": True}
            return {"cancel_requested": False}
        if action == "stdin":
            self.job(actor, params.get("id"))
            await self.runner.send_input(params["id"], str(params.get("text", "")))
            self._audit(actor, "stdin", params["id"] + "; input content omitted")
            return {"sent": True}
        if action == "eof":
            self.job(actor, params.get("id"))
            proc = self.runner.active.get(params["id"])
            if not proc or not proc.stdin:
                raise Denied("This job has no open input.")
            proc.stdin.close()
            return {"stdin_closed": True}
        if action == "upload":
            try:
                encoded = params.get("data", "")
                if len(encoded) > MAX_UPLOAD * 4 / 3 + 8:
                    raise Denied("Upload too large.")
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                raise Denied("Invalid file encoding.") from None
            meta = self.files.stage(actor, params.get("name", ""), data)
            self._audit(actor, "upload", meta["id"] + " " + meta["source_sha256"])
            return meta
        if action == "preview":
            return {"output": self.redactor.clean(self.files.preview(actor, params["id"], params["name"]))}
        if action == "uploads":
            return [json.loads(r['data']) for r in self.store.rows('SELECT data,actor FROM uploads ORDER BY at DESC LIMIT 100') if self.policy.same_owner(r['actor'], actor)][:20]
        if action == "events":
            self.store.bind_recipients(self.policy.owners)
            return self.store.events_for(actor)
        if action == "ack":
            event_id = int(params["id"])
            self.store.acknowledge(event_id, actor)
            return {"acknowledged": event_id}
        if action == "export":
            return self.export()
        if action == "backup":
            return await asyncio.to_thread(self.encrypted_backup, params.get("password", ""))
        raise Denied("Unknown API action.")

    def save_settings(self, actor, params):
        current = self.store.settings()
        revision = params.get("revision")
        change = params.get("changes", {})
        if not isinstance(change, dict) or set(change) - set(DEFAULTS):
            raise Denied("Unknown settings field.")
        value = {**current, **change}
        for key in ("enabled", "auto_recovery", 'inventory_daily', 'telegram_reports', 'daily_checkpoint'):
            if not isinstance(value[key], bool):
                raise Denied("Enabled and recovery flags must be true or false.")
        for key, low, high in (("hourly_seconds", 3600, 86400), ("daily_hour", 0, 23), ("job_timeout", 10, 900),
                               ("retention_days", 1, 90), ("disk_min_percent", 1, 30), ("memory_min_percent", 1, 30), ('output_limit_mib', 1, 64), ('cert_warn_days', 1, 60)):
            if isinstance(value[key], bool) or not isinstance(value[key], int) or not low <= value[key] <= high:
                raise Denied("Invalid setting: " + key)
        ZoneInfo(value["timezone"])
        if not isinstance(value["report_channel"], int) or value["report_channel"] < 0:
            raise Denied("Report channel must be a numeric ID.")
        if not isinstance(value["maintenance_until"], (int, float)) or not 0 <= value["maintenance_until"] <= time.time() + 7 * 86400:
            raise Denied("Maintenance is limited to seven days.")
        self.store.save_settings(value, revision)
        self._audit(actor, "settings_saved", json.dumps(change))
        return self.store.settings()

    def validate_job(self, actor, value):
        kind = value.get("kind")
        payload = dict(value.get("payload", {}))
        self.access.job(actor, kind, payload)
        if kind == "service":
            if payload.get("action") not in SERVICE_ACTIONS:
                raise Denied("Unknown service action.")
            payload = {"unit": unit_name(payload.get("unit")), "action": payload["action"]}
        elif kind == 'container':
            target = self.store.target(payload.get('target'))
            if not target or target.get('driver') != 'docker' or payload.get('action') not in ('start', 'stop', 'restart'):
                raise Denied('Select an enrolled container and start, stop or restart.')
            payload = {'target': target['id'], 'container': target['container'], 'action': payload['action']}
        elif kind == 'recipe':
            recipe = self.recipes.get(payload.get('recipe'))
            if not self.store.target(recipe['target']):
                raise Denied('Enroll the recipe target before execution.')
            payload = {'recipe': recipe['id'], 'recipe_digest': recipe['digest']}
        elif kind == 'checkpoint':
            payload = {}
        elif kind == 'package':
            plan=self.packages.plan(payload.get('target','all'))
            if payload.get('plan') is not None and digest(payload['plan'])!=digest(plan):
                raise Denied('Package scope changed after review. Prepare a new package request.')
            payload = {'target':payload.get('target','all'),'plan':plan}
        elif kind == 'application':
            plan=self.applications.plan(payload)
            payload={k:payload[k] for k in ('adapter','action','revision','changes','validation') if k in payload}
            payload['plan']=plan
        elif kind == 'restore_stage':
            if not re.fullmatch('[a-f0-9]{24}', str(payload.get('checkpoint', ''))):
                raise Denied('Choose a completed checkpoint ID.')
            payload = {'checkpoint': payload['checkpoint']}
        elif kind in ("shell", "file"):
            profile = payload.get("profile", "runner")
            if profile not in ("runner", "root") or (profile == "root" and not self.policy.root_commands):
                raise Denied("This execution profile is disabled by the local policy.")
            timeout = payload.get("timeout", self.store.settings()["job_timeout"])
            if not isinstance(timeout, int) or not 1 <= timeout <= 900:
                raise Denied("Job timeout must be between 1 and 900 seconds.")
            payload = {**payload, "profile": profile, "timeout": timeout}
            if payload.get("upload"):
                meta = self.files.metadata(actor, payload["upload"])
                payload["upload_digest"] = digest(meta)
            if kind == "shell":
                code = payload.get("code")
                if not isinstance(code, str) or not code.strip() or len(code.encode()) > 16000 or "\x00" in code:
                    raise Denied("Submit nonempty shell code of at most 16000 bytes.")
                if payload.get("cwd") and (not Path(payload["cwd"]).is_absolute() or not Path(payload["cwd"]).is_dir()):
                    raise Denied("Working directory must be an existing absolute directory.")
                if payload.get("cwd") and payload.get("upload"):
                    raise Denied("Uploaded jobs run in their own workspace. Do not set cwd.")
            else:
                meta = self.files.metadata(actor, payload.get("upload"))
                if payload.get("entry") not in {f["name"] for f in meta["files"]}:
                    raise Denied("Entry file must belong to the reviewed upload.")
                if payload.get("runtime") not in ("python", "bash", "node"):
                    raise Denied("Choose python, bash or node.")
                args = payload.get("args", [])
                if not isinstance(args, list) or len(args) > 30 or any(not isinstance(x, str) or len(x) > 2000 or "\x00" in x for x in args):
                    raise Denied("Arguments must be a JSON array of at most 30 strings.")
                payload["args"] = args
        elif kind == "reboot":
            if not self.policy.host_reboot or payload.get("phrase") != "REBOOT " + socket.gethostname():
                raise Denied("Host reboot is disabled or the exact hostname phrase is missing.")
            payload = {"phrase": payload["phrase"]}
        else:
            raise Denied("Choose shell, file, service, container, recipe, checkpoint, restore_stage or reboot.")
        if len(json.dumps(payload).encode()) > (256000 if kind in ('application','package') else 24000):
            raise Denied("Job request too large.")
        return kind, payload

    def prepare(self, actor, value):
        kind, payload = self.validate_job(actor, value)
        key, token = secrets.token_hex(12), secrets.token_urlsafe(32)
        sha = digest({"kind": kind, "payload": payload, "actor": actor})
        expires = time.time() + 300
        self.store.run("INSERT INTO pending(id,actor,kind,payload,digest,token_hash,expires) VALUES(?,?,?,?,?,?,?)",
                       (key, actor, kind, json.dumps(payload), sha, secret_hash(token), expires))
        self._audit(actor, "prepare", f"{key} kind={kind} sha256={sha}; code content omitted")
        return {"id": key, "token": token, "digest": sha, "expires": expires, "kind": kind,
                "preview": self.redactor.clean(json.dumps(payload, indent=2))}

    def confirm(self, actor, value):
        if len(self.jobs) >= 2:
            raise Denied("Two jobs are already active. Wait or cancel a job.")
        key = value.get("id")
        with self.store.lock:
            row = self.store.one("SELECT * FROM pending WHERE id=?", (key,))
            if not row or row["actor"] != actor or row["used"] or row["expires"] < time.time() or not secrets.compare_digest(row["token_hash"], secret_hash(str(value.get("token", "")))) or not secrets.compare_digest(row["digest"], str(value.get("digest", ""))):
                raise Denied("Review expired, changed, already used or belongs to another owner.")
            payload = json.loads(row["payload"])
            self.validate_job(actor, {"kind": row["kind"], "payload": payload})
            if payload.get("upload") and digest(self.files.metadata(actor, payload["upload"])) != payload["upload_digest"]:
                raise Denied("Upload metadata changed after review.")
            self.store.run("UPDATE pending SET used=1 WHERE id=?", (key,))
            job_id = secrets.token_hex(12)
            self.store.run("INSERT INTO jobs(id,actor,kind,state,payload,created) VALUES(?,?,?,?,?,?)",
                           (job_id, actor, row["kind"], "queued", row["payload"], time.time()))
        self._audit(actor, "confirmed", job_id + " sha256=" + row["digest"])
        task = asyncio.create_task(self.execute_job(job_id, actor, row["kind"], payload), name="maestro-job-" + job_id)
        self.jobs[job_id] = task
        task.add_done_callback(lambda _: self.jobs.pop(job_id, None))
        return {"id": job_id, "state": "queued"}

    def job(self, actor, key):
        row = self.store.one("SELECT * FROM jobs WHERE id=?", (key,))
        if not row or not self.policy.same_owner(row['actor'], actor):
            raise Denied("Job is missing or belongs to another owner.")
        row.pop("payload")
        if row["state"] == "running":
            row["output"] = self.runner.output(key)
        row["output"] = self.redactor.clean(row["output"])
        operation = self.store.one('SELECT state,data FROM operations WHERE id=?', (key,))
        if operation:
            row['operation'] = {**operation, 'data': json.loads(operation['data'])}
        return row

    def job_argv(self, kind, payload, workspace):
        if kind == "service":
            return ["systemctl", payload["action"], payload["unit"]], workspace
        if kind == 'container':
            return ['docker', payload['action'], payload['container']], workspace
        if kind == "reboot":
            return ["systemctl", "reboot", "--no-wall"], workspace
        if kind == "shell":
            argv = ["/bin/bash", "--noprofile", "--norc", "-c", payload["code"]]
        else:
            runtime = {"python": "/usr/bin/python3", "bash": "/bin/bash", "node": "/usr/bin/node"}[payload["runtime"]]
            argv = [runtime, str(workspace / payload["entry"]), *payload["args"]]
        cwd = Path(payload.get("cwd") or workspace)
        if payload["profile"] == "runner":
            account = pwd.getpwnam(self.policy.runner)
            if account.pw_uid == 0:
                raise Denied('The runner profile must use a non-root account.')
            # The agent owns reviewed staging. Each runner receives only its own writable copy.
            os.chown(workspace, account.pw_uid, account.pw_gid)
            workspace.chmod(0o700)
            for child in workspace.rglob("*"):
                os.chown(child, account.pw_uid, account.pw_gid)
                child.chmod(0o700 if child.is_dir() else 0o600)
            argv = ["/usr/sbin/runuser", "-u", self.policy.runner, "--", *argv]
        return argv, cwd

    async def execute_job(self, key, actor, kind, payload):
        state, code, output = "failed", None, ""
        workspace = self.workspaces / key
        operation = {'checkpoint': None}
        try:
            async with self.mutation_lock:
                self.access.job(actor, kind, payload)
                if kind in ('application','package'):
                    self.validate_job(actor, {'kind':kind,'payload':payload})
                self.store.operation(key, actor, kind, 'backing_up', operation)
                self.store.run("UPDATE jobs SET state='running' WHERE id=?", (key,))
                workspace.mkdir(mode=0o700)
                if self.policy.backup_required or kind in ('checkpoint','package'):
                    backup_targets=self.store.targets()
                    if kind=='package':
                        backup_targets += [{'id':'scan-'+hashlib.sha256(p.encode()).hexdigest()[:12],'database':p} for p in payload['plan'].get('databases',[])]
                    operation['checkpoint'] = await self.checkpoints.create('job:' + key, backup_targets)
                self.access.job(actor, kind, payload)
                self.store.operation(key, actor, kind, 'running', operation)
                if kind == 'checkpoint':
                    state, code, output = 'completed', 0, json.dumps(operation['checkpoint'], indent=2)
                elif kind == 'package':
                    operation['packages'] = await self.packages.create(actor,key,operation['checkpoint'],payload['plan'])
                    state,code,output='completed',0,json.dumps(operation['packages'],indent=2)
                elif kind == 'application':
                    outcome=await self.applications.apply(payload['plan'],actor)
                    operation['application']=outcome
                    state,code,output=outcome['state'],0 if outcome['state']=='completed' else 1,json.dumps(outcome,indent=2)
                elif kind == 'restore_stage':
                    restored = await self.checkpoints.restore_stage(payload['checkpoint'])
                    state, code, output = 'completed', 0, json.dumps(restored, indent=2)
                elif kind == 'recipe':
                    result = await self.run_recipe(key, actor, payload, operation)
                    operation['repair'] = result
                    state, code, output = result['state'], result['exit_code'], json.dumps(result, indent=2)
                else:
                    for target in self.store.targets():
                        if target.get('env_file'):
                            self.redactor.load_env(target['env_file'])
                    if payload.get("upload"):
                        self.files.materialize(actor, payload["upload"], workspace)
                    argv, cwd = self.job_argv(kind, payload, workspace)
                    result = await self.runner.run(argv, cwd=cwd, timeout=payload.get("timeout", 90), key=key,
                        open_stdin=kind in ("shell", "file"), log_path=self.outputs.path(key, '.raw'),
                        log_limit=self.store.settings()['output_limit_mib']*1024*1024)
                    state, code, output = result.state, result.code, result.output
                    if code and state == "completed":
                        state = "failed"
                    async with self.output_lock:
                        operation['output'] = await asyncio.to_thread(self.outputs.finalize, key, self.store.settings()['output_limit_mib']*1024*1024,
                            result.total_bytes, state)
        except asyncio.CancelledError:
            state, output = "cancelled", self.runner.output(key)
        except Exception as exc:
            output = type(exc).__name__ + ": " + self.redactor.clean(exc)
        finally:
            if self.outputs.path(key, '.raw').is_file() and not operation.get('output'):
                try:
                    async with self.output_lock:
                        operation['output'] = await asyncio.to_thread(self.outputs.finalize, key, self.store.settings()['output_limit_mib']*1024*1024,
                            self.outputs.path(key, '.raw').stat().st_size, state)
                except Exception as exc:
                    operation['output_error'] = type(exc).__name__
            output = self.redactor.clean(output)
            self.runner.buffers.pop(key, None)
            self.store.run("UPDATE jobs SET state=?,finished=?,exit_code=?,output=? WHERE id=?", (state, time.time(), code, output, key))
            self.store.operation(key, actor, kind, state, operation)
            self._event("job_finished", {"id": key, "actor": actor, "state": state, "exit_code": code, "kind": kind})
            self._audit(actor, "job_finished", f"{key} {state} exit={code}")

    def incident(self, target, code, detail):
        key = target + ":" + code
        row = self.store.one("SELECT * FROM incidents WHERE key=?", (key,))
        now = time.time()
        if row and row["state"] == "open":
            increment = int(now - row["last_seen"] >= 30)
            self.store.run("UPDATE incidents SET last_seen=?,count=count+?,detail=? WHERE key=?", (now, increment, detail, key))
        else:
            self.store.run("INSERT INTO incidents VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET first_seen=excluded.first_seen,last_seen=excluded.last_seen,count=1,state='open',detail=excluded.detail",
                           (key, target, code, now, now, 1, "open", detail))
            self._event("incident_open", {"target": target, "code": code, "detail": detail})

    def resolve(self, target, observed_codes):
        for row in self.store.rows("SELECT * FROM incidents WHERE target=? AND state='open'", (target,)):
            if row["code"] not in observed_codes:
                self.store.run("UPDATE incidents SET state='resolved',last_seen=? WHERE key=?", (time.time(), row["key"]))
                self._event("incident_resolved", {"target": target, "code": row["code"]})

    async def recover(self, target, report, settings):
        if target.get('recovery_owner') == 'external':
            return {'action': 'observe', 'detail': 'This target has its own recovery controller. Maestro does not compete.'}
        if self.mutation_lock.locked():
            return {'action': 'defer', 'detail': 'Another owner operation or repair is active.'}
        if not settings["auto_recovery"] or not (target["auto_restart"] or target.get('auto_recipe')) or not target["expected_running"] or settings["maintenance_until"] > time.time():
            return None
        if target["unit"] == "maestro-agent.service":
            return None
        if self.store.one("SELECT 1 FROM incidents WHERE target=? AND code='database_failed' AND state='open'", (target["id"],)):
            return {"action": "escalate", "detail": "An unresolved database integrity/configuration incident blocks restart."}
        registry = Path(self.policy.data.get("coordination_path", "/var/lib/ripcars-bots/coordination.sqlite3"))
        if registry.is_file():
            connection = sqlite3.connect(registry.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
            try:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in ("leases", "locks"):
                    if table in tables and connection.execute(f"SELECT 1 FROM {table} WHERE expires>? LIMIT 1", (time.time(),)).fetchone():
                        return {"action": "defer", "detail": "A shared setup lease is active; controller restart deferred."}
            finally:
                connection.close()
        failure_codes = {i["code"] for i in report["issues"]}
        evidence_codes = {r["code"] for r in classify(report.get("journal", ""))}
        recipe = None
        if target.get('auto_recipe'):
            try:
                enrolled = self.recipes.get(target['auto_recipe'])
                if enrolled['target'] == target['id'] and enrolled.get('automatic') and set(enrolled['match_codes']) <= (failure_codes | evidence_codes):
                    recipe = enrolled
            except (OSError, ValueError):
                return {'action': 'escalate', 'detail': 'Automatic recipe configuration is unavailable or invalid.'}
        if (not recipe and (not target['auto_restart'] or not failure_codes & {"unit_down", "watchdog_stale", "endpoint_unhealthy", 'worker_unhealthy'})) or evidence_codes & AUTO_BLOCKERS or "database_failed" in failure_codes:
            return None
        # Respect an intentional stop. Start recovery requires failed state, or a still-active unhealthy worker.
        if report["fields"].get("ActiveState") not in ("failed", "active"):
            return None
        if not any((self.store.one("SELECT count FROM incidents WHERE key=?", (target["id"] + ":" + code,)) or {"count": 0})["count"] >= 2 for code in failure_codes):
            return None
        now = time.time()
        rows = self.store.rows("SELECT at FROM repairs WHERE target=? AND at>? ORDER BY at DESC", (target["id"], now - 86400))
        if len(rows) >= 3 or (rows and now - rows[0]["at"] < 900) or sum(r["at"] > now - 3600 for r in rows) >= 2:
            return {"action": "escalate", "detail": "Recovery budget or cooldown reached; no restart attempted."}
        self.store.run("INSERT INTO repairs(at,target,action,result) VALUES(?,?,?,?)", (now, target["id"], "restart", "reserved"))
        checkpoint = None
        async with self.mutation_lock:
            try:
                if self.policy.backup_required:
                    checkpoint = await self.checkpoints.create('recovery:' + target['id'], self.store.targets())
            except (OSError, ValueError, asyncio.TimeoutError) as exc:
                detail = {'target': target['id'], 'action': 'blocked', 'detail': 'Checkpoint failed: ' + self.redactor.clean(exc)[:300]}
                self.store.run('UPDATE repairs SET result=? WHERE target=? AND at=?', (json.dumps(detail), target['id'], now))
                self._event('recovery', detail)
                return detail
            if recipe:
                operation = {'checkpoint': checkpoint}
                repair_key = secrets.token_hex(12)
                outcome = await self.run_recipe(repair_key, 'system:recovery', {'recipe': recipe['id'], 'recipe_digest': recipe['digest']}, operation)
                self.store.operation(repair_key, 'system:recovery', 'recipe', outcome['state'], {**operation, 'repair': outcome})
                detail = {'target': target['id'], 'action': 'recipe', 'recipe': recipe['id'], 'result': outcome,
                          'checkpoint': checkpoint['id'] if checkpoint else None}
            else:
                argv = ['docker', 'restart', target['container']] if target.get('driver') == 'docker' else ['systemctl', 'restart', unit_name(target['unit'])]
                result = await self.runner.run(argv, timeout=90)
                verified = await self.probes.service(target, daily=False)
                detail = {"target": target["id"], "action": "restart", "command_exit": result.code, "verified_ok": verified["ok"],
                          "detail": self.redactor.clean(result.output)[-2000:], 'checkpoint': checkpoint['id'] if checkpoint else None}
        self.store.run("UPDATE repairs SET result=? WHERE target=? AND at=?", (json.dumps(detail), target["id"], now))
        self._event("recovery", detail)
        return detail

    async def check_cycle(self, kind, only=None, manual=False):
        if self.monitor_lock.locked():
            raise Denied("A monitoring cycle is already running.")
        async with self.monitor_lock:
            settings = self.store.settings()
            targets = [t for t in self.store.targets() if (t["enabled"] or (manual and only == t["id"])) and (not only or t["id"] == only)]
            if only and not targets:
                raise Denied("Unknown target.")
            result = {"kind": kind, "at": time.time(), "targets": [], "host": host_check(settings, self.policy.data.get('disk_roots', ['/', '/opt', '/srv', '/root', '/var/lib', '/home'])), "manual": manual}
            for target in targets:
                target = {**target, 'cert_warn_days': target.get('cert_warn_days', settings['cert_warn_days'])}
                self.scheduler_tick = time.monotonic()
                try:
                    report = await self.probes.service(target, daily=kind == "daily")
                    for issue in report["issues"]:
                        issue["detail"] = self.redactor.clean(issue["detail"])
                        self.incident(target["id"], issue["code"], issue["detail"])
                    # Watch/hourly checks cannot resolve an incident whose daily-only probe did not run.
                    observed = {i["code"] for i in report["issues"]}
                    if kind != "daily":
                        observed |= {r["code"] for r in self.store.rows("SELECT code FROM incidents WHERE target=? AND state='open' AND code IN ('database_failed','backup_failed','certificate_review')", (target["id"],))}
                    self.resolve(target["id"], observed)
                    report["runbooks"] = classify(report.get("journal", ""))
                    if report["issues"]:
                        report["diagnostics"] = {}
                        names = {EVIDENCE_DIAGNOSTICS[r["code"]] for r in report["runbooks"] if r["code"] in EVIDENCE_DIAGNOSTICS}
                        for name in names:
                            evidence = await self.runner.run(DIAGNOSTICS[name], timeout=15)
                            report["diagnostics"][name] = {"exit_code": evidence.code, "output": self.redactor.clean(evidence.output)[-6000:]}
                        if report["runbooks"] or report["diagnostics"]:
                            signature = digest({"issues": [i["code"] for i in report["issues"]], "rules": [r["code"] for r in report["runbooks"]]})
                            if self.store.get("diagnosis:" + target["id"]) != signature:
                                self._event("diagnosis", {"target": target["id"], "issues": report["issues"], "runbooks": report["runbooks"], "evidence": report["diagnostics"], "journal": report.get("journal", "")[-4000:]})
                                self.store.put("diagnosis:" + target["id"], signature)
                    else:
                        self.store.put("diagnosis:" + target["id"], None)
                    if not manual:
                        report["recovery"] = await self.recover(target, report, settings)
                    if kind == "daily" and target.get("repo"):
                        try:
                            repo = await self.probes.repository(target["repo"])
                            previous = self.store.get("repo:" + target["id"])
                            repo["changed"] = bool(repo.get("sha") and previous and previous != repo["sha"])
                            if repo.get("sha"):
                                self.store.put("repo:" + target["id"], repo["sha"])
                            report["repository"] = repo
                        except Exception as exc:
                            report["repository"] = {"ok": False, "detail": type(exc).__name__}
                    self.store.run("INSERT INTO snapshots(at,kind,target,data) VALUES(?,?,?,?)", (time.time(), kind, target["id"], json.dumps(report)))
                    result["targets"].append(report)
                except Exception as exc:
                    detail = type(exc).__name__ + ": " + self.redactor.clean(exc)
                    self.incident(target["id"], "probe_failed", detail)
                    result["targets"].append({"id": target["id"], "ok": False, "issues": [{"code": "probe_failed", "detail": detail}]})
            for issue in result["host"]["issues"]:
                self.incident("host", "resource_pressure", issue)
            self.resolve("host", {"resource_pressure"} if result["host"]["issues"] else set())
            if kind != 'watch':
                # Observe failures outside our enrolled applications without claiming
                # their resources or choosing a repair for an unknown service.
                known = {t['unit'] for t in targets if t.get('driver', 'systemd') == 'systemd'}
                try:
                    units = await self.runner.run(DIAGNOSTICS['failed_units'], timeout=15)
                    other = []
                    if units.code == 0:
                        for line in units.output.splitlines()[:500]:
                            words = line.split()
                            if len(words) >= 4 and words[2] == 'failed':
                                try:
                                    unit = unit_name(words[0])
                                except ValueError:
                                    continue
                                if unit not in known:
                                    other.append(unit)
                        result['other_failed_services'] = other
                        if other:
                            self.incident('host-services', 'failed_units', 'Unenrolled failed services: ' + ', '.join(other))
                        else:
                            self.resolve('host-services', set())
                    else:
                        result['service_inventory_error'] = 'Failed-unit observation unavailable.'
                except OSError as exc:
                    result['service_inventory_error'] = type(exc).__name__
            if kind == "daily":
                if settings['inventory_daily']:
                    inventory = await self.inventory.collect()
                    previous = self.store.get('inventory', {})
                    old = {x['unit'] for x in previous.get('services', [])}
                    result['new_services'] = [x['unit'] for x in inventory['services'] if x['unit'] not in old]
                    self.store.put('inventory', inventory)
                    result['inventory_summary'] = {'services':len(inventory.get('services', [])), 'containers':len(inventory.get('containers', [])),
                                                   'complete':inventory.get('complete', False), 'errors':inventory.get('errors', [])}
                    if result['new_services']:
                        self._event('inventory_changed', {'new_services': result['new_services'], 'detail': 'Discovered only. Enroll targets explicitly before recovery.'})
                if self.checkpoints.status()['configured']:
                    try:
                        if not manual and settings['daily_checkpoint']:
                            if self.mutation_lock.locked() or settings['maintenance_until'] > time.time():
                                result['daily_checkpoint'] = {'deferred': True, 'detail': 'Owner operation or maintenance is active.'}
                            else:
                                async with self.mutation_lock:
                                    result['daily_checkpoint'] = await self.checkpoints.create('scheduled:daily', self.store.targets())
                        result['backup_repository'] = await self.checkpoints.inspect_repository(read_data=not manual)
                        self.store.put('last_backup_check', result['backup_repository'])
                    except (OSError, ValueError, sqlite3.Error, asyncio.TimeoutError) as exc:
                        result['backup_repository'] = {'ok': False, 'detail': type(exc).__name__}
                    if not result['backup_repository']['ok']:
                        self._event('checkpoint_review', result['backup_repository'])
                        self.incident('backups', 'checkpoint_failed', result['backup_repository']['detail'])
                    elif not result['backup_repository'].get('deferred'):
                        self.resolve('backups', set())
                else:
                    result['backup_repository'] = {'ok':False, 'not_configured':True, 'detail':'Backup coverage is not configured; execution remains blocked.'}
                path = self.policy.data.get("coordination_path", "/var/lib/ripcars-bots/coordination.sqlite3")
                if Path(path).is_file():
                    import ripcars_coordination
                    result["coordination"] = await asyncio.to_thread(ripcars_coordination.inspect, path)
                    if not result["coordination"]["ok"]:
                        self._event("coordination_review", {"issues": result["coordination"]["issues"]})
                self.store.prune(settings["retention_days"])
                self.files.prune(settings["retention_days"])
                self.outputs.prune(settings['retention_days'], self.jobs)
                self.packages.prune(settings['retention_days'])
                cutoff = time.time() - settings["retention_days"] * 86400
                for folder in self.workspaces.iterdir():
                    if folder.is_dir() and not folder.is_symlink() and folder.stat().st_mtime < cutoff and folder.name not in self.jobs:
                        shutil.rmtree(folder)
            if kind != "watch":
                self._event("scheduled_report" if not manual else "manual_report", result)
            return result

    async def scheduler_step(self, now=None):
        now = time.time() if now is None else now
        settings = self.store.settings()
        if now - self.store.get('last_discovery', 0) >= 300 and not self.inventory.lock.locked():
            try:
                inventory = await self.inventory.collect()
                self.store.put('inventory', inventory)
                found = reconcile(self.store, inventory)
                if found:
                    self._event('services_added', {'targets': found, 'detail': 'New service cards are available. Recovery remains under explicit local enrollment.'})
            except (ValueError, OSError) as exc:
                self._audit('system', 'discovery_failed', type(exc).__name__)
            finally:
                self.store.put('last_discovery', now)
        if not settings["enabled"]:
            return
        if now - self.store.get("last_watch", 0) >= 60:
            await self.check_cycle("watch")
            self.store.put("last_watch", now)
        if now - self.store.get("last_hourly", 0) >= settings["hourly_seconds"]:
            await self.check_cycle("hourly")
            self.store.put("last_hourly", now)
        local = datetime.fromtimestamp(now, ZoneInfo(settings["timezone"]))
        day = local.date().isoformat()
        if local.hour >= settings["daily_hour"] and self.store.get("last_daily") != day:
            await self.check_cycle("daily")
            self.store.put("last_daily", day)

    async def scheduler(self):
        while True:
            self.scheduler_tick = time.monotonic()
            try:
                await self.scheduler_step()
                self.scheduler_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.scheduler_error = type(exc).__name__ + ": " + self.redactor.clean(exc)
                self.incident("assistant", "scheduler", self.scheduler_error)
            await asyncio.sleep(15)

    def export(self):
        return {"version": __version__, "settings": self.store.settings(), "targets": self.store.targets(),
                "incidents": self.store.rows("SELECT * FROM incidents ORDER BY last_seen DESC LIMIT 100"),
                "audit": self.store.rows("SELECT * FROM audit ORDER BY id DESC LIMIT 200"),
                "jobs": self.store.rows("SELECT id,actor,kind,state,created,finished,exit_code FROM jobs ORDER BY created DESC LIMIT 100"),
                'inventory': self.store.get('inventory', {}),
                'checkpoints': [{**r, 'data': json.loads(r['data'])} for r in self.store.rows('SELECT * FROM backup_runs ORDER BY at DESC LIMIT 30')],
                'operations': [{**r, 'data': json.loads(r['data'])} for r in self.store.rows('SELECT id,at,kind,state,data FROM operations ORDER BY at DESC LIMIT 30')]}

    async def run_recipe(self, key, actor, payload, operation):
        recipe = self.recipes.get(payload['recipe'])
        if recipe['digest'] != payload['recipe_digest']:
            raise Denied('Repair recipe changed after review. Open a new review.')
        target = self.store.target(recipe['target'])
        rollback = FileRollback(self.root/('rollback-' + key), recipe.get('rollback_paths', []), [x.get('database', '') for x in self.store.targets()])
        await asyncio.to_thread(rollback.capture)
        operation['rollback_directory'] = str(rollback.folder)
        self.store.operation(key, actor, 'recipe', 'running', operation)
        if recipe.get('preflight'):
            checked = await self.runner.run(recipe['preflight'], timeout=recipe.get('timeout', 120))
            if checked.code:
                return {'state': 'failed', 'exit_code': checked.code, 'recipe': recipe['id'],
                        'detail': 'Repair preflight failed. No action executed.', 'output': self.redactor.clean(checked.output)[-4000:]}
        try:
            result = await self.runner.run(recipe['argv'], timeout=recipe.get('timeout', 120))
            deadline = time.monotonic() + recipe.get('verify_seconds', 20)
            while True:
                checked = await self.probes.service(target)
                if checked['ok'] or time.monotonic() >= deadline or result.code:
                    break
                await asyncio.sleep(2)
            if result.code or not checked['ok']:
                raise Denied('Repair did not pass its post-action health check.')
            return {'state': 'completed', 'exit_code': 0, 'recipe': recipe['id'], 'verified': True,
                    'output': self.redactor.clean(result.output)[-4000:]}
        except BaseException as exc:
            self.store.operation(key, actor, 'recipe', 'restoring', operation)
            try:
                await asyncio.to_thread(rollback.restore)
            except Exception as restore_error:
                result = {'state': 'rollback_failed', 'exit_code': 1, 'recipe': recipe['id'], 'files_restored': False,
                          'detail': self.redactor.clean(exc), 'rollback_error': self.redactor.clean(restore_error),
                          'rollback_directory': str(rollback.folder)}
                self._event('recovery', {'target': recipe['target'], 'action': 'escalate', 'result': result})
                return result
            if recipe.get('rollback_argv'):
                restored = await self.runner.run(recipe['rollback_argv'], timeout=recipe.get('timeout', 120))
                if restored.code:
                    return {'state':'rollback_failed','exit_code':restored.code,'recipe':recipe['id'],'files_restored':True,
                            'verified':False,'detail':'Files restored but the rollback service action failed. Inspect the preserved operation.'}
            checked = await self.probes.service(target)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return {'state': 'rolled_back', 'exit_code': 1, 'recipe': recipe['id'], 'verified': checked['ok'],
                    'detail': self.redactor.clean(exc), 'files_restored': True}

    def encrypted_backup(self, password):
        if not isinstance(password, str) or len(password) < 12:
            raise Denied("Choose a backup password of at least 12 characters.")
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        with tempfile.TemporaryDirectory(dir=self.root) as temp:
            path = Path(temp) / "maestro.sqlite3"
            self.store.backup(path)
            raw = path.read_bytes()
            if len(raw) > 6*1024*1024:
                raise Denied("Maestro state exceeds the transport backup limit. Use the VPS backup guide.")
            salt = secrets.token_bytes(16)
            kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600000)
            key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
            data = b"RCA1" + salt + Fernet(key).encrypt(raw)
            return {"name": "maestro-backup.rca", "data": base64.b64encode(data).decode(), "sha256": hashlib.sha256(data).hexdigest()}

    async def close(self):
        for task in list(self.jobs.values()):
            task.cancel()
        await asyncio.gather(*list(self.jobs.values()), return_exceptions=True)
        self.store.close()
