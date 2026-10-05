import json
import sqlite3
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

from aiohttp import web
import ripcars_coordination as protocol

from maestro.adapters import verifier_config
from maestro.security import Denied
from maestro.process import Result
from maestro.verifier_bridge import DiscordREST, VIEW, SEND, EMBED, ATTACH, HISTORY
from .helpers import EngineCase, OWNER

GUILD=4455667788
BOT='9996663333'
MEMBER=4455667790


class DiscordFixture:
    def __init__(self):
        self.calls=[];self.channels=[];self.messages={};self.next=8800000000;self.fail_create=False
        self.roles=[{'id':str(GUILD),'name':'@everyone','permissions':'0','position':0},
                    {'id':str(MEMBER),'name':'Ripper','permissions':'0','position':1},
                    {'id':'5566770001','name':'Verifier','permissions':str((1<<4)|(1<<28)|VIEW|SEND|EMBED|ATTACH|HISTORY),'position':10,'managed':True}]

    async def start(self):
        app=web.Application();app.router.add_route('*','/api/v10/{path:.*}',self.handle)
        self.runner=web.AppRunner(app);await self.runner.setup();site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
        self.base='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/api/v10'

    async def handle(self,request):
        path='/'+request.match_info['path'];method=request.method
        payload=await request.json() if request.can_read_body else None
        self.calls.append((method,path,payload))
        if not request.headers.get('Authorization','').startswith('Bot '):return web.json_response({},status=401)
        if method=='GET':
            if path=='/users/@me':value={'id':BOT,'bot':True}
            elif path.endswith('/roles'):value=self.roles
            elif path.endswith('/channels'):value=self.channels
            elif '/members/' in path:value={'user':{'id':BOT},'roles':['5566770001']}
            elif '/messages/' in path:value=self.messages[path.rsplit('/',1)[1]]
            else:raise web.HTTPNotFound()
        elif method=='POST':
            if self.fail_create and path.endswith('/channels'):return web.json_response({'message':'uncertain fixture'},status=500)
            self.next+=1;rid=str(self.next)
            if path.endswith('/roles'):
                value={**payload,'id':rid,'position':1};self.roles.append(value)
            elif path.endswith('/channels'):
                value={**payload,'id':rid};self.channels.append(value)
            elif path.endswith('/messages'):
                value={**payload,'id':rid,'author':{'id':BOT}};self.messages[rid]=value
            else:raise web.HTTPNotFound()
        elif method=='PATCH':
            rid=path.rsplit('/',1)[1];value={**self.messages[rid],**payload};self.messages[rid]=value
        else:raise web.HTTPMethodNotAllowed(method,['GET','POST','PATCH'])
        return web.json_response(value)

    async def close(self):await self.runner.cleanup()


