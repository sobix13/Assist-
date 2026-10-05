import base64
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from maestro.delivery import job_attachments
from maestro.engine import Engine
from maestro.security import Denied, Policy
from .helpers import EngineCase, OWNER, TOKEN
from .test_checkpoints_output import FakeRestic
from maestro.inventory import scan_databases


class PackageTests(EngineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.db=self.folder/'db.sqlite3';c=sqlite3.connect(self.db);c.execute('CREATE TABLE n(value)');c.execute('INSERT INTO n VALUES(7)');c.commit();c.close()
        self.env=self.folder/'restic.env';self.env.write_text('RESTIC_REPOSITORY='+str(self.folder/'repo')+'\nRESTIC_PASSWORD=package-test-password\n');self.env.chmod(0o600)
        self.config=self.folder/'backup.json';self.config.write_text(json.dumps({'coverage_reviewed':True,'paths':[str(self.folder)],'env_file':str(self.env),'databases':[{'name':'app','kind':'sqlite','path':str(self.db)}]}));self.config.chmod(0o600)
        self.engine.policy.data.update(backup_required=True,backup_config=str(self.config))
        self.engine.checkpoints.command=FakeRestic(self.folder)

    async def test_zip_is_consistent_classified_private_and_delivered_as_files(self):
        job=await self.execute(kind='package',target='all');self.assertEqual(job['state'],'completed',job['output'])
        packages=job['operation']['data']['packages'];self.assertEqual(len(packages),2)
        key=next(p['id'] for p in packages if 'databases' in p['name'])
        result=await self.engine.dispatch(OWNER,'package_part',{'id':key})
        data=base64.b64decode(result['data']);self.assertEqual(hashlib.sha256(data).hexdigest(),result['sha256'])
        archive=self.folder/'download.zip';archive.write_bytes(data)
        with zipfile.ZipFile(archive) as z:
            manifest=json.loads(z.read('MANIFEST.json'));self.assertEqual(manifest['group'],'databases')
            restored=self.folder/'restored.sqlite3';restored.write_bytes(z.read('databases/app.sqlite3'))
        c=sqlite3.connect(restored);self.assertEqual(c.execute('SELECT value FROM n').fetchone()[0],7);c.close()
        engine=self.engine;api=type('API',(),{'call':lambda _,action,**p:engine.dispatch(OWNER,action,p)})()
        outputs=[name async for name,_ in job_attachments(api,job['id'])];self.assertEqual(len([n for n in outputs if n.endswith('.zip')]),2)

    async def test_zip_other_identity_and_modified_retained_package_are_rejected(self):
        job=await self.execute(kind='package',target='all');package=job['operation']['data']['packages'][0]
        with self.assertRaises(Denied):self.engine.packages.part('discord:111222333',package['id'],0,self.engine.policy)
        path=self.engine.packages.root/(package['id']+'.zip');data=path.read_bytes();path.write_bytes(b'x'+data[1:])
        with self.assertRaises(Denied):self.engine.packages.part(OWNER,package['id'],0,self.engine.policy)

    async def test_corrupt_restored_database_never_becomes_successful_package(self):
        original=self.engine.checkpoints.command
        async def broken(argv,*a,**kw):
            result=await original(argv,*a,**kw)
            if argv[:2]==['restic','restore'] and 'package-restore-' in argv[argv.index('--target')+1]:
                destination=Path(argv[argv.index('--target')+1]);path=next(destination.rglob('app.sqlite3'));path.write_bytes(b'corrupt')
            return result
        self.engine.checkpoints.command=broken
        job=await self.execute(kind='package',target='all');self.assertEqual(job['state'],'failed');self.assertEqual(self.engine.store.rows('SELECT * FROM artifacts'),[])

    async def test_sqlite_discovery_uses_header_not_filename_and_does_not_follow_symlinks(self):
        (self.folder/'fake.db').write_text('not a database');(self.folder/'alias.db').symlink_to(self.db)
        found=scan_databases([str(self.folder)])
        self.assertEqual({x['path'] for x in found['databases']},{str(self.db),str(self.engine.store.path)});self.assertTrue(found['complete'])

    async def test_unreadable_database_candidate_marks_discovery_incomplete(self):
        original=Path.open
        def read(path,*args,**kwargs):
            if path==self.db:raise PermissionError('fixture')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',read):found=scan_databases([str(self.folder)])
        self.assertFalse(found['complete']);self.assertTrue(found['errors'])

    async def test_source_package_never_includes_credential_named_files_or_dependencies(self):
        app=self.folder/'application';app.mkdir();(app/'main.py').write_text('print(1)');(app/'credentials.json').write_text('{}');(app/'.env').write_text('TOKEN=not-for-package')
        (app/'node_modules').mkdir();(app/'node_modules/index.js').write_text('dependency')
        target=self.engine.store.target('gate');target['install']=str(app);self.engine.store.save_target(target)
        plan=self.engine.packages.plan('gate')
        self.assertEqual([Path(x).name for x in plan['files']],['main.py'])

    async def test_package_scope_change_after_review_requires_a_new_review(self):
        app=self.folder/'application';app.mkdir();(app/'main.py').write_text('print(1)')
        target=self.engine.store.target('gate');target['install']=str(app);self.engine.store.save_target(target)
        review=await self.engine.dispatch(OWNER,'prepare',{'kind':'package','payload':{'target':'gate'}})
        (app/'new.py').write_text('print(2)')
        with self.assertRaises(Denied):
            await self.engine.dispatch(OWNER,'confirm',{k:review[k] for k in ('id','token','digest')})
        self.assertEqual(self.engine.store.rows('SELECT * FROM jobs'),[])

    async def test_real_restic_package_restores_source_and_wal_database(self):
        binary=os.environ.get('RESTIC_TEST_BINARY') or shutil.which('restic')
        if not binary:self.skipTest('Install Restic for the live ZIP acceptance test.')
        with tempfile.TemporaryDirectory(prefix='maestro-package-',dir=Path(__file__).resolve().parents[2]) as tmp:
            folder=Path(tmp);app=folder/'app';app.mkdir();(app/'main.py').write_text("print('source')")
            db=app/'data.sqlite3';writer=sqlite3.connect(db);writer.execute('PRAGMA journal_mode=WAL');writer.execute('CREATE TABLE n(value)');writer.execute('INSERT INTO n VALUES(9)');writer.commit()
            env=folder/'restic.env';env.write_text('RESTIC_REPOSITORY='+str(folder/'repo')+'\nRESTIC_PASSWORD=maestro-real-package-password\nRESTIC_CACHE_DIR='+str(folder/'cache')+'\n');env.chmod(0o600)
            subprocess.run([binary,'init'],env={'PATH':'/usr/bin:/bin','RESTIC_REPOSITORY':str(folder/'repo'),'RESTIC_PASSWORD':'maestro-real-package-password'},stdout=subprocess.DEVNULL,check=True)
            cfg=folder/'backup.json';cfg.write_text(json.dumps({'coverage_reviewed':True,'paths':[str(app)],'env_file':str(env),'databases':[{'name':'app','kind':'sqlite','path':str(db)}]}));cfg.chmod(0o600)
            engine=Engine(Policy({'owners':[OWNER],'frontends':[{'uid':os.getuid(),'token':TOKEN,'transport':'discord'}],'root_commands':True,'restic_binary':binary,'backup_config':str(cfg)}),folder/'state')
            try:
                target=engine.store.target('gate');target['install']=str(app);target['database']=str(db);engine.store.save_target(target)
                review=await engine.dispatch(OWNER,'prepare',{'kind':'package','payload':{'target':'gate'}})
                job=await engine.dispatch(OWNER,'confirm',{k:review[k] for k in ('id','token','digest')});await engine.jobs[job['id']]
                result=engine.job(OWNER,job['id']);self.assertEqual(result['state'],'completed',result['output'])
                package=result['operation']['data']['packages'][0];zip_path=engine.packages.root/(package['id']+'.zip')
                with zipfile.ZipFile(zip_path) as z:
                    self.assertEqual(z.read('applications/gate/source/main.py'),b"print('source')")
                    restored=folder/'restored.sqlite3';restored.write_bytes(z.read('databases/app.sqlite3'))
                c=sqlite3.connect(restored);self.assertEqual(c.execute('SELECT value FROM n').fetchone()[0],9);c.close()
            finally:await engine.close();writer.close()
