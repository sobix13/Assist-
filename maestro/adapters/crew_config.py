"""Validated, revisioned settings. No executable regex or remote URL fetching."""
from __future__ import annotations

import copy
import json
import re
from urllib.parse import urlsplit


FILTERS = {
    "words": (True, "warn", 2),
    "blocked_domain": (True, "warn", 5),
    "lookalike": (True, "alert", 0),
    "masked_link": (True, "alert", 0),
    "secret_request": (True, "alert", 0),
    "gift_scam": (True, "alert", 0),
    "invite": (False, "warn", 2),
    "attachment": (True, "warn", 3),
    "mentions": (True, "warn", 2),
    "flood": (True, "warn", 2),
    "duplicate": (True, "warn", 2),
    "caps": (False, "warn", 1),
    "newlines": (False, "warn", 1),
    "impersonation": (True, "alert", 0),
    "member_cooldown": (True, "delete", 0),
}

DEFAULTS = {
    "brand": "Rip Cars",
    "bot_name": "Ripcars Crew",
    "color": "#800020",
    "website": "https://app.ripcars.io",
    "moderator_roles": [],
    "member_role": 0,
    "log_channel": 0,
    "guard": {
        "enabled": False, "mode": "protect", "exempt_roles": [], "exempt_channels": [],
        "include_channels": [], "trusted_bot_ids": [], "scan_webhooks": True,
        "scan_bots": False, "warn_expiry_days": 30, "timeout_after": 3,
        "timeout_seconds": 600, "heat_threshold": 8, "heat_decay_per_minute": 1,
        "mention_limit": 6, "flood_count": 7, "flood_seconds": 8,
        "duplicate_count": 4, "duplicate_seconds": 30, "caps_ratio": 0.85,
        "caps_min_letters": 25, "newline_limit": 15, "notice_seconds": 60,
        "words": [], "allowed_domains": ["app.ripcars.io", "ripcars.io"],
        "blocked_domains": [], "official_domains": ["ripcars.io"],
        "protected_names": ["Rip Cars Support", "Ripcars Team"],
        "blocked_extensions": ["exe", "scr", "bat", "cmd", "ps1", "js", "vbs", "lnk", "msi"],
        "filters": {key: {"enabled": enabled, "action": action, "weight": weight}
                    for key, (enabled, action, weight) in FILTERS.items()},
    },
    "tickets": {
        "enabled": False, "panel_channel": 0, "open_category": 0, "closed_category": 0,
        "categories": [
            {"key": "packs", "label": "Packs & rips", "enabled": True},
            {"key": "account", "label": "Account & site", "enabled": True},
            {"key": "bugs", "label": "Report a bug", "enabled": True},
            {"key": "other", "label": "Something else", "enabled": True},
        ],
        "create_cooldown_seconds": 120, "auto_delete_days": 0,
        "transcript_messages": 2000, "transcript_bytes": 6_000_000,
    },
    "native": {"enabled": False},
    "monitoring": {
        "activity_days": 35, "store_previews": False, "preview_count": 5,
        "case_retention_days": 180, "error_retention_days": 30,
        "raid_join_count": 10, "raid_window_seconds": 30,
        "new_account_hours": 24, "audit_events": True,
    },
    "texts": {
        "ticket_title": "Rip Cars | Pit stop",
        "ticket_description": "Need a hand with a pack, your account, or the site? Pick a topic below. One open ticket at a time.",
        "ticket_welcome": "You're in the right place. Tell us what happened and the crew will help here. Never share passwords, recovery phrases, or private keys.",
        "ticket_subject": "What's up?",
        "ticket_details": "Tell us a little more",
        "warning": "Please check the server rules. A moderator can review this warning if you think we got it wrong.",
        "cooldown": "Take a short breather before your next message in this channel.",
        "native_block": "This message matches a server word filter. Please rephrase it or ask a moderator for help.",
        "safety": "Use official Rip Cars links. The crew will never ask for your password, recovery phrase, or private key.",
    },
}


def defaults():
    return copy.deepcopy(DEFAULTS)


def get_path(cfg, path):
    value = cfg
    for key in path.split("."):
        value = value[key]
    return value


def set_path(cfg, path, value):
    result = copy.deepcopy(cfg)
    node = result
    keys = path.split(".")
    for key in keys[:-1]:
        node = node[key]
    if keys[-1] not in node:
        raise ValueError("Unknown setting.")
    node[keys[-1]] = value
    validate(result)
    return result


def parse_value(cfg, path, text):
    old = get_path(cfg, path)
    if isinstance(old, str):
        value = text.strip()
    else:
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Use a JSON value: true/false, a number, or a list.") from exc
    return set_path(cfg, path, value)


def domain(value):
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ValueError("Enter a domain without a scheme, path, or wildcard.")
    value = value.rstrip(".").lower()
    try:
        encoded = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Invalid domain.") from exc
    if "." not in encoded or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                 for label in encoded.split(".")):
        raise ValueError("Invalid domain.")
    return encoded


def _same_shape(value, template, path=""):
    if isinstance(template, dict):
        if not isinstance(value, dict) or set(value) != set(template):
            raise ValueError(f"{path or 'config'} has missing or unknown fields.")
        for key in template:
            _same_shape(value[key], template[key], f"{path}.{key}".strip("."))
    elif isinstance(template, list):
        if not isinstance(value, list):
            raise ValueError(f"{path} must be a list.")
    elif type(value) is not type(template):
        raise ValueError(f"{path} has the wrong value type.")