class ApplicationTests(EngineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.use_fake_probes();self.engine.applications.probes=self.engine.probes
        self.source=self.folder/'verifier';self.source.mkdir()
        with zipfile.ZipFile(Path(__file__).parent/'fixtures/verifier-1.0.1-contract.zip') as z:z.extractall(self.source)
        self.db=self.folder/'verifier.sqlite3'
        c=sqlite3.connect(self.db)
        c.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT); INSERT INTO meta VALUES('schema_version','1'); CREATE TABLE settings(guild INTEGER PRIMARY KEY,revision INTEGER,value TEXT); CREATE TABLE ledger(guild INTEGER,event_id TEXT); CREATE TABLE jobs(guild INTEGER,next_run REAL); CREATE TABLE audit(id INTEGER PRIMARY KEY,guild INTEGER,actor INTEGER,action TEXT,detail TEXT,created REAL);")
        self.cfg=verifier_config.defaults();self.cfg['member_role']=MEMBER
        self.cfg['rules']=[{'key':'points','name':'Common Ripper','role_id':0,'kind':'deposit_points','threshold':'1000','selector':{},'enabled':True}]
        c.execute('INSERT INTO settings VALUES(?,1,?)',(GUILD,json.dumps(self.cfg)));c.commit();c.close()
        self.registry=self.folder/'coordination.sqlite3';c=sqlite3.connect(self.registry)
        c.execute('CREATE TABLE resources(guild INTEGER,key TEXT,object_id INTEGER,kind TEXT,owner TEXT,baseline TEXT,desired TEXT,state TEXT,PRIMARY KEY(guild,key))');protocol.initialize(c);c.commit();c.close()
        self.env=self.folder/'verifier.env';self.env.write_text('DISCORD_TOKEN=test-verifier-token-with-more-than-forty-characters\nCOORDINATION_PATH='+str(self.registry)+'\n');self.env.chmod(0o600)
        self.spec={'id':'verifier','kind':'verifier-v1','target':'verifier','install':str(self.source),'database':str(self.db),'env_file':str(self.env),'coordination_path':str(self.registry),'guild':GUILD}
        self.config=self.folder/'applications.json';self.write_specs([self.spec]);self.engine.policy.data['applications_config']=str(self.config)
        self.discord=DiscordFixture();await self.discord.start()
        self.engine.applications.discord_factory=lambda token:DiscordREST(token,self.discord.base)

    def write_specs(self,values):self.config.write_text(json.dumps({'adapters':values}));self.config.chmod(0o600)

    async def asyncTearDown(self):await self.discord.close();await super().asyncTearDown()

    async def request(self,action='settings',changes=None):
        current=await self.engine.dispatch(OWNER,'application',{'id':'verifier'})
        return await self.engine.dispatch(OWNER,'prepare',{'kind':'application','payload':{'adapter':'verifier','action':action,'revision':current['revision'],'changes':changes or {}}})

    async def run_request(self,review):
        job=await self.engine.dispatch(OWNER,'confirm',{k:review[k] for k in ('id','token','digest')})
        await self.engine.jobs[job['id']];return self.engine.job(OWNER,job['id'])

    async def test_settings_validate_live_discord_before_review_then_commit_with_audit(self):
        review=await self.request(changes={'sync_seconds':7200})
        self.assertTrue(self.discord.calls);self.assertTrue(all(x[0]=='GET' for x in self.discord.calls))
        self.assertEqual(self.engine.applications.read('verifier')['settings']['sync_seconds'],86400)
        job=await self.run_request(review);self.assertEqual(job['state'],'completed')
        self.assertEqual(self.engine.applications.read('verifier')['settings']['sync_seconds'],7200)
        c=sqlite3.connect(self.db);self.assertEqual(c.execute('SELECT action FROM audit').fetchone()[0],'maestro_settings');c.close()

    async def test_invalid_values_and_schema_upgrade_are_blocked_without_writes(self):
        with self.assertRaises(ValueError):await self.request(changes={'sync_seconds':1})
        c=sqlite3.connect(self.db);c.execute("UPDATE meta SET value='2'");c.commit();c.close()
        with self.assertRaises(Denied):await self.request(changes={'sync_seconds':7200})

    async def test_source_contract_drift_is_blocked_before_any_http_mutation(self):
        (self.source/'ripcars_verifier/member_ui.py').write_text('changed')
        with self.assertRaises(Denied):await self.request('provision')
        self.assertEqual(self.discord.calls,[])

    async def test_concurrent_revision_is_not_overwritten(self):
        review=await self.request(changes={'sync_seconds':7200})
        c=sqlite3.connect(self.db);c.execute('UPDATE settings SET revision=revision+1');c.commit();c.close()
        with self.assertRaises(Denied):await self.run_request(review)
        self.assertEqual(self.engine.applications.read('verifier')['settings']['sync_seconds'],86400)

    async def test_live_discord_missing_permissions_blocks_provision(self):
        self.discord.roles[-1]['permissions']='0'
        with self.assertRaises(Denied):await self.request('provision')
        self.assertFalse(any(x[0]=='POST' for x in self.discord.calls))

    async def test_provision_creates_only_owned_channels_roles_and_persistent_panel(self):
        job=await self.run_request(await self.request('provision'));self.assertEqual(job['state'],'completed',job['output'])
        cfg=self.engine.applications.read('verifier')['settings'];self.assertTrue(cfg['public_message']);self.assertTrue(cfg['rules'][0]['role_id'])
        self.assertEqual(len(self.discord.channels),2)
        public=next(c for c in self.discord.channels if int(c['id'])==cfg['verification_channel'])
        private=next(c for c in self.discord.channels if int(c['id'])==cfg['log_channel'])
        self.assertTrue(any(x['id']==str(MEMBER) and int(x['allow'])&HISTORY for x in public['permission_overwrites']))
        self.assertTrue(any(x['id']==str(MEMBER) and int(x['deny'])&VIEW for x in private['permission_overwrites']))
        message=self.discord.messages[str(cfg['public_message'])]
        self.assertEqual(message['components'][0]['components'][0]['custom_id'],'rcv:wallet:v1')
        self.assertEqual({x['owner'] for x in protocol.inspect(self.registry,GUILD)['resources']},{'bot:'+BOT})
        self.assertFalse(protocol.inspect(self.registry,GUILD)['leases'])

    async def test_repeated_provision_does_not_duplicate_discord_resources(self):
        await self.run_request(await self.request('provision'));before=(len(self.discord.channels),len(self.discord.roles),len(self.discord.messages))
        job=await self.run_request(await self.request('provision'));self.assertEqual(job['state'],'completed',job['output'])
        self.assertEqual(before,(len(self.discord.channels),len(self.discord.roles),len(self.discord.messages)))

    async def test_administrator_role_edit_is_preserved_and_blocks_conflicting_setup(self):
        await self.run_request(await self.request('provision'))
        role=self.discord.roles[-1];role['name']='Administrator choice'
        before=len(self.discord.calls)
        with self.assertRaises(Denied):await self.request('provision')
        self.assertEqual(role['name'],'Administrator choice')
        self.assertFalse(any(method!='GET' for method,_,_ in self.discord.calls[before:]))

    async def test_existing_names_are_not_adopted_implicitly(self):
        self.discord.channels.append({'id':'9900000000','name':'verifier-connect','type':0,'permission_overwrites':[]})
        with self.assertRaises(Denied):await self.request('provision')
        self.assertFalse(any(x[0]=='POST' for x in self.discord.calls))

    async def test_foreign_setup_lease_blocks_mutations_and_is_preserved(self):
        c=sqlite3.connect(self.registry);token=protocol.claim(c,GUILD);c.commit();c.close()
        job=await self.run_request(await self.request('provision'))
        self.assertEqual(job['state'],'partial');self.assertFalse(any(x[0]=='POST' for x in self.discord.calls))
        c=sqlite3.connect(self.registry);self.assertEqual(c.execute('SELECT token FROM leases').fetchone()[0],token);c.close()

    async def test_uncertain_discord_mutation_is_not_replayed(self):
        self.discord.fail_create=True
        job=await self.run_request(await self.request('provision'));self.assertEqual(job['state'],'partial')
        self.assertEqual(len([x for x in self.discord.calls if x[0]=='POST']),1)
        self.assertFalse(protocol.inspect(self.registry,GUILD)['leases'])

    async def test_deposit_mode_switch_preserves_ledger_and_is_blocked(self):
        c=sqlite3.connect(self.db);c.execute("INSERT INTO ledger VALUES(?,'solana:test')",(GUILD,));c.commit();c.close()
        with self.assertRaises(Denied):await self.request(changes={'deposit_mode':'platform'})
        c=sqlite3.connect(self.db);self.assertEqual(c.execute('SELECT COUNT(*) FROM ledger').fetchone()[0],1);c.close()

    async def test_failed_settings_postcheck_restores_settings_without_replacing_database(self):
        self.engine.applications.probes.service=AsyncMock(side_effect=[{'ok':False},{'ok':True}])
        job=await self.run_request(await self.request(changes={'sync_seconds':7200}))
        self.assertEqual(job['state'],'rolled_back');self.assertEqual(self.engine.applications.read('verifier')['settings']['sync_seconds'],86400)

    async def test_postcheck_exception_restores_verifier_settings_and_rechecks_health(self):
        self.engine.applications.probes.service=AsyncMock(side_effect=[RuntimeError('lost probe'),{'ok':True}])
        job=await self.run_request(await self.request(changes={'sync_seconds':7200}))
        self.assertEqual(job['state'],'rolled_back')
        self.assertEqual(self.engine.applications.read('verifier')['settings']['sync_seconds'],86400)

    async def test_json_reload_failure_restores_and_reloads_previous_settings(self):
        path=self.folder/'config.json';path.write_text('{"hours":24}')
        spec={'id':'json','kind':'jsonfile','target':'gate','path':str(path),'fields':{'hours':{'type':'int'}},'reload_argv':['/usr/bin/true']}
        self.write_specs([spec]);current=self.engine.applications.read('json')
        self.engine.applications.runner.run=AsyncMock(side_effect=[Result(1,'reload failed'),Result(0,'restored')])
        review=await self.engine.dispatch(OWNER,'prepare',{'kind':'application','payload':{'adapter':'json','revision':current['revision'],'changes':{'hours':12}}})
        job=await self.run_request(review)
        self.assertEqual(job['state'],'rolled_back',job['output'])
        self.assertEqual(json.loads(path.read_text()),{'hours':24})
        self.assertEqual(self.engine.applications.runner.run.await_count,2)

    async def test_failed_rollback_reload_is_reported_as_unresolved(self):
        path=self.folder/'config.json';path.write_text('{"hours":24}')
        self.write_specs([{'id':'json','kind':'jsonfile','target':'gate','path':str(path),'fields':{'hours':{'type':'int'}},'reload_argv':['/usr/bin/false']}])
        current=self.engine.applications.read('json')
        self.engine.applications.runner.run=AsyncMock(return_value=Result(1,'failed'))
        review=await self.engine.dispatch(OWNER,'prepare',{'kind':'application','payload':{'adapter':'json','revision':current['revision'],'changes':{'hours':12}}})
        job=await self.run_request(review)
        self.assertEqual(job['state'],'rollback_failed');self.assertEqual(json.loads(path.read_text()),{'hours':24})

    async def test_unsupported_activation_is_not_bypassed_through_database_write(self):
        with self.assertRaises(Denied):await self.request(changes={'enabled':True})

    async def test_telegram_backend_activation_requires_bound_resources_and_live_worker_health(self):
        await self.run_request(await self.request('provision'))
        self.env.write_text(self.env.read_text()+'PUBLIC_URL=https://verify.example.com\n')
        cfg=self.engine.applications.read('verifier')['settings'];cfg['rules'][0]['enabled']=False
        c=sqlite3.connect(self.db);c.execute('UPDATE settings SET value=?,revision=revision+1',(json.dumps(cfg),));c.commit();c.close()
        self.engine.applications.probes.endpoint=AsyncMock(return_value={'ok':False})
        with self.assertRaises(Denied):await self.request(changes={'enabled':True})
        self.engine.applications.probes.endpoint=AsyncMock(return_value={'ok':True})
        job=await self.run_request(await self.request(changes={'enabled':True}));self.assertEqual(job['state'],'completed',job['output'])
        self.assertTrue(self.engine.applications.read('verifier')['settings']['enabled'])

    async def test_jsonfile_contract_validates_values_and_preserves_file_permissions(self):
        path=self.folder/'config.json';path.write_text('{"enabled":false,"hours":24}');path.chmod(0o640)
        spec={'id':'other-app','kind':'jsonfile','target':'gate','path':str(path),'fields':{'hours':{'type':'int','min':1,'max':48}}}
        self.write_specs([self.spec,spec])
        current=self.engine.applications.read('other-app')
        review=await self.engine.dispatch(OWNER,'prepare',{'kind':'application','payload':{'adapter':'other-app','action':'settings','revision':current['revision'],'changes':{'hours':12}}})
        job=await self.run_request(review);self.assertEqual(job['state'],'completed');self.assertEqual(json.loads(path.read_text())['hours'],12)
        self.assertEqual(path.stat().st_mode&0o777,0o640)
        with self.assertRaises(Denied):self.engine.applications.plan({'adapter':'other-app','revision':self.engine.applications.read('other-app')['revision'],'changes':{'hours':True}})

    async def test_jsonfile_concurrent_edit_is_preserved(self):
        path=self.folder/'config.json';path.write_text('{"hours":24}')
        self.write_specs([{'id':'json','kind':'jsonfile','target':'gate','path':str(path),'fields':{'hours':{'type':'int'}}}])
        current=self.engine.applications.read('json')
        review=await self.engine.dispatch(OWNER,'prepare',{'kind':'application','payload':{'adapter':'json','revision':current['revision'],'changes':{'hours':12}}})
        path.write_text('{"hours":6}')
        with self.assertRaises(Denied):await self.run_request(review)
        self.assertEqual(json.loads(path.read_text())['hours'],6)
