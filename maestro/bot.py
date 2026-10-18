from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import time
from pathlib import Path

import discord

from .discord_ui import Commands, JobView, event_text, owner_allowed, respond, validate_private_channel
from .ipc import ActorClient, Client
from .security import Denied
from .watchdog import notify
from .delivery import job_attachments, text_parts
from .notifications import enabled, incident_notifications, notification
from . import guides

log = logging.getLogger("maestro.discord")


def validate_report_channel(channel, owner_id):
    validate_private_channel(channel, owner_id)


class Maestro(discord.Client):
    def __init__(self, env):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.owner_id = int(env["OWNER_DISCORD_ID"])
        self.guild_id = int(env["GUILD_ID"])
        tls = {"cert": env["AGENT_TLS_CERT"], "key": env["AGENT_TLS_KEY"], "ca": env["AGENT_TLS_CA"], "port": int(env["AGENT_TLS_PORT"])} if env.get("AGENT_TLS_PORT") else None
        self.api = ActorClient(Client(env.get("AGENT_SOCKET", "/run/maestro/agent.sock"), env["AGENT_TOKEN"], "discord:" + str(self.owner_id), tls=tls))
        self.tree = discord.app_commands.CommandTree(self)
        self.tree.on_error = self.command_error
        self.tasks = []
        self.front_state = Path(env.get("FRONTEND_STATE", "/var/lib/maestro/frontend.json"))
        self.report_state_path = self.front_state.with_name(self.front_state.stem + '-notifications.json')
        self.report_state = json.loads(self.report_state_path.read_text()) if self.report_state_path.is_file() else {}
        self.last_channel = 0
        self.last_error_alert = 0
        self.delivery_tick = time.monotonic()
        if self.front_state.is_file():
            self.last_channel = int(json.loads(self.front_state.read_text()).get("report_channel", 0))

    async def setup_hook(self):
        guild = discord.Object(id=self.guild_id)
        self.tree.add_command(Commands(self), guild=guild)
        await self.tree.sync(guild=guild)
        self.tasks = [asyncio.create_task(self.deliver(), name="assistant-delivery"), asyncio.create_task(self.heartbeat(), name="assistant-frontend-watchdog")]

    async def on_ready(self):
        notify("READY=1\nSTATUS=Discord owner interface connected")
        log.info("Discord owner interface ready")

    async def command_error(self, i, error):
        if not owner_allowed(self, i):
            await respond(i, "Owner access required.")
            return
        original = getattr(error, "original", error)
        text = str(original)[:350] if isinstance(original, (Denied, ValueError, ConnectionError)) else type(original).__name__
        await respond(i, guides.friendly_error(text))

    def remember_channel(self, key):
        self.last_channel = key
        self.front_state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = self.front_state.with_suffix(".tmp")
        temp.write_text(json.dumps({"report_channel": key}))
        temp.chmod(0o600)
        temp.replace(self.front_state)

    async def deliver_event(self, channel, event):
        validate_report_channel(channel, self.owner_id)
        text = event_text(event)
        if len(text) <= 1800:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        else:
            for name, data in text_parts('assistant-report.txt', text):
                await channel.send("Maestro report. ID: " + str(event["id"]),
                                   file=discord.File(io.BytesIO(data), filename=name), allowed_mentions=discord.AllowedMentions.none())
        if event['kind'] == 'job_finished':
            try:
                user = self.get_user(self.owner_id) or await self.fetch_user(self.owner_id)
                async for name, data in job_attachments(self.api, event['payload']['id']):
                    await user.send('Private output | job ' + event['payload']['id'], file=discord.File(io.BytesIO(data), filename=name), allowed_mentions=discord.AllowedMentions.none())
            except (Denied, discord.Forbidden):
                # Jobs of unrelated owners stay private. If owner DMs are disabled,
                # the completed job remains downloadable from the private panel.
                pass
        # Delivery is at-least-once. An API outage between send and ack may repeat this report ID.
        await self.api.call("ack", id=event["id"])

    def save_report_state(self):
        self.report_state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.report_state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.report_state))
        temporary.chmod(0o600)
        temporary.replace(self.report_state_path)

    async def send_notification(self, channel, message, job=None):
        # Short automatic notices only. Full output stays behind owner-only controls.
        if len(message) > 1900:
            message = message[:1800] + '\nDetails: /maestro panel'
        kwargs = {'allowed_mentions': discord.AllowedMentions.none()}
        if job:
            kwargs['view'] = JobView(self, job)
        await channel.send(message, **kwargs)

    async def deliver_quiet(self, channel, settings):
        # Validate before sending OR acknowledging: an unsafe destination must
        # leave its reports queued. Telegram delivery has a separate recipient.
        validate_report_channel(channel, self.owner_id)
        snapshot = await self.api.call('status')
        now = time.time()
        history = self.report_state.setdefault('incident_notifications_v2', {})
        for message, key, record in incident_notifications(snapshot, history, now):
            category = 'alerts' if record.get('announced_open') else 'recovery'
            if not enabled(settings, category):
                if category == 'recovery':
                    history[key] = record
                continue
            await self.send_notification(channel, message)
            history[key] = record
            self.save_report_state()
        self.report_state['incident_notifications_v2'] = {
            key: record for key, record in history.items()
            if record.get('announced_open') or now - record.get('seen_at', 0) < 7 * 86400}
        self.save_report_state()
        for event in await self.api.call('events'):
            kind = event['kind']
            if kind in ('incident_open', 'incident_resolved'):
                await self.api.call('ack', id=event['id'])
                continue
            if kind in ('scheduled_report', 'job_finished') and now - event['at'] > 86400:
                await self.api.call('ack', id=event['id'])
                continue
            result = notification(event, settings)
            if result is not None:
                message, key, cooldown = result
                sent = self.report_state.setdefault('notification_sent', {})
                if now - sent.get(key, 0) >= cooldown:
                    job = event['payload']['id'] if kind == 'job_finished' else None
                    await self.send_notification(channel, message, job)
                    sent[key] = now
                    self.report_state['notification_sent'] = dict(sorted(
                        ((key, stamp) for key, stamp in sent.items() if now - stamp < 7 * 86400),
                        key=lambda pair: pair[1])[-1024:])
                    # Persist first: an ack failure or restart must not repeat a notice.
                    self.save_report_state()
            await self.api.call('ack', id=event['id'])

    async def deliver_once(self):
        settings = await self.api.call('settings')
        if not settings.get('notify_discord', True):
            for event in await self.api.call('events'):
                await self.api.call('ack', id=event['id'])
            return
        key = settings['report_channel']
        if key != self.last_channel:
            self.remember_channel(key)
        guild = self.get_guild(self.guild_id)
        if key and guild:
            channel = guild.get_channel(key)
            if self.report_state.get('report_mode', 'alerts') == 'all' and all(settings.get('notify_'+category,True) for category in ('alerts','recovery','jobs','daily')):
                validate_report_channel(channel, self.owner_id)
                for event in await self.api.call('events'):
                    await self.deliver_event(channel, event)
            else:
                await self.deliver_quiet(channel, settings)

    async def alert_owner(self, text):
        if time.time() - self.last_error_alert < 1800:
            return
        self.last_error_alert = time.time()
        user = self.get_user(self.owner_id) or await self.fetch_user(self.owner_id)
        await user.send("Maestro: " + text, allowed_mentions=discord.AllowedMentions.none())

    async def deliver(self):
        await self.wait_until_ready()
        while not self.is_closed():
            self.delivery_tick = time.monotonic()
            try:
                await self.deliver_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("Report delivery unavailable: %s", type(exc).__name__)
                try:
                    await self.alert_owner("Report delivery is unavailable (" + type(exc).__name__ + "). VPS reports remain queued. Check the private panel or service journals.")
                except discord.HTTPException:
                    pass
            await asyncio.sleep(30)

    async def heartbeat(self):
        await self.wait_until_ready()
        while not self.is_closed():
            if self.is_ready() and self.tasks and all(not t.done() for t in self.tasks) and time.monotonic() - self.delivery_tick < 360:
                notify("WATCHDOG=1")
            await asyncio.sleep(20)

    async def close(self):
        notify("STOPPING=1")
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await super().close()


def main():
    required = ("DISCORD_TOKEN", "OWNER_DISCORD_ID", "GUILD_ID", "AGENT_TOKEN")
    if any(not os.environ.get(key) for key in required):
        raise SystemExit("Complete the protected frontend environment file before starting.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    bot = Maestro(dict(os.environ))
    bot.run(os.environ["DISCORD_TOKEN"], log_handler=None)


if __name__ == "__main__":
    main()
