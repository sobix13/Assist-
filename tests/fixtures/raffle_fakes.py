from __future__ import annotations

import asyncio
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock

import discord

from ripcars_raffle import config
from ripcars_raffle.bot import RaffleBot


def failure(kind=discord.Forbidden,code=50013,status=403):
    return kind(SimpleNamespace(status=status,reason="Injected failure"),{"code":code,"message":"Injected test failure"})


class Message:
    def __init__(self,mid,channel,author,embed,view):
        self.id,self.channel,self.author=mid,channel,author
        self.embeds=[embed]
        self.view=view
        self.edits=[]
        self.edit_error=None

    async def edit(self,**kwargs):
        if self.edit_error:
            raise self.edit_error
        self.edits.append(kwargs)
        if "embed" in kwargs:
            self.embeds=[kwargs["embed"]]
        self.view=kwargs.get("view",self.view)
        return self


class Channel(discord.TextChannel):
    def __init__(self,guild,cid,name,overwrites):
        super().__init__(state=guild._state,guild=guild,data={"id":str(cid),"type":0,"name":name,"position":0,"permission_overwrites":overwrites})
        self.messages={}
        self.sent=[]
        self.error=None
        self.fetch_error=None

    async def send(self,**kwargs):
        if self.error:
            raise self.error
        msg=Message(10000+self.id*100+len(self.sent),self,self.guild._state.user,kwargs.get("embed"),kwargs.get("view"))
        self.messages[msg.id]=msg
        self.sent.append(kwargs)
        return msg

    async def fetch_message(self,mid):
        if self.fetch_error:
            raise self.fetch_error
        if mid not in self.messages:
            raise failure(discord.NotFound,10008,404)
        return self.messages[mid]


class Guild(discord.Guild):
    def __init__(self,bot,gid=1):
        permission=discord.Permissions(view_channel=True,read_message_history=True,send_messages=True,embed_links=True,attach_files=True).value
        roles=[]
        for rid,name,flags in ((gid,"@everyone",0),(10,"Rippers",0),(11,"Verified holder",0),(12,"Rare Ripper",0),(30,"Raffle Manager",0),(31,"Admin",discord.Permissions(administrator=True).value),(50,"Raffle Bot",permission)):
            roles.append({"id":str(rid),"name":name,"permissions":str(flags),"position":len(roles),"color":0,"hoist":False,"managed":rid==50,"mentionable":False,**({"tags":{"bot_id":str(bot.user.id)}} if rid==50 else {})})
        super().__init__(data={"id":str(gid),"name":"Rip Cars test","owner_id":"9001","roles":roles,"features":[]},state=bot._connection)
        self.failures={}
        self.fetches=[]
        self.add_member(bot.user.id,[50],bot=True)
        self.actor=self.add_member(9001,[31])
        allow=discord.Permissions(view_channel=True,read_message_history=True,send_messages=True,embed_links=True,attach_files=True).value
        deny=discord.Permissions(view_channel=True).value
        public=[{"id":str(gid),"type":0,"allow":"0","deny":str(deny)},{"id":"10","type":0,"allow":str(allow),"deny":"0"},{"id":"50","type":0,"allow":str(allow),"deny":"0"}]
        private=[{"id":str(gid),"type":0,"allow":"0","deny":str(deny)},{"id":"30","type":0,"allow":str(allow),"deny":"0"},{"id":"50","type":0,"allow":str(allow),"deny":"0"}]
        self.public=Channel(self,100,"raffles",public)
        self.log=Channel(self,101,"raffle-log",private)
        self._channels[100]=self.public;self._channels[101]=self.log

    def add_member(self,uid,roles=None,bot=False,joined_days=100):
        m=discord.Member(data={"user":{"id":str(uid),"username":f"member{uid}","discriminator":"0","avatar":None,"bot":bot,"global_name":None,"public_flags":0},"roles":[str(r) for r in (roles or [])],"joined_at":(datetime.now(timezone.utc)-timedelta(days=joined_days)).isoformat(),"deaf":False,"mute":False,"flags":0},guild=self,state=self._state)
        self._members[uid]=m
        return m

    async def fetch_member(self,uid):
        self.fetches.append(uid)
        await asyncio.sleep(0)
        if uid in self.failures:
            raise self.failures[uid]
        if uid not in self._members:
            raise failure(discord.NotFound,10007,404)
        return self._members[uid]


class Response:
    def __init__(self):
        self.done=False
        async def complete(*args,**kwargs):
            self.done=True
        self.defer=AsyncMock(side_effect=complete)
        self.send_message=AsyncMock(side_effect=complete)
        self.edit_message=AsyncMock(side_effect=complete)
        self.send_modal=AsyncMock(side_effect=complete)

    def is_done(self):
        return self.done


def interaction(bot,guild,user=None,message=None):
    return SimpleNamespace(client=bot,guild=guild,guild_id=guild.id,user=user or guild.actor,channel=guild.public,channel_id=guild.public.id,message=message,
                           response=Response(),followup=SimpleNamespace(send=AsyncMock()))


class AsyncCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.bot=RaffleBot(self.root/"raffle.sqlite3",self.root/"coord.sqlite3")
        await self.bot._async_setup_hook()
        self.bot._connection.user=discord.ClientUser(state=self.bot._connection,data={"id":"777","username":"Rip Cars Raffle","discriminator":"0","avatar":None,"bot":True,"global_name":None,"public_flags":0})
        self.guild=Guild(self.bot)
        self.bot._connection._guilds[self.guild.id]=self.guild
        await self.bot.db.open();await self.bot.registry.open()
        self.cfg=config.fresh()
        self.cfg.update(enabled=True,member_role=10,manager_roles=[30],channel=100,log_channel=101)
        await self.bot.db.save(1,self.cfg,0,9001)
        self.bot.settings_cache[1]=self.cfg

    async def asyncTearDown(self):
        await self.bot.close()
        self.tmp.cleanup()

    async def draft(self,category="general",rules=None):
        spec=config.fresh()["templates"][category]
        spec["prize"]="Test Hot Wheels prize, delivered manually by the team"
        if rules:
            spec["rules"].update(rules)
        return await self.bot.db.create(1,9001,category,spec)

    async def published(self,category="general",rules=None):
        rid=await self.draft(category,rules)
        return await self.bot.raffles.publish(self.guild,rid,9001)

    async def draw_members(self,count=4,rules=None):
        r=await self.published(rules=rules)
        for uid in range(1000,1000+count):
            self.guild.add_member(uid,[10])
            await self.bot.db.enter(1,r["id"],uid)
        return await self.bot.raffles.draw(self.guild,r["id"],9001,"Test early ending")
