"""Locally enrolled typed adapters, revision checks and controlled backend writes."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

from .adapters import verifier_config as verifier
from .checkpoints import file_hash, protected_json
from .security import Denied, digest


def connect(path, readonly=True):
    p=Path(path)
    if not p.is_file() or p.is_symlink():raise Denied('The application database is missing or is a symlink.')
    c=sqlite3.connect(p.resolve().as_uri()+('?mode=ro' if readonly else '?mode=rw'),uri=True,timeout=5)
    c.row_factory=sqlite3.Row
    return c


def atomic(path, value):
    path=Path(path);stat=path.stat()
    if path.is_symlink() or not path.is_file():raise Denied('Configuration target must be an existing regular file.')
    temp=path.parent/('.maestro-'+secrets.token_hex(12))
    try:
        with temp.open('x') as f:
            f.write(json.dumps(value,indent=2)+'\n');f.flush();os.fsync(f.fileno())
        temp.chmod(stat.st_mode&0o777);os.chown(temp,stat.st_uid,stat.st_gid)
        temp.replace(path)
    finally:temp.unlink(missing_ok=True)


class Applications:
    def __init__(self, policy, store, redactor, probes, runner):
        self.policy,self.store,self.redactor,self.probes=policy,store,redactor,probes
        self.discord_factory=None
        self.runner=runner

    def all(self):
        path=Path(self.policy.data.get('applications_config','/etc/maestro/applications.json'))
        if not path.exists():return []
        values=protected_json(path).get('adapters',[])
        if not isinstance(values,list) or len(values)>100:raise Denied('Use at most 100 application adapters.')
        ids=set()
        for v in values:
            if not isinstance(v,dict) or not isinstance(v.get('id'),str) or not v['id'].isascii() or not v['id'].replace('-','').replace('_','').isalnum() or len(v['id'])>30 or v['id'] in ids:
                raise Denied('Adapter IDs must be unique, short ASCII names.')
            ids.add(v['id'])
            if v.get('kind') not in {'verifier-v1','jsonfile'} or not self.store.target(v.get('target')):
                raise Denied('Enroll a supported adapter kind and an existing target.')
        return values

    def spec(self, key):
        v=next((x for x in self.all() if x['id']==key),None)
        if not v:raise Denied('Enroll this application adapter on the VPS first.')
        v=copy.deepcopy(v)
        for k in ('database','install','env_file','path','coordination_path'):
            if k in v and (not isinstance(v[k],str) or not Path(v[k]).is_absolute() or '\x00' in v[k]):
                raise Denied('Adapter paths must be fixed absolute local paths.')
        for k in ('validate_argv','reload_argv'):
            if k in v and (not isinstance(v[k],list) or not 1<=len(v[k])<=30 or any(not isinstance(a,str) or len(a)>2000 or '\x00' in a for a in v[k]) or not Path(v[k][0]).is_absolute()):
                raise Denied('Adapter commands must be locally enrolled absolute argument arrays.')
        if v['kind']=='verifier-v1':
            verifier.discord_id(v.get('guild'))
            contract=json.loads((Path(__file__).parent/'adapters/verifier-contract.json').read_text())
            root=Path(v['install']).resolve()
            if (root/'VERSION').read_text().strip()!=contract['version']:
                raise Denied('Verifier version changed. Review and update its versioned Maestro adapter before writing settings.')
            for name,expected in contract['files'].items():
                p=root/name
                if p.is_symlink() or file_hash(p)!=expected:raise Denied('Verifier contract changed: '+name+'. Writes are blocked.')
        return v

    def read(self, key):
        v=self.spec(key)
        if v['kind']=='verifier-v1':
            c=connect(v['database'])
            try:
                if c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]!='1':raise Denied('Unsupported Verifier database schema.')
                row=c.execute('SELECT revision,value FROM settings WHERE guild=?',(v['guild'],)).fetchone()
                cfg=json.loads(row['value']) if row else verifier.defaults()
                verifier.validate(cfg)
                revision=row['revision'] if row else 0
            finally:c.close()
        else:
            path=Path(v['path'])
            if path.is_symlink() or not path.is_file() or path.stat().st_size>2*1024*1024:raise Denied('Invalid application JSON file.')
            cfg=json.loads(path.read_text());revision=file_hash(path)
            if not isinstance(cfg,dict):raise Denied('Application JSON must be an object.')
        return {'id':key,'kind':v['kind'],'target':v['target'],'settings':cfg,'revision':revision,'contract_digest':digest(v)}

    def plan(self, payload):
        current=self.read(payload.get('adapter'));v=self.spec(current['id'])
        if current['revision']!=payload.get('revision'):raise Denied('Application settings changed. Reopen the application panel.')
        action=payload.get('action','settings')
        if action not in ('settings','provision','publish'):raise Denied('Choose settings, provision or publish.')
        if action!='settings' and v['kind']!='verifier-v1':raise Denied('This adapter supports settings only.')
        cfg=copy.deepcopy(current['settings']);changes=payload.get('changes',{})
        if not isinstance(changes,dict):raise Denied('Send a JSON object containing fields to change.')
        if v['kind']=='verifier-v1':
            if set(changes)-set(verifier.DEFAULTS):raise Denied('Unknown Verifier field.')
            for k,val in changes.items():cfg[k]={**cfg[k],**val} if k=='texts' and isinstance(val,dict) else val
            verifier.validate(cfg)
            if action!='settings' and cfg['enabled']:raise Denied('Pause Verifier before provisioning channels, roles or the public panel.')
            c=connect(v['database'])
            try:
                modes={row[0] for row in c.execute("SELECT DISTINCT CASE WHEN event_id LIKE 'solana:%' THEN 'rpc' ELSE 'platform' END FROM ledger WHERE guild=?",(v['guild'],))}
                if cfg['deposit_mode']!='off' and modes-{cfg['deposit_mode']}:raise Denied('The deposit ledger already uses another accounting mode. No automatic ledger migration is performed.')
            finally:c.close()
        else:
            fields=v.get('fields',{})
            if not changes or set(changes)-set(fields):raise Denied('Only locally declared JSON fields are editable.')
            for k,val in changes.items():
                rule=fields[k];kind=rule.get('type')
                if any(word in k.lower() for word in ('token','password','secret','credential','private_key')):raise Denied('Secrets are enrolled on the VPS.')
                if kind=='bool' and type(val) is not bool or kind=='int' and (type(val) is not int or not rule.get('min',-2**31)<=val<=rule.get('max',2**31)) or kind=='string' and (not isinstance(val,str) or len(val)>rule.get('max_length',2000)) or kind not in {'bool','int','string'}:
                    raise Denied('Invalid value for '+k)
                if 'choices' in rule and val not in rule['choices']:raise Denied('Unsupported choice for '+k)
                cfg[k]=val
        return {'adapter':current['id'],'action':action,'revision':current['revision'],'before_digest':digest(current['settings']),
                'contract_digest':current['contract_digest'],'settings':cfg,'changes':changes}

    async def preflight(self, plan):
        spec=self.spec(plan['adapter'])
        if digest(spec)!=plan['contract_digest'] or digest(self.read(plan['adapter'])['settings'])!=plan['before_digest']:
            raise Denied('Application contract or settings changed after review.')
        if spec['kind']=='verifier-v1':
            from .verifier_bridge import VerifierBridge
            bridge=VerifierBridge(self,spec,plan)
            return await bridge.preflight()
        if spec.get('validate_argv'):
            result=await self.runner.run(spec['validate_argv'],timeout=30)
            if result.code:raise Denied('The locally enrolled application preflight failed. No change was applied.')
        return {'ok':True,'kind':'jsonfile','target':spec['target'],'fields':sorted(plan['changes'])}

    def commit_verifier(self, spec, cfg, revision, actor):
        verifier.validate(cfg)
        c=connect(spec['database'],False)
        try:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT revision FROM settings WHERE guild=?',(spec['guild'],)).fetchone()
            if (row['revision'] if row else 0)!=revision:raise Denied('Verifier settings changed during execution. No concurrent edit was overwritten.')
            modes={r[0] for r in c.execute("SELECT DISTINCT CASE WHEN event_id LIKE 'solana:%' THEN 'rpc' ELSE 'platform' END FROM ledger WHERE guild=?",(spec['guild'],))}
            if cfg['deposit_mode']!='off' and modes-{cfg['deposit_mode']}:raise Denied('Deposit accounting mode changed during execution.')
            if row:
                c.execute('UPDATE settings SET value=?,revision=revision+1 WHERE guild=? AND revision=?',(verifier.canonical(cfg),spec['guild'],revision))
            else:c.execute('INSERT INTO settings VALUES(?,1,?)',(spec['guild'],verifier.canonical(cfg)))
            c.execute('INSERT INTO audit(guild,actor,action,detail,created) VALUES(?,0,?,?,?)',(spec['guild'],'maestro_settings',verifier.canonical({'actor':actor,'settings':cfg}),time.time()))
            c.execute('UPDATE jobs SET next_run=? WHERE guild=?',(time.time(),spec['guild']))
            c.commit();return revision+1
        except BaseException:c.rollback();raise
        finally:c.close()

    async def apply(self, plan, actor):
        before=self.read(plan['adapter']);spec=self.spec(plan['adapter'])
        await self.preflight(plan)
        if spec['kind']=='verifier-v1':
            from .verifier_bridge import VerifierBridge
            bridge=VerifierBridge(self,spec,plan)
            if plan['action'] in {'provision','publish'}:return await bridge.apply(actor)
            revision=await asyncio.to_thread(self.commit_verifier,spec,plan['settings'],plan['revision'],actor)
            report=await self.postcheck(spec)
            if not report['ok']:
                try:
                    await asyncio.to_thread(self.commit_verifier,spec,before['settings'],revision,actor)
                except Exception as exc:
                    return {'state':'rollback_failed','verified':False,'detail':'Concurrent edits or a database error prevented settings rollback. Review the checkpoint.','error':self.redactor.clean(str(exc))}
                healthy=await self.postcheck(spec)
                return {'state':'rolled_back' if healthy['ok'] else 'rollback_failed','verified':healthy['ok'],
                        'detail':'Verifier settings restored. Application data and ledger were preserved.','postcheck':healthy}
            return {'state':'completed','revision':revision,'verified':True}
        path=Path(spec['path'])
        if file_hash(path)!=plan['revision']:raise Denied('Application JSON changed before commit.')
        atomic(path,plan['settings']);applied_hash=file_hash(path)
        try:
            if spec.get('validate_argv'):
                result=await self.runner.run(spec['validate_argv'],timeout=30)
                if result.code:raise Denied('The application validator rejected the written candidate.')
            if spec.get('reload_argv'):
                result=await self.runner.run(spec['reload_argv'],timeout=60)
                if result.code:raise Denied('The declared reload command failed.')
            report=await self.postcheck(spec)
            if not report['ok']:raise Denied('The application postcheck failed.')
        except Exception as exc:
            return await self.rollback_json(spec,before['settings'],applied_hash,self.redactor.clean(str(exc)))
        return {'state':'completed','revision':applied_hash,'verified':True}

    async def postcheck(self, spec):
        try:
            return await self.probes.service(self.store.target(spec['target']))
        except Exception as exc:
            return {'ok':False,'error':self.redactor.clean(str(exc))}

    async def rollback_json(self, spec, previous, applied_hash, reason):
        try:
            path=Path(spec['path'])
            if file_hash(path)!=applied_hash:
                raise Denied('A concurrent file edit prevents rollback. Review the checkpoint.')
            atomic(path,previous)
            if spec.get('reload_argv'):
                result=await self.runner.run(spec['reload_argv'],timeout=60)
                if result.code:raise Denied('The previous file was restored but its reload command failed.')
            report=await self.postcheck(spec)
            return {'state':'rolled_back' if report['ok'] else 'rollback_failed','verified':report['ok'],
                    'detail':'The previous JSON settings were restored and their declared reload was attempted.',
                    'reason':reason,'postcheck':report}
        except Exception as exc:
            return {'state':'rollback_failed','verified':False,'reason':reason,'detail':self.redactor.clean(str(exc))}
