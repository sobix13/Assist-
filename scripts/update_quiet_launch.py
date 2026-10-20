#!/usr/bin/env python3
"""Update Gate/Crew and Maestro adapters; retire Verifier without changing Raffle."""
import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maestro.security import read_env
from maestro.store import Store
from scripts.update_role_raffles import snapshot_database, switch

VERSIONS={'gate':'1.1.0','crew':'1.1.0','maestro':'1.1.4'}
UNITS=('ripcars-gate.service','ripcars-crew.service','maestro-agent.service','maestro-discord.service','maestro-telegram.service')
VERIFIER='ripcars-verifier.service'


def command(*args,check=True):
    return subprocess.run(args,check=check,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)


def validate_sources(sources):
    contract=json.loads((sources['maestro']/'maestro/adapters/suite-contract.json').read_text())
    protocol=(sources['maestro']/'ripcars_coordination.py').read_bytes()
    for key,source in sources.items():
        if (source/'VERSION').read_text().strip()!=VERSIONS[key]:raise ValueError('Unexpected source version: '+key)
        if (source/'ripcars_coordination.py').read_bytes()!=protocol:raise ValueError('Shared coordination protocol changed.')
        if key!='maestro':
            for name,expected in contract[key]['files'].items():
                data=(source/name).read_bytes()
                if hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()!=expected:
                    raise ValueError('Unreviewed companion source: '+key+'/'+name)


def stage(source,current,destination,key):
    requirements='requirements.txt' if key=='gate' else 'requirements-lock.txt'
    if (source/requirements).read_bytes()!=(current/requirements).read_bytes():
        raise ValueError('Dependencies differ. Use a reviewed full installer: '+key)
    shutil.copytree(source,destination,ignore=shutil.ignore_patterns('.git','.venv','__pycache__','*.pyc','.env','*.sqlite3*','data','releases','dist','STAGED_OK'))
    destination.chmod(0o755)
    (destination/'.venv').symlink_to((current/'.venv').resolve(strict=True),target_is_directory=True)
    python=str(destination/'.venv/bin/python')
    env={**os.environ,'PYTHON':python,'PYTHON_BIN':python,'PYTHONDONTWRITEBYTECODE':'1'}
    if key in ('gate','crew'):
        subprocess.run(['bash','run_tests.sh'],cwd=destination,env=env,check=True)
    else:
        subprocess.run([python,'-m','compileall','-q','maestro','scripts','main.py'],cwd=destination,env=env,check=True)
        subprocess.run([python,'-c','from maestro.suite import CONFIG; from maestro.applications import Applications'],cwd=destination,env=env,check=True)
    return destination


def retire_verifier(store):
    previous=[]
    for target in store.targets():
        if target.get('unit')!=VERIFIER:continue
        previous.append(target)
        store.save_target({**target,'enabled':False,'expected_running':False,'auto_restart':False,'auto_recipe':''})
    store.audit('root:quiet-launch','verifier_intentionally_stopped','Service and monitoring disabled; credentials, roles, links and balances retained.')
    return previous


def restore_targets(store,targets):
    for target in targets:store.save_target(target)


