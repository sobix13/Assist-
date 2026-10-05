#!/usr/bin/env python3
"""Enroll a versioned application adapter locally; credentials never enter chat."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maestro.applications import Applications
from maestro.security import Policy, Redactor
from maestro.store import Store


def main():
    if os.geteuid()!=0:raise SystemExit('Run this wizard with sudo.')
    policy=Policy.load('/etc/maestro/policy.json')
    store=Store('/var/lib/maestro-agent/maestro.sqlite3',recover=False)
    try:
        target=input('Existing Maestro target ID [verifier]: ').strip() or 'verifier'
        if not store.target(target):raise ValueError('Add this service to Maestro first.')
        key=input('Adapter ID [verifier]: ').strip() or 'verifier'
        kind=input('Adapter kind [verifier-v1 or jsonfile]: ').strip() or 'verifier-v1'
        if kind=='verifier-v1':
            value={'id':key,'target':target,'kind':kind,
                   'install':input('Installed source directory [/opt/ripcars-verifier/current]: ').strip() or '/opt/ripcars-verifier/current',
                   'database':input('SQLite database [/var/lib/ripcars-verifier/verifier.sqlite3]: ').strip() or '/var/lib/ripcars-verifier/verifier.sqlite3',
                   'env_file':input('Existing Verifier environment [/etc/ripcars-verifier.env]: ').strip() or '/etc/ripcars-verifier.env',
                   'coordination_path':input('Shared coordination database [/var/lib/ripcars-bots/coordination.sqlite3]: ').strip() or '/var/lib/ripcars-bots/coordination.sqlite3',
                   'guild':int(input('Verifier Discord server ID: ').strip())}
        elif kind=='jsonfile':
            value={'id':key,'target':target,'kind':kind,'path':input('Existing absolute JSON configuration path: ').strip(),
                   'fields':json.loads(input('Editable field schema JSON, e.g. {"enabled":{"type":"bool"}}: '))}
        else:raise ValueError('Unsupported adapter kind.')
        path=Path('/etc/maestro/applications.json')
        from maestro.checkpoints import protected_json
        previous=protected_json(path) if path.exists() else {'adapters':[]}
        previous['adapters']=[x for x in previous['adapters'] if x['id']!=key]+[value]
        # Validate in an isolated root-owned temporary file before replacing enrollment.
        temp=path.parent/('applications-candidate-'+str(os.getpid())+'.json')
        try:
            temp.write_text(json.dumps(previous,indent=2)+'\n');temp.chmod(0o600)
            candidate=Policy({**policy.data,'applications_config':str(temp)})
            adapters=Applications(candidate,store,Redactor(),None,None)
            adapters.read(key)
            temp.replace(path)
        finally:temp.unlink(missing_ok=True)
        print('Application adapter enrolled. Open Application settings in Telegram or /maestro application in Discord. No application setting was changed.')
    finally:store.close()


if __name__=='__main__':main()
