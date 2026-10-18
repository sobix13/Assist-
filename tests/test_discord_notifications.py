import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from maestro.bot import Maestro
from maestro.discord_ui import JobView
from maestro.security import Denied
from maestro.store import Store


def event(kind, payload, key=1, age=0):
    return dict(id=key, kind=kind, payload=payload, at=time.time() - age)


class DiscordNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'frontend.json'
        self.env = {'OWNER_DISCORD_ID': '123456789', 'GUILD_ID': '987654321',
                    'AGENT_TOKEN': 'test-agent-token', 'FRONTEND_STATE': str(self.path)}
        self.bot = Maestro(self.env)
        self.events = []
        self.incidents = []
        self.acks = []
        self.settings = {'report_channel': 222222222, 'timezone': 'Asia/Tehran'}
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.send = AsyncMock()
        default, me = SimpleNamespace(id=1), SimpleNamespace(id=777)
        self.guild = SimpleNamespace(default_role=default, me=me,
                                     get_channel=lambda key: self.channel if key == 222222222 else None)
        self.channel.guild = self.guild
        self.channel.overwrites = {}
        self.channel.permissions_for.side_effect = lambda who: SimpleNamespace(
            view_channel=who is not default, send_messages=True, attach_files=True,
            read_message_history=True, embed_links=True)
        self.bot.get_guild = MagicMock(return_value=self.guild)
        self.bot.fetch_user = AsyncMock()
        async def call(action, **params):
            if action == 'settings':
                return dict(self.settings)
            if action == 'events':
                return list(self.events)
            if action == 'status':
                return {'open_incidents': list(self.incidents)}
            if action == 'ack':
                self.acks.append(params['id'])
                self.events = [e for e in self.events if e['id'] != params['id']]
        self.bot.api = SimpleNamespace(call=AsyncMock(side_effect=call))

    async def asyncTearDown(self):
        await self.bot.close()
        self.temp.cleanup()

    async def test_routine_and_old_transitions_are_silent_and_acknowledged(self):
        self.events = [event(kind, {'kind': 'hourly'}, i) for i, kind in enumerate(
            ['scheduled_report', 'manual_report', 'diagnosis', 'services_added', 'inventory_changed'], 1)]
        self.events += [event('incident_open' if i % 2 else 'incident_resolved',
                              {'target': 'worker', 'code': 'worker_unhealthy'}, i, age=86400)
                        for i in range(6, 206)]
        await self.bot.deliver_once()
        self.channel.send.assert_not_awaited()
        self.assertEqual(len(self.acks), 205)

    async def test_current_incident_is_not_repeated_by_backlog_or_restart(self):
        self.incidents = [{'key': 'worker:unit_down', 'target': 'worker', 'code': 'unit_down', 'detail': 'current failure'}]
        self.events = [event('incident_open', {'target': 'worker', 'code': 'unit_down'}, i) for i in range(1, 21)]
        await self.bot.deliver_once()
        await self.bot.deliver_once()
        self.assertEqual(self.channel.send.await_count, 1)
        other = Maestro(self.env)
        other.api, other.get_guild = self.bot.api, self.bot.get_guild
        try:
            await other.deliver_once()
        finally:
            await other.close()
        self.assertEqual(self.channel.send.await_count, 1)
        self.assertIn('current failure', self.channel.send.call_args.args[0])

    async def test_oscillating_failures_do_not_send_recovery_until_stable(self):
        row = {'key': 'worker:unit_down', 'target': 'worker', 'code': 'unit_down'}
        for stamp, rows in [(10000, [row]), (10030, []), (10060, [row]), (10090, []), (10689, [])]:
            self.incidents = rows
            with patch('maestro.bot.time.time', return_value=stamp):
                await self.bot.deliver_once()
        self.assertEqual(self.channel.send.await_count, 1)
        with patch('maestro.bot.time.time', return_value=10690):
            await self.bot.deliver_once()
        self.assertEqual(self.channel.send.await_count, 2)
        self.assertIn('RESOLVED', self.channel.send.call_args.args[0])

    async def test_persistent_incident_does_not_repeat_hourly(self):
        self.incidents = [{'key': 'host:resource_pressure', 'target': 'host', 'code': 'resource_pressure'}]
        for stamp in (10000, 10030, 13599):
            with patch('maestro.bot.time.time', return_value=stamp):
                await self.bot.deliver_once()
        self.assertEqual(self.channel.send.await_count, 1)
        with patch('maestro.bot.time.time', return_value=13600):
            await self.bot.deliver_once()
        self.assertEqual(self.channel.send.await_count, 1)

    async def test_daily_summary_only_once_and_without_files(self):
        report = {'kind': 'daily', 'targets': [{'id': 'worker', 'ok': True, 'journal': 'hidden-log' * 1000}],
                  'host': {'disk_free_percent': 52, 'memory_available_percent': 76}, 'backup_repository': {'ok': True}}
        self.events = [event('scheduled_report', report, i) for i in (1, 2)]
        await self.bot.deliver_once()
        self.assertEqual(self.channel.send.await_count, 1)
        self.assertIn('Daily summary', self.channel.send.call_args.args[0])
        self.assertNotIn('hidden-log', self.channel.send.call_args.args[0])
        self.assertNotIn('file', self.channel.send.call_args.kwargs)

    async def test_job_results_keep_owner_only_sdk_controls_without_automatic_dms(self):
        self.events = [event('job_finished', {'id': 'job-123', 'kind': 'shell', 'state': 'completed', 'exit_code': 0})]
        await self.bot.deliver_once()
        kwargs = self.channel.send.call_args.kwargs
        self.assertIsInstance(kwargs['view'], JobView)
        self.assertEqual(kwargs['view'].key, 'job-123')
        self.assertNotIn('file', kwargs)
        self.bot.fetch_user.assert_not_awaited()
        self.assertFalse(kwargs['allowed_mentions'].everyone)
        self.assertFalse(kwargs['allowed_mentions'].users)
        self.assertFalse(kwargs['allowed_mentions'].roles)

    async def test_failed_send_is_not_acknowledged_and_retries(self):
        self.events = [event('checkpoint_review', {'detail': 'Repository check failed'})]
        self.channel.send.side_effect = ConnectionError('offline')
        with self.assertRaises(ConnectionError):
            await self.bot.deliver_once()
        self.assertEqual(self.acks, [])
        self.assertFalse(self.bot.report_state.get('notification_sent'))
        self.channel.send.side_effect = None
        await self.bot.deliver_once()
        self.assertEqual(self.acks, [1])

    async def test_failed_ack_does_not_repeat_a_sent_notice(self):
        self.events = [event('checkpoint_review', {'detail': 'Repository check failed'})]
        original = self.bot.api.call.side_effect
        async def fail_ack(action, **params):
            if action == 'ack':
                raise ConnectionError('ack failed')
            return await original(action, **params)
        self.bot.api.call.side_effect = fail_ack
        with self.assertRaises(ConnectionError):
            await self.bot.deliver_once()
        self.bot.api.call.side_effect = original
        await self.bot.deliver_once()
        self.assertEqual(self.channel.send.await_count, 1)
        self.assertEqual(self.acks, [1])

    async def test_public_report_channel_does_not_receive_or_ack_reports(self):
        self.channel.permissions_for.side_effect = None
        self.channel.permissions_for.return_value = SimpleNamespace(view_channel=True)
        self.events = [event('checkpoint_review', {'detail': 'private'})]
        with self.assertRaises(Denied):
            await self.bot.deliver_once()
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.acks, [])

    async def test_report_channel_changes_preserve_deduplication_state(self):
        self.bot.report_state['notification_sent'] = {'job:123': time.time()}
        self.bot.save_report_state()
        self.bot.remember_channel(333333333)
        other = Maestro(self.env)
        try:
            self.assertIn('job:123', other.report_state['notification_sent'])
            self.assertEqual(other.last_channel, 333333333)
            self.assertEqual(other.report_state_path.stat().st_mode & 0o777, 0o600)
        finally:
            await other.close()

    async def test_long_automatic_notice_stays_text_and_does_not_ping(self):
        await self.bot.send_notification(self.channel, '@everyone ' + 'x' * 5000)
        self.assertLessEqual(len(self.channel.send.call_args.args[0]), 2000)
        self.assertNotIn('file', self.channel.send.call_args.kwargs)
        self.assertFalse(self.channel.send.call_args.kwargs['allowed_mentions'].everyone)

    async def test_old_job_results_do_not_replay(self):
        self.events = [event('job_finished', {'id': 'old', 'kind': 'shell', 'state': 'completed', 'exit_code': 0}, age=90000)]
        await self.bot.deliver_once()
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.acks, [1])

    async def test_telegram_delivery_queue_is_independent(self):
        store = Store(Path(self.temp.name) / 'broker.sqlite3')
        try:
            key = store.event('scheduled_report', {'kind': 'hourly'}, ['discord:123456789', 'telegram:313342234'])
            async def call(action, **params):
                if action == 'events':
                    return store.events_for('discord:123456789')
                if action == 'ack':
                    return store.acknowledge(params['id'], 'discord:123456789')
                if action == 'status':
                    return {'open_incidents': []}
                return self.settings
            self.bot.api.call.side_effect = call
            await self.bot.deliver_once()
            self.assertEqual(store.events_for('discord:123456789'), [])
            self.assertEqual(store.events_for('telegram:313342234')[0]['id'], key)
        finally:
            store.db.close()