def update(sources,root=Path('/')):
    validate_sources(sources)
    stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid())
    bases={k:root/'opt'/('maestro' if k=='maestro' else 'ripcars-'+k) for k in sources}
    with ExitStack() as locks:
        for name in ('run/lock/ripcars-role-update.lock','opt/ripcars-gate/install.lock','opt/ripcars-crew/install.lock'):
            handle=locks.enter_context((root/name).open('a'))
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        previous={key:(base/'current').resolve(strict=True) for key,base in bases.items()}
        active=[unit for unit in UNITS if command('systemctl','is-active','--quiet',unit,check=False).returncode==0]
        if not all(unit in active for unit in UNITS[:2]):raise ValueError('Expected installed, running Gate and Crew. No service changed.')
        verifier_active=command('systemctl','is-active','--quiet',VERIFIER,check=False).returncode==0
        verifier_enabled=command('systemctl','is-enabled',VERIFIER,check=False).stdout.strip()
        candidates={}
        for key,source in sources.items():
            print('Staging '+key+'. Reusing unchanged installed dependencies.',flush=True)
            candidates[key]=stage(source,previous[key],bases[key]/'releases'/(VERSIONS[key]+'-quiet-'+stamp),key)
            for unit in UNITS:
                if (unit.startswith('maestro-') and key=='maestro') or unit=='ripcars-'+key+'.service':
                    user=command('systemctl','show',unit,'--property=User','--value').stdout.strip() or 'root'
                    for path in (candidates[key]/'VERSION',candidates[key]/'.venv/bin/python'):
                        command('runuser','-u',user,'--','test','-r' if path.name=='VERSION' else '-x',str(path))
        print('Preflight passed. Gate and Crew tests passed. Raffle and Verifier code will not be reinstalled.',flush=True)
        backup=root/'var/backups'/('ripcars-quiet-launch-'+stamp);backup.mkdir(mode=0o700,parents=True)
        (backup/'previous-releases.json').write_text(json.dumps({k:str(v) for k,v in previous.items()},indent=2)+'\n')
        (backup/'service-state.json').write_text(json.dumps({'active':active,'verifier_active':verifier_active,'verifier_enabled':verifier_enabled},indent=2)+'\n')
        frontends=[u for u in active if u in UNITS[-2:]]
        try:
            if frontends:command('systemctl','stop',*frontends)
            print('Verifying pre-update checkpoint.',flush=True)
            subprocess.run([str(previous['maestro']/'.venv/bin/python'),str(previous['maestro']/'scripts/local_checkpoint.py'),'--reason','quiet-launch:'+stamp],check=True)
        except BaseException:
            if frontends:command('systemctl','start',*frontends,check=False)
            raise
        state=root/'var/lib/maestro-agent/maestro.sqlite3'
        targets=None
        stopped=False
        try:
            stopped=True
            command('systemctl','stop',*UNITS,VERIFIER)
            for key in ('gate','crew','verifier'):
                env_file=root/('etc/ripcars-'+key+'.env')
                if not env_file.is_file():
                    if key=='verifier':continue
                    raise ValueError('Missing environment: '+key)
                shutil.copy2(env_file,backup/env_file.name)
                env=read_env(env_file)
                database=env.get('DATABASE_PATH' if key=='verifier' else 'DB_PATH','/var/lib/ripcars-'+key+'/'+key+'.sqlite3')
                path=root/database.lstrip('/')
                if path.exists():snapshot_database(path,backup/(key+'.sqlite3'))
                elif key!='verifier':raise ValueError('Missing database: '+key)
                shared=root/env.get('COORDINATION_PATH','/var/lib/ripcars-bots/coordination.sqlite3').lstrip('/')
                if shared.exists() and not (backup/'coordination.sqlite3').exists():snapshot_database(shared,backup/'coordination.sqlite3')
            snapshot_database(state,backup/'maestro.sqlite3')
            if (root/'etc/maestro').is_dir():shutil.copytree(root/'etc/maestro',backup/'maestro-config')
            with ExitStack() as dbs:
                store=Store(state,recover=False);dbs.callback(store.close)
                targets=retire_verifier(store)
                (backup/'verifier-targets.json').write_text(json.dumps(targets,indent=2)+'\n')
            command('systemctl','disable',VERIFIER)
            for key,candidate in candidates.items():switch(bases[key]/'current',candidate)
            if active:command('systemctl','start',*active)
            for unit in active:command('systemctl','is-active','--quiet',unit)
            if command('systemctl','is-active','--quiet',VERIFIER,check=False).returncode==0:
                raise ValueError('Verifier unexpectedly restarted. Inspect its service dependencies.')
        except BaseException:
            print('Update failed. Restoring previous code and service state. Current application data is retained.',flush=True)
            if stopped:
                command('systemctl','stop',*UNITS,VERIFIER,check=False)
                for key,old in previous.items():
                    if (bases[key]/'current').resolve()!=old:switch(bases[key]/'current',old)
                if targets is not None:
                    store=Store(state,recover=False)
                    try:restore_targets(store,targets)
                    finally:store.close()
                if verifier_enabled in ('enabled','enabled-runtime'):
                    command('systemctl','enable',*(['--runtime'] if verifier_enabled=='enabled-runtime' else []),VERIFIER,check=False)
                if active:command('systemctl','start',*active,check=False)
                if verifier_active:command('systemctl','start',VERIFIER,check=False)
            elif frontends:command('systemctl','start',*frontends,check=False)
            raise
        print('QUIET_LAUNCH_UPDATE_COMPLETED. Gate 1.1.0; Crew 1.1.0; Maestro 1.1.4.',flush=True)
        print('Verifier stopped and disabled. Existing settings, credentials, roles and links retained. Raffle unchanged.',flush=True)
        print('Gate: /gate panel > Features on / off. Crew: /crew panel > Channel rules. Backup: '+str(backup),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gate',required=True,type=Path);parser.add_argument('--crew',required=True,type=Path)
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    sources={'gate':args.gate.resolve(),'crew':args.crew.resolve(),'maestro':Path(__file__).resolve().parents[1]}
    if args.check_only:
        validate_sources(sources);print('Reviewed source versions, contracts and shared protocol match. No services changed.');return
    if os.geteuid()!=0:raise SystemExit('Run as root.')
    os.umask(0o022)
    update(sources)


if __name__=='__main__':main()
