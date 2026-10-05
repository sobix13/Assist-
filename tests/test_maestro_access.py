import asyncio
import time
from unittest.mock import AsyncMock

from maestro.discovery import reconcile, target_id
from maestro.ipc import ActorClient
from maestro.security import Denied
from .helpers import EngineCase, OWNER, TOKEN
from . import test_telegram_witness as telegram_fixtures
from maestro.telegram_bot import TelegramMaestro

DELEGATE='telegram:999888777'
callback=telegram_fixtures.callback
update=telegram_fixtures.update
TELEGRAM_ID=telegram_fixtures.TELEGRAM_ID


class AccessTests(EngineCase):
    def grant(self, role='observer', targets=None):
        return self.engine.access.grant(OWNER,{'actor':DELEGATE,'role':role,'targets':targets or ['gate'],'seconds':3600})

    async def test_unknown_identity_is_rejected_by_broker_and_peer_policy(self):
        with self.assertRaises(Denied):await self.engine.dispatch(DELEGATE,'status',{})
        with self.assertRaises(Denied):self.engine.policy.authenticate(0,TOKEN,DELEGATE)

    async def test_observer_gets_only_assigned_services_and_no_host_state(self):
        self.grant();who=await self.engine.dispatch(DELEGATE,'whoami',{})
        self.assertEqual(who['role'],'observer')
        self.assertEqual([x['id'] for x in await self.engine.dispatch(DELEGATE,'targets',{})],['gate'])
        result=await self.engine.dispatch(DELEGATE,'status',{})
        self.assertNotIn('operations',result);self.assertNotIn('checkpoint',result)

    async def test_observer_cannot_grant_export_execute_or_read_other_service(self):
        self.grant()
        for action,params in [('access_grant',{}),('export',{}),('backup',{}),('package_part',{}),('inventory',{}),('logs',{'target':'crew'}),('check',{'kind':'daily'})]:
            with self.subTest(action=action),self.assertRaises(Denied):await self.engine.dispatch(DELEGATE,action,params)
        with self.assertRaises(Denied):self.engine.prepare(DELEGATE,{'kind':'shell','payload':{'profile':'root','code':'true'}})

    async def test_operator_runs_scoped_service_action_but_never_shell(self):
        self.grant('operator');fake=self.use_fake_probes()
        review=await self.engine.dispatch(DELEGATE,'prepare',{'kind':'service','payload':{'unit':'ripcars-gate.service','action':'restart'}})
        result=await self.engine.dispatch(DELEGATE,'confirm',{k:review[k] for k in ('id','token','digest')})
        await self.engine.jobs[result['id']]
        self.assertEqual(self.engine.job(DELEGATE,result['id'])['state'],'completed')
        self.assertIn(['systemctl','restart','ripcars-gate.service'],fake.calls)
        with self.assertRaises(Denied):self.engine.prepare(DELEGATE,{'kind':'service','payload':{'unit':'ripcars-crew.service','action':'restart'}})
        with self.assertRaises(Denied):self.engine.prepare(DELEGATE,{'kind':'file','payload':{}})

    async def test_revocation_invalidates_review_immediately(self):
        self.grant('operator')
        review=self.engine.prepare(DELEGATE,{'kind':'service','payload':{'unit':'ripcars-gate.service','action':'restart'}})
        await self.engine.dispatch(OWNER,'access_revoke',{'actor':DELEGATE})
        with self.assertRaises(Denied):await self.engine.dispatch(DELEGATE,'confirm',{k:review[k] for k in ('id','token','digest')})
        self.assertTrue(self.engine.store.one('SELECT used FROM pending WHERE id=?',(review['id'],))['used'])

    async def test_expired_access_is_rejected_and_superadmin_is_not_revocable(self):
        self.grant();self.engine.store.run('UPDATE delegates SET expires=?',(time.time()-1,))
        with self.assertRaises(Denied):await self.engine.dispatch(DELEGATE,'whoami',{})
        with self.assertRaises(Denied):self.engine.access.revoke(OWNER,OWNER)
        with self.assertRaises(Denied):self.engine.access.grant(OWNER,{'actor':OWNER})

    async def test_grant_revision_and_forged_role_are_rejected(self):
        self.grant()
        with self.assertRaises(Denied):self.grant('operator')
        with self.assertRaises(Denied):self.engine.access.grant(OWNER,{'actor':'telegram:111222333','role':'superadmin','targets':['gate'],'seconds':3600})

    async def test_discovered_cards_are_stable_preserve_admin_changes_and_do_not_control_services(self):
        report={'services':[{'unit':'new.worker.service','load':'loaded','active':'active'}], 'containers':[{'Names':'new-worker','Status':'Up 1 hour'}]}
        ids=reconcile(self.engine.store,report);self.assertEqual(len(ids),2)
        self.assertNotEqual(ids[0],ids[1]);self.assertEqual(len(ids[0]),len(target_id('systemd','new.worker.service')))
        target=self.engine.store.target(ids[0]);self.assertFalse(target['enabled']);self.assertFalse(target['auto_restart']);self.assertEqual(target['recovery_owner'],'external')
        target['enabled']=True;self.engine.store.save_target(target)
        self.assertEqual(reconcile(self.engine.store,report),[]);self.assertTrue(self.engine.store.target(ids[0])['enabled'])

    async def test_discovery_runs_while_monitoring_is_paused(self):
        self.engine.inventory.collect=AsyncMock(return_value={'at':time.time(),'services':[{'unit':'brand-new.service','load':'loaded','active':'active'}]})
        await self.engine.scheduler_step(time.time())
        self.assertTrue(any(t['unit']=='brand-new.service' for t in self.engine.store.targets()))
        self.assertFalse(self.engine.store.settings()['enabled'])

    async def test_two_discord_tasks_do_not_share_actor_context(self):
        calls=[]
        class FakeClient:
            actor=OWNER;path='unused';token=TOKEN;tls=None
        proxy=ActorClient(FakeClient())
        async def run(actor):
            proxy.bind(actor);await asyncio.sleep(0);calls.append(proxy.actor_context.get())
        await asyncio.gather(run(OWNER),run('discord:999888777'))
        self.assertEqual(set(calls),{OWNER,'discord:999888777'});self.assertEqual(proxy.actor_context.get(),OWNER)


