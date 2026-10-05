"""Private numeric-owner Telegram adapter. Same broker and rules as Discord."""
from __future__ import annotations

import asyncio
import base64
import fcntl
import json
import logging
import os
import re
import secrets
import signal
import time
from pathlib import Path

import aiohttp

from .delivery import binary_parts, job_attachments, text_parts
from .discord_ui import event_text
from .files import MAX_UPLOAD, safe_name
from .ipc import Client
from .security import Denied, Redactor
from .watchdog import notify

log = logging.getLogger('maestro.telegram')
BRANCHES = {
    'setup': ('Setup & schedules', 'Activate monitoring after reviewing targets. Credentials and backup coverage are enrolled on the VPS.'),
    'targets': ('Services & health', 'Each target has its own driver, health checks and recovery owner. Newly found services are not automatically controlled.'),
    'inventory': ('Host inventory', 'Read-only service, container, repository, port, timer and PM2 declaration discovery.'),
    'recovery': ('Recovery & diagnostics', 'Fixed diagnostics and opted-in recovery. A verified checkpoint is required before service changes.'),
    'terminal': ('Terminal', 'Paste shell code, review the exact request and confirm. Root access is controlled by the local owner policy.'),
    'files': ('Files & code', 'Send a document to stage it. Inspect an entry, execute a file or run shell code with its files.'),
    'jobs': ('Jobs & output', 'View job state, download complete retained output, send stdin or cancel. Both linked owner interfaces see the same jobs.'),
    'data': ('Backups & restore', 'Create a full configured checkpoint, review snapshots, or stage a verified offline restore. Production databases are never overwritten here.'),
    'apps': ('Application settings', 'Use a locally enrolled adapter to inspect, validate and review an application change. Arbitrary database edits are never generated.'),
    'access': ('People & access', 'Only the superadmin grants or revokes access. Delegates see their assigned services and cannot grant access onward.'),
    'help': ('Guide', 'Setup > review targets and backup coverage > activate. Terminal and files use preview, confirmation, checkpoint, execution and result.'),
}


class TelegramError(Exception):
    def __init__(self, code, retry_after=0):
        self.code, self.retry_after = code, retry_after
        super().__init__('Telegram request failed with status ' + str(code))


class BotAPI:
    def __init__(self, token, base='https://api.telegram.org'):
        self.token, self.base = token, base.rstrip('/')
        self.session = None

    async def open(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40), trust_env=False)

    async def call(self, method, **params):
        if not re.fullmatch('[A-Za-z]+', method):
            raise ValueError('Invalid Telegram method.')
        async with self.session.post(self.base + '/bot' + self.token + '/' + method, json=params, allow_redirects=False) as response:
            raw = await response.content.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:
                raise TelegramError(response.status)
            try:
                result = json.loads(raw)
            except ValueError:
                raise TelegramError(response.status) from None
            if response.status != 200 or not result.get('ok'):
                raise TelegramError(result.get('error_code', response.status), min(3600, max(0, int(result.get('parameters', {}).get('retry_after', 0)))))
            return result['result']

    async def document(self, chat_id, name, data, caption=''):
        if len(data)>6*1024*1024:
            raise Denied('A transport document is limited to six MiB.')
        body = aiohttp.FormData()
        body.add_field('chat_id', str(chat_id))
        body.add_field('caption', caption[:900])
        body.add_field('document', data, filename=name, content_type='application/octet-stream')
        async with self.session.post(self.base+'/bot'+self.token+'/sendDocument', data=body, allow_redirects=False) as response:
            result = await response.json()
            if response.status != 200 or not result.get('ok'):
                raise TelegramError(result.get('error_code', response.status), min(3600, max(0, int(result.get('parameters', {}).get('retry_after', 0)))))
            return result['result']

    async def download(self, file_id):
        result = await self.call('getFile', file_id=file_id)
        path = result.get('file_path', '')
        if not re.fullmatch('[A-Za-z0-9_./-]+', path) or '..' in path.split('/') or path.startswith('/'):
            raise Denied('Telegram returned an invalid document location.')
        async with self.session.get(self.base+'/file/bot'+self.token+'/'+path, allow_redirects=False) as response:
            if response.status != 200:
                raise TelegramError(response.status)
            data = bytearray()
            async for block in response.content.iter_chunked(65536):
                data.extend(block)
                if len(data)>MAX_UPLOAD:
                    raise Denied('Upload exceeds eight MiB.')
            return bytes(data)

    async def close(self):
        if self.session:
            await self.session.close()


