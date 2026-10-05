import asyncio
from unittest.mock import AsyncMock

from aiohttp import web

from maestro.external_watcher import Witness, reachable
from maestro.security import Denied
from maestro.telegram_bot import BotAPI, TelegramMaestro, TelegramError
from .helpers import EngineCase, OWNER

TELEGRAM='telegram:345678901'
TELEGRAM_ID=345678901
TOKEN='123456789:test-telegram-credential-more-than-thirty-characters'


class LocalBotAPI:
    """A live loopback HTTP implementation of the Telegram API methods under test."""
    def __init__(self):
        self.updates=[];self.calls=[];self.documents=[];self.file=b"print('telegram-upload')";self.file_path='documents/source.py'
        self.status=200;self.error=0;self.retry=0;self.webhook=''

    async def start(self):
        app=web.Application(client_max_size=10*1024*1024)
        app.router.add_post('/bot{token}/{method}',self.handle)
        app.router.add_get('/file/bot{token}/{path:.*}',self.download)
        self.runner=web.AppRunner(app);await self.runner.setup();self.site=web.TCPSite(self.runner,'127.0.0.1',0);await self.site.start()
        self.url='http://127.0.0.1:'+str(self.site._server.sockets[0].getsockname()[1])

    async def handle(self,request):
        method=request.match_info['method']
        if method=='sendDocument':
            reader=await request.multipart();fields={}
            while item:=await reader.next():
                if item.name=='document':fields.update(name=item.filename,data=await item.read())
                else:fields[item.name]=(await item.read()).decode()
            self.documents.append(fields);self.calls.append((method,fields));result={'message_id':100}
        else:
            params=await request.json();self.calls.append((method,params))
            result={'getMe':{'id':123456789,'is_bot':True},'getWebhookInfo':{'url':self.webhook},
                    'getFile':{'file_path':self.file_path,'file_size':len(self.file)},'getUpdates':list(self.updates)}.get(method,True)
        if self.error:return web.json_response({'ok':False,'error_code':self.error,'parameters':{'retry_after':self.retry}},status=self.status)
        return web.json_response({'ok':True,'result':result})

    async def download(self,request):return web.Response(body=self.file)
    async def close(self):await self.runner.cleanup()


def update(text=None,owner=TELEGRAM_ID,chat='private',key=1,document=None,caption=''):
    message={'message_id':key,'from':{'id':owner,'is_bot':False},'chat':{'id':owner,'type':chat}}
    if text is not None:message['text']=text
    if document:message.update(document=document,caption=caption)
    return {'update_id':key,'message':message}


def callback(value,owner=TELEGRAM_ID,key=2):
    return {'update_id':key,'callback_query':{'id':'cb-'+str(key),'from':{'id':owner,'is_bot':False},'data':value,
            'message':{'message_id':1,'chat':{'id':owner,'type':'private'}}}}


