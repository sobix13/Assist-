from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path


class Denied(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def secret_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def unit_name(value):
    if not isinstance(value, str) or len(value) > 128 or not re.fullmatch(r"[A-Za-z0-9_.:@-]+\.service", value) or value.startswith("-"):
        raise Denied("Use an exact systemd .service unit name without paths or shell characters.")
    return value


def repo_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise Denied("Use an owner/repository name.")
    return value


class Redactor:
    def __init__(self, values=()):
        self.values = set(v for v in values if isinstance(v, str) and len(v) >= 8)

    def load_env(self, path):
        try:
            for line in Path(path).read_text().splitlines():
                key, sep, value = line.partition("=")
                if sep and re.search(r"TOKEN|SECRET|PASSWORD|PRIVATE_KEY|API_KEY|FERNET|CREDENTIAL", key, re.I):
                    value = value.strip().strip("\"'")
                    if len(value) >= 8:
                        self.values.add(value)
        except (OSError, UnicodeError):
            pass

    def clean(self, value):
        text = str(value)
        # Clean full accumulated output, never individual chunks of a split token.
        for item in sorted(self.values, key=len, reverse=True):
            text = text.replace(item, "[REDACTED]")
            for length in range(min(len(item) - 1, len(text)), 3, -1):
                if text.endswith(item[:length]):
                    text = text[:-length] + "[REDACTED PARTIAL]"
                    break
        text = re.sub(r"(?i)(?<![A-Z0-9_])([A-Z0-9_]{0,128}(?:TOKEN|SECRET|PASSWORD|PRIVATE_KEY|API_KEY)\s*[=:]\s*)([^\s,]+)", r"\1[REDACTED]", text)
        text = re.sub(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)\S+", r"\1[REDACTED]", text)
        text = re.sub(r"([?&](?:token|key|secret|password)=)[^&\s]+", r"\1[REDACTED]", text, flags=re.I)
        text = re.sub(r"(?<![\w-])[\w-]{23,}\.[\w-]{6,}\.[\w-]{25,}(?![\w-])", "[REDACTED]", text)
        text = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))", "", text)
        # Translation avoids a per-character temporary list for large Unicode
        # output, keeping complete spool redaction within the agent memory budget.
        return text.translate({**{code: None for code in range(32) if code not in (9, 10, 13)}, 0x202e: None})


class Policy:
    def __init__(self, data):
        self.data = data
        if any(key in data and not isinstance(data[key], bool) for key in ('root_commands', 'host_reboot', 'backup_required')):
            raise Denied('Local execution policy flags must be true or false.')
        self.owners = set(data.get("owners", []))
        if not self.owners or any(not re.fullmatch(r"(?:discord|telegram):[0-9]{5,22}", x) for x in self.owners):
            raise Denied("Set immutable numeric owner identities in the root-owned policy.")
        self.frontends = data.get("frontends", [])
        if not self.frontends:
            raise Denied("A local frontend identity is required.")
        for f in self.frontends:
            if f.get("transport") not in ("discord", "telegram") or not isinstance(f.get("uid"), int) or isinstance(f.get('uid'), bool) or f['uid'] < 0 or len(f.get("token", "")) < 40:
                raise Denied("Invalid frontend policy.")
        self.groups = data.get('owner_groups', [])
        if not isinstance(self.groups, list) or any(not isinstance(g, list) or not g or set(g) - self.owners for g in self.groups):
            raise Denied('Owner identity links must contain only locally enrolled identities.')
        linked = [owner for group in self.groups for owner in group]
        if len(linked) != len(set(linked)):
            raise Denied('An owner identity can belong to only one link group.')
        self.runner = data.get("runner_user", "maestro-runner")
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", self.runner):
            raise Denied("Invalid runner account.")

    @classmethod
    def load(cls, path):
        p = Path(path)
        st = p.stat()
        if st.st_uid != 0 or st.st_mode & 0o077 or p.is_symlink():
            raise Denied("Policy must be a root-owned regular file with mode 0600.")
        return cls(json.loads(p.read_text()))

    def authenticate(self, uid, token, actor):
        if not isinstance(actor, str) or not re.fullmatch(r'(discord|telegram):[0-9]{5,22}', actor):
            raise Denied('Invalid numeric actor identity.')
        self.eligible(actor)
        for f in self.frontends:
            if f["uid"] == uid and secrets.compare_digest(f["token"], str(token)) and actor.startswith(f["transport"] + ":"):
                return actor
        raise Denied("Local frontend credential or peer identity rejected.")

    def authenticate_certificate(self, certificate, token, actor):
        if not isinstance(actor, str) or not re.fullmatch(r'(discord|telegram):[0-9]{5,22}', actor):
            raise Denied('Invalid numeric actor identity.')
        self.eligible(actor)
        fingerprint = hashlib.sha256(certificate).hexdigest()
        for f in self.frontends:
            if secrets.compare_digest(f.get("certificate_sha256", ""), fingerprint) and secrets.compare_digest(f["token"], str(token)) and actor.startswith(f["transport"] + ":"):
                return actor
        raise Denied("Client certificate, credential or owner identity rejected.")

    def eligible(self, actor):
        if actor in self.owners:
            return
        checker = getattr(self, 'delegate_checker', None)
        if checker is None:
            raise Denied('Only an enrolled superadmin or active delegate is authorized.')
        checker(actor)

    @property
    def root_commands(self):
        return self.data.get("root_commands") is True

    @property
    def host_reboot(self):
        return self.data.get("host_reboot") is True

    def same_owner(self, first, second):
        return first == second or any(first in group and second in group for group in self.groups)

    @property
    def backup_required(self):
        # Only a deliberate local policy overrides this. No chat action bypasses backups.
        return self.data.get('backup_required', True) is not False


def read_env(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, sep, value = line.partition("=")
            if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                raise Denied("Invalid environment file line.")
            result[key] = value.strip().strip("\"'")
    return result


def isolated_env(workdir):
    return {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": str(workdir),
            "LANG": "C.UTF-8", "TERM": "dumb", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
