"""Versioned settings access for the owned Gate, Crew and Waffle applications."""
import asyncio
import copy
import hashlib
import json
import os
import pwd
from pathlib import Path
import sys
import time

from .adapters import gate_config, crew_config, raffle_config
from .security import Denied, read_env

CONFIG = {'gate-v1':gate_config, 'crew-v1':crew_config, 'raffle-v1':raffle_config}
TABLE = {'gate-v1':('settings','body'), 'crew-v1':('crew_settings','body'), 'raffle-v1':('settings','data')}


def verify(spec):
    key = spec['kind'].split('-')[0]
    contract = json.loads((Path(__file__).parent/'adapters/suite-contract.json').read_text())[key]
    root = Path(spec['install']).resolve(strict=True)
    if (root/'VERSION').read_text().strip() != contract['version']:
        raise Denied('نسخه بات تغییر کرده است. اتصال مسترو باید با نسخه جدید بررسی شود.')
    for name, expected in contract['files'].items():
        path = root/name
        if path.is_symlink() or not path.is_file():
            raise Denied('فایل‌های نسخه تأییدشده بات کامل نیست: '+name)
        data = path.read_bytes()
        actual = hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if actual != expected:
            raise Denied('کد بات با نسخه بررسی‌شده متفاوت است: '+name)


def validate(spec, cfg):
    module = CONFIG[spec['kind']]
    if spec['kind'] in ('gate-v1','crew-v1'):cfg=module.upgrade(cfg)
    original = module.fresh() if spec['kind']=='raffle-v1' else module.defaults()
    def shape(value, sample):
        if isinstance(sample, dict):
            if not isinstance(value,dict) or sample and set(value)!=set(sample):
                raise Denied('ساختار تنظیمات با نسخه بات سازگار نیست.')
            for key in sample:
                shape(value[key],sample[key])
        elif not isinstance(sample,list) and sample is not None and type(value) is not type(sample):
            raise Denied('نوع مقدار تنظیم صحیح نیست.')
        elif isinstance(sample,list) and not isinstance(value,list):
            raise Denied('این تنظیم یک فهرست است.')
    # The upstream Crew validator already permits numeric ratios.
    if spec['kind']!='crew-v1':
        shape(cfg,original)
    module.validate(cfg)
    return cfg


def read(spec, connection):
    table,column = TABLE[spec['kind']]
    row=connection.execute(f'SELECT revision,{column} FROM {table} WHERE guild=?',(spec['guild'],)).fetchone()
    module=CONFIG[spec['kind']]
    cfg=json.loads(row[column]) if row else module.fresh() if spec['kind']=='raffle-v1' else module.defaults()
    if spec['kind'] in ('gate-v1','crew-v1'):cfg=module.upgrade(cfg)
    validate(spec,cfg)
    return cfg,row['revision'] if row else 0


def validate_transition(spec, before, after):
    # Only the native bot can check current Discord access and its public panels.
    if spec['kind']=='gate-v1' and after['enabled'] and not before['enabled']:
        raise Denied('برای فعال‌سازی ورود، در دیسکورد /gate panel را باز کن و Health و Activate را بزن. تنظیمات مسترو حفظ شده‌اند.')
    if spec['kind']=='crew-v1' and any(after[k]['enabled'] for k in ('guard','tickets')) and before!=after:
        previous=copy.deepcopy(before);candidate=copy.deepcopy(after)
        enabling=any(after[k]['enabled'] and not before[k]['enabled'] for k in ('guard','tickets'))
        for key in ('guard','tickets'):
            previous[key]['enabled']=False;candidate[key]['enabled']=False
        if enabling or previous!=candidate:
            raise Denied('برای تنظیم ماژول فعال، در دیسکورد /crew panel را باز کن تا دسترسی‌ها و پنل تیکت بررسی شوند. می‌توانی ابتدا ماژول‌ها را متوقف و تنظیمات را در مسترو ویرایش کنی.')


