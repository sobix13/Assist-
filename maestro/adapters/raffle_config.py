from __future__ import annotations

import copy
import json
import math
import re
from urllib.parse import urlsplit

CATEGORIES = ("platform", "contributors", "general")
TEXTS = {
    "title": "Rip Cars Raffle",
    "intro": "Pick a raffle, check the requirements, and join. No payment or wallet connection is required by this bot.",
    "join": "Join raffle", "leave": "Leave raffle", "status": "My entry", "rules":"Rules", "claim": "Claim prize",
    "joined": "You're in. Your eligibility and weight will be checked again when the draw runs.",
    "left": "Your entry has been removed.", "claim_instructions": "Your claim is recorded. Open a support ticket and include the raffle ID. The team will arrange delivery. Never share a seed phrase or private key.",
    "guide": "Pass the Gate CAPTCHA and answer at least one question to get Rippers. Join eligible raffles with the button. One entry per Discord account. Role-based weights and requirements are shown before you join. Winners claim here; delivery is handled by the team.",
    "prize_label": "Prize", "rules_label": "Eligibility & weights", "schedule_label": "Schedule",
    "entries_label": "Entries", "commitment_label": "Seed commitment", "contract_label": "Rules hash",
    "result_label": "Draw result", "seed_label": "Revealed seed",
    "entry_notice": "One entry per Discord account. Your Discord ID, role-check evidence and final weight appear in the audit export after the draw. Managers can record early closing, cancellation or exclusions. Prize delivery is manual.",
    "no_winners": "No winners: not enough eligible entries.",
    "paused_notice": "Entries are paused by a manager. The scheduled closing time is unchanged.",
}


def rules():
    return {"any_roles": [], "all_roles": [], "blocked_roles": [], "weights": {},
            "weight_mode": "max", "weight_cap": 100, "minimum_entries": 1,
            "account_days": 0, "server_days": 0, "exclude_staff": True,
            "claim_hours": 72}


DEFAULTS = {"enabled": False, "brand": "Rip Cars", "bot_name": "Rip Cars Raffle",
            "website": "https://app.ripcars.io", "color": 0x800020,
            "member_role": 0, "manager_roles": [], "channel": 0, "log_channel": 0,
            "blocked_users": [], "max_active": 20, "max_entries": 10000,
            "panel_update_seconds": 30, "poll_seconds": 15, "backup_hours": 24,
            "backup_keep": 14, "error_alert_seconds": 60,
            "campaign_targets": {"platform": 70, "contributors": 20, "general": 10},
            "texts": TEXTS,
            "templates": {k: {"title": {"platform": "Collectors' raffle", "contributors": "Community contributors' raffle", "general": "Community raffle"}[k],
                              "prize": "Describe the prize", "description": "Check the requirements below before joining.",
                              "duration": 86400, "starts_in": 0, "winners": 1, "rules": rules()} for k in CATEGORIES}}


def fresh():
    return copy.deepcopy(DEFAULTS)


def ident(value, zero=False):
    return type(value) is int and (value >= 0 if zero else value > 0) and value < 2**63


def ids(value, limit=20):
    return isinstance(value, list) and len(value) <= limit and all(ident(x) for x in value) and len(value) == len(set(value))


def number(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name}: use a whole number from {low} to {high}.")


def text(value, limit, name, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()) or "\x00" in value:
        raise ValueError(f"{name}: use {'0' if empty else '1'}–{limit} characters.")


def url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        if parsed.port is not None and not 1<=parsed.port<=65535:
            return False
        return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password and len(value) <= 300 and not re.search(r"[\s\x00-\x1f]", value)
    except ValueError:
        return False


