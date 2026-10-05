import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from maestro.checkpoints import Checkpoints
from maestro.discord_ui import Panel
from maestro.engine import Engine
from maestro.security import Denied, Policy
from maestro.store import DEFAULTS, Store
from .helpers import EngineCase, OWNER, TOKEN


class V2LifecycleTests(EngineCase):
    async def test_backup_is_required_by_default_and_string_policy_flags_are_rejected(self):
        p=Policy({'owners':[OWNER],'frontends':[{'uid':os.getuid(),'token':TOKEN,'transport':'discord'}]})
        self.assertTrue(p.backup_required)
        with self.assertRaises(Denied):Policy({**p.data,'backup_required':'true'})

    async def test_link_groups_cannot_accidentally_merge_multiple_owners(self):
        p=self.engine.policy.data.copy();p['owner_groups']=[[OWNER],[OWNER]]
        with self.assertRaises(Denied):Policy(p)

    async def test_local_checkpoint_store_does_not_invalidate_live_jobs_or_reviews(self):
        review=self.engine.prepare(OWNER,{'kind':'shell','payload':{'code':'printf reviewed'}})
        self.engine.store.run("INSERT INTO jobs(id,actor,kind,state,payload,created) VALUES('fixture',?,'shell','running','{}',?)",(OWNER,time.time()))
        second=Store(self.engine.store.path,recover=False)
        try:
            self.assertEqual(second.one("SELECT state FROM jobs WHERE id='fixture'")['state'],'running')
            self.assertEqual(second.one('SELECT used FROM pending WHERE id=?',(review['id'],))['used'],0)
        finally:second.close()

    async def test_process_repository_lock_prevents_cross_process_backup_overlap(self):
        second=Checkpoints(self.engine.policy,self.engine.root,self.engine.store,self.engine.redactor)
        with self.engine.checkpoints.process_lock():
            with self.assertRaises(Denied):
                with second.process_lock():pass
        with second.process_lock():pass

    async def test_duplicate_service_and_container_enrollment_is_rejected(self):
        with self.assertRaises(Denied):await self.engine.dispatch(OWNER,'save_target',{'id':'duplicate','unit':'ripcars-gate.service'})
        first={'id':'container','driver':'docker','container':'worker','unit':'docker.service'}
        await self.engine.dispatch(OWNER,'save_target',first)
        with self.assertRaises(Denied):await self.engine.dispatch(OWNER,'save_target',{**first,'id':'duplicate-container'})

    async def test_interrupted_paused_checkpoint_is_reported_once_without_replaying_commands(self):
        self.engine.store.run('INSERT INTO backup_runs VALUES(?,?,?,?,?)',('b'*24,time.time(),'fixture','running',json.dumps({'stopped_units':['example.service']})))
        await self.engine.close();self.engine=Engine(self.engine.policy,self.folder/'state')
        queued=self.engine.store.events_for(OWNER)
        self.assertTrue(any(e['kind']=='checkpoint_review' for e in queued))
        count=len(queued)
        await self.engine.close();self.engine=Engine(self.engine.policy,self.folder/'state')
        self.assertEqual(len(self.engine.store.events_for(OWNER)),count)
        self.assertFalse(self.engine.jobs)

    async def test_discovery_uses_real_unit_workdir_and_relative_database_environment(self):
        app=self.folder/'app';app.mkdir();env=app/'.env';env.write_text('DATABASE_PATH=runtime.db\n')
        self.engine.probes.service=AsyncMock(return_value={'fields':{'LoadState':'loaded','ActiveState':'active','WorkingDirectory':str(app),'EnvironmentFiles':str(env)+' (ignore_errors=no)'}})
        await self.engine.dispatch(OWNER,'discover',{})
        target=self.engine.store.target('gate')
        self.assertEqual(target['install'],str(app));self.assertEqual(target['env_file'],str(env));self.assertEqual(target['database'],str(app/'runtime.db'))
        self.assertFalse(target['auto_restart'])

    async def test_target_dropdown_pages_preserve_access_to_more_than_twenty_five_services(self):
        bot=SimpleNamespace(owner_id=123456789,guild_id=987654321,api=SimpleNamespace(call=AsyncMock()))
        targets=[{'id':'service-'+str(n),'unit':'service-'+str(n)+'.service','enabled':False,'revision':0} for n in range(60)]
        panel=Panel(bot,'targets',{**DEFAULTS,'revision':0},targets)
        select=next(c for c in panel.children if isinstance(c,discord.ui.Select) and c.row==1)
        self.assertEqual(len(select.options),25)
        button=next(c for c in panel.children if isinstance(c,discord.ui.Button) and c.label=='Next')
        i=SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        await button.callback(i)
        next_panel=i.response.edit_message.call_args.kwargs['view']
        next_select=next(c for c in next_panel.children if isinstance(c,discord.ui.Select) and c.row==1)
        self.assertEqual(next_select.options[0].value,'service-25')
        third=Panel(bot,'targets',{**DEFAULTS,'revision':0},targets,page=2)
        self.assertEqual(len(next(c for c in third.children if isinstance(c,discord.ui.Select) and c.row==1).options),10)

    async def test_scheduled_daily_backup_and_report_include_final_repository_result(self):
        self.use_fake_probes();self.engine.inventory.collect=AsyncMock(return_value={'services':[]})
        self.engine.checkpoints.status=lambda:{'configured':True}
        self.engine.checkpoints.create=AsyncMock(return_value={'id':'c'*24,'state':'completed'})
        self.engine.checkpoints.inspect_repository=AsyncMock(return_value={'ok':True,'fixture':True})
        result=await self.engine.check_cycle('daily')
        self.engine.checkpoints.create.assert_awaited_once()
        event=next(e for e in self.engine.store.events_for(OWNER) if e['kind']=='scheduled_report')
        self.assertEqual(event['payload']['backup_repository'],result['backup_repository'])
        self.assertEqual(event['payload']['daily_checkpoint']['state'],'completed')

    async def test_manual_daily_checks_are_read_only_and_maintenance_defers_daily_checkpoint(self):
        self.use_fake_probes();self.engine.inventory.collect=AsyncMock(return_value={'services':[]})
        self.engine.checkpoints.status=lambda:{'configured':True}
        self.engine.checkpoints.create=AsyncMock()
        self.engine.checkpoints.inspect_repository=AsyncMock(return_value={'ok':True})
        await self.engine.check_cycle('daily',manual=True)
        self.engine.checkpoints.create.assert_not_awaited()
        self.engine.checkpoints.inspect_repository.assert_awaited_once_with(read_data=False)
        settings=self.engine.store.settings();settings['maintenance_until']=time.time()+3600;self.engine.store.save_settings(settings,settings['revision'])
        result=await self.engine.check_cycle('daily')
        self.assertTrue(result['daily_checkpoint']['deferred']);self.engine.checkpoints.create.assert_not_awaited()

    async def test_corrupt_daily_export_reports_failure_without_fifteen_second_retry_loop(self):
        self.use_fake_probes();self.engine.inventory.collect=AsyncMock(return_value={'services':[]})
        self.engine.checkpoints.status=lambda:{'configured':True}
        self.engine.checkpoints.create=AsyncMock(side_effect=sqlite3.DatabaseError('file is not a database'))
        settings=self.engine.store.settings();settings['enabled']=True;self.engine.store.save_settings(settings,settings['revision'])
        now=datetime(2026,10,5,16,tzinfo=timezone.utc).timestamp()
        await self.engine.scheduler_step(now=now)
        await self.engine.scheduler_step(now=now+15)
        self.engine.checkpoints.create.assert_awaited_once()
        self.assertTrue(self.engine.store.one("SELECT 1 FROM incidents WHERE target='backups' AND code='checkpoint_failed' AND state='open'"))

    async def test_unknown_failed_service_is_reported_hourly_without_adoption_or_restart(self):
        fake=self.use_fake_probes();original=fake.run
        async def run(argv,**kwargs):
            if argv[:2]==['systemctl','--failed']:
                from maestro.process import Result
                return Result(0,'unknown-worker.service loaded failed failed Worker\n')
            return await original(argv,**kwargs)
        self.engine.runner.run=run
        before=self.engine.store.targets();result=await self.engine.check_cycle('hourly')
        self.assertEqual(result['other_failed_services'],['unknown-worker.service'])
        self.assertEqual(self.engine.store.targets(),before)
        self.assertFalse(any(c[:2]==['systemctl','restart'] for c in fake.calls))
