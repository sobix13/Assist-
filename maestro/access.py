"""Persistent, transport-scoped delegates. Superadmins exist only in local policy."""
from __future__ import annotations

import json
import re
import time

from .security import Denied


READ_ACTIONS = {'whoami', 'targets', 'status', 'logs', 'check', 'jobs', 'job', 'job_output', 'cancel', 'discard'}
ROLES = {'observer', 'operator'}


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'(discord|telegram):[0-9]{5,22}', value):
        raise Denied('Use a numeric identity such as telegram:313342234 or discord:123456789.')
    return value


class Access:
    def __init__(self, policy, store):
        self.policy, self.store = policy, store
        store.db.execute('CREATE TABLE IF NOT EXISTS delegates(actor TEXT PRIMARY KEY,role TEXT NOT NULL,targets TEXT NOT NULL,expires REAL NOT NULL,revision INTEGER NOT NULL,granted_by TEXT NOT NULL)')
        store.db.commit()

    def user(self, actor):
        identity(actor)
        if actor in self.policy.owners:
            return {'actor': actor, 'role': 'superadmin', 'targets': ['*'], 'expires': 0}
        row = self.store.one('SELECT * FROM delegates WHERE actor=?', (actor,))
        if not row or (row['expires'] and row['expires'] <= time.time()):
            raise Denied('Access is absent, expired or revoked. Contact the enrolled superadmin.')
        return {**row, 'targets': json.loads(row['targets'])}

    def superadmin(self, actor):
        if actor not in self.policy.owners:
            raise Denied('Only the locally enrolled superadmin manages access and host-wide settings.')

    def target(self, actor, key):
        user = self.user(actor)
        if user['role'] != 'superadmin' and key not in user['targets']:
            raise Denied('This service is outside your delegated scope.')

    def authorize(self, actor, action, params):
        user = self.user(actor)
        if user['role'] == 'superadmin':
            return user
        if action not in READ_ACTIONS | {'prepare', 'confirm'}:
            raise Denied('This action requires superadmin access.')
        if action in {'logs', 'check'}:
            self.target(actor, params.get('target'))
        if action == 'prepare':
            self.job(actor, params.get('kind'), params.get('payload', {}))
        return user

    def job(self, actor, kind, payload):
        user = self.user(actor)
        if user['role'] == 'superadmin':
            return
        if user['role'] != 'operator' or kind not in {'service', 'container'}:
            raise Denied('Delegates run reviewed service actions only. Terminal, files and backups remain superadmin-only.')
        if kind == 'service':
            target = next((t for t in self.store.targets() if t.get('unit') == payload.get('unit') and t.get('driver', 'systemd') == 'systemd'), None)
        else:
            target = self.store.target(payload.get('target'))
        if not target or payload.get('action') not in {'start', 'stop', 'restart', 'reload'}:
            raise Denied('Choose an enrolled service and a supported action.')
        self.target(actor, target['id'])
        if target['unit'] in {'maestro-agent.service', 'maestro-discord.service', 'maestro-telegram.service'}:
            raise Denied('Delegates do not control Maestro interfaces or its broker.')

    def grant(self, superadmin, value):
        self.superadmin(superadmin)
        actor = identity(value.get('actor'))
        if actor in self.policy.owners:
            raise Denied('A superadmin identity is immutable in chat.')
        role, targets = value.get('role'), value.get('targets')
        if role not in ROLES or not isinstance(targets, list) or not targets or len(targets) > 100 or len(set(targets)) != len(targets):
            raise Denied('Choose observer/operator and one to 100 distinct target IDs.')
        if any(not isinstance(t, str) or not self.store.target(t) for t in targets):
            raise Denied('Every delegated target must exist.')
        seconds = value.get('seconds', 86400)
        if type(seconds) is not int or not 60 <= seconds <= 31536000:
            raise Denied('Access duration must be between 60 seconds and one year.')
        old = self.store.one('SELECT revision FROM delegates WHERE actor=?', (actor,))
        revision = value.get('revision', 0)
        if type(revision) is not int or revision != (old['revision'] if old else 0):
            raise Denied('Access changed. Reopen the access panel.')
        self.store.run('INSERT INTO delegates VALUES(?,?,?,?,?,?) ON CONFLICT(actor) DO UPDATE SET role=excluded.role,targets=excluded.targets,expires=excluded.expires,revision=excluded.revision,granted_by=excluded.granted_by',
                       (actor, role, json.dumps(sorted(targets)), time.time()+seconds, revision+1, superadmin))
        self.store.audit(superadmin, 'access_granted', actor+' '+role+' '+','.join(targets))
        return self.user(actor)

    def revoke(self, superadmin, actor):
        self.superadmin(superadmin); identity(actor)
        if actor in self.policy.owners:
            raise Denied('Superadmins are changed only through the protected VPS policy.')
        self.store.run('DELETE FROM delegates WHERE actor=?', (actor,))
        self.store.run('UPDATE pending SET used=1 WHERE actor=?', (actor,))
        self.store.audit(superadmin, 'access_revoked', actor)
        return {'revoked': actor}

    def all(self, superadmin):
        self.superadmin(superadmin)
        return [{**r, 'targets': json.loads(r['targets']), 'active': not r['expires'] or r['expires'] > time.time()} for r in self.store.rows('SELECT * FROM delegates ORDER BY actor')]