def validate_rules(r):
    if not isinstance(r, dict) or set(r) != set(rules()):
        raise ValueError("Rules have missing or unknown fields.")
    for k in ("any_roles", "all_roles", "blocked_roles"):
        if not ids(r[k]):
            raise ValueError(f"{k}: use up to 20 distinct numeric role IDs.")
    if set(r["all_roles"]) & set(r["blocked_roles"]) or set(r["any_roles"]) & set(r["blocked_roles"]):
        raise ValueError("A required role cannot also be blocked.")
    if not isinstance(r["weights"], dict) or len(r["weights"]) > 20:
        raise ValueError("weights: use at most 20 role ID → weight entries.")
    for key, value in r["weights"].items():
        if not isinstance(key, str) or not key.isdecimal() or str(int(key)) != key or not ident(int(key)):
            raise ValueError("Weight keys must be canonical numeric role IDs.")
        number(value, 1, 100, "role weight")
    if r["weight_mode"] not in ("max", "sum"):
        raise ValueError("weight_mode: max or sum.")
    number(r["weight_cap"], 1, 100, "weight_cap")
    number(r["minimum_entries"], 1, 10000, "minimum_entries")
    for k in ("account_days", "server_days"):
        number(r[k], 0, 3650, k)
    number(r["claim_hours"], 1, 720, "claim_hours")
    if type(r["exclude_staff"]) is not bool:
        raise ValueError("exclude_staff must be true or false.")
    return r


def validate_spec(spec):
    if not isinstance(spec, dict) or set(spec) != {"title", "prize", "description", "duration", "starts_in", "winners", "rules"}:
        raise ValueError("Raffle draft has missing or unknown fields.")
    for k, limit in (("title", 100), ("prize", 400), ("description", 1200)):
        text(spec[k], limit, k, k == "description")
    number(spec["duration"], 60, 7776000, "duration")
    number(spec["starts_in"], 0, 7776000, "starts_in")
    number(spec["winners"], 1, 25, "winners")
    validate_rules(spec["rules"])
    return spec


def validate(cfg):
    if not isinstance(cfg, dict) or set(cfg) != set(DEFAULTS):
        raise ValueError("Settings have missing or unknown fields.")
    if type(cfg["enabled"]) is not bool:
        raise ValueError("enabled must be true or false.")
    text(cfg["brand"], 80, "brand"); text(cfg["bot_name"], 80, "bot_name")
    if not url(cfg["website"]):
        raise ValueError("website must be a valid HTTPS URL without credentials.")
    for k in ("member_role", "channel", "log_channel"):
        if not ident(cfg[k], True):
            raise ValueError(f"{k} must be a numeric Discord ID or 0.")
    if not ids(cfg["manager_roles"], 10) or not ids(cfg["blocked_users"], 1000):
        raise ValueError("Manager roles or blocked user IDs are invalid.")
    for k, lo, hi in (("color", 0, 0xFFFFFF), ("max_active", 1, 100), ("max_entries", 1, 100000),
                      ("panel_update_seconds", 10, 3600), ("poll_seconds", 5, 300),
                      ("backup_hours", 1, 168), ("backup_keep", 1, 90), ("error_alert_seconds", 30, 3600)):
        number(cfg[k], lo, hi, k)
    if not isinstance(cfg["campaign_targets"], dict) or set(cfg["campaign_targets"]) != set(CATEGORIES):
        raise ValueError("Campaign targets need all three categories.")
    for v in cfg["campaign_targets"].values():
        number(v, 0, 100, "campaign target")
    if sum(cfg["campaign_targets"].values()) != 100:
        raise ValueError("Campaign target percentages must add up to 100.")
    if not isinstance(cfg["texts"], dict) or set(cfg["texts"]) != set(TEXTS):
        raise ValueError("Text keys are fixed; edit their contents.")
    for k, v in cfg["texts"].items():
        limit = 80 if k in ("title", "join", "leave", "status", "rules", "claim") or k.endswith("_label") else 500 if k in ("entry_notice", "paused_notice") else 1800
        text(v, limit, f"texts.{k}")
    if not isinstance(cfg["templates"], dict) or set(cfg["templates"]) != set(CATEGORIES):
        raise ValueError("Three campaign templates are required.")
    for spec in cfg["templates"].values():
        validate_spec(spec)
    return cfg


def set_path(cfg, path, value):
    candidate = copy.deepcopy(cfg)
    pieces = path.split(".")
    node = candidate
    try:
        for piece in pieces[:-1]:
            node = node[piece]
        if pieces[-1] not in node:
            raise KeyError(path)
        node[pieces[-1]] = value
    except (KeyError, TypeError):
        raise ValueError("Unknown settings path.") from None
    return validate(candidate)


def duration(raw):
    raw = raw.strip().lower()
    if raw.isdecimal():
        return int(raw)
    match = re.fullmatch(r"(\d+)(s|m|h|d|w)", raw)
    if not match:
        raise ValueError("Use seconds or a duration such as 10m, 24h, 3d, 1w.")
    return int(match[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[match[2]]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
