#!/usr/bin/env python3
"""Enroll immutable owner identities and separate Discord/Telegram credentials locally."""
import argparse
import getpass
import json
import os
import pwd
import re
import secrets
from pathlib import Path


def numeric(prompt, old=''):
    value = input(prompt + (' [' + old + ']' if old else '') + ': ').strip() or old
    if not re.fullmatch(r'[0-9]{5,22}', value):
        raise ValueError('Use the numeric user/server ID, not a username.')
    return value


def atomic(path, text):
    temp = path.with_suffix('.new')
    with temp.open('w') as file:
        os.chmod(temp, 0o600)
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    temp.replace(path)


def read_existing(path):
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077:
        raise ValueError('Existing credentials must be root-owned regular files with mode 0600.')
    return dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interfaces', choices=('both', 'discord', 'telegram'), default='both')
    parser.add_argument('--add-interface', action='store_true', help='Deliberately add a missing owner interface to an existing enrollment')
    parser.add_argument('--enable-reboot', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run this enrollment script with sudo.')
    folder = Path('/etc/maestro')
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    policy_path = folder / 'policy.json'
    existing = policy_path.exists()
    if existing:
        if policy_path.is_symlink() or policy_path.stat().st_uid != 0 or policy_path.stat().st_mode & 0o077:
            raise ValueError('Existing policy must be root-owned mode 0600.')
        policy = json.loads(policy_path.read_text())
    else:
        policy = {'owners': [], 'owner_groups': [], 'frontends': [], 'runner_user': 'maestro-runner',
                  'root_commands': True, 'host_reboot': False, 'backup_required': True,
                  'backup_config': '/etc/maestro/backup.json',
                  'recipes_config': '/etc/maestro/recipes.json',
                  'inventory_roots': ['/opt', '/srv', '/root', '/home'],
                  'coordination_path': '/var/lib/ripcars-bots/coordination.sqlite3', 'github_token': ''}
    envs, identities = {}, []
    for interface in ('discord', 'telegram'):
        if args.interfaces not in ('both', interface):
            continue
        path = folder / ('frontend.env' if interface == 'discord' else 'telegram.env')
        old = read_existing(path)
        owner_key = 'OWNER_DISCORD_ID' if interface == 'discord' else 'OWNER_TELEGRAM_ID'
        owner = numeric('Superadmin ' + interface.title() + ' user ID', old.get(owner_key, '313342234' if interface == 'telegram' else ''))
        identity = interface + ':' + owner
        if identity not in policy['owners']:
            enrolled_transport = any(x.startswith(interface + ':') for x in policy['owners'])
            if existing and (enrolled_transport or not args.add_interface):
                raise ValueError('Owner changes are not accepted. Adding a new interface requires --add-interface on the VPS.')
            policy['owners'].append(identity)
        identities.append(identity)
        token_key = 'DISCORD_TOKEN' if interface == 'discord' else 'TELEGRAM_TOKEN'
        token = getpass.getpass(interface.title() + ' bot token (blank keeps existing): ').strip() or old.get(token_key, '')
        if any(c in token for c in '\r\n\x00') or (len(token) < 40 if interface == 'discord' else not re.fullmatch(r'[0-9]{5,15}:[A-Za-z0-9_-]{25,200}', token)):
            raise ValueError('Paste a valid ' + interface.title() + ' bot token on the VPS.')
        front = next((f for f in policy['frontends'] if f['transport'] == interface), None)
        account = 'maestro' if interface == 'discord' else 'maestro-telegram'
        if not front:
            front = {'transport': interface, 'uid': pwd.getpwnam(account).pw_uid, 'token': secrets.token_urlsafe(48)}
            policy['frontends'].append(front)
        if front['uid'] != pwd.getpwnam(account).pw_uid:
            raise ValueError('Frontend account UID changed. Review local policy explicitly.')
        env = {token_key: token, owner_key: owner, 'AGENT_TOKEN': front['token'], 'AGENT_SOCKET': '/run/maestro/agent.sock'}
        if interface == 'discord':
            env.update(GUILD_ID=numeric('Discord server ID', old.get('GUILD_ID', '')), FRONTEND_STATE='/var/lib/maestro/frontend.json')
        else:
            env['TELEGRAM_STATE'] = '/var/lib/maestro-telegram/frontend.json'
        # Preserve explicitly enrolled loopback mTLS transport settings during updates.
        env.update({k:v for k,v in old.items() if k.startswith('AGENT_TLS_')})
        envs[path] = env
    if len(identities) == 2:
        linked = [g for g in policy.get('owner_groups', []) if set(g) & set(identities)]
        if linked and any(set(g) - set(identities) for g in linked):
            raise ValueError('Existing owner links need explicit local review before merging identities.')
        policy['owner_groups'] = [g for g in policy.get('owner_groups', []) if g not in linked] + [identities]
    policy['backup_required'] = True
    policy['secret_envs'] = ['/etc/maestro/frontend.env', '/etc/maestro/telegram.env']
    if args.enable_reboot:
        policy['host_reboot'] = True
    atomic(policy_path, json.dumps(policy, indent=2) + '\n')
    for path, env in envs.items():
        atomic(path, '\n'.join(k + '=' + v for k, v in env.items()) + '\n')
    print('Owners enrolled. Configure backups before executing commands. Monitoring and read-only setup work independently.')
    print('Start the agent plus the selected interfaces. Open /maestro panel in Discord and /start in the private Telegram chat.')


if __name__ == '__main__':
    main()