def validate(cfg):
    _same_shape(cfg, DEFAULTS)
    for key in ("brand", "bot_name"):
        if not 1 <= len(cfg[key]) <= 80:
            raise ValueError(f"{key}: use 1–80 characters.")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", cfg["color"]):
        raise ValueError("Color must be #RRGGBB.")
    url = urlsplit(cfg["website"])
    if url.scheme != "https" or not url.hostname or url.username or len(cfg["website"]) > 300:
        raise ValueError("Website must be an HTTPS URL without credentials.")
    ids = ["moderator_roles", "guard.exempt_roles", "guard.exempt_channels",
           "guard.include_channels", "guard.trusted_bot_ids"]
    for path in ids:
        values = get_path(cfg, path)
        if len(values) > 50 or any(type(v) is not int or not 0 < v < 2**63 for v in values):
            raise ValueError(f"{path}: use at most 50 positive numeric IDs.")
        if len(set(values)) != len(values):
            raise ValueError(f"{path}: duplicate IDs.")
    for path in ("member_role", "log_channel", "tickets.panel_channel", "tickets.open_category", "tickets.closed_category"):
        if not 0 <= get_path(cfg, path) < 2**63:
            raise ValueError(f"{path}: invalid ID.")
    if cfg["guard"]["mode"] not in ("protect", "observe"):
        raise ValueError("Guard mode must be protect or observe.")
    if cfg["native"]["enabled"] and (not cfg["guard"]["enabled"] or cfg["guard"]["mode"] != "protect"):
        raise ValueError("Turn native.enabled off before disabling protection or using observe mode; then sync native rules.")
    ranges = {
        "guard.warn_expiry_days": (1, 365), "guard.timeout_after": (0, 100),
        "guard.timeout_seconds": (1, 2419200), "guard.heat_threshold": (1, 1000),
        "guard.heat_decay_per_minute": (0, 100), "guard.mention_limit": (1, 50),
        "guard.flood_count": (2, 100), "guard.flood_seconds": (1, 120),
        "guard.duplicate_count": (2, 100), "guard.duplicate_seconds": (1, 300),
        "guard.caps_ratio": (0.1, 1.0), "guard.caps_min_letters": (5, 1000),
        "guard.newline_limit": (2, 100), "guard.notice_seconds": (10, 3600),
        "tickets.create_cooldown_seconds": (0, 86400), "tickets.auto_delete_days": (0, 365),
        "tickets.transcript_messages": (10, 10000), "tickets.transcript_bytes": (10000, 7000000),
        "monitoring.activity_days": (7, 365), "monitoring.preview_count": (1, 10),
        "monitoring.case_retention_days": (1, 3650), "monitoring.error_retention_days": (1, 365),
        "monitoring.raid_join_count": (2, 100), "monitoring.raid_window_seconds": (5, 300),
        "monitoring.new_account_hours": (0, 720),
    }
    for path, (low, high) in ranges.items():
        value = get_path(cfg, path)
        if not low <= value <= high:
            raise ValueError(f"{path}: range {low}–{high}.")
    guard = cfg["guard"]
    if len(guard["words"]) > 300:
        raise ValueError("Use at most 300 word rules.")
    seen = set()
    for word in guard["words"]:
        if not isinstance(word, dict) or set(word) != {"text", "mode"}:
            raise ValueError("Word format: {\"text\":\"word or phrase\",\"mode\":\"exact\"}.")
        if not isinstance(word["text"], str) or not 1 <= len(word["text"].strip()) <= 60:
            raise ValueError("Word rules need 1–60 characters.")
        if word["mode"] not in ("exact", "phrase", "contains"):
            raise ValueError("Word modes: exact, phrase, contains. Regex is not supported.")
        pair = (word["text"].strip().casefold(), word["mode"])
        if pair in seen:
            raise ValueError("Duplicate word rule.")
        seen.add(pair)
    for key in ("allowed_domains", "blocked_domains", "official_domains"):
        if len(guard[key]) > 200:
            raise ValueError("Use at most 200 domains per list.")
        for host in guard[key]:
            domain(host)
    for key in ("protected_names", "blocked_extensions"):
        if len(guard[key]) > 100 or any(not isinstance(v, str) or not 1 <= len(v) <= 80 for v in guard[key]):
            raise ValueError(f"Invalid {key} list.")
    for rule in guard["filters"].values():
        if rule["action"] not in ("alert", "delete", "warn", "timeout") or not 0 <= rule["weight"] <= 100:
            raise ValueError("Filter action: alert/delete/warn/timeout; weight: 0–100.")
    cats = cfg["tickets"]["categories"]
    if not 1 <= len(cats) <= 20:
        raise ValueError("Use 1–20 ticket topics.")
    keys = set()
    for cat in cats:
        if not isinstance(cat, dict) or set(cat) != {"key", "label", "enabled"}:
            raise ValueError("Ticket topic fields: key, label, enabled.")
        if not isinstance(cat["key"], str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,24}", cat["key"]) or cat["key"] in keys:
            raise ValueError("Ticket topic keys must be short, unique lowercase IDs.")
        keys.add(cat["key"])
        if not isinstance(cat["label"], str) or not 1 <= len(cat["label"]) <= 70 or type(cat["enabled"]) is not bool:
            raise ValueError("Invalid ticket topic label or enabled value.")
    for key, text in cfg["texts"].items():
        limit = 45 if key in ("ticket_subject", "ticket_details") else 1800
        if not 1 <= len(text) <= limit:
            raise ValueError(f"texts.{key}: use 1–{limit} characters.")
    return cfg