class TelegramTests(EngineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.engine.policy.owners.add(TELEGRAM);self.engine.policy.groups=[[OWNER,TELEGRAM]]
        engine=self.engine
        self.agent=type('Adapter',(),{'token':'local-agent-credential-test','call':lambda _,action,**params:engine.dispatch(TELEGRAM,action,params)})()
        self.server=LocalBotAPI();await self.server.start()
        self.api=BotAPI(TOKEN,self.server.url);await self.api.open()
        self.front=TelegramMaestro(TELEGRAM_ID,self.agent,self.api,self.folder/'telegram-state.json')

    async def asyncTearDown(self):
        await self.api.close();await self.server.close();await super().asyncTearDown()

    async def test_real_http_start_registers_private_commands_and_builds_structured_panel(self):
        await self.front.handle(update('/start'))
        commands=[params for method,params in self.server.calls if method=='setMyCommands'][0]
        self.assertEqual(commands['scope'],{'type':'chat','chat_id':TELEGRAM_ID})
        messages=[params for method,params in self.server.calls if method=='sendMessage']
        actions=[button['callback_data'] for row in messages[-1]['reply_markup']['inline_keyboard'] for button in row]
        self.assertIn('section:inventory',actions);self.assertIn('section:terminal',actions);self.assertIn('section:data',actions)
        for method,params in self.server.calls:
            if method=='sendMessage':self.assertEqual(params['chat_id'],TELEGRAM_ID)

    async def test_unauthorized_users_groups_and_forged_callbacks_never_reach_agent(self):
        self.front.agent=type('Agent',(),{'call':AsyncMock()})()
        for payload in [update('/exec root touch /tmp/forbidden',owner=999999999),update('/exec root true',chat='group'),callback('confirm:fake',owner=999999999)]:
            await self.front.handle(payload)
        self.front.agent.call.assert_not_awaited();self.assertEqual(self.server.calls,[])

    async def test_exact_review_confirm_runs_once_and_delivers_complete_output(self):
        await self.front.handle(update('/exec root printf from-telegram'))
        review_key=next(iter(self.front.state['reviews']))
        await self.front.handle(callback('confirm:'+review_key))
        jobs=await self.engine.dispatch(TELEGRAM,'jobs',{})
        await self.engine.jobs[jobs[0]['id']]
        self.assertEqual(self.engine.job(TELEGRAM,jobs[0]['id'])['output'],'from-telegram')
        with self.assertRaises(Denied):await self.front.handle(callback('confirm:'+review_key,key=3))
        self.assertEqual(len(await self.engine.dispatch(TELEGRAM,'jobs',{})),1)
        await self.front.deliver_once()
        self.assertTrue(any(b'from-telegram' in row['data'] for row in self.server.documents))
        self.assertTrue(self.engine.store.events_for(OWNER));self.assertEqual(self.engine.store.events_for(TELEGRAM),[])

    async def test_restart_persists_consumed_updates_and_never_executes_them_again(self):
        self.server.updates=[update('/exec root printf prepared',key=8)]
        await self.front.poll_once();self.assertEqual(self.front.state['offset'],9)
        front=TelegramMaestro(TELEGRAM_ID,self.agent,self.api,self.front.path)
        await front.poll_once();self.assertEqual(len(front.state['reviews']),1)
        self.assertEqual(await self.engine.dispatch(TELEGRAM,'jobs',{}),[])
        self.assertEqual([p['offset'] for m,p in self.server.calls if m=='getUpdates'],[0,9])

    async def test_live_file_download_stage_and_code_caption_stays_review_only(self):
        await self.front.handle(update(document={'file_id':'source','file_name':'source.py','file_size':len(self.server.file)},caption='/exec root python3 source.py'))
        uploads=await self.engine.dispatch(TELEGRAM,'uploads',{});self.assertEqual(len(uploads),1)
        self.assertIn('telegram-upload',self.engine.files.preview(TELEGRAM,uploads[0]['id'],'source.py'))
        self.assertEqual(await self.engine.dispatch(TELEGRAM,'jobs',{}),[])
        self.assertEqual(len(self.front.state['reviews']),1)

    async def test_upload_limits_and_traversal_are_blocked(self):
        with self.assertRaises(Denied):await self.front.handle(update(document={'file_id':'x','file_name':'../source.py','file_size':12}))
        with self.assertRaises(Denied):await self.front.handle(update(document={'file_id':'x','file_name':'source.py','file_size':9*1024*1024}))
        self.server.file_path='../secret.env'
        with self.assertRaises(Denied):await self.api.download('x')
        self.server.file_path='documents/source.py';self.server.file=b'x'*(8*1024*1024+1)
        with self.assertRaises(Denied):await self.api.download('x')

    async def test_live_telegram_rate_limit_preserves_retry_delay_and_report_queue(self):
        self.server.error,self.server.status,self.server.retry=429,429,7
        with self.assertRaises(TelegramError) as context:await self.api.call('sendMessage',chat_id=TELEGRAM_ID,text='test')
        self.assertEqual(context.exception.retry_after,7)
        self.engine._event('incident_open',{'target':'worker','code':'unit_down','detail':'failed'});self.front.state['started']=True
        with self.assertRaises(TelegramError):await self.front.deliver_once()
        self.assertTrue(self.engine.store.events_for(TELEGRAM))

    async def test_existing_webhook_is_not_deleted_or_overridden(self):
        self.server.webhook='https://existing.example/webhook'
        with self.assertRaises(Denied):await self.front.run()
        self.assertFalse(any(m=='deleteWebhook' for m,p in self.server.calls))

    async def test_input_mode_is_explicit_and_cancel_does_not_execute_plain_chat(self):
        await self.front.handle(update('plain text'))
        self.assertEqual(await self.engine.dispatch(TELEGRAM,'jobs',{}),[])
        await self.front.callback('shell:root');await self.front.handle(update('printf only-reviewed'))
        self.assertEqual(len(self.front.state['reviews']),1)
        await self.front.handle(update('/cancel'));self.assertIsNone(self.front.state['mode'])
        self.assertEqual(await self.engine.dispatch(TELEGRAM,'jobs',{}),[])

    async def test_polling_can_queue_owner_input_without_blocking_on_a_long_operation(self):
        self.server.updates=[update('/check daily',key=1),update('/panel',key=2),update('/exec root true',owner=999999999,key=3)]
        queue=asyncio.Queue(maxsize=64);await self.front.poll_once(queue)
        self.assertEqual(queue.qsize(),2);self.assertEqual(self.front.state['offset'],4)
        self.assertFalse(any(m=='sendMessage' for m,p in self.server.calls))

    async def test_long_multibyte_report_is_sent_as_readable_document(self):
        text='Car 🏎\n'*1000;await self.front.text(text)
        self.assertEqual(self.server.documents[-1]['data'].decode(),text)
        self.assertEqual(int(self.server.documents[-1]['chat_id']),TELEGRAM_ID)

    async def test_setting_revision_conflict_does_not_overwrite_other_interface(self):
        await self.front.callback('settings_edit')
        settings=self.engine.store.settings();self.engine.save_settings(OWNER,{'revision':settings['revision'],'changes':{'daily_hour':20}})
        with self.assertRaises(ValueError):await self.front.input('{"daily_hour":21}')
        self.assertEqual(self.engine.store.settings()['daily_hour'],20)


class WitnessTests(EngineCase):
    async def test_real_tcp_reachability_and_listener_loss(self):
        async def connection(reader,writer):writer.close();await writer.wait_closed()
        server=await asyncio.start_server(connection,'127.0.0.1',0);port=server.sockets[0].getsockname()[1]
        self.assertTrue(await reachable('127.0.0.1',port))
        server.close();await server.wait_closed();self.assertFalse(await reachable('127.0.0.1',port))

    async def test_two_failures_deduplicate_incident_and_recovery_is_reported(self):
        witness=Witness({'WATCH_HOST':'127.0.0.1','WATCH_PORT':'22','WATCH_STATE':str(self.folder/'witness.json'),'TELEGRAM_TOKEN':TOKEN,'OWNER_TELEGRAM_ID':str(TELEGRAM_ID)})
        for good in (False,False,False,True):witness.observe(good,now=1700000000)
        # Two transition reports plus one daily report at the fixture's UTC hour.
        transitions=[r for r in witness.state['queue'] if 'Daily external' not in r['text']]
        self.assertEqual(len(transitions),2);self.assertIn('Unreachable',transitions[0]['text']);self.assertIn('Recovered',transitions[1]['text'])
        loaded=Witness(witness.env);self.assertFalse(loaded.state['down']);self.assertEqual(loaded.state['queue'],witness.state['queue'])
