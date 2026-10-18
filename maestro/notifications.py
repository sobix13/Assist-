"""Shared low-volume reporting. Monitoring and the broker outbox are unchanged."""
import hashlib
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from .guides import issue_help

STABLE_SECONDS = 600


def enabled(settings, category, transport=None):
    return (not transport or settings.get('notify_' + transport, True)) and settings.get('notify_' + category, True)


def severity(row):
    detail = str(row.get('detail', '')).lower()
    if any(word in detail for word in ('malformed', 'corrupt', 'file is not a database', 'disk i/o error', 'disk is full', 'no space left', 'read-only filesystem','critical resources')):
        return 3
    return 2 if row.get('code') in ('database_failed', 'checkpoint_failed', 'resource_pressure', 'unit_down') else 1


def daily_only(row, targets=None):
    """Monitoring/configuration reviews stay visible in the daily digest.

    This changes delivery only. It never marks a backend incident healthy.
    A real central-backup failure, stopped daemon or database error is retained.
    """
    target, code, detail = str(row.get('target', '')), row.get('code'), str(row.get('detail', ''))
    unit = (targets or {}).get(target, {}).get('unit', '')
    session = re.fullmatch(r'user(?:-runtime-dir)?@\d+\.service', unit) or re.match(r'^s-user-(?:\d+-|runtime-dir-)', target)
    if code == 'unit_down' and session and detail == 'Expected service is not active: inactive':
        return True
    if target == 'assistant' and code == 'scheduler' and detail == 'Denied: A monitoring cycle is already running.':
        return True
    if code == 'database_review' or code == 'database_failed' and detail == 'OperationalError: interrupted':
        return True
    return code == 'backup_failed' and detail in ('No backup found', 'Configured backup directory is missing.')


def incident_notifications(snapshot, history, now):
    """Plan alerts from current broker state, never replay incident transitions.

    Returns (message, state key, updated record) items. Callers persist a record
    only after successful delivery; silent stability observations are persisted
    immediately. A truncated snapshot must never claim missing rows recovered.
    """
    if not isinstance(snapshot, dict) or not any(k in snapshot for k in ('open_incidents', 'incidents')):
        return []
    rows = snapshot.get('open_incidents', snapshot.get('incidents', []))
    targets = {t['id']: t for t in snapshot.get('targets', [])}
    active = {str(row['key']): row for row in rows}
    plans = []
    for key, row in active.items():
        record = dict(history.get(key, {}))
        was_clear = record.get('closed_since') is not None
        record['closed_since'] = None
        record['seen_at'] = now
        if daily_only(row, targets):
            record['announced_open'] = False
            history[key] = record
            continue
        if row.get('code') == 'worker_unhealthy' and not record.get('announced_open'):
            stamp = row.get('first_seen')
            if isinstance(stamp, (int, float)):
                record['failure_since'] = min(stamp, now)
            elif was_clear or record.get('failure_since') is None:
                record['failure_since'] = now
            if now - record['failure_since'] < STABLE_SECONDS:
                history[key] = record
                continue
        # No hourly reminder. The same continuously open issue is announced once.
        level = severity(row)
        if not record.get('announced_open') or level > record.get('severity', 2):
            record.update(last_alert=now, announced_open=True, severity=level)
            text = 'Maestro | ALERT\n' + str(row['target']) + ': ' + str(row['code']) + '\n' + issue_help(row['code']) + '\n' + str(row.get('detail', 'Needs review'))[:800]
            plans.append((text, key, record))
        else:
            history[key] = record
    if 'incidents' in snapshot or len(rows) < 30:
        for key, previous in list(history.items()):
            if key in active:
                continue
            record = dict(previous)
            record['failure_since'] = None
            if not previous.get('announced_open'):
                history[key] = record
                continue
            if record.get('closed_since') is None:
                record['closed_since'] = now
            record['seen_at'] = now
            if now - record['closed_since'] >= STABLE_SECONDS:
                record['announced_open'] = False
                record['recovered_at'] = now
                plans.append(('Maestro | RESOLVED\n' + key + '\nClear for at least 10 minutes.', key, record))
            else:
                history[key] = record
    return plans


