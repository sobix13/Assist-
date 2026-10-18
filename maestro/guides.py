"""Shared, plain-language screens and typed inputs for both private interfaces."""
import copy
import re

BOT_NAMES = {'gate': 'Gate | ورود اعضا', 'crew': 'Crew | پشتیبانی و مدیریت',
             'raffle': 'Waffle | قرعه‌کشی', 'verifier': 'Verifier | تأیید دارایی'}
NOTICES = {'notify_telegram': 'ارسال در تلگرام', 'notify_discord': 'ارسال در دیسکورد',
           'notify_alerts': 'خطاهای مهم و منابع سرور', 'notify_recovery': 'رفع مشکل و بازیابی',
           'notify_jobs': 'نتیجه کارهای درخواستی', 'notify_daily': 'خلاصه روزانه'}
GROUPS = {
    'verifier': [('identity', 'کانال‌ها و نقش اعضا', ['member_role','verification_channel','log_channel','rip_channel']),
                 ('sources', 'اتصال داده‌ها', ['wallet_linking','account_linking','balance_mode','asset_mode','deposit_mode','platform_points','rip_feed','chain_webhook','mint','collections','platform_connect_url']),
                 ('rules', 'شرط نقش و منابع واریز', ['rules','sources']), ('texts', 'متن‌های عمومی', ['texts']),
                 ('timing', 'زمان بررسی', ['sync_seconds','max_wallets','snapshot_max_age'])],
    'raffle': [('identity', 'کانال‌ها و مدیران', ['member_role','channel','log_channel','manager_roles']),
               ('texts', 'متن‌های عمومی', ['texts']), ('timing', 'حدود و زمان‌ها', ['max_active','max_entries','panel_update_seconds'])],
    'crew': [('identity', 'کانال‌ها و مدیران', ['member_role','log_channel','moderator_roles']),
             ('guard', 'محافظت گفتگو', ['guard.enabled','guard.mode','guard.allowed_domains','guard.blocked_domains','guard.words','guard.mention_limit','guard.flood_count']),
             ('tickets', 'تیکت پشتیبانی', ['tickets.enabled','tickets.panel_channel','tickets.open_category','tickets.closed_category','tickets.create_cooldown_seconds']),
             ('texts', 'متن‌های عمومی', ['texts'])],
    'gate': [('entry', 'ورود اعضا', ['enabled','captcha_length','captcha_ttl','max_attempts','lock_seconds']),
             ('texts', 'متن‌های عمومی', ['texts']), ('questions', 'سؤال‌های ورود', ['questions']),
             ('brand', 'نام و لینک پروژه', ['brand','website'])],
}
LABELS = {
 'member_role':'نقش اعضای تأییدشده', 'channel':'کانال قرعه‌کشی', 'log_channel':'کانال گزارش خصوصی',
 'verification_channel':'کانال اتصال کیف پول', 'rip_channel':'کانال نمایش باز شدن پک‌ها',
 'manager_roles':'نقش‌های مدیر قرعه‌کشی', 'moderator_roles':'نقش‌های تیم پشتیبانی',
 'wallet_linking':'اتصال کیف پول', 'account_linking':'اتصال حساب سایت', 'balance_mode':'منبع موجودی توکن',
 'asset_mode':'منبع مجموعه دارایی‌ها', 'deposit_mode':'منبع واریزها', 'platform_points':'امتیازهای سایت',
 'rip_feed':'نمایش باز شدن پک‌ها', 'chain_webhook':'دریافت رویداد زنجیره', 'mint':'آدرس توکن CARS',
 'collections':'آدرس مجموعه‌های رسمی', 'platform_connect_url':'صفحه اتصال حساب سایت',
 'sync_seconds':'فاصله بررسی‌ها به ثانیه', 'max_wallets':'حداکثر کیف پول هر عضو',
 'snapshot_max_age':'حداکثر عمر داده به ثانیه', 'enabled':'فعال بودن', 'brand':'نام پروژه', 'website':'لینک سایت',
 'guard.enabled':'محافظت خودکار', 'guard.mode':'روش محافظت', 'guard.words':'کلمه‌ها و عبارت‌های ممنوع',
 'guard.allowed_domains':'دامنه‌های مجاز', 'guard.blocked_domains':'دامنه‌های مسدود',
 'guard.mention_limit':'حداکثر منشن در یک پیام', 'guard.flood_count':'تعداد پیام برای تشخیص اسپم',
 'tickets.enabled':'باز کردن تیکت', 'tickets.panel_channel':'کانال دکمه تیکت',
 'tickets.open_category':'دسته تیکت‌های باز', 'tickets.closed_category':'دسته تیکت‌های بسته',
 'tickets.create_cooldown_seconds':'فاصله ساخت تیکت به ثانیه',
 'captcha_length':'تعداد حروف کپچا', 'captcha_ttl':'مهلت کپچا به ثانیه',
 'max_attempts':'تعداد تلاش مجاز', 'lock_seconds':'زمان انتظار پس از خطا به ثانیه',
 'max_active':'حداکثر قرعه‌کشی هم‌زمان', 'max_entries':'حداکثر شرکت‌کننده',
 'panel_update_seconds':'فاصله تازه‌سازی پیام به ثانیه', 'rules':'شرط‌های دریافت نقش', 'questions':'سؤال‌های ورود',
 'texts.title':'عنوان پیام', 'texts.description':'توضیح پیام', 'texts.welcome':'پیام خوشامد',
 'texts.welcome_title':'عنوان خوشامد', 'texts.rules':'قوانین سرور', 'texts.faq':'پرسش‌های رایج',
 'texts.official_links':'لینک‌های رسمی', 'texts.ticket_title':'عنوان پنل تیکت',
 'texts.ticket_description':'راهنمای تیکت', 'texts.ticket_welcome':'پیام آغاز تیکت', 'sources':'منابع رسمی واریز',
 'title':'عنوان', 'prize':'جایزه', 'description':'توضیح', 'duration':'مدت به ثانیه',
 'starts_in':'تأخیر شروع به ثانیه', 'winners':'تعداد برنده', 'raffle_id':'شماره قرعه‌کشی', 'category':'نوع قرعه‌کشی',
}
CHOICES = {'balance_mode':['off','rpc','platform'], 'asset_mode':['off','das','platform'],
           'deposit_mode':['off','rpc','platform'], 'guard.mode':['observe','protect']}
