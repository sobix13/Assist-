# Troubleshooting

Use Health, Host inventory, target checks, fixed diagnostics and private output in either interface. The bot records incidents and known diagnosis evidence; unknown failures remain visible for review.

| Symptom | Evidence / next action |
|---|---|
| New service is not monitored automatically | Its card is created disabled with external recovery ownership. Review paths/health, then enable the intended monitoring/recovery fields. |
| Delegate denied or old button stopped working | Check numeric transport identity, assigned service IDs, grant expiry/revision and revocation. Only the superadmin can renew. |
| Verifier contract mismatch | Installed source/version/schema changed. Update a reviewed versioned adapter rather than bypassing the pin. |
| Verifier setup partial | Inspect recorded IDs and live Discord resources. No uncertain HTTP mutation is retried automatically. |
| Application rollback failed | Previous values may be restored while reload/health is still failing. Inspect logs/checkpoint; the result is unresolved. |
| Database ZIP coverage incomplete | Review bounded inventory roots and manifest, enroll missing stores/consistent export hooks, or select a smaller service. |
| Execution blocked by checkpoint | Inspect coverage/status and failed checkpoint. Check repository credentials, free space, exports, integrity, restore verification and writer resumption. Do not bypass backup because the command seems small. |
| Partial Restic backup | Some source data was unreadable; execution did not run. Review filesystem/exclusions/consistent stores, then create a new checkpoint. |
| Interrupted writer pause | Startup report names recorded writer units. Check whether they resumed; use a reviewed service start if needed. No owner code was replayed. |
| Invalid token | Replace locally in its protected environment file. Recheck only that service. A restart cannot fix credentials. |
| Discord channel access failure | Inspect effective bot permissions and non-owner access. Save the selected channel. If owner DMs are disabled, download output from the panel. |
| Telegram webhook / competing poller | Resolve the existing integration locally. This frontend never silently deletes another webhook. One poller per bot token. |
| Telegram 429 / network outage | Retry delay/backoff applies; recipient reports remain queued. Look for repeating report IDs rather than treating them as new incidents. |
| Corrupt SQLite | Preserve DB/WAL; block recovery, verify backup, stage offline restore and review schema compatibility. |
| SQLite busy / shared setup lease | Inspect writers and leases; do not delete lock tables or replace a live database. |
| Port conflict | Use Ports diagnostic and correct the reviewed configuration. Do not kill an unknown listener. |
| Missing dependency | Review interpreter/requirements; stage a tested isolated release. No automatic pip upgrade in a running app. |
| Disk/inodes/memory pressure | Inspect host/volume metrics and diagnostics. Data/backups are never automatically deleted. |
| Expiring/invalid certificate | Check public certificate path, expiry and clock. Do not disable TLS verification. |
| Process active but stale worker | Supply actual readiness/heartbeat; FUTARCHIST uses its SQLite worker records. Process state alone is not useful-work proof. |
| Melee not restarted | Default external recovery ownership prevents competition with its existing controller. |
| Recipe rolled back / rollback failed | Inspect action outcome, file rollback directory and verification result. New database data stops automatic deletion; retained snapshots remain available. |
| Output exceeds limit | Process group stopped; output is marked incomplete. Increase bounded limit or write the command's result to an explicitly reviewed file. |
| Backend disconnected mid-confirm | Inspect job history before retrying. Confirmation is single-use; no automatic replay. |
| Runner UID failure | Verify the separate account and permissions. Runner never falls back to root. |
| VPS fully offline | Use external witness/provider console and off-host backup. On-host services cannot send during total host loss. |

Service journals:

```bash
sudo journalctl -u maestro-agent.service -n 100 --no-pager -l
sudo journalctl -u maestro-discord.service -n 100 --no-pager -l
sudo journalctl -u maestro-telegram.service -n 100 --no-pager -l
```

Use the packaged DEPLOYMENT, BACKUPS and ACCEPTANCE guides for initial setup and recovery. Keep passwords out of reports and public issue descriptions.
