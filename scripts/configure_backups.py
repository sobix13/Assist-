#!/usr/bin/env python3
"""Local backup enrollment. Credentials never pass through Discord or Telegram."""
import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from configure import atomic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--init', action='store_true', help='Explicitly initialize the configured Restic repository if new')
    args = parser.parse_args()
    if os.geteuid() != 0 or not shutil.which('restic'):
        raise SystemExit('Run with sudo after installing Restic (0.16 or newer).')
    folder = Path('/etc/maestro')
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    old = json.loads((folder/'backup.json').read_text()) if (folder/'backup.json').is_file() else {}
    print('Use an encrypted off-host repository or a separate mounted backup disk. A repository on this VPS alone cannot survive host loss.')
    repository = input('Restic repository (absolute local path, sftp:..., s3:..., rest:...): ').strip()
    if not repository or any(c in repository for c in '\x00\r\n'):
        raise ValueError('Invalid repository.')
    password = getpass.getpass('Restic encryption password (save it outside this VPS): ')
    if len(password) < 16 or any(c in password for c in '\x00\r\n'):
        raise ValueError('Use a backup password of at least sixteen characters.')
    values = {'RESTIC_REPOSITORY': repository, 'RESTIC_PASSWORD': password, 'RESTIC_CACHE_DIR': '/var/lib/maestro-agent/restic-cache'}
    print('Optional backend credentials as KEY=VALUE, one per prompt. Blank finishes. Nothing is echoed.')
    for _ in range(20):
        value = getpass.getpass('Backend credential: ')
        if not value:
            break
        key, sep, text = value.partition('=')
        if not sep or not re.fullmatch(r'(?:AWS_[A-Z_]+|B2_[A-Z_]+|AZURE_[A-Z_]+|GOOGLE_[A-Z_]+|RESTIC_[A-Z_]+)', key) or any(c in text for c in '\x00\r\n'):
            raise ValueError('Use a supported backend environment credential name.')
        if key in ('RESTIC_PASSWORD_COMMAND', 'RESTIC_PASSWORD_FILE', 'RESTIC_REPOSITORY_FILE', 'RESTIC_REPOSITORY', 'RESTIC_PASSWORD', 'RESTIC_CACHE_DIR'):
            raise ValueError('Do not override repository/password settings with an extra credential.')
        values[key] = text
    default = ' '.join(old.get('paths', ['/']))
    paths = (input('Absolute filesystem roots, space-separated ['+default+']: ').strip() or default).split()
    if any(not Path(p).is_absolute() or not Path(p).exists() for p in paths):
        raise ValueError('Each backup root must exist. Edit the protected JSON for paths containing spaces.')
    databases = old.get('databases', [])
    print('Enrolled target SQLite databases are added automatically. Declare other databases now. Blank finishes.')
    for _ in range(100-len(databases)):
        path = input('Additional SQLite database absolute path: ').strip()
        if not path:
            break
        if not Path(path).is_absolute() or not Path(path).is_file():
            raise ValueError('Additional database must exist.')
        databases.append({'name':'extra-'+str(len(databases)+1), 'kind':'sqlite', 'path':path})
    print('PostgreSQL, MySQL, Docker volumes and unknown stores need their consistent export hook or paused writer. See BACKUPS.md before marking coverage reviewed.')
    if input('Type COVERAGE REVIEWED after checking all application stores: ').strip() != 'COVERAGE REVIEWED':
        raise SystemExit('Enrollment cancelled. Existing configuration was preserved.')
    env_path = folder/'restic.env'
    config = {**old, 'coverage_reviewed':True, 'paths':paths, 'env_file':str(env_path), 'databases':databases,
              'quiesce_units':old.get('quiesce_units', []), 'timeout_seconds':old.get('timeout_seconds', 1800),
              'export_limit_mib':old.get('export_limit_mib',512),
              'exclude':old.get('exclude', ['/var/lib/maestro-agent/restic-cache', '/var/cache'])}
    # Validate locally before replacing either file.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from maestro.checkpoints import validate_config
    validate_config(config)
    env = {'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME':'/root', 'LANG':'C.UTF-8', **values}
    if args.init:
        subprocess.run(['restic','init'], env=env, check=True)
    subprocess.run(['restic','cat','config'], env=env, check=True, stdout=subprocess.DEVNULL)
    atomic(env_path, '\n'.join(k+'='+v for k,v in values.items())+'\n')
    atomic(folder/'backup.json', json.dumps(config,indent=2)+'\n')
    print('Backup coverage enrolled. Test /maestro checkpoint or /checkpoint. Only a completed and verified checkpoint authorizes execution.')


if __name__ == '__main__':
    main()
