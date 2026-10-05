# Optional second-host witness

Deploy this component on **another machine** if alerts must survive the monitored VPS stopping. It is outbound notification plus a TCP connection probe; it never executes VPS commands and does not open the root broker publicly.

Choose an externally reachable listener such as your SSH port. Two failed observations produce one incident; a successful observation produces recovery. Default interval is 60 seconds. A daily UTC summary is sent after 18:00. A closed SSH port can be a listener/firewall problem rather than loss of the whole VPS.

Install the same tested release on the second host, without enrolling/starting the main agent or chat interfaces. Create only the witness account and state path:

```bash
sudo useradd --system --user-group --home-dir /var/lib/maestro-external --shell /usr/sbin/nologin maestro-external
sudo install -d -m 0700 -o maestro-external -g maestro-external /var/lib/maestro-external
sudo install -d -m 0700 /etc/maestro
sudo touch /etc/maestro/external.env
sudo chmod 0600 /etc/maestro/external.env
sudoedit /etc/maestro/external.env
```

Protected file fields:

```text
WATCH_HOST=YOUR_MONITORED_VPS_ADDRESS
WATCH_PORT=22
WATCH_INTERVAL=60
WATCH_STATE=/var/lib/maestro-external/state.json
TELEGRAM_TOKEN=YOUR_NOTIFICATION_BOT_TOKEN
OWNER_TELEGRAM_ID=YOUR_NUMERIC_OWNER_ID
DISCORD_WEBHOOK=https://discord.com/api/webhooks/ID/PRIVATE_WEBHOOK_TOKEN
```

At least one alert interface is required. Omit both Telegram fields or the Discord webhook if unused. Use a webhook in a private owner channel. Telegram needs an existing private chat with the notification bot. This witness only sends messages; it does not poll updates. A separate notification token limits sharing credentials between hosts.

Copy the supplied unit and start:

```bash
sudo install -m 0644 /opt/maestro/current/maestro-external.service /etc/systemd/system/maestro-external.service
sudo systemctl daemon-reload
sudo systemctl enable --now maestro-external.service
sudo journalctl -u maestro-external.service -n 50 --no-pager
```

Retained witness events are acknowledged separately per alert interface and bounded at 200 recent events. Delivery can repeat an event ID after interruption. The main package installer does not automatically deploy/start this component. Provider-level console/reboot/snapshot recovery remains an explicitly enrolled future adapter.