def commit(spec, connection, cfg, revision, actor):
    cfg=validate(spec,cfg)
    table,column=TABLE[spec['kind']]
    connection.execute('BEGIN IMMEDIATE')
    try:
        row=connection.execute(f'SELECT revision,{column} FROM {table} WHERE guild=?',(spec['guild'],)).fetchone()
        if (row['revision'] if row else 0)!=revision:
            raise Denied('تنظیمات هم‌زمان تغییر کرده است. صفحه بات را دوباره باز کن.')
        before=json.loads(row[column]) if row else (CONFIG[spec['kind']].fresh() if spec['kind']=='raffle-v1' else CONFIG[spec['kind']].defaults())
        validate_transition(spec,before,cfg)
        if row:
            if spec['kind']=='raffle-v1':
                connection.execute('INSERT INTO history(guild,data,actor,at) VALUES(?,?,0,?)',(spec['guild'],row[column],time.time()))
            else:
                history='crew_history' if spec['kind']=='crew-v1' else 'history'
                connection.execute(f'INSERT INTO {history}(guild,actor,at,body) VALUES(?,0,?,?)',(spec['guild'],time.time(),row[column]))
            connection.execute(f'UPDATE {table} SET {column}=?,revision=revision+1 WHERE guild=?',(json.dumps(cfg),spec['guild']))
        elif spec['kind']=='crew-v1':
            connection.execute('INSERT INTO crew_settings(guild,revision,body,started) VALUES(?,1,?,?)',(spec['guild'],json.dumps(cfg),time.time()))
        else:
            connection.execute(f'INSERT INTO {table}(guild,revision,{column}) VALUES(?,1,?)',(spec['guild'],json.dumps(cfg)))
        connection.commit()
        return revision+1
    except BaseException:
        connection.rollback()
        raise


def raffle_plan(spec, payload, cfg, connection):
    if payload['action']=='raffle_create':
        value=copy.deepcopy(payload.get('changes',{}))
        category=value.pop('category','general')
        if category not in raffle_config.CATEGORIES:
            raise Denied('نوع قرعه‌کشی را انتخاب کن.')
        if cfg.get('raffle_role'):
            rule=value['rules']
            roles=rule['any_roles'] or [cfg['member_role']]
            selected=set(roles+rule['all_roles'])
            if cfg['raffle_role'] in selected:
                raise Denied('نقش موقت رافل شرط ورود نیست؛ نقش واجد شرایط را انتخاب کن.')
            rule.update(any_roles=roles,weights={str(r):rule['weights'].get(str(r),cfg['role_tickets'].get(str(r),1)) for r in selected},weight_mode='tickets',weight_cap=2000)
        raffle_config.validate_spec(value)
        if category!='general' and not (value['rules']['any_roles'] or value['rules']['all_roles']):
            raise Denied('این قرعه‌کشی به نقش واجد شرایط نیاز دارد.')
        return {'category':category,'spec':value}
    rid=payload.get('changes',{}).get('raffle_id')
    if type(rid) is not int or rid<1:
        raise Denied('شماره قرعه‌کشی معتبر نیست.')
    row=connection.execute('SELECT * FROM raffles WHERE guild=? AND id=?',(spec['guild'],rid)).fetchone()
    if not row or row['status']!='draft':
        raise Denied('این قرعه‌کشی دیگر پیش‌نویس نیست. وضعیت تازه را بررسی کن.')
    if not cfg['enabled']:
        raise Denied('ابتدا تنظیمات Waffle را تکمیل و ورود شرکت‌کننده را روشن کن.')
    return {'raffle_id':rid,'draft_revision':row['revision']}


async def publish(applications, spec, data):
    # Run the native, pinned publisher in a separate process. It uses Waffle's
    # database publication lock and journal, not a second scheduler or command sync.
    env=read_env(spec['env_file'])
    applications.redactor.load_env(spec['env_file'])
    identity={}
    if os.geteuid()==0:
        stat=Path(spec['database']).stat();account=pwd.getpwuid(stat.st_uid)
        identity={'user':stat.st_uid,'group':stat.st_gid,'extra_groups':os.getgrouplist(account.pw_name,stat.st_gid)}
    process=await asyncio.create_subprocess_exec(
        sys.executable,'-m','maestro.suite_worker',spec['install'],str(spec['guild']),
        spec['database'],spec['coordination_path'],str(data['raffle_id']),str(data['draft_revision']),
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
        env={'PATH':os.defpath,'PYTHONPATH':str(Path(__file__).resolve().parent.parent),'PYTHONDONTWRITEBYTECODE':'1'},**identity)
    try:
        out,err=await asyncio.wait_for(process.communicate(env.get('DISCORD_TOKEN','').encode()),120)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode:
        raise Denied('انتشار تأیید نشد. پیش از تلاش دوباره وضعیت قرعه‌کشی را بررسی کن. '+applications.redactor.clean(err.decode(errors='replace'))[-800:])
    return json.loads(out)