CHOICE_NAMES = {'off':'خاموش','rpc':'مستقیم از سولانا','das':'ارائه‌دهنده مجموعه‌های سولانا',
                'platform':'از API سایت ریپ‌کارز','observe':'فقط گزارش بده','protect':'محافظت و اجرای قوانین'}


def get_value(cfg, path):
    for part in path.split('.'):
        cfg = cfg[part]
    return cfg


def change_value(cfg, path, value):
    result = copy.deepcopy(cfg)
    node = result
    parts = path.split('.')
    for part in parts[:-1]:
        node = node[part]
    if parts[-1] not in node:
        raise ValueError('این تنظیم وجود ندارد. صفحه بات را دوباره باز کن.')
    node[parts[-1]] = value
    # Application adapters validate the entire candidate, including unchanged fields.
    return {parts[0]: result[parts[0]]}


def fields(bot, group, cfg):
    paths = next((paths for key, _, paths in GROUPS[bot] if key == group), [])
    result = []
    for path in paths:
        value = get_value(cfg, path)
        if isinstance(value, dict):
            result.extend(path + '.' + key for key in value)
        else:
            result.append(path)
    return result


def label(path):
    return LABELS.get(path, path.removeprefix('texts.').replace('_',' '))


def display(value):
    if type(value) is bool:
        return 'روشن' if value else 'خاموش'
    if value in (None, '', 0):
        return 'هنوز وارد نشده'
    if isinstance(value, (list, dict)):
        return str(len(value)) + ' مورد'
    return str(value)[:180]


def input_help(path, current):
    title = label(path) + '\nمقدار فعلی: ' + display(current)
    if path in ('mint','collections'):
        help_text = 'آدرس رسمی سولانا را از تیم پروژه بگیر. آدرس کیف پول شخصی جای آدرس توکن یا مجموعه نیست.'
    elif path.endswith(('role','channel','category')) or path in ('manager_roles','moderator_roles'):
        help_text = 'شناسه را از Discord با Copy ID بگیر. در Settings > Advanced گزینه Developer Mode را روشن کن، سپس روی نقش یا کانال نگه دار. برای چند شناسه بین آن‌ها فاصله بگذار.'
    elif path == 'guard.words':
        help_text = 'هر کلمه یا عبارت را در یک خط بفرست. تطبیق دقیق است. برای پاک کردن فهرست، - بفرست.'
    elif isinstance(current, list):
        help_text = 'هر مقدار را در یک خط یا با فاصله وارد کن. برای پاک کردن فهرست، - بفرست.'
    elif isinstance(current, int):
        help_text = 'یک عدد کامل وارد کن. اگر عنوان به ثانیه است، یک ساعت برابر 3600 ثانیه است.'
    else:
        help_text = 'متن کامل را بفرست. اگر لینک خواسته شده، لینک https را از تیم پروژه بگیر.'
    return title + '\n\n' + help_text + '\nانصراف: /cancel'


