"""Discord views for the same guided workflows used in Telegram."""
import time

import discord

from .guided import GuidedFlow
from . import guides
from .security import Denied


class Context(GuidedFlow):
    def __init__(self,bot,interaction):
        self.bot,self.i=bot,interaction
        self.agent=bot.api
        if not hasattr(bot,'guided_sessions'):bot.guided_sessions={}
        now=time.time()
        bot.guided_sessions={k:v for k,v in bot.guided_sessions.items() if now-v.get('at',0)<900}
        self.state=bot.guided_sessions.setdefault(interaction.user.id,{'mode':None,'at':now})
        self.state['at']=now

    def save(self):
        self.state['at']=time.time()

    async def set_mode(self,name,**fields):
        self.state['mode']={'name':name,'at':time.time(),**fields};self.save()

    @staticmethod
    def button(label,action):
        return {'text':label,'callback_data':action}

    async def text(self,value,keyboard=None,name='report.txt'):
        from .discord_ui import show_text
        rows=list(keyboard or [])
        if self.state.get('mode'):
            rows.insert(0,[self.button('وارد کردن مقدار','input')])
        await show_text(self.i,str(value),name,GuidedView(self.bot,rows))

    async def review(self,kind,payload):
        prepared=await self.agent.call('prepare',kind=kind,payload=payload)
        await self.present_review(prepared,prepared.get('summary') or guides.review_text(kind,payload))

    async def present_review(self,prepared,summary=None):
        from .discord_ui import ConfirmView,show_text
        await show_text(self.i,(summary or prepared['preview'])+'\n\nپیش از اجرا بکاپ بررسی می‌شود. تأیید تا پنج دقیقه اعتبار دارد.',view=ConfirmView(self.bot,prepared))

    async def route(self,action):
        if await self.guided(action):return
        from .discord_ui import open_panel,show_text,JobView
        key,_,value=action.partition(':')
        if key=='section':
            await open_panel(self.bot,self.i,value)
        elif key=='jobs':
            rows=await self.agent.call('jobs')
            await self.text('کارهای من\nبرای دیدن مرحله فعلی، یک کار را انتخاب کن.',
                [[self.button(r['id'][:8]+' | '+r['state'],'job:'+r['id'])] for r in rows]+[[self.button('صفحه اصلی','g:home')]])
        elif key=='job':
            job=await self.agent.call('job',id=value)
            await show_text(self.i,guides.job_text(job),view=JobView(self.bot,value,terminal=job.get('kind') in ('shell','file'),active=job['state'] in ('queued','running')))
        elif key=='target':
            result=await self.agent.call('check',target=value,kind='hourly')
            await self.text(guides.health_text(result),
                [[self.button('راه‌اندازی دوباره','restart:'+value),self.button('روشن کردن','start:'+value)],
                 [self.button('خاموش کردن','stop:'+value),self.button('بات‌ها','g:bots')]])
        elif key in ('start','stop','restart'):
            target=next(t for t in await self.agent.call('targets') if t['id']==value)
            await self.review('service',{'unit':target['unit'],'action':key})
        elif key in ('app_setup','app_publish'):
            current=await self.agent.call('application',id=value)
            await self.review('application',{'adapter':value,'action':'provision' if key=='app_setup' else 'publish','revision':current['revision']})
        elif key=='checkpoint':
            await self.review('checkpoint',{})
        elif key=='monitor':
            settings=await self.agent.call('settings')
            await self.agent.call('save_settings',revision=settings['revision'],changes={'enabled':not settings['enabled']})
            await self.guided('g:server')
        elif key in ('checkpoints','coverage'):
            result=await self.agent.call('checkpoint_status' if key=='coverage' else key)
            await self.text(str(result),[[self.button('بکاپ‌ها','g:backup')]])
        else:
            raise Denied('این دکمه قدیمی است. صفحه اصلی را دوباره باز کن.')


class GuidedView(discord.ui.View):
    def __init__(self,bot,rows):
        super().__init__(timeout=900)
        self.bot=bot
        flat=[button for row in rows for button in row]
        # Use selects for longer groups rather than overflowing Discord's 25 buttons.
        if len(flat)>20:
            for offset in range(0,min(len(flat),100),25):
                chunk=flat[offset:offset+25]
                menu=discord.ui.Select(placeholder='انتخاب تنظیمات | '+str(offset//25+1),
                    options=[discord.SelectOption(label=b['text'][:100],value=b['callback_data']) for b in chunk])
                async def choose(i,picker=menu):
                    await self.run(i,picker.values[0])
                menu.callback=choose;self.add_item(menu)
        else:
            for index,button in enumerate(flat):
                item=discord.ui.Button(label=button['text'][:80],row=index//4)
                async def clicked(i,action=button['callback_data']):
                    await self.run(i,action)
                item.callback=clicked;self.add_item(item)

    async def interaction_check(self,i):
        from .discord_ui import authorize
        return await authorize(self.bot,i)

    async def run(self,i,action):
        context=Context(self.bot,i)
        if action=='input':
            if not context.state.get('mode'):raise Denied('Input expired. Reopen the panel.')
            await i.response.send_modal(Input(context))
            return
        await i.response.defer(ephemeral=True)
        await context.route(action)

    async def on_error(self,i,error,item):
        from .discord_ui import respond
        await respond(i,guides.friendly_error(error))


class Input(discord.ui.Modal):
    def __init__(self,context):
        super().__init__(title='ورود اطلاعات',timeout=900)
        self.context=context
        self.mode=context.state['mode'].copy()
        self.value=discord.ui.TextInput(label='مقدار خواسته‌شده در راهنما',style=discord.TextStyle.paragraph,max_length=4000)
        self.add_item(self.value)

    async def interaction_check(self,i):
        from .discord_ui import authorize
        if i.user.id!=self.context.i.user.id:
            return False
        return await authorize(self.context.bot,i)

    async def on_submit(self,i):
        await i.response.defer(ephemeral=True)
        self.context.i=i
        if self.context.state.get('mode')!=self.mode:raise Denied('Input expired. Reopen the panel.')
        await self.context.guided_input(str(self.value))

    async def on_error(self,i,error):
        from .discord_ui import respond
        await respond(i,guides.friendly_error(error))
