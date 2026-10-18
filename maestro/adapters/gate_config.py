"""Pure policies and editable defaults for Ripcars Gate."""
from __future__ import annotations
import copy
import re
import unicodedata
from urllib.parse import urlparse

VERSION = "1.0.2"
OWNER = "ripcars-gate"
CLAIM_KEYS = ("collectors", "updates", "announcements", "game")
DEFAULTS = {
    "enabled": False, "color": 0x800020, "brand": "Ripcars Gate",
    "website": "https://app.ripcars.io", "auto_repair": True,
    "captcha_length": 6, "captcha_ttl": 300, "max_attempts": 3,
    "lock_seconds": 600, "answer_ttl": 1800, "ops_channel_id": None,
    "texts": {
        "welcome_title": "Welcome to Rip Cars",
        "welcome": "Complete the CAPTCHA and answer at least one question to join the garage. The other questions are optional.",
        "success": "You're in. Your Rippers role is ready. See you in General!",
        "questions_intro": "Tell us a little about yourself. Answer at least one question, then finish. Skip the others if you want.",
        "rules": "Keep it friendly. No harassment, scams, spam, or impersonation. Use the right channel. Never share wallet recovery phrases. Staff will never ask for them. Follow Discord's rules. Trades are arranged between members; ask questions before making a deal.",
        "faq": "Rip Cars is an onchain gacha for die-cast cars, including Hot Wheels. Start at https://app.ripcars.io. For an account, pack, or delivery issue, use Ticket when support opens. Check the app for current stock, pack details, and shipping terms.",
        "official_links": "Rip Cars app: https://app.ripcars.io\nUse links posted here by the team. Other official links will be added by an admin.",
        "announcements": "Project announcements from the Rip Cars team. Read the updates and react below.",
        "twitter_posts": "Posts from the official Rip Cars account will appear here when the feed is connected.",
        "community_news": "Community updates, events, and news from the garage.",
        "proposals": "Read project proposals and react. The team publishes updates here.",
        "notifications": "Choose what you want to hear about. Click a button to add or remove its role.",
        "ticket": "Support is being set up. The ticket bot will be connected here when it is ready. This panel does not open tickets yet.",
        "gcars": "One text post per member every 24 hours. Use Submit text below. No links, images, files, or mentions.",
    },
    "questions": [
        {"id": "collector", "label": "Are you a collector?", "prompt": "Do you collect Hot Wheels or other die-cast cars?", "enabled": True},
        {"id": "packs", "label": "Have you opened a Rip Cars pack?", "prompt": "Have you opened a pack on Rip Cars?", "enabled": True},
        {"id": "dream", "label": "What's your dream car?", "prompt": "What's your dream car?", "enabled": True},
    ],
    "claims": {"collectors": "Collectors", "updates": "Updates and News", "announcements": "Announcements", "game": "Game"},
}

ROLE_SPECS = {
    "rippers": ("Rippers", []), "og": ("OG", []),
    "moderator": ("Moderator", ["view_channel", "read_message_history", "send_messages", "attach_files", "embed_links", "add_reactions", "manage_messages", "manage_threads", "kick_members", "ban_members", "moderate_members", "view_audit_log", "manage_nicknames"]),
    "admin": ("Admin", ["administrator"]), "team": ("Team", ["administrator"]),
    **{f"claim_{key}": (name, []) for key, name in DEFAULTS["claims"].items()},
}

def item(name, parent=None, kind="text", access="member", mode="chat", slowmode=0, links=True, images=True, topic=""):
    return dict(name=name, parent=parent, kind=kind, access=access, mode=mode,
                slowmode=slowmode, links=links, images=images, topic=topic)

BLUEPRINT = {
    "start": item("Start Here", kind="category", access="public"),
    "official": item("Official", kind="category"),
    "garage": item("Main Garage", kind="category"),
    "repair": item("Repair Services", kind="category"),
    "owners": item("$CARS Owners", kind="category"),
    "open_tickets": item("Open", kind="category", access="staff"),
    "closed_tickets": item("Closed", kind="category", access="staff"),
    "operations": item("Server Operations", kind="category", access="staff"),
    "rules": item("rules", "start", access="public", mode="read"),
    "verification": item("verification", "start", access="public", mode="read"),
    "holder": item("holder-verification", "start", access="staff", mode="read"),
    "notifications": item("notifications", "start", mode="read"),
    "announcements": item("announcements", "official", mode="read"),
    "official_links": item("official-links", "official", access="public", mode="read"),
    "twitter_posts": item("twitter-posts", "official", mode="read"),
    "faq": item("faq", "official", access="public", mode="read"),
    "general": item("general", "garage"),
    "show_off": item("show-off", "garage", links=False),
    "trades": item("trades", "garage"),
    "hot_wheels": item("hot-wheels-irl", "garage"),
    "gcars": item("gcars", "garage", mode="submit", slowmode=86400, links=False, images=False),
    "community_news": item("community-news", "garage", mode="read"),
    "content": item("content", "garage", slowmode=7200),
    "ticket": item("ticket", "repair", mode="read"),
    "need_that": item("need-that", "repair"),
    "feedback": item("feedback-or-bugs", "repair"),
    "owners_chat": item("owners-chat", "owners", access="og"),
    "proposals": item("proposals", "owners", mode="read"),
    "gate_log": item("gate-log", "operations", access="staff", mode="read"),
}