def parse_input(path, current, text):
    text = text.strip()
    if len(text) > 6000 or '\x00' in text:
        raise ValueError('ورودی بیش از حد طولانی است.')
    if path == 'guard.words':
        return [{'text': line.strip(), 'mode':'exact'} for line in text.splitlines() if line.strip()] if text != '-' else []
    if type(current) is int:
        return int(text)
    if type(current) is float:
        return float(text)
    if isinstance(current, list):
        parts = re.split(r'[\s,،]+', text) if text != '-' else []
        return [int(x) for x in parts if x] if path.endswith('roles') else [x for x in parts if x]
    if type(current) is bool:
        if text.lower() not in ('true','false','on','off','روشن','خاموش'):
            raise ValueError('از دکمه روشن یا خاموش استفاده کن.')
        return text.lower() in ('true','on','روشن')
    return '' if text == '-' else text


def friendly_error(error):
    raw = str(error)
    if 'ownership' in raw.lower() or 'another controller' in raw:
        return 'یک نقش یا کانال مشترک نیاز به بازبینی مالکیت دارد. از داشبورد خود Gate یا بات مالک، تغییرهای دستی را بررسی کن. پس از تأیید مالکیت دوباره ادامه بده.\nجزئیات: '+raw[:500]
    if 'PrivilegedIntents' in raw or 'privileged intents' in raw.lower():
        return 'مجوز لازم بات در Discord Developer Portal فعال یا تأیید نشده است. مسئول بات باید همان مجوز را تکمیل کند. راه‌اندازی دوباره این مجوز را تغییر نمی‌دهد.'
    if 'Missing Access' in raw or '50001' in raw:
        return 'بات به سرور انتخاب‌شده دسترسی ندارد. ورود همان بات، شناسه سرور و مجوز اتصال آن را بررسی کن.'
    if 'integrity check failed' in raw:
        return 'بررسی بکاپ موفق نبود و تغییر اجرا نشد. از سرور > بکاپ‌ها، جزئیات بررسی را ببین.\n' + raw[-800:]
    if 'already running' in raw or 'already active' in raw:
        return 'یک بررسی یا کار دیگر در حال اجراست. از «کارهای من» وضعیتش را ببین و سپس دوباره امتحان کن.'
    if 'changed' in raw or 'expired' in raw or 'outdated' in raw:
        return 'این صفحه قدیمی شده است. صفحه مربوط را دوباره باز کن تا اطلاعات تازه و تأیید جدید دریافت کنی.'
    if 'superadmin' in raw or 'delegat' in raw or 'access' in raw.lower():
        return 'این کار در محدوده دسترسی حساب تو نیست. مالک مسترو باید دسترسی مناسب را ثبت کند.'
    return 'این مرحله انجام نشد. ورودی را بررسی کن یا «راهنما» را باز کن.\nجزئیات: ' + raw[:700]


def issue_help(code):
    return {'unit_down':'سرویس مورد انتظار روشن نیست. از داشبورد بات، سلامت و گزارش آن را بررسی کن.',
            'database_failed':'بررسی دیتابیس موفق نبود. پیش از تغییر داده‌ها، جزئیات و بکاپ را بررسی کن.',
            'database_review':'بررسی دیتابیس کامل نشد. این پیام تأیید خرابی یا تأیید سلامت نیست.',
            'checkpoint_failed':'بکاپ نیاز به بررسی دارد. از سرور > بکاپ‌ها > بررسی مخزن بکاپ، جزئیات را ببین.',
            'resource_pressure':'فضای دیسک یا حافظه سرور کم است. داشبورد سرور و جزئیات منابع را بررسی کن.',
            'worker_unhealthy':'یکی از پردازش‌های بات برای مدتی پاسخ سالم نداده است. گزارش همان بات را بررسی کن.',
            'endpoint_unhealthy':'پاسخ سلامت سرویس دریافت نشد. وضعیت سرویس و آدرس اتصال آن را بررسی کن.'}.get(code,'این مورد نیاز به بررسی دارد. جزئیات از داشبورد سرور در دسترس است.')


