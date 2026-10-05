"""Version-pinned Verifier setup through its own Discord identity and shared lease."""
from __future__ import annotations

import copy
import asyncio
import json
import os
from pathlib import Path

import aiohttp
import ripcars_coordination as protocol

from .applications import connect
from .adapters.verifier_readiness import readiness
from .security import Denied, read_env

VIEW=1<<10;SEND=1<<11;EMBED=1<<14;ATTACH=1<<15;HISTORY=1<<16;COMMANDS=1<<31
BOT_ACCESS=VIEW|SEND|EMBED|ATTACH|HISTORY
DANGEROUS=(1<<3)|(1<<4)|(1<<5)|(1<<1)|(1<<2)|(1<<13)|(1<<28)|(1<<40)


class DiscordREST:
    def __init__(self, token, base='https://discord.com/api/v10'):
        self.token,self.base=token,base

    async def call(self, method, path, payload=None):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30),trust_env=False) as session:
            async with session.request(method,self.base+path,json=payload,headers={'Authorization':'Bot '+self.token},allow_redirects=False) as response:
                raw=await response.content.read(4*1024*1024+1)
                if len(raw)>4*1024*1024:raise Denied('Discord response exceeds the adapter limit.')
                if response.status not in (200,201,204):
                    # Mutations are not replayed after uncertain delivery, including 5xx/timeouts.
                    raise Denied('Discord returned '+str(response.status)+'. No mutation retry was attempted. Review the operation and existing resources.')
                return json.loads(raw) if raw else None


def permissions(roles, member, channel=None):
    ids={str(r) for r in member['roles']};guild_role=roles[0]['id']
    value=0
    for role in roles:
        if role['id']==guild_role or role['id'] in ids:value|=int(role['permissions'])
    if value&(1<<3):return (1<<53)-1
    if channel:
        overwrites=channel.get('permission_overwrites',[])
        for overwrite in overwrites:
            if overwrite['id']==guild_role:value=(value&~int(overwrite['deny']))|int(overwrite['allow'])
        deny=allow=0
        for overwrite in overwrites:
            if overwrite.get('type')==0 and overwrite['id'] in ids:
                deny|=int(overwrite['deny']);allow|=int(overwrite['allow'])
        value=(value&~deny)|allow
        for overwrite in overwrites:
            if overwrite.get('type')==1 and overwrite['id']==str(member['user']['id']):value=(value&~int(overwrite['deny']))|int(overwrite['allow'])
    return value


