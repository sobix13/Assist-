"""Versioned observations and fixed recovery actions. No generated code or AI."""

RULES = (
    ("discord_permissions", ("Missing Permissions", "Missing Access", "error code: 50013", "error code: 50001"),
     "Inspect the affected channel overwrites and bot role hierarchy in Discord. Run that bot's Doctor. A restart does not grant permissions."),
    ("invalid_token", ("LoginFailure", "Improper token", "401 Unauthorized", "Invalid token"),
     "Replace the affected credential in its protected VPS environment file. Restart only that service after reviewing the change."),
    ("port_conflict", ("Address already in use", "Errno 98", "EADDRINUSE"),
     "Inspect listeners with the ports diagnostic. Select a free backend port or correct the conflicting service. Do not terminate an unknown process."),
    ("database_corrupt", ("database disk image is malformed", "file is not a database", "database corruption"),
     "Pause recovery for this target. Preserve the database and WAL. Verify a backup and plan an explicit offline restore."),
    ("database_busy", ("database is locked", "database table is locked"),
     "Inspect leases and active writers. Do not delete lock tables or replace a live database."),
    ("missing_dependency", ("ModuleNotFoundError", "ImportError: No module"),
     "Inspect the service interpreter and that release's requirements lock. Stage a tested release in its own virtual environment."),
    ("provider_failure", ("429 Too Many", "TimeoutError", "ClientConnectorError", "503 Service"),
     "Inspect provider health and configured rate limits. Use a reviewed fallback endpoint. Do not change the holdings ledger."),
    ("disk_full", ("No space left on device", "Errno 28"),
     "Inspect disk and inode use. Remove only reviewed expendable files. Never automatically delete member data or backups."),
    ('dns_failure', ('Temporary failure in name resolution', 'Name or service not known', 'ClientConnectorDNSError'),
     'Inspect resolver and provider connectivity. Do not overwrite resolver configuration automatically. Use a locally reviewed recipe if this host has a known reversible fix.'),
    ('tls_failure', ('CERTIFICATE_VERIFY_FAILED', 'certificate has expired', 'SSLCertVerificationError'),
     'Review certificate expiry, the host clock and trusted CA configuration. Do not disable TLS verification to conceal the error.'),
    ('memory_pressure', ('Out of memory', 'oom-kill', 'OOMKilled'),
     'Review memory and service limits. Preserve diagnostics and backups. Enroll a targeted restart or resource-limit recipe instead of a restart loop.'),
    ('start_limit', ('Start request repeated too quickly', 'start-limit-hit'),
     'Inspect the original failure and its journal. Reset-failed only after the cause is corrected and the operation is reviewed.'),
    ('telegram_conflict', ('terminated by other getUpdates request', 'webhook is active', 'Conflict: terminated'),
     'Stop the competing poller or explicitly remove an obsolete webhook. Do not run two polling services for the same Telegram bot token.'),
)


def classify(text):
    lowered = text.casefold()
    return [{"code": code, "guide": guide} for code, needles, guide in RULES if any(n.casefold() in lowered for n in needles)]


DIAGNOSTICS = {
    "ports": ["ss", "-lntup"],
    "disk": ["df", "-h", "/"],
    "inodes": ["df", "-i", "/"],
    "memory": ["free", "-m"],
    "failed_units": ["systemctl", "--failed", "--no-pager", "--plain"],
    "boot": ["journalctl", "-b", "-p", "err", "-n", "60", "--no-pager"],
}

SERVICE_ACTIONS = {"start", "stop", "restart", "reset-failed", "enable", "disable"}
AUTO_BLOCKERS = {"invalid_token", "discord_permissions", "database_corrupt", "database_busy", "missing_dependency", "port_conflict", "disk_full"}

EVIDENCE_DIAGNOSTICS = {"port_conflict": "ports", "disk_full": "disk", "missing_dependency": "failed_units", "invalid_token": "failed_units"}
