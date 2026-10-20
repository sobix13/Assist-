"""One-shot Waffle publication through its own checked service implementation."""
import asyncio
import json
import sys


async def run():
    install,guild_id,database,coordination,rid,revision=sys.argv[1:]
    token=sys.stdin.read().strip()
    if not token:
        raise ValueError('توکن Waffle ثبت نشده است.')
    sys.path.insert(0,install)
    import discord
    from ripcars_raffle.storage import Store
    from ripcars_raffle.coordination import Registry
    from ripcars_raffle.service import Raffles
    from ripcars_raffle.operations import Reporter

    class Publisher(discord.Client):
        async def setup_hook(self):
            self.db=Store(database)
            self.registry=Registry(coordination)
            self.raffles=Raffles(self)
            self.reporter=Reporter(self)
            self.settings_cache={}
            self.result=None
            self.failure=None

        async def on_ready(self):
            if getattr(self,'attempted',False):
                return
            self.attempted=True
            try:
                guild=self.get_guild(int(guild_id))
                if not guild:
                    raise ValueError('Waffle در سرور انتخاب‌شده حضور ندارد.')
                self.result=await self.raffles.publish(guild,int(rid),0,draft_revision=int(revision))
            except Exception as exc:
                self.failure=exc
            finally:
                await self.close()

    intents=discord.Intents.none()
    intents.guilds=True
    intents.members=True
    client=Publisher(intents=intents,allowed_mentions=discord.AllowedMentions.none())
    async with client:
        await client.start(token,reconnect=False)
    if client.failure:
        raise client.failure
    if not client.result:
        raise ValueError('نتیجه انتشار دریافت نشد.')
    result=client.result
    print(json.dumps({'state':'completed','raffle_id':result['id'],
                      'message':result['message'],'channel':result['channel'],'verified':True}))


if __name__=='__main__':
    asyncio.run(run())
