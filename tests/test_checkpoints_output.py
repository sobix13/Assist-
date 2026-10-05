import asyncio
import base64
import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from maestro.checkpoints import validate_config
from maestro.delivery import binary_parts, job_attachments, text_parts
from maestro.engine import Engine
from maestro.security import Denied, Policy
from .helpers import EngineCase, OWNER, OTHER, TOKEN

TELEGRAM = 'telegram:345678901'


class FakeRestic:
    """Fault injection at the CLI boundary; SQLite and snapshot files remain real."""
    def __init__(self, root):
        self.root, self.calls = root, []
        self.partial = self.bad_check = self.bad_restore = self.bad_resume = False
        self.saved, self.stage = None, None

    async def __call__(self, argv, env, timeout, **kwargs):
        self.calls.append(argv)
        if argv[:2] == ['systemctl', 'is-active']:
            return 0, 'active'
        if argv[:2] == ['systemctl', 'start']:
            return (1 if self.bad_resume else 0), ''
        if argv[:2] == ['restic', 'backup']:
            self.stage = Path(argv[-1]); self.saved = self.root/('snapshot-'+self.stage.name)
            shutil.copytree(self.stage, self.saved)
            return (3 if self.partial else 0), json.dumps({'message_type':'summary','snapshot_id':'a'*64})
        if argv[:2] == ['restic', 'check']:
            return (1 if self.bad_check else 0), ''
        if argv[:2] == ['restic', 'restore']:
            if self.bad_restore:
                return 1, 'restore failed'
            dest = Path(argv[argv.index('--target')+1])/str(self.stage).lstrip('/')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(self.saved,dest)
            return 0, ''
        return 0, ''


