import ast
import asyncio
import copy
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import zipfile

import discord

from maestro import guides, suite
from maestro.adapters import verifier_config, raffle_config
from maestro.discord_guided import Context, GuidedView, Input
from maestro.notifications import incident_notifications
from maestro.security import Denied, read_env
from maestro.telegram_bot import TelegramMaestro
from .helpers import EngineCase, OWNER, OTHER
from .test_telegram_witness import LocalBotAPI, TELEGRAM, TELEGRAM_ID, BotAPI, TOKEN, update, callback


class GuidedTests(EngineCase):
    async def test_role_raffle_bridge_uses_selected_presets_and_reviewed_results(self):
        spec=self.engine.applications.spec('raffle')
        cfg=raffle_config.fresh()
        cfg.update(member_role=100,raffle_role=200,role_tickets={'100':1,'300':3,'400':10})
        c=sqlite3.connect(spec['database'])
        c.execute('INSERT OR REPLACE INTO settings VALUES(?,?,1)',(spec['guild'],json.dumps(cfg)));c.commit();c.close()
        draft=copy.deepcopy(cfg['templates']['general'])
        draft['prize']='A collectible';draft['rules']['any_roles']=[100,300]
        payload={'adapter':'raffle','revision':1,'action':'raffle_create','changes':{'category':'general',**draft}}
        plan=self.engine.applications.plan(payload)
        self.assertEqual(plan['raffle']['spec']['rules']['weights'],{'100':1,'300':3})
        self.assertEqual(plan['raffle']['spec']['rules']['weight_mode'],'tickets')
        result=await self.engine.applications.apply(plan,TELEGRAM)
        c=sqlite3.connect(spec['database'])
        mode=c.execute('SELECT result_mode FROM raffles WHERE id=?',(result['raffle_id'],)).fetchone()[0];c.close()
        self.assertEqual(mode,'review')

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.engine.policy.owners.add(TELEGRAM)
        self.engine.policy.groups=[[OWNER,TELEGRAM]]
        self.specs=[]
        source=self.folder/'source'
        with zipfile.ZipFile(Path(__file__).parent/'fixtures/suite-contract.zip') as z:z.extractall(source)
        for key,config in [('gate',suite.gate_config),('crew',suite.crew_config),('raffle',raffle_config)]:
            root=source/key
            spec={'id':key,'target':key,'kind':key+'-v1','guild':123456789,
                  'install':str(root),'database':str(self.folder/(key+'.sqlite3')),
                  'env_file':str(self.folder/(key+'.env')),'coordination_path':str(self.folder/'coordination.sqlite3')}
            text=(root/('db.py' if key=='gate' else 'ripcars_'+key+'/storage.py')).read_text()
            tree=ast.parse(text)
            schema=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SCHEMA' for t in n.targets))
            c=sqlite3.connect(spec['database']);c.executescript(schema);c.close()
            Path(spec['env_file']).write_text('DISCORD_TOKEN=fixture-token-for-own-bot\nGUILD_ID=123456789\n');Path(spec['env_file']).chmod(0o600)
            self.specs.append(spec)
        root=source/'verifier';root.mkdir()
        with zipfile.ZipFile(Path(__file__).parent/'fixtures/verifier-1.1.0-contract.zip') as z:z.extractall(root)
        spec={'id':'verifier','target':'verifier','kind':'verifier-v1','guild':123456789,'install':str(root),
              'database':str(self.folder/'verifier.sqlite3'),'env_file':str(self.folder/'verifier.env'),'coordination_path':str(self.folder/'coordination.sqlite3')}
        c=sqlite3.connect(spec['database']);c.executescript("CREATE TABLE meta(key TEXT,value TEXT);INSERT INTO meta VALUES('schema_version','1');CREATE TABLE settings(guild INTEGER PRIMARY KEY,revision INTEGER,value TEXT);CREATE TABLE ledger(guild INTEGER,event_id TEXT);CREATE TABLE jobs(guild INTEGER,next_run REAL);CREATE TABLE audit(id INTEGER PRIMARY KEY,guild INTEGER,actor INTEGER,action TEXT,detail TEXT,created REAL);");c.close()
        Path(spec['env_file']).write_text('DISCORD_TOKEN=fixture-token-for-own-bot\nGUILD_ID=123456789\n');Path(spec['env_file']).chmod(0o600)
        self.specs.append(spec)
        self.spec_file=self.folder/'applications.json';self.spec_file.write_text(json.dumps({'adapters':self.specs}));self.spec_file.chmod(0o600)
        self.engine.policy.data['applications_config']=str(self.spec_file)
        self.use_fake_probes();self.engine.applications.probes=self.engine.probes
        self.agent=SimpleNamespace(call=lambda action,**p:self.engine.dispatch(TELEGRAM,action,p),token='')
        self.server=LocalBotAPI();await self.server.start()
        self.api=BotAPI(TOKEN,self.server.url);await self.api.open()
        self.front=TelegramMaestro(TELEGRAM_ID,self.agent,self.api,self.folder/'telegram.json')

    async def asyncTearDown(self):
        await self.api.close();await self.server.close();await super().asyncTearDown()

    def message(self):
        return next(p for method,p in reversed(self.server.calls) if method in ('sendMessage','editMessageText'))

    def actions(self):
        return [b['callback_data'] for row in self.message().get('reply_markup',{}).get('inline_keyboard',[]) for b in row]

    async def confirm(self):
        key=next(reversed(self.front.state['reviews']))
        await self.front.handle(callback('confirm:'+key))
        jobs=await self.engine.dispatch(TELEGRAM,'jobs',{})
        task=self.engine.jobs.get(jobs[0]['id'])
        if task:await task
        return self.engine.job(TELEGRAM,jobs[0]['id'])

    async def test_gate_activation_cannot_bypass_native_setup_checks(self):
        current=self.engine.applications.read('gate')
        with self.assertRaisesRegex(Denied,'/gate panel'):
            self.engine.applications.plan({'adapter':'gate','revision':current['revision'],'changes':{'enabled':True}})
        self.assertFalse(self.engine.applications.read('gate')['settings']['enabled'])

    async def test_native_activation_guard_also_protects_direct_commit(self):
        from maestro.applications import connect
        spec=self.engine.applications.spec('gate');current=self.engine.applications.read('gate')
        cfg=copy.deepcopy(current['settings']);cfg['enabled']=True
        c=connect(spec['database'],False)
        try:
            with self.assertRaisesRegex(Denied,'/gate panel'):
                suite.commit(spec,c,cfg,current['revision'],TELEGRAM)
        finally:c.close()
        self.assertFalse(self.engine.applications.read('gate')['settings']['enabled'])

    async def test_guided_gate_activation_explains_native_health_check(self):
        await self.front.handle(callback('g:enable:gate'))
        self.assertIn('/gate panel',self.message()['text'])
        self.assertEqual(await self.engine.dispatch(TELEGRAM,'jobs',{}),[])

    async def test_crew_active_config_needs_native_check_but_pause_remains_available(self):
        c=sqlite3.connect(self.specs[1]['database'])
        cfg=suite.crew_config.defaults();cfg['guard']['enabled']=True;cfg['guard']['mode']='observe'
        c.execute('INSERT INTO crew_settings VALUES(?,?,?,?)',(123456789,1,json.dumps(cfg),0));c.commit();c.close()
        cfg['guard']['mode']='protect'
        with self.assertRaisesRegex(Denied,'/crew panel'):
            self.engine.applications.plan({'adapter':'crew','revision':1,'changes':{'guard':cfg['guard']}})
        cfg['guard']['mode']='observe';cfg['guard']['enabled']=False
        plan=self.engine.applications.plan({'adapter':'crew','revision':1,'changes':{'guard':cfg['guard']}})
        self.assertFalse(plan['settings']['guard']['enabled'])

    async def test_home_is_small_and_all_old_sections_remain_accessible(self):
        await self.front.handle(update('/start'))
        self.assertEqual(set(self.actions()),{'g:server','g:bots','g:help','jobs'})
        await self.front.handle(callback('g:advanced'))
        self.assertEqual(self.server.calls[-1][0],'editMessageText')
        for key in ('targets','inventory','terminal','files','recovery','access','setup','apps'):
            self.assertIn('section:'+key,self.actions())

    async def test_bot_cards_groups_and_input_help_never_require_json(self):
        await self.front.handle(callback('g:bots'))
        self.assertEqual(len([a for a in self.actions() if a.startswith('g:bot:')]),4)
        for key in guides.BOT_NAMES:
            await self.front.handle(callback('g:bot:'+key))
            self.assertIn('g:setup:'+key,self.actions())
            self.assertNotIn('"revision"',self.message()['text'])
            for group,_,_ in guides.GROUPS[key]:
                await self.front.handle(callback('g:group:'+key+':'+group))
                self.assertTrue(self.actions())
                for value in self.actions():self.assertLessEqual(len(value.encode()),64)

    async def test_guided_save_uses_review_and_leaves_other_bot_untouched(self):
        before=self.engine.applications.read('gate')['settings']
        await self.front.handle(callback('g:field:raffle:channel'))
        await self.front.handle(update('987654321'))
        self.assertEqual(self.engine.applications.read('raffle')['settings']['channel'],0)
        self.assertEqual(await self.engine.dispatch(TELEGRAM,'jobs',{}),[])
        job=await self.confirm()
        self.assertEqual(job['state'],'completed',job['output'])
        self.assertEqual(self.engine.applications.read('raffle')['settings']['channel'],987654321)
        self.assertEqual(self.engine.applications.read('gate')['settings'],before)

    async def test_nested_crew_setting_preserves_all_siblings_and_native_history(self):
        await self.front.handle(callback('g:field:crew:guard.allowed_domains'))
        await self.front.handle(update('ripcars.io\napp.ripcars.io\nexample.org'))
        job=await self.confirm();self.assertEqual(job['state'],'completed',job['output'])
        current=self.engine.applications.read('crew')['settings']
        self.assertIn('example.org',current['guard']['allowed_domains'])
        self.assertFalse(current['guard']['enabled']);self.assertEqual(len(current['guard']['filters']),15)
        await self.front.handle(callback('g:field:crew:guard.mention_limit'))
        await self.front.handle(update('8'));await self.confirm()
        c=sqlite3.connect(self.specs[1]['database'])
        self.assertEqual(c.execute('SELECT COUNT(*) FROM crew_history').fetchone()[0],1);c.close()

    async def test_invalid_input_keeps_form_open_and_revisions_prevent_overwrite(self):
        await self.front.handle(callback('g:field:raffle:channel'))
        with self.assertRaises(ValueError):await self.front.handle(update('not a number'))
        self.assertEqual(self.front.state['mode']['name'],'guided_field')
        c=sqlite3.connect(self.specs[2]['database'])
        cfg=raffle_config.fresh();cfg['channel']=123
        c.execute('INSERT INTO settings VALUES(?,?,?)',(123456789,json.dumps(cfg),1));c.commit();c.close()
        with self.assertRaises(Denied):await self.front.handle(update('999'))
        self.assertEqual(self.engine.applications.read('raffle')['settings']['channel'],123)

    async def test_rafﬂe_wizard_recovers_from_bad_duration_and_creates_only_a_draft(self):
        await self.front.handle(callback('g:raffle_new:raffle'))
        for text in ('Community gift','One Hot Wheels car','Shipping arranged by team'):
            await self.front.handle(update(text))
        with self.assertRaises(ValueError):await self.front.handle(update('yesterday'))
        self.assertEqual(self.front.state['mode']['step'],3)
        for text in ('24h','2','-'):await self.front.handle(update(text))
        job=await self.confirm();self.assertEqual(job['state'],'completed',job['output'])
        outcome=job['operation']['data']['application']
        self.assertEqual(outcome['status'],'draft')
        c=sqlite3.connect(self.specs[2]['database']);row=c.execute('SELECT status,message,spec FROM raffles').fetchone();c.close()
        self.assertEqual(row[:2],('draft',0));self.assertEqual(json.loads(row[2])['duration'],86400)
        await self.front.handle(callback('g:raffles:raffle'))
        self.assertIn('g:raffle_publish:raffle:'+str(outcome['raffle_id']),self.actions())

    async def test_notification_buttons_persist_at_broker_and_do_not_disable_monitoring(self):
        for key in guides.NOTICES:
            await self.front.handle(callback('g:notice:'+key))
            self.assertFalse(self.engine.store.settings()[key])
        self.assertFalse(self.engine.store.settings()['enabled'])
        self.assertTrue(self.engine.store.settings()['daily_checkpoint'])
        self.assertTrue(self.engine.policy.root_commands)
        self.assertFalse(self.engine.policy.backup_required)
        self.assertTrue(all(key in self.engine.store.settings() for key in guides.NOTICES))

    async def test_private_api_inputs_are_encrypted_in_pending_jobs_and_never_echoed(self):
        secret='private-platform-key-very-long-test-value'
        await self.front.handle(callback('g:bot:verifier'))
        await self.front.handle(callback('g:connection:PLATFORM_API_KEY'))
        await self.front.handle(update(secret))
        serialized=json.dumps(self.engine.store.rows('SELECT * FROM pending'))+self.front.path.read_text()
        self.assertNotIn(secret,serialized)
        self.assertNotIn(secret,json.dumps(self.server.calls))
        self.assertNotIn(secret,Path(self.specs[3]['env_file']).read_text())
        job=await self.confirm();self.assertEqual(job['state'],'completed',job['output'])
        self.assertEqual(read_env(self.specs[3]['env_file'])['PLATFORM_API_KEY'],secret)
        self.assertNotIn(secret,json.dumps(self.engine.export()))
        self.assertEqual(Path(self.specs[3]['env_file']).stat().st_mode&0o777,0o600)

    async def test_connection_rejects_unsupported_fields_injection_and_stale_files(self):
        for field,value in [('DISCORD_TOKEN','x'*50),('PUBLIC_URL','http://127.0.0.1'),('RPC_URL','https://rpc.example.org\nINJECTED=true')]:
            with self.assertRaises(ValueError):await self.engine.dispatch(TELEGRAM,'prepare_connection',{'adapter':'verifier','field':field,'value':value})
        review=await self.engine.dispatch(TELEGRAM,'prepare_connection',{'adapter':'verifier','field':'PLATFORM_API_KEY','value':'a-secret-long-enough-for-api-key'})
        with Path(self.specs[3]['env_file']).open('a') as f:f.write('OTHER=changed\n')
        with self.assertRaises(Denied):await self.engine.dispatch(TELEGRAM,'confirm',{k:review[k] for k in ('id','token','digest')})

    async def test_failed_checkpoint_blocks_connection_and_keeps_existing_environment(self):
        self.engine.policy.data['backup_required']=True
        self.engine.checkpoints.create=AsyncMock(side_effect=Denied('Backup repository integrity check failed.'))
        before=Path(self.specs[3]['env_file']).read_bytes()
        await self.front.handle(callback('g:bot:verifier'));await self.front.handle(callback('g:connection:PLATFORM_API_KEY'))
        await self.front.handle(update('a-private-key-long-enough-for-testing'))
        job=await self.confirm();self.assertEqual(job['state'],'failed')
        self.assertEqual(Path(self.specs[3]['env_file']).read_bytes(),before)

    async def test_reviewed_publication_rechecks_draft_revision_and_never_replays(self):
        c=sqlite3.connect(self.specs[2]['database']);cfg=raffle_config.fresh();cfg['enabled']=True
        c.execute('INSERT INTO settings VALUES(?,?,?)',(123456789,json.dumps(cfg),1))
        spec=copy.deepcopy(cfg['templates']['general']);spec['prize']='Car'
        rid=c.execute('INSERT INTO raffles(guild,actor,category,spec,created) VALUES(?,0,?,?,0)',(123456789,'general',json.dumps(spec))).lastrowid
        c.commit();c.close()
        current=self.engine.applications.read('raffle')
        review=await self.engine.dispatch(TELEGRAM,'prepare',{'kind':'application','payload':{'adapter':'raffle','action':'raffle_publish','revision':current['revision'],'changes':{'raffle_id':rid}}})
        with patch('maestro.suite.publish',AsyncMock(return_value={'state':'completed','raffle_id':rid,'verified':True})) as published:
            job=await self.engine.dispatch(TELEGRAM,'confirm',{k:review[k] for k in ('id','token','digest')})
            await self.engine.jobs[job['id']];self.assertEqual(self.engine.job(TELEGRAM,job['id'])['state'],'completed')
            with self.assertRaises(Denied):await self.engine.dispatch(TELEGRAM,'confirm',{k:review[k] for k in ('id','token','digest')})
            self.assertEqual(published.await_count,1)
        review=await self.engine.dispatch(TELEGRAM,'prepare',{'kind':'application','payload':{'adapter':'raffle','action':'raffle_publish','revision':current['revision'],'changes':{'raffle_id':rid}}})
        c=sqlite3.connect(self.specs[2]['database']);c.execute('UPDATE raffles SET revision=revision+1');c.commit();c.close()
        # Confirmation regenerates the current plan, while execution retains and checks the reviewed plan.
        job=await self.engine.dispatch(TELEGRAM,'confirm',{k:review[k] for k in ('id','token','digest')})
        await self.engine.jobs[job['id']]
        self.assertEqual(self.engine.job(TELEGRAM,job['id'])['state'],'failed')

    async def test_source_drift_blocks_app_writes(self):
        source=Path(self.specs[0]['install'])/'core.py';source.write_text(source.read_text()+'\n# changed\n')
        with self.assertRaises(Denied):self.engine.applications.read('gate')

    async def test_native_waffle_publication_ownership_and_ambiguous_delivery(self):
        process=await asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).parent/'native_publish_case.py'),self.specs[2]['install'],
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        out,err=await asyncio.wait_for(process.communicate(),30)
        self.assertEqual(process.returncode,0,err.decode())
        self.assertEqual(json.loads(out)['native_publication'],'passed')

    async def test_large_rule_list_has_pages_and_toggle_does_not_remove_other_rules(self):
        cfg=verifier_config.defaults()
        cfg['rules']=[{'key':'rule_'+str(i),'name':'Role '+str(i),'role_id':0,'kind':'asset_count','threshold':'1','selector':{},'enabled':False} for i in range(30)]
        c=sqlite3.connect(self.specs[3]['database']);c.execute('INSERT INTO settings VALUES(?,1,?)',(123456789,json.dumps(cfg)));c.commit();c.close()
        await self.front.handle(callback('g:list:verifier:rules:2'))
        self.assertIn('g:list_edit:verifier:rules:29',self.actions())
        self.assertNotIn('g:list_edit:verifier:rules:0',self.actions())
        await self.front.handle(callback('g:list_edit:verifier:rules:29'))
        self.assertEqual(self.front.state['mode']['index'],29)
        await self.front.handle(update('Role renamed'))
        await self.front.handle(callback('g:answer:asset_count'))
        await self.front.handle(update('2'))
        # The adapter's live Discord preflight is independently tested by the HTTP fixtures.
        with patch.object(self.engine.applications,'preflight',AsyncMock(return_value={'ok':True})):
            await self.front.handle(update('0'))
            review=next(iter(self.front.state['reviews'].values()))
            row=self.engine.store.one('SELECT payload FROM pending WHERE id=?',(review['id'],))
            rules=json.loads(row['payload'])['changes']['rules']
            self.assertEqual(len(rules),30);self.assertEqual(rules[29]['key'],'rule_29')
            self.assertEqual(rules[29]['name'],'Role renamed');self.assertFalse(rules[29]['enabled'])

    async def test_notification_mute_does_not_replay_old_events_after_reenable(self):
        self.front.state['started']=True
        self.engine.incident('host','resource_pressure','Disk low')
        await self.front.handle(callback('g:notice:notify_telegram'))
        count=len(self.server.calls)
        await self.front.deliver_once()
        self.assertEqual(len(self.server.calls),count)
        self.assertFalse(self.engine.store.events_for(TELEGRAM))
        await self.front.handle(callback('g:notice:notify_telegram'))
        await self.front.deliver_once()
        alerts=[p for m,p in self.server.calls if m=='sendMessage' and 'ALERT' in p.get('text','')]
        self.assertEqual(len(alerts),1)
        await self.front.deliver_once()
        self.assertEqual(len([p for m,p in self.server.calls if m=='sendMessage' and 'ALERT' in p.get('text','')]),1)

    async def test_guided_navigation_does_not_edit_automatic_alerts_into_the_panel(self):
        self.front.state['started']=True
        self.engine.incident('gate','unit_down','failed')
        await self.front.handle(callback('g:server'))
        self.assertIsNone(self.front.panel_message.get())
        await self.front.deliver_once()
        self.assertEqual(self.server.calls[-1][0],'sendMessage')

    async def test_connection_fields_report_required_values_only_for_enabled_sources(self):
        rows=await self.engine.dispatch(TELEGRAM,'connections',{'adapter':'verifier'})
        self.assertEqual([r['field'] for r in rows if r['required']],[])
        cfg=verifier_config.defaults();cfg.update(asset_mode='das',platform_points=True)
        c=sqlite3.connect(self.specs[3]['database']);c.execute('INSERT INTO settings VALUES(?,1,?)',(123456789,json.dumps(cfg)));c.commit();c.close()
        rows=await self.engine.dispatch(TELEGRAM,'connections',{'adapter':'verifier'})
        required={r['field'] for r in rows if r['required']}
        self.assertEqual(required,{'DAS_URL','PLATFORM_API_URL','PLATFORM_API_KEY'})
        cfg['wallet_linking']=True
        c=sqlite3.connect(self.specs[3]['database']);c.execute('UPDATE settings SET value=? WHERE guild=?',(json.dumps(cfg),123456789));c.commit();c.close()
        rows=await self.engine.dispatch(TELEGRAM,'connections',{'adapter':'verifier'})
        self.assertTrue(next(r['required'] for r in rows if r['field']=='PUBLIC_URL'))

    async def test_api_inputs_never_leak_after_frontend_restart_or_failed_job(self):
        value='private-api-key-for-persistent-review'
        prepared=await self.engine.dispatch(TELEGRAM,'prepare_connection',{'adapter':'verifier','field':'PLATFORM_API_KEY','value':value})
        from maestro.security import Redactor
        self.engine.redactor=Redactor()
        self.engine.connections.redactor=self.engine.redactor
        self.engine.connections.validate(json.loads(self.engine.store.one('SELECT payload FROM pending WHERE id=?',(prepared['id'],))['payload']))
        self.assertNotIn(value,self.engine.redactor.clean('error '+value))

    async def test_delegates_cannot_open_setup_or_change_connections(self):
        delegate='telegram:9988776655'
        self.engine.access.grant(OWNER,{'actor':delegate,'role':'operator','targets':['raffle']})
        for action,params in [('connections',{'adapter':'verifier'}),('applications',{}),('prepare_connection',{'adapter':'verifier','field':'PUBLIC_URL','value':'https://verify.example.org'})]:
            with self.assertRaises(Denied):await self.engine.dispatch(delegate,action,params)

    async def test_actual_discord_sdk_views_modals_and_every_home_action(self):
        async def call(action,**params):return await self.engine.dispatch(OWNER,action,params)
        bot=SimpleNamespace(api=SimpleNamespace(call=call),owner_id=123456789,guild_id=123456789)
        i=SimpleNamespace(user=SimpleNamespace(id=123456789),response=SimpleNamespace(is_done=lambda:True),followup=SimpleNamespace(send=AsyncMock()))
        ctx=Context(bot,i)
        for action in ('g:home','g:server','g:bots','g:notifications','g:backup','g:advanced','g:setup:verifier','g:bot:raffle','g:group:crew:texts'):
            await ctx.route(action)
            kwargs=i.followup.send.call_args.kwargs
            self.assertIsInstance(kwargs['view'],GuidedView)
            self.assertLessEqual(len(kwargs['view'].children),25)
            for item in kwargs['view'].children:
                if isinstance(item,discord.ui.Select):self.assertLessEqual(len(item.options),25)
        await ctx.route('g:field:raffle:channel')
        modal=Input(ctx)
        self.assertLessEqual(len(modal.children),5)

    async def test_scheduler_contention_is_deferred_without_false_incident(self):
        settings=self.engine.store.settings();self.engine.store.save_settings({**settings,'enabled':True},settings['revision'])
        self.engine.store.put('last_discovery',10**12)
        async with self.engine.monitor_lock:await self.engine.scheduler_step()
        self.assertIsNone(self.engine.store.one("SELECT * FROM incidents WHERE code='scheduler'"))

    async def test_database_severity_escalation_is_delivered_once(self):
        history={}
        def cycle(detail):
            row={'key':'maestro:database_failed','target':'maestro','code':'database_failed','detail':detail}
            plans=incident_notifications({'open_incidents':[row]},history,10000)
            for _,key,value in plans:history[key]=value
            return plans
        self.assertEqual(len(cycle('OperationalError: unable to open database file')),1)
        self.assertEqual(len(cycle('DatabaseError: database disk image is malformed')),1)
        self.assertEqual(cycle('DatabaseError: database disk image is malformed'),[])