class VerifierBridge:
    def __init__(self, applications, spec, plan):
        self.applications,self.spec,self.plan=applications,spec,plan
        self.cfg=copy.deepcopy(plan['settings']);self.guild=str(spec['guild']);self.revision=plan['revision']
        env=read_env(spec['env_file']);self.env=env;token=env.get('DISCORD_TOKEN','')
        if len(token)<40:raise Denied('Enroll the existing Verifier token in its protected environment file.')
        applications.redactor.load_env(spec['env_file'])
        self.api=applications.discord_factory(token) if applications.discord_factory else DiscordREST(token)
        self.registry_path=Path(spec.get('coordination_path') or env.get('COORDINATION_PATH',''))
        if not self.registry_path.is_absolute() or not self.registry_path.is_file() or self.registry_path.is_symlink():
            raise Denied('Enroll the initialized shared coordination database before Discord provisioning.')

    async def preflight(self):
        self.bot=await self.api.call('GET','/users/@me')
        if self.bot.get('bot') is not True:raise Denied('Application credential is not a Discord bot.')
        self.roles=await self.api.call('GET','/guilds/'+self.guild+'/roles')
        # @everyone is located by guild ID, never by array order.
        self.roles.sort(key=lambda r:r['id']!=self.guild)
        self.channels=await self.api.call('GET','/guilds/'+self.guild+'/channels')
        self.member=await self.api.call('GET','/guilds/'+self.guild+'/members/'+self.bot['id'])
        member_role=next((r for r in self.roles if int(r['id'])==self.cfg['member_role']),None)
        if not member_role or member_role['id']==self.guild or member_role.get('managed') or int(member_role['permissions'])&DANGEROUS:
            raise Denied('Choose the ordinary Gate/member role before configuring Verifier resources.')
        self.permission=permissions(self.roles,self.member)
        registry=protocol.inspect(self.registry_path,int(self.guild))
        if not registry['ok']:raise Denied('Shared resource ownership needs review: '+'; '.join(registry['issues'])[:200])
        foreign={str(r['object_id']):r for r in registry['resources']}
        connection=connect(self.registry_path)
        try:
            baselines={str(r['object_id']):json.loads(r['baseline']) for r in connection.execute('SELECT object_id,baseline FROM resources WHERE guild=?',(int(self.guild),))}
        finally:connection.close()
        for key in ('verification_channel','rip_channel','log_channel'):
            selected=self.cfg[key]
            if not selected:continue
            channel=next((c for c in self.channels if int(c['id'])==selected and c['type']==0),None)
            if not channel:raise Denied('A selected '+key+' is missing or not a text channel.')
            if permissions(self.roles,self.member,channel)&BOT_ACCESS!=BOT_ACCESS:raise Denied('Verifier lacks required permissions in '+key)
            entry=foreign.get(str(selected));expected='verifier:channel:'+self.bot['id']+':'+key
            if entry and (entry['key']!=expected or entry['owner']!='bot:'+self.bot['id'] or entry['state']!='active'):
                raise Denied('The selected channel belongs to another controller or was pinned.')
            ordinary={'user':{'id':'0'},'roles':[str(self.cfg['member_role'])]}
            if key=='log_channel':
                for role in self.roles:
                    if int(role['permissions'])&((1<<3)|(1<<5)|(1<<13)) or role.get('managed'):continue
                    if permissions(self.roles,{'user':{'id':'0'},'roles':[role['id']]},channel)&VIEW:raise Denied('Verifier log channel exposes data to an ordinary role.')
                if any(x.get('type')==1 and x['id']!=self.bot['id'] and int(x['allow'])&VIEW for x in channel.get('permission_overwrites',[])):
                    raise Denied('Verifier log channel has another explicit member allow. Review it in Discord before use.')
            elif permissions(self.roles,ordinary,channel)&(VIEW|HISTORY)!=(VIEW|HISTORY):raise Denied('Members need View Channel and Read History in '+key)
        bot_top=max((r.get('position',0) for r in self.roles if r['id'] in set(self.member['roles'])),default=0)
        for rule in self.cfg['rules']:
            if not rule['role_id']:continue
            role=next((r for r in self.roles if int(r['id'])==rule['role_id']),None)
            if not role or role.get('managed') or int(role['permissions'])!=0 or role.get('position',0)>=bot_top:
                raise Denied('Every qualifying role must have zero permission bits and remain below the Verifier role.')
            entry=foreign.get(str(rule['role_id']));expected='verifier:role:'+self.bot['id']+':'+rule['key']
            if entry and (entry['key']!=expected or entry['owner']!='bot:'+self.bot['id'] or entry['state']!='active'):
                raise Denied('This qualifying role belongs to another controller or was pinned.')
            if entry:
                baseline=baselines[str(rule['role_id'])]
                if baseline.get('name')!=role['name'] or int(baseline.get('permissions',-1))!=int(role['permissions']):
                    raise Denied('An administrator changed a qualifying role. Review its ownership baseline before continuing.')
            if self.cfg['enabled'] and not entry:raise Denied('Bind this qualifying role before activating Verifier.')
        if self.cfg['enabled']:
            blockers=readiness(self.cfg,self.env)
            if blockers:raise Denied('Verifier activation blockers: '+'; '.join(blockers)[:900])
            base='/channels/'+str(self.cfg['verification_channel'])+'/messages/'+str(self.cfg['public_message'])
            panel=await self.api.call('GET',base)
            if panel['author']['id']!=self.bot['id']:raise Denied('The published Verifier panel belongs to another bot.')
            host=self.env.get('WEB_HOST','127.0.0.1');port=int(self.env.get('WEB_PORT','8092'))
            if host not in ('127.0.0.1','::1') or not 1<=port<=65535:raise Denied('Verifier health must use a fixed loopback listener.')
            health=await self.applications.probes.endpoint('http://'+('['+host+']' if host=='::1' else host)+':'+str(port)+'/healthz','ok')
            if not health['ok']:raise Denied('Verifier live worker, database and Discord health check did not pass.')
        if self.plan['action']=='provision':
            if self.permission&((1<<4)|(1<<28))!=((1<<4)|(1<<28)) or bot_top<2:
                raise Denied('Give Verifier Manage Channels and Manage Roles, and place its bot role above ordinary roles.')
            for key,name in self.channel_plan():
                if not self.cfg[key] and any(c['name']==name for c in self.channels):raise Denied('Channel name collision: '+name+'. Select the existing channel explicitly.')
            for rule in self.cfg['rules']:
                if not rule['role_id'] and any(r['name']==rule['name'] for r in self.roles):raise Denied('Role name collision: '+rule['name']+'. Select its existing ID explicitly.')
        return {'ok':True,'bot_id':self.bot['id'],'guild':self.guild,'action':self.plan['action'],
                'create_channels':[name for key,name in self.channel_plan() if not self.cfg[key]] if self.plan['action']=='provision' else [],
                'create_roles':[r['name'] for r in self.cfg['rules'] if not r['role_id']] if self.plan['action']=='provision' else []}

    def channel_plan(self):
        return [('verification_channel','verifier-connect'),('log_channel','verifier-log')]+([('rip_channel','verifier-pulls')] if self.cfg['rip_feed'] else [])

    def ownership(self, c, key, rid, kind, baseline):
        row=c.execute('SELECT key,owner,state FROM resources WHERE guild=? AND object_id=?',(int(self.guild),int(rid))).fetchone()
        if row and (row[0]!=key or row[1]!='bot:'+self.bot['id'] or row[2]!='active'):raise Denied('Resource ownership changed during setup.')
        value=json.dumps(baseline,sort_keys=True,separators=(',',':'))
        c.execute("INSERT OR IGNORE INTO resources VALUES(?,?,?,?,?,?,?,'active')",(int(self.guild),key,int(rid),kind,'bot:'+self.bot['id'],value,value))

    def lease(self, c, token):
        c.execute('BEGIN IMMEDIATE')
        try:protocol.renew(c,int(self.guild),'server-setup',token,error=Denied);c.commit()
        except BaseException:c.rollback();raise

    def sidefile_permissions(self):
        stat=self.registry_path.stat()
        for suffix in ('-wal','-shm'):
            p=Path(str(self.registry_path)+suffix)
            if p.exists() and p.stat().st_uid==os.geteuid():
                os.chown(p,stat.st_uid,stat.st_gid);p.chmod(0o660)

    async def apply(self, actor):
        await self.preflight()
        c=connect(self.registry_path,False);token=None;created=[]
        try:
            c.execute('BEGIN IMMEDIATE');token=protocol.claim(c,int(self.guild),error=Denied);c.commit();self.sidefile_permissions()
            if self.plan['action']=='provision':
                for key,name in self.channel_plan():
                    if not self.cfg[key]:
                        self.lease(c,token)
                        overwrites=[{'id':self.guild,'type':0,'allow':'0','deny':str(VIEW)},
                            {'id':str(self.cfg['member_role']),'type':0,'allow':'0' if key=='log_channel' else str(VIEW|HISTORY|COMMANDS),'deny':str(VIEW) if key=='log_channel' else str(SEND)},
                            {'id':self.bot['id'],'type':1,'allow':str(BOT_ACCESS),'deny':'0'}]
                        channel=await self.api.call('POST','/guilds/'+self.guild+'/channels',{'name':name,'type':0,'permission_overwrites':overwrites})
                        self.cfg[key]=int(channel['id']);created.append({'kind':'channel','id':channel['id'],'name':name})
                    self.lease(c,token)
                    self.ownership(c,'verifier:channel:'+self.bot['id']+':'+key,self.cfg[key],'channel',{});c.commit()
                    self.revision=self.applications.commit_verifier(self.spec,self.cfg,self.revision,actor)
                for rule in self.cfg['rules']:
                    if not rule['role_id']:
                        self.lease(c,token)
                        role=await self.api.call('POST','/guilds/'+self.guild+'/roles',{'name':rule['name'],'permissions':'0','mentionable':False,'hoist':False})
                        rule['role_id']=int(role['id']);created.append({'kind':'role','id':role['id'],'name':role['name']})
                    self.lease(c,token)
                    self.ownership(c,'verifier:role:'+self.bot['id']+':'+rule['key'],rule['role_id'],'role',{'name':rule['name'],'permissions':0});c.commit()
                    self.revision=self.applications.commit_verifier(self.spec,self.cfg,self.revision,actor)
            if self.plan['action']=='publish' or self.plan['action']=='provision':
                if not self.cfg['verification_channel']:raise Denied('Select the verification text channel first.')
                self.lease(c,token)
                components=[]
                for key,label,show in [('wallet','Connect wallet',self.cfg['wallet_linking']),('account','Connect account',self.cfg['account_linking']),('status','My status',True),('refresh','Refresh',True),('disconnect','Disconnect',True)]:
                    if show:components.append({'type':2,'style':1 if key=='wallet' else 2,'label':label,'custom_id':'rcv:'+key+':v1'})
                payload={'embeds':[{'title':self.cfg['texts']['title'],'description':self.cfg['texts']['description'],'color':self.cfg['color']}],
                         'components':[{'type':1,'components':components}],'allowed_mentions':{'parse':[]}}
                base='/channels/'+str(self.cfg['verification_channel'])+'/messages'
                if self.cfg['public_message']:
                    old=await self.api.call('GET',base+'/'+str(self.cfg['public_message']))
                    if old['author']['id']!=self.bot['id']:raise Denied('The saved public panel belongs to another bot.')
                    await self.api.call('PATCH',base+'/'+str(self.cfg['public_message']),payload)
                else:
                    message=await self.api.call('POST',base,payload);self.cfg['public_message']=int(message['id'])
                    created.append({'kind':'message','id':message['id']})
                    self.revision=self.applications.commit_verifier(self.spec,self.cfg,self.revision,actor)
            await self.preflight()
            return {'state':'completed','revision':self.revision,'created':created,'verified':True,'detail':'Verifier owns these resources. Its persistent member buttons handle their interactions.'}
        except (Exception,asyncio.CancelledError) as exc:
            return {'state':'partial','created':created,'revision':self.revision,'detail':type(exc).__name__+': '+str(exc)[:300],
                    'review_required':True,'mutation_replayed':False}
        finally:
            if token:
                c.rollback();c.execute('BEGIN IMMEDIATE');protocol.release(c,int(self.guild),'server-setup',token);c.commit()
            self.sidefile_permissions();c.close()