def defaults():
    result = copy.deepcopy(DEFAULTS)
    result["blueprint"] = copy.deepcopy(BLUEPRINT)
    result["role_names"] = {key:name for key,(name,_) in ROLE_SPECS.items()}
    return result

def validate(cfg):
    if not any(q["enabled"] for q in cfg["questions"]):
        raise ValueError("Keep at least one question enabled.")
    if len(cfg["questions"]) > 20 or len({q["id"] for q in cfg["questions"]}) != len(cfg["questions"]):
        raise ValueError("Questions need unique IDs and a maximum of 20 entries.")
    for q in cfg["questions"]:
        if not re.fullmatch(r"[a-z0-9_]{1,30}", q["id"]) or not 1 <= len(q["label"]) <= 45 or not 1 <= len(q["prompt"]) <= 100:
            raise ValueError("Question ID, label, or prompt is invalid.")
    if not 0 <= cfg["color"] <= 0xFFFFFF or not 1 <= len(cfg["brand"]) <= 80:
        raise ValueError("Invalid brand or color.")
    parsed = urlparse(cfg["website"])
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Website must use HTTPS.")
    for k, lo, hi in [("captcha_length",4,8),("captcha_ttl",60,900),("max_attempts",1,10),("lock_seconds",60,3600),("answer_ttl",300,7200)]:
        if not lo <= cfg[k] <= hi: raise ValueError(f"Invalid {k}: use {lo} to {hi}.")
    for key, text in cfg["texts"].items():
        if not isinstance(text, str) or not 1 <= len(text) <= 3500:
            raise ValueError(f"Text {key} must be 1 to 3500 characters.")
    if any(not 1 <= len(name) <= 80 for name in cfg["role_names"].values()):raise ValueError("Role names must be 1 to 80 characters.")
    for key, spec in cfg["blueprint"].items():
        if spec["kind"] not in ("text","forum","category") or spec["access"] not in ("public","member","og","staff") or spec["mode"] not in ("chat","read","submit"):
            raise ValueError(f"Invalid channel settings for {key}.")
        if not 1 <= len(spec["name"]) <= 100 or len(spec["topic"]) > 1000:
            raise ValueError("Channel name or topic is too long.")
        if spec["parent"] and (spec["parent"] not in cfg["blueprint"] or cfg["blueprint"][spec["parent"]]["kind"] != "category"):
            raise ValueError("Parent must be an existing category key.")
        if not 0 <= spec["slowmode"] <= 21600 and key != "gcars":
            raise ValueError("Native slowmode must be 0 to 21600 seconds.")
        if key == "gcars" and not 1 <= spec["slowmode"] <= 604800: raise ValueError("Invalid gCARS cooldown.")
    if cfg["blueprint"]["holder"]["access"] != "staff":
        raise ValueError("Holder Verification stays staff-only until its own bot is connected.")

URL = re.compile(r"(?:https?\s*[:：]|www\.|discord\s*[.．]\s*(?:gg|com)|(?:[\w-]+\.)+(?:com|io|org|net|gg|xyz|co|app|me|dev|uk)\b)", re.I)
def has_link(text):
    normalized = unicodedata.normalize("NFKC", text)
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Cf")
    return bool(URL.search(normalized))

def normalize_answer(value):
    return " ".join(value.split())[:1000]

def eligible(session, now):
    return bool(session and session["captcha_until"] > now and session["answers"] and not session["locked_until"] > now)

def patch_matches(actual, expected):
    return all(actual.get(k) == v for k, v in expected.items())

def access_policy(spec):
    """Permission maps independent of Discord transport, also used by tests."""
    chat=spec["mode"]=="chat"
    member=dict(view_channel=spec["access"] in ("member","public"),read_message_history=True,
                send_messages=chat,send_messages_in_threads=chat,
                create_public_threads=chat and spec["kind"]=="forum",create_private_threads=False,
                add_reactions=True,attach_files=chat and spec["images"],embed_links=chat and spec["links"],
                mention_everyone=False,manage_messages=False,manage_channels=False,manage_roles=False,
                manage_webhooks=False,manage_threads=False,send_voice_messages=False,send_tts_messages=False,
                send_polls=False,use_external_apps=False,use_application_commands=True,use_external_stickers=False)
    public=dict(view_channel=spec["access"]=="public",read_message_history=True,send_messages=False,
                send_messages_in_threads=False,create_public_threads=False,create_private_threads=False,
                add_reactions=spec["access"]=="public",attach_files=False,embed_links=False,mention_everyone=False,
                use_application_commands=True,use_external_apps=False,send_voice_messages=False,send_tts_messages=False,send_polls=False)
    staff=dict(view_channel=True,read_message_history=True,send_messages=True,send_messages_in_threads=True,
               add_reactions=True,attach_files=True,embed_links=True,manage_messages=True,manage_threads=True)
    og=dict(view_channel=True,read_message_history=True,send_messages=True,send_messages_in_threads=True,
            attach_files=True,embed_links=True,add_reactions=True) if spec["access"]=="og" else {}
    bot=dict(view_channel=True,read_message_history=True,send_messages=True,embed_links=True,attach_files=True,
             manage_messages=True,manage_channels=True)
    return {"public":public,"rippers":member,"og":og,"staff":staff,"bot":bot}
