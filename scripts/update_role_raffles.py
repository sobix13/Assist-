#!/usr/bin/env python3
"""Stage three tested releases, verify a checkpoint, then switch only their services."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maestro.security import read_env
from scripts.stage_release import stage

UNITS=('ripcars-raffle.service','ripcars-verifier.service','maestro-agent.service','maestro-discord.service','maestro-telegram.service')
VERSIONS={'raffle':'1.1.0','verifier':'1.2.0','maestro':'1.1.4'}


def command(*args,check=True,capture=False):
    return subprocess.run(args,check=check,text=True,stdout=subprocess.PIPE if capture else None)


def validate_sources(sources):
    for key,source in sources.items():
        if (source/'VERSION').read_text().strip()!=VERSIONS[key]:
            raise ValueError('Unexpected source version: '+key)
    adapters=sources['maestro']/'maestro/adapters'
    raffle=json.loads((adapters/'suite-contract.json').read_text())['raffle']
    verifier=json.loads((adapters/'verifier-contract.json').read_text())
    for key,contract in [('raffle',raffle),('verifier',verifier)]:
        for name,expected in contract['files'].items():
            data=(sources[key]/name).read_bytes()
            actual=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() if key=='raffle' else hashlib.sha256(data).hexdigest()
            if actual not in (expected if isinstance(expected,list) else [expected]):raise ValueError('Source does not match the reviewed adapter: '+key+'/'+name)
    protocol=(sources['maestro']/'ripcars_coordination.py').read_bytes()
    if any((s/'ripcars_coordination.py').read_bytes()!=protocol for s in sources.values()):
        raise ValueError('Coordination protocol mismatch.')


def snapshot_database(source,destination):
    source=Path(source);destination=Path(destination)
    if source.is_symlink() or not source.is_file():raise ValueError('Existing database is missing or a symlink: '+str(source))
    destination.touch(mode=0o600,exist_ok=False)
    try:
        with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True,timeout=30) as original:
            with sqlite3.connect(destination) as copied:
                original.backup(copied)
                if copied.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise ValueError('Database backup integrity check failed: '+source.name)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def switch(current,target):
    pending=current.with_name(current.name+'.role-update')
    if pending.exists() or pending.is_symlink():raise ValueError('A pending release link already exists: '+str(pending))
    pending.symlink_to(target)
    pending.replace(current)


def activate(releases,previous,unit_folder,backup):
    """Rollback code and unit files on failure. Never roll back application data."""
    try:
        for current,candidate in releases.items():switch(current,candidate)
        for unit in UNITS:
            owner='maestro' if unit.startswith('maestro-') else unit.removesuffix('.service')
            candidate=next(p for current,p in releases.items() if current.parent.name==owner)
            shutil.copyfile(candidate/unit,unit_folder/unit)
            (unit_folder/unit).chmod(0o644)
        command('systemctl','daemon-reload')
        command('systemctl','start',*UNITS)
        time.sleep(3)
        for unit in UNITS:command('systemctl','is-active','--quiet',unit)
    except BaseException:
        print('Activation failed. Restoring previous code; keeping all current application data.',flush=True)
        command('systemctl','stop',*UNITS,check=False)
        for current,old in previous.items():
            if current.resolve()!=old:switch(current,old)
        for unit in UNITS:shutil.copyfile(backup/unit,unit_folder/unit)
        command('systemctl','daemon-reload',check=False)
        command('systemctl','start',*UNITS,check=False)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raffle',required=True,type=Path)
    parser.add_argument('--verifier',required=True,type=Path)
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    sources={'raffle':args.raffle.resolve(),'verifier':args.verifier.resolve(),'maestro':Path(__file__).resolve().parents[1]}
    validate_sources(sources)
    if args.check_only:
        print('Source versions, pinned adapters and shared protocol match. No services changed.');return
    if os.geteuid()!=0:raise SystemExit('Run this existing-installation updater as root.')
    os.umask(0o077)
    with ExitStack() as locks:
        for path in ['/run/lock/ripcars-role-update.lock','/opt/ripcars-raffle/install.lock','/opt/ripcars-verifier/install.lock']:
            handle=locks.enter_context(open(path,'a'))
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        previous={}
        for key in sources:
            base=Path('/opt')/('maestro' if key=='maestro' else 'ripcars-'+key)
            current=base/'current'
            if not current.is_symlink() or not (current.resolve()/'.venv/bin/python').is_file():
                raise ValueError('Existing installed release is required: '+key)
            previous[current]=current.resolve()
        for unit in UNITS:
            command('systemctl','is-active','--quiet',unit)
            if not Path('/etc/systemd/system',unit).is_file():raise ValueError('Existing unit file is required: '+unit)
        for file in ['/etc/maestro/policy.json','/etc/ripcars-raffle.env','/etc/ripcars-verifier.env']:
            if not Path(file).is_file():raise ValueError('Existing configuration is required: '+file)
        stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid())
        releases={}
        for index,(key,source) in enumerate(sources.items(),1):
            print(f'Staging {index}/3: {key}. Running its complete test suite before stopping services.',flush=True)
            base=Path('/opt')/('maestro' if key=='maestro' else 'ripcars-'+key)
            candidate=stage(source,base/'releases'/(VERSIONS[key]+'-'+stamp))
            releases[base/'current']=candidate
            for unit in UNITS:
                if unit.startswith('maestro-')!=(key=='maestro') or key!='maestro' and unit!='ripcars-'+key+'.service':continue
                user=command('systemctl','show',unit,'--property=User','--value',capture=True).stdout.strip() or 'root'
                command('runuser','-u',user,'--','test','-x',str(candidate/'.venv/bin/python'))
                command('runuser','-u',user,'--','test','-r',str(candidate/'VERSION'))
        verifier=releases[Path('/opt/ripcars-verifier/current')]
        command(str(verifier/'.venv/bin/python'),str(verifier/'scripts/preflight.py'),'--env-file','/etc/ripcars-verifier.env')
        backup=Path('/var/backups')/('ripcars-role-update-'+stamp);backup.mkdir(mode=0o700)
        for unit in UNITS:shutil.copy2(Path('/etc/systemd/system')/unit,backup/unit)
        for app in ('raffle','verifier'):shutil.copy2('/etc/ripcars-'+app+'.env',backup/('ripcars-'+app+'.env'))
        shutil.copytree('/etc/maestro',backup/'maestro-config')
        (backup/'previous-releases.json').write_text(json.dumps({str(k):str(v) for k,v in previous.items()},indent=2)+'\n')
        frontends=UNITS[-2:]
        command('systemctl','stop',*frontends)
        try:
            maestro=releases[Path('/opt/maestro/current')]
            print('Verifying the pre-update Maestro checkpoint. Failure leaves the current releases in place.',flush=True)
            command(str(maestro/'.venv/bin/python'),str(maestro/'scripts/local_checkpoint.py'),'--reason','role-raffle-update:'+stamp)
        except BaseException:
            command('systemctl','start',*frontends,check=False)
            raise
        command('systemctl','stop',*UNITS)
        try:
            raffle_env=read_env('/etc/ripcars-raffle.env');verifier_env=read_env('/etc/ripcars-verifier.env')
            databases={'raffle':raffle_env.get('DB_PATH','/var/lib/ripcars-raffle/raffle.sqlite3'),'verifier':verifier_env['DATABASE_PATH']}
            for key,path in databases.items():snapshot_database(path,backup/(key+'.sqlite3'))
            shared={raffle_env.get('COORDINATION_PATH','/var/lib/ripcars-bots/coordination.sqlite3'),verifier_env['COORDINATION_PATH']}
            for index,path in enumerate(sorted(shared)):snapshot_database(path,backup/f'coordination-{index}.sqlite3')
            activate(releases,previous,Path('/etc/systemd/system'),backup)
        except BaseException:
            command('systemctl','start',*UNITS,check=False)
            raise
        print('ROLE_RAFFLE_UPDATE_COMPLETED. Raffle 1.1.0; Verifier 1.2.0; Maestro 1.1.4.',flush=True)
        print('Open /verifier panel > Token holder setup. Configure RPC and the official mint, review roles, then update the member panel. Existing qualification roles and bot settings are retained.',flush=True)
        print('Gate and Crew services were not changed. Backup: '+str(backup),flush=True)


if __name__=='__main__':main()