class TelegramMaestro:
    def __init__(self, owner, agent, api, state_path):
        self.owner, self.agent, self.api = int(owner), agent, api
        self.path = Path(state_path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state = {'offset': 0, 'reviews': {}, 'started': False, 'mode': None}
        if self.path.is_file():
            self.state.update(json.loads(self.path.read_text()))
        self.redactor = Redactor([getattr(api, 'token', ''), getattr(agent, 'token', '')])
        self.poll_tick = self.delivery_tick = time.monotonic()
        self.stop = asyncio.Event()
        self.sessions = {}
        self.access_info = {'role': 'superadmin'}
        self.last_access_refresh = 0

    def save(self):
        tmp = self.path.with_suffix('.new')
        tmp.write_text(json.dumps(self.state))
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def authorized(self, update):
        message = update.get('message') or update.get('callback_query', {}).get('message')
        sender = update.get('callback_query', {}).get('from') if 'callback_query' in update else (message or {}).get('from')
        return bool(message and sender and not sender.get('is_bot') and sender.get('id') in {self.owner, *self.sessions}
                    and message.get('chat', {}).get('type') == 'private' and message['chat'].get('id') == sender.get('id'))

    async def refresh_access(self, force=False):
        if not force and time.monotonic()-self.last_access_refresh < 30:
            return
        grants = await self.agent.call('access_list')
        active = {int(row['actor'].split(':')[1]) for row in grants if row.get('active') and row['actor'].startswith('telegram:')}
        for person in set(self.sessions)-active:
            del self.sessions[person]
        for person in active-set(self.sessions):
            if not isinstance(self.agent, Client):
                continue
            client = Client(self.agent.path, self.agent.token, 'telegram:'+str(person), tls=self.agent.tls)
            self.sessions[person] = TelegramMaestro(person, client, self.api, self.path.parent/'delegates'/str(person)/'frontend.json')
        self.last_access_refresh = time.monotonic()

    async def text(self, value, keyboard=None, name='assistant-report.txt'):
        value = str(value)
        if len(value) <= 3500:
            params = {'chat_id': self.owner, 'text': value, 'link_preview_options': {'is_disabled': True}}
            if keyboard:
                params['reply_markup'] = {'inline_keyboard': keyboard}
            await self.api.call('sendMessage', **params)
        else:
            # Reports are bounded by their backend query limits. Text parts remain readable.
            for filename, data in text_parts(name, value):
                await self.api.document(self.owner, filename, data)
            if keyboard:
                await self.api.call('sendMessage', chat_id=self.owner, text='Choose the next action.', reply_markup={'inline_keyboard': keyboard})

    @staticmethod
    def button(label, action):
        return {'text': label, 'callback_data': action}

    async def panel(self, section='home'):
        if self.access_info.get('role') != 'superadmin':
            rows = [[self.button('My services','targets:0'),self.button('My jobs','jobs')],[self.button('Guide','delegate_guide')]]
            await self.text('Maestro | '+self.access_info['role'].title()+'\nYour access is limited to assigned services. Choose a service to check it or read its logs.', rows)
            return
        if section == 'home':
            buttons = [self.button(name, 'section:'+key) for key,(name,_) in BRANCHES.items()]
            rows = [buttons[i:i+2] for i in range(0,len(buttons),2)]
            if not self.state.get('wizard_complete'):
                rows.insert(0,[self.button('Start setup wizard','wizard:identity')])
            await self.text('Maestro | Personal control center\nChoose one section. Discord and Telegram share one operation queue and separate private sessions.', rows)
            return
        label, guide = BRANCHES[section]
        rows = [[self.button('Home','home'),self.button('Guide','section:help')]]
        actions = {
            'setup': [('Setup wizard','wizard:identity'),('Status','status'),('Activate / Pause','monitor'),('Edit schedule','settings_edit')],
            'targets': [('List targets','targets:0'),('Add target','target_new'),('Hourly checks','check:hourly'),('Daily checks','check:daily')],
            'inventory': [('Scan host','inventory'),('Discover installed presets','discover')],
            'recovery': [('Automatic recovery on / off','recovery'),('Service action','service_prompt'),('Maintenance 1 hour','maintenance'),('Repair recipes','recipes')],
            'terminal': [('Runner shell','shell:runner'),('Root shell','shell:root')],
            'files': [('Recent uploads','uploads')],
            'jobs': [('Recent jobs','jobs')],
            'data': [('Backup coverage','coverage'),('Create checkpoint','checkpoint'),('Download ZIP packages','package:all'),('List checkpoints','checkpoints'),('Export reports','export')],
            'apps': [('Choose application','apps')],
            'access': [('List people','access_list'),('Grant access','access_grant')],
            'help': [('Backend health','status')],
        }
        action_rows = [[self.button(name, action)] for name,action in actions[section]]
        await self.text('Maestro | '+label+'\n\n'+guide, action_rows + rows)

    async def review(self, kind, payload):
        value = await self.agent.call('prepare', kind=kind, payload=payload)
        key = secrets.token_hex(8)
        reviews = self.state['reviews']
        reviews.clear() if len(reviews)>=30 else None
        reviews[key] = {k: value[k] for k in ('id','token','digest','expires')}
        self.save()
        await self.text('Review exact request\n'+value['preview']+'\n\nA verified checkpoint is required before execution. This review expires in five minutes.',
            [[self.button('Confirm execution','confirm:'+key),self.button('Discard','discard:'+key)]], name='request-review.txt')

    async def set_mode(self, name, **fields):
        self.state['mode'] = {'name': name, **fields}
        self.save()

    async def handle(self, update):
        if not self.authorized(update):
            return
        sender = (update.get('callback_query',{}).get('from') or update['message']['from'])['id']
        if sender != self.owner:
            child = self.sessions[sender]
            try:
                await child.handle(update)
            except (Denied, ValueError, ConnectionError, OSError) as exc:
                await child.text('Operation stopped: '+child.redactor.clean(exc)[:300])
            return
        self.access_info = await self.agent.call('whoami')
        self.state['started'] = True
        self.save()
        if 'callback_query' in update:
            callback = update['callback_query']
            await self.api.call('answerCallbackQuery', callback_query_id=callback['id'])
            await self.callback(callback.get('data', ''))
            return
        message = update['message']
        if message.get('document'):
            await self.upload(message)
            return
        text = message.get('text', '')
        if not text:
            return
        if text.startswith('/'):
            await self.command(text)
        elif self.state.get('mode'):
            await self.input(text)
        else:
            await self.text('Open /panel, choose Terminal and paste your code. Plain chat text is never executed automatically.')

    async def callback(self, data):
        key, _, value = data.partition(':')
        if key == 'home':
            self.state['mode'] = None; self.save()
            await self.panel()
        elif key == 'wizard':
            await self.wizard(value)
        elif key == 'delegate_guide':
            await self.text('Select My services, open a service, then Check or Logs. Operators also review Start, Stop and Restart. Access expires automatically and the superadmin revokes it at any time.')
        elif key == 'access_list':
            people = await self.agent.call('access_list')
            rows = [[self.button('Revoke '+row['actor'], 'revoke:'+row['actor'])] for row in people if row['active']]
            await self.text(json.dumps(people,indent=2) if people else 'No delegates. Only the superadmin has access.', rows+[[self.button('Grant access','access_grant'),self.button('Home','home')]])
        elif key == 'access_grant':
            await self.agent.call('access_list')
            await self.set_mode('grant_id')
            await self.text('Step 1 of 4. Send the numeric identity, for example telegram:123456789 or discord:123456789. /cancel exits.')
        elif key == 'grant_role':
            mode = self.state.get('mode') or {}
            if mode.get('name') != 'grant_role' or value not in ('observer','operator'):
                raise Denied('Open a new access grant.')
            await self.set_mode('grant_targets',actor=mode['actor'],role=value)
            await self.text('Step 3 of 4. Send assigned service IDs separated by spaces.\n'+'\n'.join(t['id'] for t in await self.agent.call('targets')))
        elif key == 'revoke':
            await self.text(json.dumps(await self.agent.call('access_revoke',actor=value),indent=2))
            await self.refresh_access(force=True)
        elif key == 'package':
            await self.review('package', {'target':value})
        elif key == 'apps':
            applications = await self.agent.call('applications')
            await self.text('Choose a configured application adapter.', [[self.button(x['id'],'app:'+x['id'])] for x in applications]+[[self.button('Home','home')]])
        elif key == 'app':
            current=await self.agent.call('application',id=value)
            rows=[[self.button('Edit fields','app_edit:'+value)]]
            if current['kind']=='verifier-v1':
                rows += [[self.button('Features','app_features:'+value),self.button('Channels & member role','app_fields:'+value)],
                         [self.button('Texts','app_field:'+value+':texts'),self.button('Role rules','app_field:'+value+':rules')],
                         [self.button('Payment sources','app_field:'+value+':sources'),self.button('Refresh interval','app_field:'+value+':sync_seconds')],
                         [self.button('Create channels & roles','app_setup:'+value),self.button('Publish panel','app_publish:'+value)],
                         [self.button('Activate / Pause','app_enable:'+value)]]
            await self.text('Maestro | '+value+'\nRevision: '+str(current['revision'])+'\nSelect a setting group.\n'+json.dumps(current['settings'],indent=2),rows+[[self.button('Home','home')]])
        elif key == 'app_features':
            current=await self.agent.call('application',id=value)
            fields=['wallet_linking','account_linking','platform_points','rip_feed','chain_webhook']
            rows=[[self.button(field.replace('_',' ').title()+': '+('On' if current['settings'][field] else 'Off'),'app_toggle:'+value+':'+field)] for field in fields]
            for field,choices in [('balance_mode',['off','rpc','platform']),('asset_mode',['off','das','platform']),('deposit_mode',['off','rpc','platform'])]:
                rows.append([self.button(field.split('_')[0].title()+' '+choice,'app_mode:'+value+':'+field+':'+choice) for choice in choices])
            await self.text('Feature settings | '+value+'\nEach change receives its own validation, review and checkpoint. Configure the selected provider on the VPS before activation.',rows+[[self.button('Application','app:'+value)]])
        elif key == 'app_fields':
            rows=[[self.button(field.replace('_',' ').title(),'app_field:'+value+':'+field)] for field in ['member_role','verification_channel','log_channel','rip_channel','platform_connect_url','mint','collections']]
            await self.text('Channels, membership and provider identifiers\nSend an existing Discord ID or the provider identifier requested in the next step.',rows+[[self.button('Application','app:'+value)]])
        elif key in ('app_toggle','app_mode'):
            parts=value.split(':');adapter,field=parts[:2]
            current=await self.agent.call('application',id=adapter)
            new_value=not current['settings'][field] if key=='app_toggle' else parts[2]
            await self.review('application',{'adapter':adapter,'action':'settings','revision':current['revision'],'changes':{field:new_value}})
        elif key == 'app_field':
            adapter,field=value.split(':',1);current=await self.agent.call('application',id=adapter)
            if field not in current['settings']:raise Denied('Unknown application field.')
            await self.set_mode('application_field',adapter=adapter,revision=current['revision'],field=field,current=current['settings'][field])
            await self.text('Edit '+field+'\nCurrent value: '+json.dumps(current['settings'][field],indent=2)+'\nSend a number, plain text, or JSON for a list/object. /cancel exits.')
        elif key == 'app_enable':
            current=await self.agent.call('application',id=value)
            await self.review('application',{'adapter':value,'action':'settings','revision':current['revision'],'changes':{'enabled':not current['settings']['enabled']}})
        elif key in ('app_edit','app_setup','app_publish'):
            current = await self.agent.call('application',id=value)
            if key in ('app_setup','app_publish'):
                await self.review('application',{'adapter':value,'action':'provision' if key=='app_setup' else 'publish','revision':current['revision']})
            else:
                await self.set_mode('application',adapter=value,revision=current['revision'])
                await self.text('Send only the fields to change as a JSON object. The current settings are shown below. Validation and preview run before confirmation.\n'+json.dumps(current['settings'],indent=2))
        elif key == 'section' and value in BRANCHES:
            await self.panel(value)
        elif key in ('confirm','discard'):
            review = self.state['reviews'].get(value)
            if not review or review['expires']<time.time():
                raise Denied('Review expired. Prepare a new exact request.')
            # Persist consumption before calling the agent. A crash never replays a command.
            del self.state['reviews'][value]
            self.save()
            params = {k:review[k] for k in ('id','token','digest')}
            result = await self.agent.call(key, **params)
            await self.text(json.dumps(result, indent=2), [[self.button('Recent jobs','jobs')]])
        elif key in ('status','inventory','discover','coverage','checkpoints','export','jobs','uploads','recipes'):
            action = {'coverage':'checkpoint_status'}.get(key,key)
            result = await self.agent.call(action)
            rows = []
            if key == 'jobs':
                rows = [[self.button(row['id'][:8]+' '+row['state'],'job:'+row['id'])] for row in result[:15]]
            elif key == 'uploads':
                rows = [[self.button(row['source_name'][:40],'upload:'+row['id'])] for row in result[:15]]
            elif key == 'recipes':
                rows = [[self.button(row['id'],'recipe:'+row['id'])] for row in result]
            await self.text(json.dumps(result, indent=2), rows, name=key+'.json')
        elif key == 'check':
            await self.text(json.dumps(await self.agent.call('check', kind=value), indent=2), name='health-check.json')
        elif key in ('monitor','recovery','maintenance'):
            current = await self.agent.call('settings')
            field = {'monitor':'enabled','recovery':'auto_recovery','maintenance':'maintenance_until'}[key]
            changes = {field: time.time()+3600 if key=='maintenance' else not current[field]}
            await self.text(json.dumps(await self.agent.call('save_settings', revision=current['revision'], changes=changes), indent=2))
        elif key == 'settings_edit':
            settings = await self.agent.call('settings')
            await self.set_mode('settings', revision=settings['revision'])
            await self.text('Send a JSON object containing only the schedule/settings fields you want to change.\n'+json.dumps(settings, indent=2))
        elif key == 'shell':
            await self.set_mode('shell', profile=value)
            await self.text('Paste plain Bash code for the '+value+' profile. It will be reviewed before execution. /cancel clears this input step.')
        elif key == 'service_prompt':
            await self.set_mode('service')
            await self.text('Send ACTION UNIT, for example: restart nginx.service')
        elif key == 'targets':
            targets = await self.agent.call('targets')
            page = int(value)
            rows = [[self.button(t['id'],'target:'+t['id'])] for t in targets[page*10:page*10+10]]
            if page:
                rows.append([self.button('Previous','targets:'+str(page-1))])
            if len(targets)>(page+1)*10:
                rows.append([self.button('Next','targets:'+str(page+1))])
            await self.text('Select a target. Page '+str(page+1), rows)
        elif key == 'target':
            targets = await self.agent.call('targets')
            target = next((t for t in targets if t['id']==value),None)
            if not target:
                raise Denied('Target is missing.')
            if self.access_info.get('role')=='superadmin':target=await self.agent.call('inspect_target',target=value)
            result = await self.agent.call('check',target=value,kind='hourly')
            rows = [[self.button('Check now','target:'+value),self.button('Logs','target_logs:'+value)]]
            if self.access_info.get('role') in ('superadmin','operator'):
                rows += [[self.button('Start','target_action:start:'+value),self.button('Stop','target_action:stop:'+value),self.button('Restart','target_action:restart:'+value)]]
            if self.access_info.get('role') == 'superadmin':
                rows += [[self.button('Edit monitoring','target_edit:'+value),self.button('ZIP backup','package:'+value)]]
            await self.text('Maestro | '+value+'\n'+json.dumps(result,indent=2),rows+[[self.button('Services','targets:0'),self.button('Home','home')]])
        elif key == 'target_logs':
            await self.text((await self.agent.call('logs',target=value))['output'],name=value+'-logs.txt')
        elif key == 'target_action':
            action, target_id = value.split(':',1)
            target = next(t for t in await self.agent.call('targets') if t['id']==target_id)
            await self.review('container' if target['driver']=='docker' else 'service',{'target':target_id,'unit':target['unit'],'action':action})
        elif key == 'target_edit':
            target = next(t for t in await self.agent.call('targets') if t['id']==value)
            await self.set_mode('target', id=value, revision=target['revision'])
            await self.text('Send only the fields to change as JSON, or /cancel. The saved revision prevents conflicting edits.\n'+json.dumps(target,indent=2))
        elif key == 'target_new':
            await self.set_mode('new_target')
            await self.text('Send target JSON, for example:\n'+json.dumps({'id':'new-service','unit':'example.service','enabled':False,'expected_running':False,'auto_restart':False},indent=2))
        elif key == 'checkpoint':
            await self.review('checkpoint', {})
        elif key == 'recipe':
            await self.review('recipe', {'recipe':value})
        elif key == 'job':
            await self.show_job(value)
        elif key == 'output':
            async for name,data in job_attachments(self.agent,value):
                await self.api.document(self.owner,name,data)
        elif key == 'stopjob':
            await self.text(json.dumps(await self.agent.call('cancel',id=value)))
        elif key == 'stdin':
            await self.set_mode('stdin', id=value)
            await self.text('Send one line of terminal input. A newline is appended.')
        elif key == 'eof':
            await self.text(json.dumps(await self.agent.call('eof',id=value)))
        elif key == 'upload':
            uploads = await self.agent.call('uploads')
            meta = next((x for x in uploads if x['id']==value), None)
            if not meta:
                raise Denied('Upload is missing.')
            await self.set_mode('file_select', upload=value)
            await self.text(json.dumps(meta,indent=2),[[self.button('Run entry file','file_prompt:'+value)],[self.button('Code with these files','code_upload:'+value)]],name='upload-manifest.json')
        elif key == 'file_prompt':
            await self.set_mode('file', upload=value)
            await self.text('Send JSON with entry, runtime, profile and optional args/timeout.\n'+json.dumps({'entry':'main.py','runtime':'python','profile':'runner','args':[],'timeout':120},indent=2))
        elif key == 'code_upload':
            await self.set_mode('shell', profile='runner', upload=value)
            await self.text('Paste plain Bash code. These files will be copied into the job workspace. Use /execfile root UPLOAD_ID CODE for root execution.')
        else:
            raise Denied('Unknown or outdated panel action. Open /panel again.')

    async def input(self, text):
        mode = self.state.get('mode') or {}
        kind = mode.get('name')
        if kind == 'grant_id':
            from .access import identity
            actor = identity(text.strip())
            await self.set_mode('grant_role',actor=actor)
            await self.text('Step 2 of 4. Observer reads assigned status and logs. Operator also reviews service Start, Stop, Restart and Reload.',[[self.button('Observer','grant_role:observer'),self.button('Operator','grant_role:operator')]])
            return
        elif kind == 'grant_targets':
            await self.set_mode('grant_duration',actor=mode['actor'],role=mode['role'],targets=text.split())
            await self.text('Step 4 of 4. Send the duration in hours, from 1 to 8760. Access expires automatically.')
            return
        elif kind == 'grant_duration':
            value = {k:mode[k] for k in ('actor','role','targets')};value['seconds']=int(text)*3600
            await self.text(json.dumps(await self.agent.call('access_grant',**value),indent=2))
            await self.refresh_access(force=True)
        elif kind == 'application_field':
            value=json.loads(text) if isinstance(mode['current'],(list,dict,bool,int)) else text.strip()
            await self.review('application',{'adapter':mode['adapter'],'revision':mode['revision'],'action':'settings','changes':{mode['field']:value}})
        elif kind == 'application':
            await self.review('application',{'adapter':mode['adapter'],'revision':mode['revision'],'action':'settings','changes':json.loads(text)})
        elif kind == 'shell':
            payload = {'profile':mode['profile'],'code':text}
            if mode.get('upload'):
                payload['upload'] = mode['upload']
            await self.review('shell',payload)
        elif kind == 'service':
            action, unit = text.split()
            await self.review('service',{'action':action,'unit':unit})
        elif kind in ('target','new_target'):
            value = json.loads(text)
            if kind=='target':
                old = next(t for t in await self.agent.call('targets') if t['id']==mode['id'])
                value = {**old,**value}
                value['id'],value['revision'] = mode['id'],mode['revision']
            await self.text(json.dumps(await self.agent.call('save_target', **value),indent=2))
        elif kind == 'settings':
            value = json.loads(text)
            value.pop('revision', None)
            await self.text(json.dumps(await self.agent.call('save_settings',revision=mode['revision'],changes=value),indent=2))
        elif kind == 'file':
            value = json.loads(text)
            value['upload'] = mode['upload']
            await self.review('file',value)
        elif kind == 'stdin':
            await self.text(json.dumps(await self.agent.call('stdin',id=mode['id'],text=text)))
        else:
            raise Denied('Open a new panel action before sending this input.')
        self.state['mode'] = None
        self.save()

    async def upload(self, message):
        document = message['document']
        if document.get('file_size',MAX_UPLOAD+1)>MAX_UPLOAD:
            raise Denied('Upload one nonempty document of at most eight MiB.')
        name = document.get('file_name','upload.bin')
        safe_name(name)
        if '/' in name:
            raise Denied('Document name must not include a path.')
        data = await self.api.download(document['file_id'])
        meta = await self.agent.call('upload',name=name,data=base64.b64encode(data).decode())
        await self.text(json.dumps(meta,indent=2),[[self.button('Inspect / run files','upload:'+meta['id'])]],name='upload-manifest.json')
        caption = message.get('caption','')
        if caption.startswith('/exec '):
            _, profile, code = caption.split(' ',2)
            await self.review('shell',{'code':code,'profile':profile,'upload':meta['id']})

    async def command(self, text):
        name, _, rest = text.partition(' ')
        name = name.split('@',1)[0]
        if name in ('/start','/panel','/help','/guide'):
            if name == '/start':
                await self.api.call('setMyCommands', scope={'type':'chat','chat_id':self.owner}, commands=[
                    {'command': key, 'description': desc} for key,desc in [
                        ('panel','Private control center'),('health','Backend and VPS health'),('inventory','Discover services and projects'),
                        ('targets','Edit monitored targets'),('jobs','Recent jobs and output'),('checkpoint','Review a verified backup'),
                        ('checkpoints','Recent backup records'),('exec','Review PROFILE SHELL_CODE'),('service','Review ACTION UNIT'),
                        ('repair','Review a locally enrolled recipe'),('guide','Setup and command guide'),('cancel','Clear current input step')]])
            self.state['mode'] = None
            self.save()
            await self.panel('help' if name in ('/help','/guide') else 'home')
        elif name == '/cancel':
            self.state['mode'] = None
            self.save()
            await self.text('Input step cleared. A running job is cancelled with /cancel_job JOB_ID.')
        elif name in ('/health','/inventory','/jobs','/checkpoints','/targets','/export'):
            await self.callback({'/health':'status','/targets':'targets:0'}.get(name,name[1:]))
        elif name == '/check':
            args = rest.split()
            await self.text(json.dumps(await self.agent.call('check',kind='daily' if 'daily' in args else 'hourly',target=args[0] if args and args[0]!='daily' else None),indent=2))
        elif name in ('/exec','/execfile'):
            if name=='/exec':
                profile, _, code = rest.partition(' ')
                await self.review('shell',{'profile':profile,'code':code})
            else:
                profile, upload, code = rest.split(' ',2)
                await self.review('shell',{'profile':profile,'code':code,'upload':upload})
        elif name == '/run_json':
            await self.review('file',json.loads(rest))
        elif name == '/service':
            action, unit = rest.split()
            await self.review('service',{'action':action,'unit':unit})
        elif name == '/container':
            action, target = rest.split()
            await self.review('container',{'action':action,'target':target})
        elif name == '/repair':
            await self.review('recipe',{'recipe':rest.strip()})
        elif name == '/checkpoint':
            await self.review('checkpoint',{})
        elif name == '/restore':
            await self.review('restore_stage',{'checkpoint':rest.strip()})
        elif name == '/job':
            await self.show_job(rest.strip())
        elif name in ('/output','/cancel_job','/eof'):
            await self.callback({'/output':'output:','/cancel_job':'stopjob:','/eof':'eof:'}[name]+rest.strip())
        elif name == '/stdin':
            key, value = rest.split(' ',1)
            await self.text(json.dumps(await self.agent.call('stdin',id=key,text=value)))
        elif name == '/preview':
            key, entry = rest.split(' ',1)
            await self.text((await self.agent.call('preview',id=key,name=entry))['output'],name='file-preview.txt')
        elif name == '/maestro_backup':
            value = await self.agent.call('backup',password=rest)
            for filename, data in binary_parts(value['name'], base64.b64decode(value['data'])):
                await self.api.document(self.owner,filename,data,'Encrypted Maestro state. Join numbered binary parts in order if split. SHA-256: '+value['sha256'])
        elif name == '/reboot':
            await self.review('reboot',{'phrase':rest})
        elif name == '/diagnostic':
            await self.text(json.dumps(await self.agent.call('diagnostic',name=rest.strip()),indent=2))
        else:
            await self.text('Unknown command. Open /panel or /guide.')

    async def wizard(self, step):
        self.access_info = await self.agent.call('whoami')
        if self.access_info['role'] != 'superadmin':
            raise Denied('The setup wizard belongs to the superadmin.')
        if step == 'identity':
            await self.text('Setup 1 of 5 | Identity\nYour numeric identity is '+self.access_info['actor']+'. Bot credentials and this superadmin identity are enrolled on the VPS. People & access manages delegates separately.',[[self.button('Continue','wizard:services'),self.button('Home','home')]])
        elif step == 'services':
            result = await self.agent.call('inventory')
            await self.text('Setup 2 of 5 | Services\nFound '+str(len(result.get('services',[])))+' services and '+str(len(result.get('containers',[])))+' containers. Service cards are available automatically. Unknown recovery controllers remain under their existing ownership.',[[self.button('Continue','wizard:backup'),self.button('Services','targets:0')]])
        elif step == 'backup':
            status = await self.agent.call('checkpoint_status')
            await self.text('Setup 3 of 5 | Backups\n'+json.dumps(status,indent=2)+'\nUse the protected VPS backup wizard once to enroll storage credentials and consistent database exports. Commands remain blocked until checkpoint verification succeeds.',[[self.button('Continue','wizard:schedule'),self.button('Back','wizard:services')]])
        elif step == 'schedule':
            await self.text('Setup 4 of 5 | Schedule\nHourly reports and daily checks at 18:00 Asia/Tehran are the defaults. Change them in Setup & schedules.',[[self.button('Keep schedule','wizard:finish'),self.button('Edit schedule','settings_edit')]])
        elif step == 'finish':
            settings = await self.agent.call('settings')
            await self.agent.call('save_settings',revision=settings['revision'],changes={'enabled':True})
            self.state['wizard_complete'] = True;self.save()
            await self.text('Setup 5 of 5 | Monitoring active\nReports and service discovery run automatically. Automatic recovery stays off until you enable it globally and for each reviewed target.',[[self.button('Home','home'),self.button('Services','targets:0')]])
        else:
            raise Denied('Open the setup wizard again.')

    async def show_job(self, key):
        row = await self.agent.call('job',id=key)
        await self.text(json.dumps(row,indent=2),[
            [self.button('Refresh','job:'+key), self.button('Download output','output:'+key)],
            [self.button('Send input','stdin:'+key),self.button('Close stdin','eof:'+key)],
            [self.button('Cancel job','stopjob:'+key)]], name='job-status.json')

    async def poll_once(self, enqueue=None):
        updates = await self.api.call('getUpdates',offset=self.state['offset'],timeout=25,allowed_updates=['message','callback_query'])
        self.poll_tick = time.monotonic()
        for update in updates:
            key = update.get('update_id')
            if not isinstance(key,int) or key<self.state['offset']:
                continue
            self.state['offset'] = key+1
            self.save()
            if enqueue is not None:
                if self.authorized(update):
                    if enqueue.full():
                        await self.text('Command queue is full. No operation was started. Check /jobs before retrying.')
                    else:
                        enqueue.put_nowait(update)
                continue
            try:
                await self.handle(update)
            except (Denied, ValueError, KeyError, TypeError, ConnectionError, OSError) as exc:
                if self.authorized(update):
                    await self.text('Operation failed: '+self.redactor.clean(exc)[:350]+'. Check /health and job history before retrying execution.')

    async def deliver_once(self):
        self.delivery_tick = time.monotonic()
        if not self.state.get('started'):
            return
        settings = await self.agent.call('settings')
        if not settings['telegram_reports']:
            return
        for event in await self.agent.call('events'):
            await self.text(event_text(event),name='assistant-report-'+str(event['id'])+'.txt')
            if event['kind']=='job_finished':
                try:
                    async for name,data in job_attachments(self.agent,event['payload']['id']):
                        await self.api.document(self.owner,name,data,'Job '+event['payload']['id'])
                except Denied:
                    # Another enrolled owner may receive operational metadata, never their files.
                    pass
            await self.agent.call('ack',id=event['id'])

    async def run(self):
        identity = await self.api.call('getMe')
        if identity.get('is_bot') is not True:
            raise Denied('Configured Telegram account is not a bot.')
        webhook = await self.api.call('getWebhookInfo')
        if webhook.get('url'):
            raise Denied('An existing Telegram webhook blocks polling. Resolve it locally before starting this frontend.')
        notify('READY=1\nSTATUS=Telegram private owner interface connected')
        inputs = asyncio.Queue(maxsize=64)
        async def polling():
            failures = 0
            while not self.stop.is_set():
                try:
                    await self.refresh_access()
                    await self.poll_once(inputs)
                    failures = 0
                except TelegramError as exc:
                    if exc.code in (401,409):
                        raise Denied('Telegram credential rejected or a competing poller is active.') from None
                    failures += 1
                    await asyncio.sleep(exc.retry_after or min(60,2**min(failures,6)))
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                    failures += 1
                    await asyncio.sleep(min(60,2**min(failures,6)))
        async def handling():
            while not self.stop.is_set():
                update = await inputs.get()
                try:
                    await self.handle(update)
                except (Denied, ValueError, KeyError, TypeError, ConnectionError, OSError, aiohttp.ClientError, asyncio.TimeoutError, TelegramError) as exc:
                    try:
                        await self.text('Operation failed: '+self.redactor.clean(exc)[:350]+'. Check /health and job history before retrying.')
                    except (aiohttp.ClientError, asyncio.TimeoutError, TelegramError):
                        log.error('Owner response unavailable; inspect job history before retrying')
                finally:
                    inputs.task_done()
        async def delivery():
            while not self.stop.is_set():
                try:
                    await self.deliver_once()
                except (OSError, ValueError, ConnectionError, aiohttp.ClientError, asyncio.TimeoutError, TelegramError) as exc:
                    log.error('Private report delivery unavailable: %s',type(exc).__name__)
                    if isinstance(exc,TelegramError) and exc.retry_after:
                        await asyncio.sleep(exc.retry_after)
                await asyncio.sleep(30)
        tasks = [asyncio.create_task(polling()),asyncio.create_task(delivery()),asyncio.create_task(handling())]
        async def watchdog():
            while not self.stop.is_set():
                if all(not t.done() for t in tasks) and time.monotonic()-self.poll_tick<180 and time.monotonic()-self.delivery_tick<360:
                    notify('WATCHDOG=1')
                await asyncio.sleep(20)
        tasks.append(asyncio.create_task(watchdog()))
        waiter = asyncio.create_task(self.stop.wait())
        try:
            done, _ = await asyncio.wait([*tasks,waiter],return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task is not waiter:
                    task.result()
        finally:
            notify('STOPPING=1')
            for task in [*tasks,waiter]:
                task.cancel()
            await asyncio.gather(*tasks,waiter,return_exceptions=True)


async def serve():
    required = ('TELEGRAM_TOKEN','OWNER_TELEGRAM_ID','AGENT_TOKEN')
    if any(not os.environ.get(key) for key in required):
        raise SystemExit('Complete the protected Telegram environment before starting.')
    folder = Path(os.environ.get('TELEGRAM_STATE','/var/lib/maestro-telegram/frontend.json'))
    folder.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (folder.parent/'poller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        tls = {key: os.environ['AGENT_TLS_'+key.upper()] for key in ('cert','key','ca')} if os.environ.get('AGENT_TLS_PORT') else None
        if tls:
            tls['port'] = int(os.environ['AGENT_TLS_PORT'])
        owner = int(os.environ['OWNER_TELEGRAM_ID'])
        client = Client(os.environ.get('AGENT_SOCKET','/run/maestro/agent.sock'),os.environ['AGENT_TOKEN'],'telegram:'+str(owner),tls=tls)
        api = BotAPI(os.environ['TELEGRAM_TOKEN'])
        await api.open()
        assistant = TelegramMaestro(owner,client,api,folder)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM,signal.SIGINT):
            loop.add_signal_handler(sig,assistant.stop.set)
        try:
            await assistant.run()
        finally:
            await api.close()


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
    asyncio.run(serve())


if __name__=='__main__':
    main()
