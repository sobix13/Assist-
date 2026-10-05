import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock

from maestro.inventory import Inventory, scan_projects, safe_remote
from maestro.process import Result
from maestro.probes import futarchist_health, validate_target
from maestro.recipes import FileRollback
from maestro.security import Denied
from .helpers import EngineCase, OWNER


class RepairTests(EngineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        # A production recipe only accepts specific paths under operational roots.
        parent = Path('/root') if os.geteuid()==0 else Path.home()
        self.repair_temp=tempfile.TemporaryDirectory(prefix='assistant-repair-test-',dir=parent)
        self.app=Path(self.repair_temp.name)/'application';self.app.mkdir();self.config=self.app/'config.txt';self.config.write_text('original')
        self.config.chmod(0o640)
        self.path=self.folder/'recipes.json';self.engine.policy.data['recipes_config']=str(self.path)
        self.recipe={'id':'test-repair','target':'gate','argv':['/usr/bin/python3','-c',"from pathlib import Path;Path("+repr(str(self.config))+").write_text('changed')"],
                     'rollback_paths':[str(self.app)],'verify_seconds':1,'timeout':5,'automatic':False}
        self.save_recipe()

    def save_recipe(self):
        self.path.write_text(json.dumps({'recipes':[self.recipe]}));self.path.chmod(0o600)

    async def asyncTearDown(self):
        self.repair_temp.cleanup();await super().asyncTearDown()

    async def test_failed_postcheck_restores_original_code_and_permissions(self):
        self.engine.probes.service=AsyncMock(side_effect=[{'ok':False},{'ok':False},{'ok':True}])
        job=await self.execute(kind='recipe',recipe='test-repair')
        self.assertEqual(job['state'],'rolled_back');self.assertEqual(self.config.read_text(),'original')
        self.assertEqual(self.config.stat().st_mode&0o777,0o640)
        self.assertTrue(job['operation']['data']['repair']['files_restored'])
        self.assertTrue(job['operation']['data']['repair']['verified'])

    async def test_successful_repair_preserves_changed_code_and_records_health(self):
        self.engine.probes.service=AsyncMock(return_value={'ok':True})
        job=await self.execute(kind='recipe',recipe='test-repair')
        self.assertEqual(job['state'],'completed');self.assertEqual(self.config.read_text(),'changed')
        self.assertTrue(job['operation']['data']['repair']['verified'])

    async def test_preflight_failure_does_not_run_or_restore_any_files(self):
        self.recipe['preflight']=['/usr/bin/python3','-c','raise SystemExit(2)'];self.save_recipe()
        before=self.config.stat().st_ino
        job=await self.execute(kind='recipe',recipe='test-repair')
        self.assertEqual(job['state'],'failed');self.assertEqual(self.config.read_text(),'original')
        self.assertEqual(self.config.stat().st_ino,before)

    async def test_recipe_changed_after_review_blocks_execution(self):
        prepared=self.engine.prepare(OWNER,{'kind':'recipe','payload':{'recipe':'test-repair'}})
        self.recipe['argv']=['/usr/bin/python3','-c','print("changed-recipe")'];self.save_recipe()
        key=self.engine.confirm(OWNER,{k:prepared[k] for k in ('id','token','digest')})['id'];await self.engine.jobs[key]
        job=self.engine.job(OWNER,key)
        self.assertEqual(job['state'],'failed');self.assertIn('changed after review',job['output'])
        self.assertEqual(self.config.read_text(),'original')

    async def test_new_database_stops_rollback_without_deleting_fresh_data(self):
        rollback=FileRollback(self.folder/'rollback',[str(self.app)]);rollback.capture()
        db=sqlite3.connect(self.app/'fresh.data');db.execute('CREATE TABLE n(value)');db.execute('INSERT INTO n VALUES(88)');db.commit();db.close()
        self.config.write_text('changed')
        with self.assertRaises(Denied):rollback.restore()
        self.assertEqual(self.config.read_text(),'changed')
        check=sqlite3.connect(self.app/'fresh.data');self.assertEqual(check.execute('SELECT value FROM n').fetchone()[0],88);check.close()
        self.assertTrue((rollback.folder/'manifest.json').is_file())

    async def test_existing_database_parent_alias_and_overlapping_paths_rejected(self):
        database=self.app/'data.sqlite3';database.write_bytes(b'not-code')
        with self.assertRaises(Denied):FileRollback(self.folder/'database-rollback',[str(self.app)]).capture()
        with self.assertRaises(Denied):FileRollback(self.folder/'overlap',[str(self.app),str(self.config)])
        alias=self.folder/'alias';alias.symlink_to(self.app,target_is_directory=True)
        with self.assertRaises(Denied):FileRollback(self.folder/'aliases',[str(alias/'config.txt')]).capture()

    async def test_corrupt_rollback_copy_never_deletes_live_configuration(self):
        rollback=FileRollback(self.folder/'corrupt-copy',[str(self.app)]);rollback.capture()
        row=next(x for x in rollback.entries if x['kind']=='file')
        (rollback.folder/row['stored']).write_bytes(b'corrupted')
        self.config.write_text('live-current')
        with self.assertRaises(Denied):rollback.restore()
        self.assertEqual(self.config.read_text(),'live-current')

    async def test_external_controller_prevents_recovery_even_when_enabled(self):
        fake=self.use_fake_probes();target=self.engine.store.target('melee-zone');target.update(enabled=True,expected_running=True,auto_restart=True)
        fake.states[target['unit']]='failed'
        report=await self.engine.probes.service(target)
        result=await self.engine.recover(target,report,{**self.engine.store.settings(),'auto_recovery':True})
        self.assertEqual(result['action'],'observe');self.assertFalse(any(c[:2]==['systemctl','restart'] for c in fake.calls))

    async def test_automatic_recipe_requires_matching_codes_and_both_opt_ins(self):
        self.recipe.update(automatic=True,match_codes=['worker_unhealthy']);self.save_recipe()
        target=self.engine.store.target('gate');target.update(enabled=True,expected_running=True,auto_restart=False,auto_recipe='test-repair')
        self.engine.store.save_target(target)
        self.engine.store.run("INSERT INTO incidents VALUES('gate:worker_unhealthy','gate','worker_unhealthy',0,0,2,'open','stale')")
        self.engine.probes.service=AsyncMock(return_value={'ok':True})
        report={'issues':[{'code':'worker_unhealthy','detail':'stale'}],'journal':'','fields':{'ActiveState':'active'}}
        settings={**self.engine.store.settings(),'auto_recovery':False}
        self.assertIsNone(await self.engine.recover(target,report,settings))
        result=await self.engine.recover(target,report,{**settings,'auto_recovery':True})
        self.assertEqual(result['action'],'recipe');self.assertTrue(result['result']['verified'])
        self.assertEqual(self.config.read_text(),'changed')


class InventoryTests(EngineCase):
    async def test_bounded_inventory_does_not_adopt_services_or_expose_container_env(self):
        runner=type('InventoryRunner',(),{'run':AsyncMock()})()
        async def run(argv,**kwargs):
            if argv[:2]==['systemctl','list-units']:return Result(0,'unknown-app.service loaded active running Description\n')
            if argv[0]=='docker':return Result(0,json.dumps({'ID':'123','Names':'worker','Image':'test','Status':'Up','Env':['PASSWORD=hidden']})+'\n')
            return Result(0,'')
        runner.run.side_effect=run
        inventory=Inventory(runner,self.engine.redactor,[str(self.folder)])
        before=self.engine.store.targets();result=await inventory.collect()
        self.assertEqual(result['services'][0]['unit'],'unknown-app.service');self.assertNotIn('hidden',json.dumps(result))
        self.assertEqual(self.engine.store.targets(),before)
        self.assertFalse(any('pm2' in arg for call in runner.run.call_args_list for arg in call.args[0]))

    async def test_project_scan_excludes_dependencies_and_masks_remote_credentials(self):
        project=self.folder/'project';project.mkdir();(project/'requirements.txt').write_text('test');(project/'VERSION').write_text('1.2.3');(project/'.env').write_text('PASSWORD=not-read')
        git=project/'.git';git.mkdir();(git/'config').write_text('[remote "origin"]\n url = https://private-password@github.com/user/repo.git\n')
        nested=project/'node_modules/dependency';nested.mkdir(parents=True);(nested/'package.json').write_text('{}')
        report=scan_projects([str(self.folder)])
        self.assertEqual(len(report['projects']),1);self.assertEqual(report['projects'][0]['version'],'1.2.3')
        self.assertNotIn('private-password',json.dumps(report));self.assertNotIn('not-read',json.dumps(report))
        self.assertEqual(report['projects'][0]['environment_files'],['.env'])
        self.assertIn('[REDACTED]',safe_remote('https://token@github.com/a/b'))

    async def test_futarchist_live_heartbeat_stale_and_degraded_are_distinguished(self):
        path=self.folder/'fut.sqlite3';db=sqlite3.connect(path);db.execute('CREATE TABLE heartbeats(component TEXT,last_at REAL,state TEXT)')
        for component in ('poller','inbox','outbox','maintenance','web'):db.execute('INSERT INTO heartbeats VALUES(?,?,?)',(component,time.time(),'ok'))
        db.commit();self.assertTrue(futarchist_health(str(path),'all')['ok'])
        db.execute("UPDATE heartbeats SET last_at=0 WHERE component='poller'");db.commit();self.assertFalse(futarchist_health(str(path),'bot')['ok'])
        db.execute("UPDATE heartbeats SET last_at=?,state='degraded' WHERE component='poller'",(time.time(),));db.commit();self.assertFalse(futarchist_health(str(path),'bot')['ok'])
        self.assertTrue(futarchist_health(str(path),'web')['ok']);db.close()

    async def test_docker_crash_and_intentional_stop_have_different_recovery_states(self):
        target=validate_target({'id':'docker-app','unit':'docker.service','driver':'docker','container':'app','expected_running':True})
        runner=type('Runner',(),{'run':AsyncMock()})()
        from maestro.probes import Probes
        for exit_code,expected in [(0,'inactive'),(1,'failed')]:
            runner.run.side_effect=lambda argv,**kwargs: Result(0,json.dumps({'Status':'exited','Running':False,'ExitCode':exit_code}) if argv[1]=='inspect' else '')
            report=await Probes(runner,self.engine.redactor).service(target)
            self.assertEqual(report['fields']['ActiveState'],expected)

    async def test_heartbeat_and_certificate_failures_are_reported_without_repair(self):
        fake=self.use_fake_probes();path=self.folder/'heartbeat';path.write_text('success');os.utime(path,(0,0))
        target=validate_target({'id':'worker','unit':'worker.service','expected_running':True,'heartbeat_file':str(path),'certificate_file':str(self.folder/'missing.pem')})
        report=await self.engine.probes.service(target,daily=True)
        self.assertEqual({x['code'] for x in report['issues']},{'worker_unhealthy','certificate_review'})
        self.assertFalse(any(c[:2]==['systemctl','restart'] for c in fake.calls))
