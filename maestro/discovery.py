"""Automatic passive service cards, with stable identities and no resource adoption."""
from __future__ import annotations

import hashlib
import re

from .probes import validate_target


def target_id(driver, resource):
    label = re.sub('[^a-z0-9_-]', '-', resource.lower().removesuffix('.service')).strip('-')
    return 's-' + (label or 'service')[:18] + '-' + hashlib.sha256((driver+':'+resource).encode()).hexdigest()[:8]


def reconcile(store, report):
    known = {(t.get('driver', 'systemd'), t.get('container') if t.get('driver') == 'docker' else t['unit']): t for t in store.targets()}
    found = []
    resources = [('systemd', r.get('unit'), r.get('active') == 'active') for r in report.get('services', []) if r.get('load') == 'loaded']
    resources += [('docker', r.get('Names'), str(r.get('Status', '')).startswith('Up')) for r in report.get('containers', [])]
    for driver, resource, active in resources:
        if not resource or (driver, resource) in known:
            continue
        value = {'id': target_id(driver, resource), 'unit': resource if driver == 'systemd' else 'docker.service',
                 'driver': driver, 'enabled': False, 'expected_running': active, 'auto_restart': False,
                 'recovery_owner': 'external'}
        if driver == 'docker':
            value['container'] = resource
        value = validate_target(value)
        store.save_target(value)
        store.put('discovered:'+value['id'], {'at': report.get('at'), 'resource': resource, 'initial_state': 'active' if active else 'inactive'})
        known[(driver, resource)] = value
        found.append(value['id'])
    return found
