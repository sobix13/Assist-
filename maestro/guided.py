"""One guided workflow shared by Telegram buttons and Discord private views."""
import copy
import json
import time

from . import guides
from .security import Denied


class GuidedFlow:
    async def guided(self, data):
        if not data.startswith('g:'):
            return False
        if (await self.agent.call('whoami')).get('role')!='superadmin':
            raise Denied('This action requires superadmin access.')
        parts=data.split(':');page=parts[1];args=parts[2:]
        if page=='answer':
            await self.guided_input(args[0])
            return True
        self.state['mode']=None
        self.save()
        home=[[self.button('صفحه اصلی','g:home')]]
        if page=='home':
            status=await self.agent.call('status')
            await self.text('مسترو | مرکز مدیریت\n\nسرور: سلامت، بکاپ و کارهای در حال اجرا\nبات‌های دیسکورد: تنظیم و مدیریت هر بات ریپ‌کارز\n\nکارهای در حال اجرا: '+str(status.get('jobs_active',0)),
                [[self.button('سرور','g:server'),self.button('بات‌های دیسکورد','g:bots')],
                 [self.button('کارهای من','jobs'),self.button('راهنمای شروع','g:help')]])
        elif page=='server':
            status=await self.agent.call('status');settings=status['settings']
            await self.text('داشبورد سرور\nپایش: '+guides.display(settings['enabled'])+
                '\nکارهای در حال اجرا: '+str(status['jobs_active'])+'\nمشکل‌های باز: '+str(len(status.get('open_incidents',[])))+
                '\nفضای آزاد دیسک: '+str(status.get('host',{}).get('disk_free_percent','؟'))+'٪ | حافظه آزاد: '+str(status.get('host',{}).get('memory_available_percent','؟'))+'٪'+
                '\n\nپایش فقط وضعیت را بررسی می‌کند. برای تغییر سرویس، نتیجه را مرور و تأیید می‌کنی.',
                [[self.button('بررسی سلامت','g:health'),self.button('مشکل‌های باز','g:issues')],
                 [self.button('اعلان‌ها','g:notifications'),self.button('بکاپ‌ها','g:backup')],
                 [self.button('زمان‌بندی','g:schedule'),self.button('ابزارهای پیشرفته','g:advanced')]]+home)
        elif page=='help':
            await self.text('از کجا شروع کنم؟\n\n۱. در «سرور» سلامت و بکاپ را بررسی کن.\n۲. در «بات‌های دیسکورد» بات موردنظر را انتخاب کن.\n۳. «راهنمای راه‌اندازی» را باز کن. هر مرحله می‌گوید چه چیزی لازم است و از کجا بگیری.\n۴. پیش از ذخیره، خلاصه تغییر را می‌بینی و تأیید می‌کنی.\n۵. نتیجه در «کارهای من» است. اگر بکاپ در حال بررسی بود، درخواست را دوباره نفرست.\n\nتوکن، آدرس توکن، نقش و کانال چیزهای متفاوتی هستند. هر فرم نوع ورودی خودش را توضیح می‌دهد.',home)
        elif page=='advanced':
            await self.text('ابزارهای پیشرفته\nتمام ابزارهای قبلی مسترو اینجا در دسترس‌اند.',
                [[self.button('سرویس‌ها','section:targets'),self.button('فهرست سرور','section:inventory')],
                 [self.button('ترمینال','section:terminal'),self.button('فایل و کد','section:files')],
                 [self.button('بازیابی و تشخیص','section:recovery'),self.button('افراد و دسترسی','section:access')],
                 [self.button('تنظیمات پیشرفته','section:setup'),self.button('اتصال‌های سفارشی','section:apps')],
                 [self.button('بسته‌ها، خروجی و بازیابی','section:data')]]+home)
        elif page=='health':
            result=await self.agent.call('check',kind='hourly')
            await self.text(guides.health_text(result),[[self.button('جزئیات فنی','g:technical'),self.button('داشبورد سرور','g:server')]])
        elif page=='technical':
            await self.text(json.dumps(await self.agent.call('status'),indent=2),home,name='maestro-status.json')
        elif page=='issues':
            status=await self.agent.call('status')
            lines=['مشکل‌های باز']
            for row in status.get('open_incidents',[]):
                lines.append(row['target']+': '+row['code']+'\n'+str(row['detail'])[:200])
            await self.text('\n\n'.join(lines) if len(lines)>1 else 'مشکل بازی ثبت نشده است.',[[self.button('بررسی تازه','g:health')]]+home)
        elif page=='notifications':
            settings=await self.agent.call('settings')
            settings={**settings,'notify_telegram':settings.get('notify_telegram',True) and settings.get('telegram_reports',True)}
            rows=[[self.button(name+': '+guides.display(settings.get(key,True)),'g:notice:'+key)] for key,name in guides.NOTICES.items()]
            await self.text('اعلان‌ها\nهر دکمه وضعیت فعلی را نشان می‌دهد. با لمس آن وضعیت عوض و روی VPS ذخیره می‌شود.\nخاموش کردن ارسال پیام، پایش سرور یا بکاپ را متوقف نمی‌کند. پاسخ دکمه‌ها و درخواست‌های خودت همیشه نمایش داده می‌شود.\nخطای ثابت تکرار نمی‌شود. رفع مشکل پس از ۱۰ دقیقه پایداری اعلام می‌شود.',rows+home)
        elif page=='notice':
            key=args[0]
            if key not in guides.NOTICES:raise Denied('Choose a notification category.')
            settings=await self.agent.call('settings')
            current=settings.get(key,True) and (settings.get('telegram_reports',True) if key=='notify_telegram' else True)
            changes={key:not current}
            if key=='notify_telegram' and changes[key]:changes['telegram_reports']=True
            await self.agent.call('save_settings',revision=settings['revision'],changes=changes)
            await self.guided('g:notifications')
        elif page=='backup':
            status=await self.agent.call('checkpoint_status')
            await self.text('بکاپ‌ها\nاتصال مخزن: '+('ثبت شده' if status.get('configured') else 'نیاز به راه‌اندازی')+
                '\nدر حال اجرا: '+guides.display(status.get('busy',False))+
                '\nپیش از تغییر سرویس، بکاپ باید ساخته و بررسی شود. خطای مخزن با خاموش کردن اعلان رفع نمی‌شود.',
                [[self.button('ساخت و بررسی بکاپ','checkpoint'),self.button('بکاپ‌های قبلی','checkpoints')],
                 [self.button('پوشش و جزئیات','coverage'),self.button('بررسی مخزن بکاپ','g:backup_check')]]+home)
        elif page=='backup_check':
            result=await self.agent.call('backup_check')
            await self.text('بررسی مخزن بکاپ\n'+('کار دیگری از مخزن استفاده می‌کند. بعداً بررسی کن.' if result.get('deferred') else 'بررسی ساختار مخزن موفق بود.' if result.get('ok') else 'بررسی مخزن موفق نبود. تغییرات سرور تا رفع این مشکل اجرا نمی‌شوند.')+'\n'+str(result.get('detail',''))[-2200:],[[self.button('بکاپ‌ها','g:backup')]])
        elif page=='schedule':
            settings=await self.agent.call('settings')
            await self.text('زمان‌بندی\nپایش: '+guides.display(settings['enabled'])+'\nخلاصه روزانه ساعت '+str(settings['daily_hour'])+
                ' | '+settings['timezone'],[[self.button('تغییر ساعت خلاصه','g:hour'),self.button('روشن / خاموش کردن پایش','monitor')]]+home)
        elif page=='hour':
            settings=await self.agent.call('settings')
            await self.set_mode('guided_hour',revision=settings['revision'])
            await self.text('ساعت گزارش روزانه را از ۰ تا ۲۳ بفرست. مثال: ۱۸ برای ساعت شش عصر. منطقه زمانی فعلی همان منطقه ثبت‌شده سرور است.',home)
        elif page=='connection':
            adapter=self.state.get('guided_adapter')
            fields=await self.agent.call('connections',adapter=adapter)
            field=next((v for v in fields if v['field']==args[0]),None)
            if not field:raise Denied('Choose an enrolled connection field.')
            await self.set_mode('guided_connection',adapter=adapter,field=field['field'])
            await self.text(field['label']+'\nمقدار کامل را از تیم بک‌اند یا ارائه‌دهنده API بگیر و در همین گفتگوی خصوصی بفرست. برای کلید وبهوک، هر دو سمت باید دقیقاً یک مقدار داشته باشند.\nدامنه HTTPS و تنظیم مسیر شبکه باید روی سرور آماده باشد.\nپس از ذخیره، راه‌اندازی دوباره بات لازم است. انصراف: /cancel',home)
        elif page=='bots':
            await self.text('بات‌های دیسکورد\nبات موردنظر را انتخاب کن. هر بات تنظیمات و راهنمای خودش را دارد.',
                [[self.button(name,'g:bot:'+key)] for key,name in guides.BOT_NAMES.items()]+home)
        elif page=='service':
            key,action=args
            if key not in guides.BOT_NAMES or action not in ('start','stop','restart'):raise Denied('Choose a bot service action.')
            target=next(t for t in await self.agent.call('targets') if t['id']==key)
            await self.review('service',{'unit':target['unit'],'action':action})
        elif page in ('bot','setup','group','field','choice','toggle','enable','native','raffles','raffle_new','raffle_publish','list','list_add','list_edit','list_remove','list_toggle'):
            await self.bot_page(page,args)
        else:
            raise Denied('Unknown guided page. Open /panel again.')
        return True

    async def bot_page(self,page,args):
        key=args[0]
        if key not in guides.BOT_NAMES:raise Denied('Choose a Rip Cars bot.')
        back=[[self.button('داشبورد بات','g:bot:'+key),self.button('بات‌های دیسکورد','g:bots')]]
        adapters=await self.agent.call('applications')
        spec=next((v for v in adapters if v['target']==key and v['kind'].startswith(key+'-')),None)
        if not spec:
            await self.text(guides.BOT_NAMES[key]+'\n\nاتصال تنظیمات هنوز آماده نیست. فایل‌های نسخه سازگار، توکن بات، شناسه سرور و دیتابیس بات باید روی VPS ثبت شده باشند.\nبا «بررسی سرویس» جزئیات را ببین. راه‌اندازی اتصال موجود، هیچ نقش یا کانالی را از بات دیگر نمی‌گیرد.',
                [[self.button('بررسی سرویس','target:'+key),self.button('راهنمای اتصال','g:help')],
                 [self.button('بات‌های دیسکورد','g:bots')]])
            return
        current=await self.agent.call('application',id=spec['id']);cfg=current['settings']
        self.state['guided_adapter']=spec['id'];self.save()
        if page=='bot':
            rows=[[self.button('راهنمای راه‌اندازی','g:setup:'+key),self.button('بررسی سرویس','target:'+key)]]
            rows += [[self.button(name,'g:group:'+key+':'+group)] for group,name,_ in guides.GROUPS[key]]
            if key=='verifier':rows.append([self.button('اتصال API و سرویس‌ها','g:group:verifier:connections')])
            if key=='raffle':rows.append([self.button('قرعه‌کشی‌های من','g:raffles:raffle'),self.button('قرعه‌کشی جدید','g:raffle_new:raffle')])
            if key=='verifier':rows.append([self.button('ساخت کانال و نقش','app_setup:'+spec['id']),self.button('انتشار پنل','app_publish:'+spec['id'])])
            rows.append([self.button('روشن کردن سرویس','g:service:'+key+':start'),self.button('راه‌اندازی دوباره','g:service:'+key+':restart')])
            rows.append([self.button('روشن / متوقف کردن ورود','g:enable:'+key),self.button('پنل بومی دیسکورد','g:native:'+key)])
            await self.text(guides.BOT_NAMES[key]+'\n\nوضعیت تنظیمات: '+guides.display(cfg.get('enabled',cfg.get('guard',{}).get('enabled',False)))+
                ('\nاولین اجرا هنوز کامل نشده است. اتصال‌های لازم را وارد کن، سپس سرویس را روشن کن.' if current.get('initialized') is False else '')+
                '\nبرای شروع، «راهنمای راه‌اندازی» را بزن. برای تغییر یک مورد، بخش همان تنظیم را باز کن.',rows+back[-1:])
        elif page=='setup':
            descriptions={
             'verifier':'۱. کانال‌ها و نقش عضو Gate را انتخاب کن.\n۲. فقط داده‌هایی را روشن کن که تیم پروژه منبعشان را داده است.\n۳. آدرس توکن یا مجموعه رسمی را وارد کن.\n۴. اتصال API و سرویس‌ها را تکمیل کن.\n۵. شرط نقش‌ها را بساز. کانال‌ها و نقش‌های اختصاصی وریفایر را ایجاد کن، پنل را منتشر و سپس فعال کن.\nبرای وصل شدن وب، دامنه HTTPS و مسیر شبکه باید از قبل آماده باشد.',
             'raffle':'۱. نقش اعضای Gate، مدیران، کانال قرعه‌کشی و گزارش خصوصی را انتخاب کن.\n۲. ورود شرکت‌کننده را روشن کن.\n۳. قرعه‌کشی جدید را با عنوان، جایزه، زمان و تعداد برنده بساز.\n۴. پیش‌نویس را ببین و با تأیید جداگانه منتشر کن.\nشرط‌ها بر اساس نقش دیسکورد هستند. موجودی کیف پول را وریفایر تأیید می‌کند.',
             'crew':'۱. نقش اعضا، نقش مدیران و کانال گزارش خصوصی را انتخاب کن.\n۲. تنظیمات محافظت و دامنه‌های مجاز را مرور کن. حالت مشاهده فقط گزارش می‌دهد.\n۳. برای تیکت، کانال پنل و دسته تیکت‌های باز و بسته را مشخص کن.\n۴. متن‌ها را تنظیم و پنل تیکت را از داشبورد خود Crew منتشر کن.',
             'gate':'۱. نام پروژه و لینک رسمی را تنظیم کن.\n۲. سؤال‌های ورود و متن قوانین را مرور کن.\n۳. تنظیمات کپچا را بررسی کن.\n۴. برای اعمال تغییرات روی کانال‌ها و پیام‌های عمومی، انتشار را از داشبورد خود Gate اجرا کن.\nGate مالک ورود اعضا و نقش Rippers باقی می‌ماند.'}
            await self.text('راهنمای '+guides.BOT_NAMES[key]+'\n\n'+descriptions[key],
                [[self.button(name,'g:group:'+key+':'+group)] for group,name,_ in guides.GROUPS[key]]+back)
        elif page=='group':
            group=args[1]
            if group=='connections' and key=='verifier':
                state=await self.agent.call('connections',adapter=spec['id'])
                await self.text('اتصال‌های وریفایر\nلینک API، کلید دسترسی و آدرس توکن با هم فرق دارند. لینک و کلید را از تیم بک‌اند یا ارائه‌دهنده سولانا بگیر. مقادیر محرمانه در خلاصه‌ها نشان داده نمی‌شوند.',
                    [[self.button(v['label']+': '+('ثبت شده' if v['configured'] else 'لازم است' if v['required'] else 'اختیاری'),'g:connection:'+v['field'])] for v in state]+back)
                return
            paths=guides.fields(key,group,cfg)
            if not paths:raise Denied('Unknown settings group.')
            await self.text('تنظیمات | '+next(name for g,name,_ in guides.GROUPS[key] if g==group)+'\nهر دکمه مقدار فعلی را نشان می‌دهد.',
                [[self.button((guides.label(path)+': '+guides.display(guides.get_value(cfg,path)))[:60],'g:field:'+key+':'+path)] for path in paths]+back)
        elif page in ('list','list_add','list_edit','list_remove','list_toggle'):
            path=args[1];items=guides.get_value(cfg,path)
            if path not in ('rules','questions','sources') or not isinstance(items,list):raise Denied('Choose a supported list.')
            index=int(args[2]) if len(args)>2 and page!='list' else None
            if index is not None and not 0<=index<len(items):raise Denied('List changed. Reopen the panel.')
            if page=='list':
                rows=[[self.button('افزودن مورد','g:list_add:'+key+':'+path)]]
                offset=int(args[2]) if len(args)>2 else 0
                if offset<0:raise Denied('Choose a list page.')
                for idx,item in enumerate(items[offset*10:offset*10+10],offset*10):
                    name=str(item.get('name',item.get('label',item.get('id','مورد '+str(idx+1)))))[:25]
                    rows.append([self.button('ویرایش '+name,'g:list_edit:'+key+':'+path+':'+str(idx)),self.button('حذف '+name,'g:list_remove:'+key+':'+path+':'+str(idx))])
                    if 'enabled' in item:rows.append([self.button(name+': '+guides.display(item['enabled']),'g:list_toggle:'+key+':'+path+':'+str(idx))])
                if offset:rows.append([self.button('قبلی','g:list:'+key+':'+path+':'+str(offset-1))])
                if len(items)>(offset+1)*10:rows.append([self.button('بعدی','g:list:'+key+':'+path+':'+str(offset+1))])
                await self.text(guides.label(path)+'\nموارد قبلی را ویرایش کن یا یک مورد تازه اضافه کن. حذف هم پیش از اجرا تأیید می‌خواهد.',rows+back)
            elif page in ('list_remove','list_toggle'):
                items=copy.deepcopy(items)
                if page=='list_remove':items.pop(index)
                elif 'enabled' in items[index]:items[index]['enabled']=not items[index]['enabled']
                else:raise Denied('This item has no on/off setting.')
                await self.review('application',{'adapter':spec['id'],'revision':current['revision'],'action':'settings','changes':{path:items}})
            else:
                await self.set_mode('guided_list',adapter=spec['id'],revision=current['revision'],field=path,current=items,step=0,answers=[],index=index)
                await self.list_prompt()
        elif page in ('field','choice','toggle','enable'):
            path=args[1] if page!='enable' else 'enabled' if 'enabled' in cfg else 'guard.enabled'
            value=guides.get_value(cfg,path)
            if page in ('toggle','enable'):
                if type(value) is not bool:raise Denied('Choose an on/off field.')
                if not value and (key=='gate' and path=='enabled' or key=='crew' and path in ('guard.enabled','tickets.enabled')):
                    command='/gate panel' if key=='gate' else '/crew panel'
                    await self.text('برای روشن کردن این بخش، در دیسکورد '+command+' را باز کن. آنجا دسترسی‌ها و آماده بودن پنل عمومی بررسی می‌شوند؛ سپس فعال‌سازی را تأیید کن.',back)
                    return
                await self.review('application',{'adapter':spec['id'],'action':'settings','revision':current['revision'],'changes':guides.change_value(cfg,path,not value)})
            elif page=='choice':
                if args[2] not in guides.CHOICES.get(path,[]):raise Denied('Choose a supported option.')
                await self.review('application',{'adapter':spec['id'],'action':'settings','revision':current['revision'],'changes':guides.change_value(cfg,path,args[2])})
            elif type(value) is bool:
                await self.text(guides.label(path)+': '+guides.display(value),[[self.button('تغییر وضعیت','g:toggle:'+key+':'+path)]]+back)
            elif path in guides.CHOICES:
                await self.text(guides.label(path)+'\nمنبع داده را انتخاب کن. انتخاب منبع به معنی اتصال موفق آن نیست.',
                    [[self.button(guides.CHOICE_NAMES[v],'g:choice:'+key+':'+path+':'+v)] for v in guides.CHOICES[path]]+back)
            elif path in ('rules','questions','sources'):
                await self.bot_page('list',[key,path])
            else:
                await self.set_mode('guided_field',adapter=spec['id'],revision=current['revision'],field=path,current=value,cfg=cfg)
                await self.text(guides.input_help(path,value),back)
        elif page=='native':
            command={'gate':'/gate panel','crew':'/crew panel','raffle':'/raffle panel','verifier':'/verifier'}[key]
            await self.text('انتشار پیام‌های عمومی\n'+('از دکمه «انتشار پنل» در همین داشبورد استفاده کن.' if key=='verifier' else 'پنل بومی بات را در دیسکورد با '+command+' باز کن. تنظیمات ذخیره‌شده در مسترو حفظ می‌شوند. انتشار پیام‌ها و مدیریت نقش‌ها توسط خود بات انجام می‌شود.') ,back)
        elif page=='raffle_new':
            await self.set_mode('guided_raffle',adapter=spec['id'],revision=current['revision'],step=0,answers=[],template=cfg['templates']['general'])
            await self.raffle_prompt()
        elif page=='raffles':
            rows=await self.agent.call('raffles',adapter=spec['id'])
            await self.text('قرعه‌کشی‌ها\n'+('\n'.join('#'+str(r['id'])+' | '+r['title']+' | '+r['status'] for r in rows) or 'هنوز قرعه‌کشی ثبت نشده است.'),
                [[self.button('مرور انتشار #'+str(r['id']),'g:raffle_publish:raffle:'+str(r['id']))] for r in rows if r['status']=='draft']+back)
        elif page=='raffle_publish':
            await self.review('application',{'adapter':spec['id'],'action':'raffle_publish','revision':current['revision'],'changes':{'raffle_id':int(args[1])}})

    async def guided_input(self,text):
        mode=self.state.get('mode') or {};kind=mode.get('name','')
        if not kind.startswith('guided_'):return False
        if mode.get('at',time.time())+900<time.time():raise Denied('Input expired. Reopen the panel.')
        if kind=='guided_hour':
            hour=int(text)
            if not 0<=hour<=23:raise ValueError('ساعت باید بین ۰ و ۲۳ باشد.')
            await self.agent.call('save_settings',revision=mode['revision'],changes={'daily_hour':hour})
            self.state['mode']=None;self.save();await self.guided('g:schedule')
        elif kind=='guided_field':
            value=guides.parse_input(mode['field'],mode['current'],text)
            await self.review('application',{'adapter':mode['adapter'],'revision':mode['revision'],'action':'settings','changes':guides.change_value(mode['cfg'],mode['field'],value)})
            self.state['mode']=None;self.save()
        elif kind=='guided_connection':
            prepared=await self.agent.call('prepare_connection',adapter=mode['adapter'],field=mode['field'],value=text.strip())
            self.state['mode']=None;self.save()
            await self.present_review(prepared,'ذخیره اتصال '+mode['field']+'\nمقدار محرمانه نمایش داده نمی‌شود.')
        elif kind=='guided_list':
            if mode['field']=='rules' and mode['step']==1 and text.strip() not in ('token_balance','asset_count','platform_points','deposit_points'):
                raise ValueError('نوع شرط را از گزینه‌های راهنما انتخاب کن.')
            if mode['field']=='rules' and mode['step']==2:
                from .adapters.verifier_config import decimal
                decimal(text.strip())
            if mode['field']=='rules' and mode['step']==3:int(text)
            mode['answers'].append(text.strip());mode['step']+=1
            try:await self.list_prompt()
            except Exception:
                mode['answers'].pop();mode['step']-=1;self.state['mode']=mode;self.save();raise
        elif kind=='guided_raffle':
            if mode['step']==3:
                from .adapters.raffle_config import duration
                seconds=duration(text)
                if not 60<=seconds<=7776000:raise ValueError('مدت قرعه‌کشی باید از یک دقیقه تا ۹۰ روز باشد.')
            if mode['step']==4 and not 1<=int(text)<=25:raise ValueError('تعداد برنده باید بین ۱ و ۲۵ باشد.')
            if mode['step']==5 and text.strip()!='-':
                if any(not 0<int(v)<2**63 for v in text.split()):raise ValueError('شناسه نقش معتبر نیست.')
            mode['answers'].append(text.strip());mode['step']+=1
            try:await self.raffle_prompt()
            except Exception:
                mode['answers'].pop();mode['step']-=1;self.state['mode']=mode;self.save();raise
        return True

    async def list_prompt(self):
        mode=self.state['mode']
        if mode['field']=='rules':
            prompts=['نام نقش واجد شرایط را بفرست. مثال: CARS Holder',
                     'نوع شرط را بفرست: token_balance برای موجودی CARS، asset_count برای تعداد دارایی، platform_points برای امتیاز سایت، deposit_points برای واریز.',
                     'حداقل مقدار لازم را بفرست. مثال: 100',
                     'شناسه نقش موجود دیسکورد را بفرست. برای ساخت نقش اختصاصی بعداً، عدد 0 را بفرست.']
        elif mode['field']=='sources':
            prompts=['آدرس خزانه رسمی دریافت واریز را از تیم پروژه بگیر و بفرست. این آدرس عمومی سولانا است، نه کلید خصوصی.',
                     'آدرس توکن پرداخت را بفرست. مثال: آدرس رسمی USDC روی سولانا. آن را از تیم پروژه تأیید بگیر.',
                     'تعداد رقم اعشار این توکن چیست؟ عدد را از مشخصات رسمی توکن بگیر. برای USDC معمولاً 6 است.',
                     'ارزش یک توکن پرداخت به دلار را بفرست. مثال: 1. این مقدار مبنای امتیاز واریز است و نرخ بازار خودکار نیست.']
        else:
            prompts=['عنوان کوتاه سؤال ورود را بفرست. مثال: ماشین موردعلاقه', 'متن کامل سؤال را بفرست. مثال: ماشین موردعلاقه تو چیست؟']
        if mode['step']<len(prompts):
            buttons=[]
            if mode['field']=='rules' and mode['step']==1:
                buttons=[[self.button(name,'g:answer:'+value)] for value,name in [('token_balance','موجودی توکن CARS'),('asset_count','تعداد دارایی'),('platform_points','امتیاز سایت'),('deposit_points','امتیاز واریز')]]
            self.save();await self.text('مرحله '+str(mode['step']+1)+' از '+str(len(prompts))+'\n'+prompts[mode['step']]+'\nموارد قبلی حفظ می‌شوند. انصراف: /cancel',buttons)
            return
        a=mode['answers'];items=copy.deepcopy(mode['current'])
        if mode['field']=='rules':
            items.append({'key':'role_'+str(int(time.time()*1000)),'name':a[0],'role_id':int(a[3]),'kind':a[1],
                          'threshold':a[2],'selector':{},'enabled':True})
        elif mode['field']=='sources':
            items.append({'id':'source_'+str(int(time.time()*1000)),'program_ids':[],'treasury_owners':[a[0]],
                          'token_accounts':[],'mints':{a[1]:{'decimals':int(a[2]),'usd_per_token':a[3]}}})
        else:
            items.append({'id':'q_'+str(int(time.time()*1000)),'label':a[0],'prompt':a[1],'enabled':True})
        if mode.get('index') is not None:
            replacement=items.pop()
            original=mode['current'][mode['index']]
            # Preserve identifiers used by the native bot, plus advanced selectors.
            identity='key' if mode['field']=='rules' else 'id'
            replacement[identity]=original[identity]
            if mode['field']=='rules':replacement['selector']=original['selector'];replacement['enabled']=original['enabled']
            if mode['field']=='questions':replacement['enabled']=original['enabled']
            if mode['field']=='sources':
                replacement['program_ids']=original['program_ids'];replacement['token_accounts']=original['token_accounts']
                replacement['treasury_owners']=[a[0],*original['treasury_owners'][1:]]
                previous_mints=copy.deepcopy(original['mints'])
                previous_mints.pop(next(iter(previous_mints)),None)
                replacement['mints']={**previous_mints,**replacement['mints']}
            items[mode['index']]=replacement
        await self.review('application',{'adapter':mode['adapter'],'revision':mode['revision'],'action':'settings','changes':{mode['field']:items}})
        self.state['mode']=None;self.save()

    async def raffle_prompt(self):
        mode=self.state['mode'];prompts=['عنوان قرعه‌کشی را بفرست.', 'جایزه دقیق چیست؟',
             'توضیح کوتاه شرکت در قرعه‌کشی را بفرست.', 'مدت قرعه‌کشی را بفرست. مثال: 24h یا 3d',
             'چند برنده انتخاب شوند؟ عدد ۱ تا ۲۵.', 'شناسه نقش واجد شرایط را بفرست. برای عموم اعضای تأییدشده، - بفرست.']
        if mode['step']<len(prompts):
            self.save();await self.text('قرعه‌کشی جدید | مرحله '+str(mode['step']+1)+' از ۶\n'+prompts[mode['step']]+'\nانصراف: /cancel')
            return
        from .adapters.raffle_config import duration,validate_spec
        a=mode['answers'];spec=copy.deepcopy(mode['template'])
        spec.update(title=a[0],prize=a[1],description=a[2],duration=duration(a[3]),starts_in=0,winners=int(a[4]))
        spec['rules']['any_roles']=[] if a[5]=='-' else [int(x) for x in a[5].split()]
        validate_spec(spec)
        await self.review('application',{'adapter':mode['adapter'],'revision':mode['revision'],'action':'raffle_create','changes':{'category':'general',**spec}})
        self.state['mode']=None;self.save()
