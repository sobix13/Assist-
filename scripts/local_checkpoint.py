#!/usr/bin/env python3
"""Root-only checkpoint for a direct VPS update; never resets live agent job state."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maestro.checkpoints import Checkpoints
from maestro.security import Policy, Redactor
from maestro.store import Store


async def run(reason):
    if os.geteuid() != 0:
        raise SystemExit('Run the local checkpoint helper with sudo.')
    policy = Policy.load('/etc/maestro/policy.json')
    root = Path('/var/lib/maestro-agent')
    store = Store(root/'maestro.sqlite3', recover=False)
    try:
        if store.one("SELECT id FROM jobs WHERE state IN ('queued','running') LIMIT 1"):
            raise SystemExit('An owner job is active. Wait for it before a direct VPS update.')
        redactor = Redactor([f['token'] for f in policy.frontends])
        checkpoint = await Checkpoints(policy, root, store, redactor).create(reason, store.targets())
        store.event('checkpoint_created', {'id':checkpoint['id'],'reason':reason,'snapshot_id':checkpoint['snapshot_id']}, policy.owners)
        print(json.dumps({'id':checkpoint['id'],'state':checkpoint['state'],'export_restore_verified':True}))
    finally:
        store.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reason', default='local:pre-update')
    args = parser.parse_args()
    asyncio.run(run(args.reason))
