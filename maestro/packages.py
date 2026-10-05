"""Categorized owner-only ZIPs assembled from restored, verified snapshot files."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import time
import zipfile
from pathlib import Path

from .checkpoints import file_hash
from .security import Denied

PART_BYTES = 6*1024*1024
MAX_PACKAGE_BYTES = 512*1024*1024
CODE_SUFFIXES = {'.py','.js','.ts','.json','.toml','.yaml','.yml','.md','.txt','.service','.sh','.html','.css','.sql'}
SKIP = {'.git','.venv','node_modules','__pycache__','backups','releases','data','jobs','outputs','uploads','.cache','checkpoints'}


class Packages:
    def __init__(self, root, checkpoints, store):
        self.root, self.checkpoints, self.store = Path(root), checkpoints, store
        self.root.mkdir(mode=0o700, exist_ok=True); self.root.chmod(0o700)
        store.db.execute('CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,actor TEXT NOT NULL,job TEXT NOT NULL,at REAL NOT NULL,data TEXT NOT NULL)')
        store.db.commit()

    def plan(self, target):
        targets = self.store.targets() if target == 'all' else [self.store.target(target)]
        if not targets or any(t is None for t in targets):
            raise Denied('Choose all or an existing target ID.')
        files, count, total = {}, 0, 0
        for t in targets:
            base = Path(t.get('install') or '/nonexistent').resolve()
            if not base.is_dir() or base == Path('/') or len(base.parts) < 3:
                continue
            for here, dirs, names in os.walk(base, followlinks=False):
                dirs[:] = [n for n in dirs if n not in SKIP and not (Path(here)/n).is_symlink()]
                for name in names:
                    p = Path(here)/name
                    if p.is_symlink() or p.suffix not in CODE_SUFFIXES or name.startswith('.') or re.search('(?i)(secret|credential|private|token|password|policy)', name):
                        continue
                    size=p.stat().st_size
                    count+=1;total+=size
                    if count>1000 or total>64*1024*1024 or size>8*1024*1024:
                        raise Denied('Source package limit reached. Select a smaller application or use the encrypted filesystem checkpoint.')
                    files[str(p)]={'target':t['id'],'name':'applications/'+t['id']+'/source/'+str(p.relative_to(base))}
        inventory=self.store.get('inventory',{}).get('database_inventory',{})
        databases=[x['path'] for x in inventory.get('databases',[])] if target=='all' else []
        return {'target':target,'files':files,'targets':[t['id'] for t in targets], 'source_bytes':total,'databases':databases,
                'database_discovery_complete':inventory.get('complete',False)}

    async def create(self, actor, job, checkpoint, plan):
        exports = checkpoint['exports']
        if plan['target'] != 'all':
            target=self.store.target(plan['target']);source=target.get('database','')
            exports=[x for x in exports if x['name']=='target-'+target['id'] or source and str(Path(x['source']).resolve())==str(Path(source).resolve())]
        paths = list(plan['files'])+[x['file'] for x in exports]+[str(Path(checkpoint['stage'])/'manifest.json')]
        restored = await self.checkpoints.restore_selected(checkpoint['id'], paths)
        destination=Path(restored['destination'])
        try:
            # Consistency comes from the checkpoint, not a second walk of live files.
            grouped={}
            for export in exports:
                p=destination/export['file'].lstrip('/')
                if not p.is_file() or p.is_symlink() or await asyncio.to_thread(file_hash,p)!=export['sha256']:
                    raise Denied('Restored database export failed its checksum.')
                group='databases' if plan['target']=='all' else plan['target']
                grouped.setdefault(group,[]).append((p,'databases/'+Path(export['file']).name))
            for path,meta in plan['files'].items():
                p=destination/path.lstrip('/')
                if not p.is_file() or p.is_symlink():
                    raise Denied('A selected source file is outside backup coverage or was excluded: '+meta['name'])
                grouped.setdefault(meta['target'],[]).append((p,meta['name']))
            grouped.setdefault('maestro-reports', [])
            results=[]
            for group, files in grouped.items():
                results.append(await asyncio.to_thread(self._zip, actor, job, group, files, checkpoint, plan))
            return results
        finally:
            shutil.rmtree(destination)

    def _zip(self, actor, job, group, files, checkpoint, plan):
        if sum(p.stat().st_size for p,_ in files)>MAX_PACKAGE_BYTES:
            raise Denied('This package exceeds 512 MiB. Use the checkpoint or select fewer databases.')
        key=secrets.token_hex(12);path=self.root/(key+'.zip')
        manifest={'format':'maestro-package-v1','checkpoint':checkpoint['id'],'snapshot':checkpoint['snapshot_id'],
                  'group':group,'target':plan['target'],'created_at':time.time(),'files':[],'credential_files_included':False,
                  'database_discovery_complete':plan.get('database_discovery_complete',False)}
        try:
            with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
                for file,name in sorted(files,key=lambda x:x[1]):
                    manifest['files'].append({'name':name,'sha256':file_hash(file),'bytes':file.stat().st_size})
                    archive.write(file,name)
                archive.writestr('MANIFEST.json',json.dumps(manifest,indent=2))
                if group=='maestro-reports':
                    archive.writestr('monitoring/settings.json',json.dumps(self.store.settings(),indent=2))
                    archive.writestr('monitoring/targets.json',json.dumps(self.store.targets(),indent=2))
                    archive.writestr('README.txt','Maestro portable packages contain selected source files and consistent enrolled database exports. Full filesystem coverage remains in the encrypted checkpoint. Credentials and execution payloads are excluded from the reports package. Database exports contain private application data.\n')
            path.chmod(0o600)
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:
                    raise Denied('ZIP integrity check failed.')
            value={'id':key,'name':'maestro-'+group+'-'+checkpoint['id'][:8]+'.zip','bytes':path.stat().st_size,'sha256':file_hash(path),'checkpoint':checkpoint['id']}
            self.store.run('INSERT INTO artifacts VALUES(?,?,?,?,?)',(key,actor,job,time.time(),json.dumps(value)))
            return value
        except BaseException:
            path.unlink(missing_ok=True);raise

    def part(self, actor, key, offset, policy):
        row=self.store.one('SELECT * FROM artifacts WHERE id=?',(key,))
        if not row or actor not in policy.owners or not policy.same_owner(actor,row['actor']):
            raise Denied('The package is private to its linked superadmin identities.')
        if type(offset) is not int or offset<0 or offset%PART_BYTES:
            raise Denied('Use a valid package part offset.')
        value=json.loads(row['data']);path=self.root/(key+'.zip')
        if not path.is_file() or path.is_symlink() or path.stat().st_size!=value['bytes']:
            raise Denied('The retained package is missing or changed. Create a new backup.')
        if offset==0 and file_hash(path)!=value['sha256']:
            raise Denied('The retained ZIP failed its integrity checksum. Create a new backup.')
        if offset>=value['bytes']:
            raise Denied('Package offset is outside the file.')
        with path.open('rb') as file:
            file.seek(offset);data=file.read(PART_BYTES)
        name=value['name'] if value['bytes']<=PART_BYTES else value['name']+'.part'+str(offset//PART_BYTES+1).zfill(3)
        return {'name':name,'data':base64.b64encode(data).decode(),'next_offset':offset+len(data),'done':offset+len(data)>=value['bytes'],'sha256':value['sha256'],'part_sha256':hashlib.sha256(data).hexdigest()}

    def prune(self, days):
        for row in self.store.rows('SELECT id FROM artifacts WHERE at<?',(time.time()-days*86400,)):
            (self.root/(row['id']+'.zip')).unlink(missing_ok=True)
            self.store.run('DELETE FROM artifacts WHERE id=?',(row['id'],))