def health_text(result):
    if 'targets' not in result:
        return 'وضعیت مسترو\nکارهای در حال اجرا: ' + str(result.get('jobs_active',0)) + '\nمشکل‌های باز: ' + str(len(result.get('open_incidents',[])))
    lines = ['نتیجه بررسی']
    for target in result.get('targets',[]):
        state = target.get('fields',{}).get('ActiveState','نامشخص')
        lines.append(target['id'] + ': ' + ('بررسی‌ها موفق بود' if target.get('ok') else 'نیاز به بررسی') + ' | سرویس: ' + state)
        for issue in target.get('issues',[])[:4]:
            lines.append('  ' + issue_help(issue['code']))
    host = result.get('host',{})
    if host:
        lines.append('فضای آزاد دیسک: ' + str(host.get('disk_free_percent','؟')) + '٪ | حافظه آزاد: ' + str(host.get('memory_available_percent','؟')) + '٪')
    return '\n'.join(lines)[:3200]


def review_text(kind, payload):
    if kind == 'application':
        action = payload.get('action','settings')
        title = {'settings':'ذخیره تنظیمات','provision':'ساخت کانال‌ها و نقش‌های لازم','publish':'انتشار پنل عمومی',
                 'raffle_create':'ساخت پیش‌نویس قرعه‌کشی','raffle_publish':'انتشار قرعه‌کشی'}.get(action,action)
        lines = [title + ' | ' + str(payload['adapter'])]
        for path, value in payload.get('plan',{}).get('preview_changes',payload.get('changes',{})).items():
            if isinstance(value,list):
                shown='، '.join(str(v.get('name',v.get('label',v.get('id',v.get('text','مورد'))))) if isinstance(v,dict) else str(v) for v in value) or 'فهرست خالی'
            elif isinstance(value,dict):
                shown='، '.join(label(k)+': '+display(v) for k,v in value.items())
            else:shown=display(value)
            lines.append(label(path) + ': ' + shown[:1100])
        return '\n'.join(lines)
    if kind == 'service':
        return {'restart':'راه‌اندازی دوباره','start':'روشن کردن','stop':'خاموش کردن'}.get(payload['action'],payload['action']) + ' ' + payload['unit']
    if kind == 'checkpoint':
        return 'ساخت و بررسی بکاپ از مسیرها و دیتابیس‌های ثبت‌شده'
    return None


def changed_fields(before, after, prefix=''):
    result={}
    for key,value in after.items():
        path=(prefix+'.'+key).lstrip('.')
        old=before.get(key)
        if isinstance(value,dict) and isinstance(old,dict):result.update(changed_fields(old,value,path))
        elif value!=old:result[path]=value
    return result


def job_text(job):
    stage = job.get('operation',{}).get('state',job.get('state'))
    names = {'queued':'در صف اجرا','backing_up':'در حال ساخت و بررسی بکاپ پیش از تغییر',
             'running':'در حال اجرا','completed':'انجام شد','failed':'انجام نشد',
             'cancelled':'لغو شد','interrupted':'اجرا قطع شد','verifying':'در حال بررسی نتیجه'}
    lines = ['کار ' + job['id'][:8], names.get(stage,stage or 'نامشخص')]
    if stage == 'backing_up':
        lines.append('این مرحله بسته به اندازه بکاپ چند دقیقه زمان می‌برد. درخواست را دوباره نفرست.')
    if job.get('state') == 'failed':
        lines.append(friendly_error(job.get('output','')))
    outcome=job.get('operation',{}).get('data',{}).get('application',{})
    if outcome.get('raffle_id'):
        lines.append('شماره قرعه‌کشی: '+str(outcome['raffle_id']))
        if outcome.get('status')=='draft':lines.append('پیش‌نویس ساخته شد. از داشبورد Waffle آن را مرور و منتشر کن.')
    connection=job.get('operation',{}).get('data',{}).get('connection',{})
    if connection.get('detail'):lines.append(connection['detail'])
    return '\n'.join(lines)
