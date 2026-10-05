"""Optional reachability witness on a second host. It never executes VPS commands."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

import aiohttp

from .telegram_bot import BotAPI


async def reachable(host, port, timeout=5):
    writer = None
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        return True
    except (OSError, asyncio.TimeoutError):
        return False
    finally:
        if writer:
            writer.close()
            await writer.wait_closed()


class Witness:
    def __init__(self, env):
        self.env = env
        self.host, self.port = env['WATCH_HOST'], int(env.get('WATCH_PORT', 22))
        self.interval = int(env.get('WATCH_INTERVAL', 60))
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,253}', self.host) or not 1 <= self.port <= 65535 or not 30 <= self.interval <= 3600:
            raise ValueError('Use a valid locally configured reachability target and interval.')
        self.path = Path(env.get('WATCH_STATE', '/var/lib/maestro-external/state.json'))
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state = {'failed':0, 'down':False, 'day':'', 'queue':[]}
        if self.path.is_file():
            self.state.update(json.loads(self.path.read_text()))
        self.telegram = BotAPI(env['TELEGRAM_TOKEN']) if env.get('TELEGRAM_TOKEN') else None
        self.owner = int(env['OWNER_TELEGRAM_ID']) if self.telegram else 0
        self.webhook = env.get('DISCORD_WEBHOOK', '')
        if self.webhook and not re.fullmatch(r'https://discord\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+', self.webhook):
            raise ValueError('Use a Discord webhook from a private owner channel.')
        if not self.telegram and not self.webhook:
            raise ValueError('Configure at least one external alert interface.')

    def save(self):
        temp = self.path.with_suffix('.new')
        temp.write_text(json.dumps(self.state))
        temp.chmod(0o600)
        temp.replace(self.path)

    def observe(self, good, now=None):
        now = time.time() if now is None else now
        self.state['failed'] = 0 if good else self.state['failed']+1
        self.state['last_probe'] = now
        down = self.state['failed'] >= 2
        if good and self.state['down'] or down and not self.state['down']:
            self.state['down'] = down
            self.enqueue(('Recovered' if good else 'Unreachable') + ' TCP ' + self.host + ':' + str(self.port) +
                         '. This checks the chosen listener from another host; it does not prove every application is healthy.')
        day = time.strftime('%Y-%m-%d', time.gmtime(now))
        if time.gmtime(now).tm_hour >= 18 and self.state['day'] != day:
            self.state['day'] = day
            self.enqueue('Daily external reachability: ' + ('listener reachable' if good else 'listener unreachable') +
                         ' | ' + self.host + ':' + str(self.port))
        self.save()

    def enqueue(self, text):
        key = str(time.time_ns())
        self.state['queue'].append({'id':key, 'text':text, 'telegram':not bool(self.telegram), 'discord':not bool(self.webhook)})
        self.state['queue'] = self.state['queue'][-200:]

    async def deliver(self, session):
        for row in self.state['queue'][:10]:
            text = 'Maestro | external witness\nReport '+row['id']+'\n'+row['text']
            if not row['telegram']:
                await self.telegram.call('sendMessage', chat_id=self.owner, text=text)
                row['telegram'] = True
                self.save()
            if not row['discord']:
                async with session.post(self.webhook, json={'content':text,'allowed_mentions':{'parse':[]}}, allow_redirects=False) as response:
                    if response.status not in (200,204):
                        raise ConnectionError('External Discord delivery unavailable.')
                row['discord'] = True
                self.save()
        self.state['queue'] = [r for r in self.state['queue'] if not (r['telegram'] and r['discord'])]
        self.save()

    async def run(self):
        if self.telegram:
            await self.telegram.open()
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), trust_env=False) as session:
                while True:
                    self.observe(await reachable(self.host, self.port))
                    try:
                        await self.deliver(session)
                    except Exception as exc:
                        logging.error('External delivery unavailable: %s', type(exc).__name__)
                    await asyncio.sleep(self.interval)
        finally:
            if self.telegram:
                await self.telegram.close()


def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(Witness(dict(os.environ)).run())


if __name__ == '__main__':
    main()