def notification(event, settings):
    """Return (text, deduplication key, cooldown), or None for routine events."""
    kind, data = event['kind'], event['payload']
    category = 'daily' if kind == 'scheduled_report' else 'jobs' if kind == 'job_finished' else 'recovery' if kind in ('incident_resolved', 'recovery') else 'alerts'
    if not enabled(settings, category):
        return None
    if kind in ('manual_report', 'diagnosis', 'services_added', 'inventory_changed', 'coordination_review'):
        return None
    # Repository failures also produce the authoritative backups incident.
    # Interrupted checkpoints with paused writers have a separate review event.
    if kind == 'checkpoint_review' and data.get('ok') is False:
        return None
    if kind == 'scheduled_report':
        if data.get('kind') != 'daily':
            return None
        day = datetime.fromtimestamp(event['at'], ZoneInfo(settings.get('timezone', 'Asia/Tehran'))).date()
        targets = data.get('targets', [])
        failed = [str(t['id']) for t in targets if not t.get('ok')]
        host = data.get('host', {})
        lines = ['Maestro | Daily summary ' + str(day),
                 f'Services checked: {len(targets)}. Need review: {len(failed)}.']
        if failed:
            lines.append('Review: ' + ', '.join(failed[:15]))
        reviews = []
        for target in targets:
            for issue in target.get('issues', []):
                if daily_only({'target': target['id'], **issue}, {target['id']: target}):
                    reviews.append(str(target['id']) + ': ' + str(issue['detail'])[:100])
        lines.extend(reviews[:8])
        if 'disk_free_percent' in host:
            lines.append(f"Disk free: {host['disk_free_percent']}%. Memory available: {host.get('memory_available_percent', '?')}%.")
        for issue in host.get('issues', [])[:4]:
            lines.append(str(issue)[:200])
        if data.get('other_failed_services'):
            lines.append('Other failed services: ' + ', '.join(data['other_failed_services'][:10]))
        backup = data.get('backup_repository', {})
        if backup:
            lines.append('Backup check: ' + ('passed' if backup.get('ok') and not backup.get('deferred') else str(backup.get('detail', 'needs review'))[:200]))
        coordination = data.get('coordination', {}).get('issues', [])
        if coordination:
            lines.append('Coordination reviews: ' + str(len(coordination)) + '. See the full check for details.')
        return '\n'.join(lines), 'daily:' + str(day), 7 * 86400
    if kind in ('incident_open', 'incident_resolved'):
        label = 'ALERT' if kind == 'incident_open' else 'RESOLVED'
        lines = [f"Maestro | {label}", f"{data.get('target', 'host')}: {data.get('code', 'issue')}"]
        if kind == 'incident_open':
            lines.append(str(data.get('detail', 'Check /health for details.'))[:800])
        # Each transition is delivered once, including reopen after resolution.
        key = 'incident:' + str(data.get('target')) + ':' + str(data.get('code'))
        return '\n'.join(lines), key + ':' + kind, 7 * 86400
    if kind == 'job_finished':
        key = str(data['id'])
        text = f"Maestro | {data.get('kind', 'Job')} {data.get('state', 'finished')}\nJob: {key}\nExit code: {data.get('exit_code')}"
        return text, 'job:' + key, 7 * 86400
    # Stable keys omit changing timestamps, checkpoint IDs and output. Repeated
    # operational reviews belong in the daily digest, not hourly messages.
    compact = json.dumps(data, ensure_ascii=False, sort_keys=True)
    identity = [kind, data.get('target', ''), data.get('action', ''), data.get('code', ''), data.get('verified_ok', data.get('state', ''))]
    signature = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return 'Maestro | ' + kind.replace('_', ' ').title() + '\n' + compact[:1800], 'alert:' + signature, 86400
