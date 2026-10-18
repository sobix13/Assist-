import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from maestro.notifications import notification, incident_notifications, daily_only
from maestro.telegram_bot import TelegramMaestro


def event(kind, payload, key=1):
    return dict(id=key, kind=kind, payload=payload, at=time.time())


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.json'
        self.events = []
        self.acks = []
        self.open_incidents = []
        self.settings = {'telegram_reports': True, 'timezone': 'Asia/Tehran'}
        async def call(action, **params):
            if action == 'settings':
                return self.settings
            if action == 'events':
                return list(self.events)
            if action == 'status':
                return {'open_incidents': list(self.open_incidents)}
            if action == 'ack':
                self.acks.append(params['id'])
                self.events = [e for e in self.events if e['id'] != params['id']]
        self.agent = type('Agent', (), {'call': AsyncMock(side_effect=call), 'token': ''})()
        self.api = type('API', (), {'call': AsyncMock(), 'document': AsyncMock(), 'token': ''})()
        self.bot = TelegramMaestro(123456789, self.agent, self.api, self.path)
        self.bot.state['started'] = True

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_hourly_manual_inventory_and_duplicate_diagnosis_are_acked_without_messages(self):
        self.events = [event(k, {'kind': 'hourly'}, i) for i, k in enumerate(
            ['scheduled_report', 'manual_report', 'services_added', 'inventory_changed', 'diagnosis'], 1)]
        await self.bot.deliver_once()
        self.assertEqual(len(self.acks), 5)
        self.api.call.assert_not_awaited()
        self.api.document.assert_not_awaited()

    async def test_alert_and_resolution_are_short_and_reopening_is_not_silenced(self):
        data = {'target': 'worker', 'code': 'unit_down', 'detail': 'Failed'}
        for i, kind in enumerate(['incident_open', 'incident_open', 'incident_resolved', 'incident_resolved', 'incident_open'], 1):
            self.open_incidents = [{**data, 'key': 'worker:unit_down'}] if kind == 'incident_open' else []
            self.events = [event(kind, data, i)]
            await self.bot.deliver_once()
        self.assertEqual(self.api.call.await_count, 1)
        self.assertIn('ALERT', self.api.call.call_args.kwargs['text'])

    async def test_backup_resource_and_recovery_alerts_remain_visible(self):
        self.open_incidents = [{'key': 'host:resource_pressure', 'target': 'host', 'code': 'resource_pressure', 'detail': 'Disk low'}]
        self.events = [event('checkpoint_review', {'detail': 'Repository failed'}, 1),
                       event('incident_open', {'target': 'host', 'code': 'resource_pressure', 'detail': 'Disk low'}, 2),
                       event('recovery', {'target': 'worker', 'action': 'blocked', 'detail': 'Checkpoint failed'}, 3)]
        await self.bot.deliver_once()
        self.assertEqual(self.api.call.await_count, 3)
        self.api.document.assert_not_awaited()

    async def test_duplicate_backup_alerts_are_limited_to_one_per_hour(self):
        self.events = [event('checkpoint_review', {'detail': 'Repository failed'}, i) for i in (1, 2)]
        await self.bot.deliver_once()
        self.assertEqual(self.api.call.await_count, 1)
        self.assertEqual(len(self.acks), 2)

    async def test_daily_summary_is_once_per_day_and_has_no_raw_journal(self):
        data = {'kind': 'daily', 'targets': [{'id': 'worker', 'ok': False, 'journal': 'private-log' * 1000}],
                'host': {'disk_free_percent': 50, 'memory_available_percent': 70},
                'backup_repository': {'ok': False, 'detail': 'check failed'},
                'other_failed_services': ['nginx.service']}
        self.events = [event('scheduled_report', data, i) for i in (1, 2)]
        await self.bot.deliver_once()
        self.assertEqual(self.api.call.await_count, 1)
        text = self.api.call.call_args.kwargs['text']
        self.assertIn('worker', text)
        self.assertIn('nginx.service', text)
        self.assertIn('check failed', text)
        self.assertNotIn('private-log', text)

    async def test_job_result_has_details_button_without_automatic_attachments(self):
        self.events = [event('job_finished', {'id': 'abc123', 'state': 'failed', 'exit_code': 1, 'kind': 'service'})]
        await self.bot.deliver_once()
        params = self.api.call.call_args.kwargs
        self.assertIn('failed', params['text'])
        self.assertEqual(params['reply_markup']['inline_keyboard'][0][0]['callback_data'], 'job:abc123')
        self.api.document.assert_not_awaited()

    async def test_failed_send_is_not_acknowledged_and_can_retry(self):
        self.open_incidents = [{'key': 'worker:unit_down', 'target': 'worker', 'code': 'unit_down'}]
        self.events = [event('incident_open', {'target': 'worker', 'code': 'unit_down'})]
        self.api.call.side_effect = ConnectionError('offline')
        with self.assertRaises(ConnectionError):
            await self.bot.deliver_once()
        self.assertEqual(self.acks, [])
        self.assertFalse(self.bot.state.get('notification_sent'))
        self.api.call.side_effect = None
        await self.bot.deliver_once()
        self.assertEqual(self.acks, [1])

    async def test_ack_failure_and_frontend_restart_do_not_repeat_sent_alert(self):
        self.open_incidents = [{'key': 'worker:unit_down', 'target': 'worker', 'code': 'unit_down'}]
        self.events = [event('incident_open', {'target': 'worker', 'code': 'unit_down'})]
        call = self.agent.call.side_effect
        async def fail_ack(action, **params):
            if action == 'ack':
                raise ConnectionError('ack failed')
            return await call(action, **params)
        self.agent.call.side_effect = fail_ack
        with self.assertRaises(ConnectionError):
            await self.bot.deliver_once()
        self.agent.call.side_effect = call
        bot = TelegramMaestro(123456789, self.agent, self.api, self.path)
        await bot.deliver_once()
        self.assertEqual(self.api.call.await_count, 1)
        self.assertEqual(self.acks, [1])

    async def test_hundreds_of_old_transitions_do_not_replay_as_live_alerts(self):
        self.events = [event('incident_open' if i % 2 else 'incident_resolved',
                            {'target': 'worker', 'code': 'worker_unhealthy'}, i) for i in range(1, 201)]
        for row in self.events:
            row['at'] -= 86400
        self.open_incidents = [{'key': 'worker:worker_unhealthy', 'target': 'worker',
                                'code': 'worker_unhealthy', 'detail': 'poller stale now', 'first_seen': time.time() - 900}]
        await self.bot.deliver_once()
        self.assertEqual(len(self.acks), 200)
        self.assertEqual(self.api.call.await_count, 1)
        self.assertIn('poller stale now', self.api.call.call_args.kwargs['text'])
        self.assertNotIn('RESOLVED', self.api.call.call_args.kwargs['text'])

    async def test_old_open_events_for_a_currently_healthy_worker_stay_silent(self):
        self.events = [event('incident_open', {'target': 'worker', 'code': 'worker_unhealthy'})]
        await self.bot.deliver_once()
        self.api.call.assert_not_awaited()
        self.assertEqual(self.acks, [1])

    async def test_recovery_waits_ten_minutes_and_reappearing_failure_cancels_it(self):
        row = {'key': 'worker:unit_down', 'target': 'worker', 'code': 'unit_down'}
        with patch('maestro.telegram_bot.time.time', return_value=10000):
            self.open_incidents = [row]
            await self.bot.deliver_once()
        with patch('maestro.telegram_bot.time.time', return_value=10060):
            self.open_incidents = []
            await self.bot.deliver_once()
        with patch('maestro.telegram_bot.time.time', return_value=10180):
            self.open_incidents = [row]
            await self.bot.deliver_once()
        self.assertEqual(self.api.call.await_count, 1)
        with patch('maestro.telegram_bot.time.time', return_value=10200):
            self.open_incidents = []
            await self.bot.deliver_once()
        with patch('maestro.telegram_bot.time.time', return_value=10799):
            await self.bot.deliver_once()
        self.assertEqual(self.api.call.await_count, 1)
        with patch('maestro.telegram_bot.time.time', return_value=10800):
            await self.bot.deliver_once()
        self.assertEqual(self.api.call.await_count, 2)
        self.assertIn('RESOLVED', self.api.call.call_args.kwargs['text'])
        with patch('maestro.telegram_bot.time.time', return_value=10820):
            self.open_incidents = [row]
            await self.bot.deliver_once()
        self.assertEqual(self.api.call.await_count, 3)
        self.assertIn('ALERT', self.api.call.call_args.kwargs['text'])

    async def test_live_persistent_failure_has_no_hourly_reminders(self):
        self.open_incidents = [{'key': 'host:resource_pressure', 'target': 'host', 'code': 'resource_pressure'}]
        for stamp in (10000, 10030, 11000, 13599):
            with patch('maestro.telegram_bot.time.time', return_value=stamp):
                await self.bot.deliver_once()
        self.assertEqual(self.api.call.await_count, 1)
        with patch('maestro.telegram_bot.time.time', return_value=13600):
            await self.bot.deliver_once()
        self.assertEqual(self.api.call.await_count, 1)

    def test_truncated_or_unavailable_snapshot_never_claims_recovery(self):
        history = {'missing:unit_down': {'announced_open': True, 'closed_since': 100, 'seen_at': 100}}
        rows = [{'key': str(i), 'target': str(i), 'code': 'unit_down', 'detail': ''} for i in range(30)]
        plans = incident_notifications({'open_incidents': rows}, history, 10000)
        self.assertFalse(any('RESOLVED' in text for text, key, record in plans))
        self.assertEqual(incident_notifications({}, history, 10000), [])

    async def test_direct_replies_and_requested_downloads_are_not_filtered(self):
        await self.bot.text('Requested reply')
        await self.bot.text('x' * 4000, name='requested.txt')
        self.assertEqual(self.api.call.await_count, 1)
        self.assertEqual(self.api.document.await_count, 1)

    async def test_reports_off_setting_is_respected_without_modifying_it(self):
        self.settings['telegram_reports'] = False
        self.events = [event('incident_open', {})]
        await self.bot.deliver_once()
        self.api.call.assert_not_awaited()
        self.assertEqual(self.acks, [])

    async def test_notification_mode_is_configurable_and_persisted(self):
        await self.bot.callback('notify_mode:all')
        bot = TelegramMaestro(123456789, self.agent, self.api, self.path)
        self.assertEqual(bot.state['report_mode'], 'all')
        await bot.callback('notify_mode:alerts')
        self.assertEqual(bot.state['report_mode'], 'alerts')

    async def test_all_mode_preserves_legacy_delivery(self):
        self.bot.state['report_mode'] = 'all'
        self.events = [event('incident_open', {'target': 'worker', 'code': 'unit_down'})]
        await self.bot.deliver_once()
        self.assertIn('Report ID', self.api.call.call_args.kwargs['text'])

    def test_unknown_future_alert_is_not_silenced(self):
        message, key, cooldown = notification(event('new_failure', {'detail': 'important'}), self.settings)
        self.assertIn('important', message)
        self.assertEqual(cooldown, 86400)

    def test_daily_summary_respects_timezone(self):
        row = event('scheduled_report', {'kind': 'daily'})
        row['at'] = 0
        self.assertIn('1970-01-01', notification(row, self.settings)[0])