class PersonalTelegramTests(EngineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        identity='telegram:'+str(TELEGRAM_ID)
        self.engine.policy.owners.add(identity)
        self.engine.policy.groups=[[OWNER,identity]]
        engine=self.engine
        self.agent=type('Agent',(),{'token':'test','call':lambda _,action,**p:engine.dispatch(identity,action,p)})()
        self.server=telegram_fixtures.LocalBotAPI();await self.server.start()
        self.api=telegram_fixtures.BotAPI(telegram_fixtures.TOKEN,self.server.url);await self.api.open()
        self.front=TelegramMaestro(TELEGRAM_ID,self.agent,self.api,self.folder/'telegram/state.json')

    async def asyncTearDown(self):
        await self.api.close();await self.server.close();await super().asyncTearDown()
    async def test_wizard_registers_owner_and_finishes_without_enabling_automatic_recovery(self):
        self.front.agent.call= lambda action,**p: self.engine.dispatch('telegram:'+str(TELEGRAM_ID),action,p)
        await self.front.handle(update('/start'))
        await self.front.handle(callback('wizard:identity'))
        await self.front.handle(callback('wizard:finish',key=3))
        self.assertTrue(self.engine.store.settings()['enabled']);self.assertFalse(self.engine.store.settings()['auto_recovery'])
        self.assertTrue(self.front.state['wizard_complete'])

    async def test_delegate_session_has_separate_state_and_no_terminal_button(self):
        self.engine.access.grant(OWNER,{'actor':DELEGATE,'role':'observer','targets':['gate'],'seconds':3600})
        engine=self.engine
        agent=type('Delegate',(),{'token':'test','call':lambda _,action,**p:engine.dispatch(DELEGATE,action,p)})()
        child=TelegramMaestro(999888777,agent,self.api,self.folder/'delegate/state.json')
        self.front.sessions[999888777]=child
        await self.front.handle(update('/start',owner=999888777))
        messages=[p for method,p in self.server.calls if method=='sendMessage' and p['chat_id']==999888777]
        self.assertTrue(messages)
        buttons=[b['callback_data'] for row in messages[-1]['reply_markup']['inline_keyboard'] for b in row]
        self.assertNotIn('section:terminal',buttons);self.assertIn('targets:0',buttons)
        self.assertIsNone(self.front.state['mode'])

    async def test_revoked_delegate_cached_session_still_fails_before_action(self):
        self.engine.access.grant(OWNER,{'actor':DELEGATE,'role':'operator','targets':['gate'],'seconds':3600})
        engine=self.engine
        child=TelegramMaestro(999888777,type('Delegate',(),{'token':'test','call':lambda _,action,**p:engine.dispatch(DELEGATE,action,p)})(),self.api,self.folder/'delegate/state.json')
        self.front.sessions[999888777]=child
        self.engine.access.revoke(OWNER,DELEGATE)
        await self.front.handle(update('/exec root touch /tmp/never',owner=999888777))
        self.assertEqual(self.engine.store.rows('SELECT * FROM pending'),[])