class CheckpointTests(EngineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.app = self.folder/'application'; self.app.mkdir()
        self.db_path = self.app/'wallet.sqlite3'
        self.writer = sqlite3.connect(self.db_path)
        self.writer.execute('PRAGMA journal_mode=WAL')
        self.writer.execute('CREATE TABLE records(n)')
        self.writer.execute('INSERT INTO records VALUES(42)'); self.writer.commit()
        self.env = self.folder/'restic.env'
        self.env.write_text('RESTIC_REPOSITORY='+str(self.folder/'repository')+'\nRESTIC_PASSWORD=mock-repository-encryption-password\n')
        self.env.chmod(0o600)
        self.config = {'coverage_reviewed':True,'env_file':str(self.env),'paths':[str(self.app)],
                       'databases':[{'name':'wallet','kind':'sqlite','path':str(self.db_path)}]}
        self.config_path = self.folder/'backup.json'; self.save_config()
        self.engine.policy.data.update(backup_required=True, backup_config=str(self.config_path))
        self.fake = FakeRestic(self.folder)
        self.engine.checkpoints.command = self.fake

    def save_config(self):
        self.config_path.write_text(json.dumps(self.config)); self.config_path.chmod(0o600)

    async def asyncTearDown(self):
        self.writer.close()
        await super().asyncTearDown()

    async def test_before_execution_checkpoint_restores_wal_and_command_runs_after_verification(self):
        marker = self.app/'executed'
        job = await self.execute(code='printf verified > '+str(marker))
        self.assertEqual(job['state'],'completed'); self.assertEqual(marker.read_text(),'verified')
        checkpoint = job['operation']['data']['checkpoint']
        self.assertTrue(checkpoint['repository_check']); self.assertTrue(checkpoint['export_restore_verified'])
        saved = sqlite3.connect(self.fake.saved/'wallet.sqlite3')
        self.assertEqual(saved.execute('SELECT n FROM records').fetchone()[0],42); saved.close()
        self.assertFalse(Path(checkpoint['stage']).exists())

    async def test_partial_backup_metadata_failure_and_restore_failure_all_block_execution(self):
        for flag in ('partial','bad_check','bad_restore'):
            with self.subTest(flag=flag):
                setattr(self.fake,flag,True)
                marker = self.app/flag
                job = await self.execute(code='touch '+str(marker))
                self.assertEqual(job['state'],'failed'); self.assertFalse(marker.exists())
                self.assertIsNone(job['exit_code'])
                self.assertEqual(self.engine.store.one('SELECT state FROM backup_runs ORDER BY at DESC LIMIT 1')['state'],'failed')
                setattr(self.fake,flag,False)

    async def test_missing_database_blocks_and_never_creates_empty_database(self):
        missing = self.app/'missing.sqlite3';self.config['databases'][0]['path']=str(missing);self.save_config()
        job=await self.execute(code='printf must-not-run')
        self.assertEqual(job['state'],'failed');self.assertFalse(missing.exists())
        self.assertNotIn('must-not-run',job['output'])

    async def test_missing_backup_config_and_unreviewed_coverage_block_execution(self):
        self.config_path.unlink()
        marker = self.app/'forbidden'
        job=await self.execute(code='touch '+str(marker))
        self.assertEqual(job['state'],'failed');self.assertFalse(marker.exists())
        self.config['coverage_reviewed']=False;self.save_config()
        job=await self.execute(code='touch '+str(marker))
        self.assertEqual(job['state'],'failed');self.assertFalse(marker.exists())

    async def test_paused_writers_resume_after_failure_and_resume_failure_blocks_commands(self):
        self.config['quiesce_units']=['example.service'];self.save_config();self.fake.partial=True
        await self.execute(code='true')
        self.assertIn(['systemctl','stop','example.service'],self.fake.calls)
        self.assertIn(['systemctl','start','example.service'],self.fake.calls)
        self.fake.partial=False;self.fake.bad_resume=True
        marker=self.app/'resume-failure';job=await self.execute(code='touch '+str(marker))
        self.assertEqual(job['state'],'failed');self.assertFalse(marker.exists())

    async def test_offline_restore_preserves_new_production_data(self):
        checkpoint=await self.engine.checkpoints.create('test',self.engine.store.targets())
        self.writer.execute('INSERT INTO records VALUES(99)');self.writer.commit()
        result=await self.engine.checkpoints.restore_stage(checkpoint['id'])
        export=checkpoint['exports'][0];database=Path(result['destination'])/export['file'].lstrip('/')
        restored=sqlite3.connect(database)
        self.assertEqual(restored.execute('SELECT n FROM records').fetchall(),[(42,)]);restored.close()
        self.assertEqual(self.writer.execute('SELECT n FROM records').fetchall(),[(42,),(99,)])
        self.assertTrue(result['verified'])

    async def test_restore_checksum_detects_corrupt_export(self):
        checkpoint=await self.engine.checkpoints.create('test',self.engine.store.targets())
        (self.fake.saved/'wallet.sqlite3').write_bytes(b'corrupt')
        with self.assertRaises(Denied):
            await self.engine.checkpoints.restore_stage(checkpoint['id'])

    async def test_unsafe_credentials_and_own_quiescing_are_rejected(self):
        self.env.chmod(0o644)
        self.assertFalse(self.engine.checkpoints.status()['configured'])
        self.env.chmod(0o600)
        self.config['quiesce_units']=['maestro-agent.service']
        with self.assertRaises(Denied):validate_config(self.config)

    async def test_failed_backup_blocks_automatic_restart_and_reports_it(self):
        fake=self.use_fake_probes();target=self.engine.store.target('gate');target.update(enabled=True,expected_running=True,auto_restart=True)
        self.engine.store.save_target(target);fake.states[target['unit']]='failed'
        self.engine.store.run("INSERT INTO incidents VALUES('gate:unit_down','gate','unit_down',0,0,2,'open','failed')")
        self.fake.partial=True
        report=await self.engine.probes.service(target)
        settings={**self.engine.store.settings(),'auto_recovery':True}
        result=await self.engine.recover(target,report,settings)
        self.assertEqual(result['action'],'blocked')
        self.assertFalse(any(c[:2]==['systemctl','restart'] for c in fake.calls))
        self.assertTrue(self.engine.store.events_for(OWNER))

    async def test_real_restic_encrypted_snapshot_restores_committed_wal_and_runs_job(self):
        binary=os.environ.get('RESTIC_TEST_BINARY') or shutil.which('restic')
        if not binary:self.skipTest('Install Restic or set RESTIC_TEST_BINARY for the real backup acceptance test.')
        # Restic excludes /tmp from broad host roots. Keep this integration fixture
        # outside /tmp so it uses exactly the production exclusion behavior.
        with tempfile.TemporaryDirectory(prefix='restic-integration-',dir=Path(__file__).resolve().parents[2]) as temp:
            folder=Path(temp);app=folder/'app';app.mkdir();database=app/'data.sqlite3'
            writer=sqlite3.connect(database);writer.execute('PRAGMA journal_mode=WAL');writer.execute('CREATE TABLE n(value)');writer.execute('INSERT INTO n VALUES(7)');writer.commit()
            env=folder/'restic.env';env.write_text('RESTIC_REPOSITORY='+str(folder/'repo')+'\nRESTIC_PASSWORD=integration-encryption-password\nRESTIC_CACHE_DIR='+str(folder/'cache')+'\n');env.chmod(0o600)
            subprocess.run([binary,'init'],env={'PATH':'/usr/bin:/bin','RESTIC_REPOSITORY':str(folder/'repo'),'RESTIC_PASSWORD':'integration-encryption-password','RESTIC_CACHE_DIR':str(folder/'cache')},stdout=subprocess.DEVNULL,check=True)
            cfg=folder/'backup.json';cfg.write_text(json.dumps({'coverage_reviewed':True,'paths':[str(app)],'env_file':str(env),'databases':[{'name':'application','kind':'sqlite','path':str(database)}]}));cfg.chmod(0o600)
            p=Policy({'owners':[OWNER],'frontends':[{'uid':os.getuid(),'token':TOKEN,'transport':'discord'}],'root_commands':True,'backup_config':str(cfg),'restic_binary':binary})
            engine=Engine(p,folder/'state')
            try:
                preview=engine.prepare(OWNER,{'kind':'shell','payload':{'profile':'root','code':'printf real-backup-verified'}})
                key=engine.confirm(OWNER,{k:preview[k] for k in ('id','token','digest')})['id'];await engine.jobs[key]
                job=engine.job(OWNER,key);self.assertEqual(job['state'],'completed');self.assertEqual(job['output'],'real-backup-verified')
                checkpoint=job['operation']['data']['checkpoint'];restored=await engine.checkpoints.restore_stage(checkpoint['id'])
                clone=sqlite3.connect(Path(restored['destination'])/checkpoint['exports'][0]['file'].lstrip('/'))
                self.assertEqual(clone.execute('SELECT value FROM n').fetchone()[0],7);clone.close()
                self.assertTrue((await engine.checkpoints.inspect_repository())['ok'])
            finally:await engine.close();writer.close()


class OutputAndIdentityTests(EngineCase):
    async def test_linked_interfaces_share_jobs_uploads_but_confirmation_stays_transport_bound(self):
        self.engine.policy.owners.add(TELEGRAM);self.engine.policy.groups=[[OWNER,TELEGRAM]]
        upload=self.engine.files.stage(OWNER,'hello.py',b"print('linked')")
        self.assertEqual(self.engine.files.metadata(TELEGRAM,upload['id'])['id'],upload['id'])
        review=self.engine.prepare(OWNER,{'kind':'shell','payload':{'profile':'root','code':'printf linked'}})
        with self.assertRaises(Denied):self.engine.confirm(TELEGRAM,{k:review[k] for k in ('id','token','digest')})
        key=self.engine.confirm(OWNER,{k:review[k] for k in ('id','token','digest')})['id'];await self.engine.jobs[key]
        self.assertEqual(self.engine.job(TELEGRAM,key)['output'],'linked')
        with self.assertRaises(Denied):self.engine.job(OTHER,key)

    async def test_independent_report_ack_survives_backend_reopen(self):
        self.engine.policy.owners.add(TELEGRAM);self.engine._event('fixture',{'text':'persistent'})
        first=self.engine.store.events_for(OWNER)[0]
        self.engine.store.acknowledge(first['id'],OWNER)
        self.assertEqual(self.engine.store.events_for(OWNER),[])
        self.assertEqual(self.engine.store.events_for(TELEGRAM)[0]['id'],first['id'])
        self.assertEqual(self.engine.store.one('SELECT delivered FROM outbox WHERE id=?',(first['id'],))['delivered'],0)
        from maestro.store import Store
        self.engine.store.close();self.engine.store=Store(self.folder/'state/maestro.sqlite3')
        self.assertEqual(self.engine.store.events_for(TELEGRAM)[0]['id'],first['id'])
        self.engine.store.acknowledge(first['id'],TELEGRAM)
        self.assertTrue(self.engine.store.events_for(OTHER))

    async def test_long_output_is_complete_redacted_and_downloadable(self):
        self.engine.redactor.values.add('private-split-token-123456')
        job=await self.execute(code="python3 -c \"import sys;sys.stdout.write('A'*200000+'private-split-'+'token-123456'+'Z'*200000)\"")
        metadata=job['operation']['data']['output'];self.assertFalse(metadata['incomplete']);self.assertGreater(metadata['bytes'],390000)
        api=type('API',(),{'call':lambda _,action,**p:self.engine.dispatch(OWNER,action,p)})()
        chunks=[data async for name,data in job_attachments(api,job['id'])]
        raw=b''.join(chunks)
        self.assertTrue(raw.startswith(b'A'*200000));self.assertTrue(raw.endswith(b'Z'*200000))
        self.assertNotIn(b'private-split-token',raw);self.assertIn(b'[REDACTED]',raw)
        self.assertFalse(self.engine.outputs.path(job['id'],'.raw').exists())

    async def test_output_limit_stops_flood_and_marks_incomplete(self):
        current=self.engine.store.settings();self.engine.save_settings(OWNER,{'revision':current['revision'],'changes':{'output_limit_mib':1}})
        job=await self.execute(code="python3 -c \"import sys;sys.stdout.write('X'*4000000)\"")
        self.assertEqual(job['state'],'output_limited');self.assertTrue(job['operation']['data']['output']['incomplete'])
        self.assertLess(job['operation']['data']['output']['bytes'],1100000)
        self.assertFalse(self.engine.runner.active)

    async def test_cancelled_output_is_preserved_and_never_replayed(self):
        review=self.engine.prepare(OWNER,{'kind':'shell','payload':{'profile':'root','code':'printf before-cancel; sleep 30'}})
        key=self.engine.confirm(OWNER,{k:review[k] for k in ('id','token','digest')})['id']
        for _ in range(100):
            if 'before-cancel' in self.engine.runner.output(key):break
            await asyncio.sleep(.01)
        await self.engine.dispatch(OWNER,'cancel',{'id':key});await self.engine.jobs[key]
        row=self.engine.job(OWNER,key);self.assertEqual(row['state'],'cancelled')
        part=self.engine.outputs.part(key);self.assertIn(b'before-cancel',base64.b64decode(part['data']))
        self.assertTrue(part['metadata']['incomplete'])

    async def test_jobs_are_serialized_across_owner_interfaces(self):
        self.engine.policy.owners.add(TELEGRAM);self.engine.policy.groups=[[OWNER,TELEGRAM]]
        marker=self.folder/'order'
        keys=[]
        for actor,code in [(OWNER,'printf A >> '+str(marker)+'; sleep .1; printf B >> '+str(marker)),(TELEGRAM,'printf C >> '+str(marker))]:
            prepared=self.engine.prepare(actor,{'kind':'shell','payload':{'profile':'root','code':code}})
            keys.append(self.engine.confirm(actor,{k:prepared[k] for k in ('id','token','digest')})['id'])
        await asyncio.gather(*(self.engine.jobs[k] for k in keys))
        self.assertEqual(marker.read_text(),'ABC')

    async def test_transport_parts_preserve_utf8_and_binary_bytes(self):
        text='hello 🏎 '*100
        parts=list(text_parts('unicode.txt',text,limit=17))
        self.assertEqual(b''.join(data for name,data in parts).decode(),text)
        for name,data in parts:data.decode('utf-8')
        data=os.urandom(100);self.assertEqual(b''.join(x for n,x in binary_parts('test.rca',data,17)),data)

    async def test_gzip_large_export_has_correct_content_and_hash(self):
        job=await self.execute(code="python3 -c \"print('abc'*2200000)\"")
        api=type('API',(),{'call':lambda _,action,**p:self.engine.dispatch(OWNER,action,p)})()
        outputs=[(n,d) async for n,d in job_attachments(api,job['id'])]
        self.assertEqual(len(outputs),1);self.assertTrue(outputs[0][0].endswith('.gz'))
        self.assertEqual(len(gzip.decompress(outputs[0][1])),6600001)

    async def test_large_unicode_redaction_preserves_content_and_removes_controls(self):
        content=('Car 車🏎\x07\u202e\n'*100000)+'PASSWORD=hidden-value'
        cleaned=self.engine.redactor.clean(content)
        self.assertEqual(cleaned.count('車🏎'),100000)
        self.assertNotIn('\x07',cleaned);self.assertNotIn('\u202e',cleaned);self.assertNotIn('hidden-value',cleaned)
