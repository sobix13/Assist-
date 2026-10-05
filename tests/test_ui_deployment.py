import os
import pwd
import sqlite3
import time
import warnings
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from maestro.discord_ui import AccessModal, ApplicationModal, PackageModal, BackupModal, BRANCHES, Commands, FileModal, JsonModal, OwnerView, Panel, RebootModal, ServiceModal, ShellModal, UploadView, owner_allowed, validate_private_channel
from maestro.security import Denied
from maestro.store import DEFAULTS, Store
from .helpers import EngineCase, OWNER


class InterfaceTests(EngineCase):
    def bot_stub(self):
        return SimpleNamespace(owner_id=123456789, guild_id=987654321, api=SimpleNamespace(call=AsyncMock()))

    async def test_actual_sdk_registers_all_command_branches(self):
        commands = Commands(self.bot_stub())
        names = {c.name for c in commands.commands}
        self.assertEqual(names, {"panel", "health", "check", "logs", "terminal", "service", "upload", "run_file", "job", "export", "backup", "guide", 'inventory', 'output', 'checkpoint', 'checkpoints', 'restore', 'recipes', 'repair', 'container', 'application', 'package', 'access'})
        for command in commands.commands:
            self.assertLessEqual(len(command.description), 100)

    async def test_actual_sdk_builds_all_panels_and_modal_limits(self):
        bot = self.bot_stub()
        for key in BRANCHES:
            panel = Panel(bot, key, {**DEFAULTS, "revision": 0}, self.engine.store.targets())
            self.assertLessEqual(len(panel.children), 25)
            self.assertTrue(any(isinstance(c, discord.ui.Select) and c.row == 0 for c in panel.children))
        meta = self.engine.files.stage(OWNER, "file.py", b"print('ok')")
        modals = [AccessModal(bot), AccessModal(bot, True), ApplicationModal(bot), PackageModal(bot), ShellModal(bot, "root"), ServiceModal(bot), RebootModal(bot), BackupModal(bot), JsonModal(bot, "target", self.engine.store.target("gate")), FileModal(bot, meta, "file.py", "root")]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            for modal in modals:
                self.assertLessEqual(len(modal.title), 45)
                for child in modal.children:
                    self.assertLessEqual(len(child.label), 45)
                    if child.default:
                        self.assertLessEqual(len(child.default), 4000)

    async def test_owner_auth_also_requires_correct_guild(self):
        bot = self.bot_stub()
        self.assertTrue(owner_allowed(bot, SimpleNamespace(user=SimpleNamespace(id=bot.owner_id), guild_id=bot.guild_id)))
        self.assertFalse(owner_allowed(bot, SimpleNamespace(user=SimpleNamespace(id=bot.owner_id), guild_id=1)))
        self.assertFalse(owner_allowed(bot, SimpleNamespace(user=SimpleNamespace(id=2), guild_id=bot.guild_id)))

    async def test_stolen_view_is_rejected_before_callback(self):
        bot = self.bot_stub()
        i = SimpleNamespace(user=SimpleNamespace(id=2), guild_id=bot.guild_id, response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()))
        self.assertFalse(await OwnerView(bot).interaction_check(i))
        i.response.send_message.assert_awaited_once()

    async def test_channel_selection_remains_in_draft_view(self):
        panel = Panel(self.bot_stub(), "setup", {**DEFAULTS, "revision": 0}, [])
        picker = next(c for c in panel.children if isinstance(c, discord.ui.ChannelSelect))
        picker._values = [SimpleNamespace(id=222222222)]
        i = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        await picker.callback(i)
        self.assertEqual(panel.channel_id, 222222222)
        self.assertEqual(picker.default_values[0].id, 222222222)
        self.assertEqual(panel.settings["report_channel"], 0)

    async def test_long_upload_paths_use_bounded_dropdown_values(self):
        bot = self.bot_stub()
        path = "a" * 150 + ".py"
        meta = {"id": "upload", "files": [{"name": path}]}
        view = UploadView(bot, meta)
        selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
        for select in selects:
            self.assertTrue(all(len(o.label) <= 100 and len(o.value) <= 100 for o in select.options))

    def channel(self, public=False, overwrites=None):
        channel = MagicMock(spec=discord.TextChannel)
        default = SimpleNamespace(id=1)
        me = SimpleNamespace(id=777)
        channel.guild = SimpleNamespace(default_role=default, me=me)
        channel.overwrites = overwrites or {}
        channel.permissions_for.side_effect = lambda item: SimpleNamespace(view_channel=public if item is default else True, send_messages=True, attach_files=True, read_message_history=True, embed_links=True)
        return channel

    async def test_report_channel_public_access_rejected(self):
        with self.assertRaises(Denied):
            validate_private_channel(self.channel(public=True), 123456789)

    async def test_everyone_deny_is_not_enough_when_member_role_can_view(self):
        role = MagicMock(spec=discord.Role)
        role.id, role.permissions, role.tags = 42, SimpleNamespace(administrator=False), None
        role.is_bot_managed.return_value = False
        channel = self.channel(overwrites={role: SimpleNamespace(view_channel=True)})
        with self.assertRaises(Denied):
            validate_private_channel(channel, 123456789)

    async def test_private_owner_channel_is_accepted(self):
        validate_private_channel(self.channel(), 123456789)


class LifecycleTests(EngineCase):
    async def test_backend_restart_marks_jobs_interrupted_and_invalidates_reviews(self):
        prepared = self.engine.prepare(OWNER, {"kind": "shell", "payload": {"code": "printf not-replayed"}})
        self.engine.store.run("INSERT INTO jobs(id,actor,kind,state,payload,created) VALUES('fixture',?,'shell','running','{}',?)", (OWNER, time.time()))
        self.engine.store.close()
        self.engine.store = Store(self.folder / "state" / "maestro.sqlite3")
        self.engine.files.store = self.engine.store
        self.assertEqual(self.engine.store.one("SELECT state FROM jobs WHERE id='fixture'")["state"], "interrupted")
        with self.assertRaises(Denied):
            self.engine.confirm(OWNER, {k: prepared[k] for k in ("id", "token", "digest")})

    async def test_runner_profile_never_falls_back_to_root(self):
        # Production installs run this under root. The development fixture uses a real low-privilege UID.
        account = pwd.getpwnam(self.engine.policy.runner)
        if os.geteuid() != 0:
            result = await self.engine.runner.run(["id", "-u"])
            self.assertEqual(int(result.output), account.pw_uid)
        else:
            meta = self.engine.files.stage(OWNER, "main.py", b"import os; print(os.getuid())")
            job = await self.execute(kind="file", profile="runner", upload=meta["id"], entry="main.py", runtime="python")
            from pathlib import Path
            mapping = [tuple(map(int, line.split())) for line in Path("/proc/self/uid_map").read_text().splitlines()]
            mapped = any(start <= account.pw_uid < start + count for start, outside, count in mapping)
            if mapped:
                self.assertEqual(job["state"], "completed")
                self.assertEqual(int(job["output"]), account.pw_uid)
                self.assertNotEqual(account.pw_uid, 0)
            else:
                self.assertEqual(job["state"], "failed")
                self.assertIn("Errno 22", job["output"])
                self.assertIsNone(job["exit_code"])

    async def test_shared_protocol_inspection_leaves_foreign_and_manual_resources_unchanged(self):
        import ripcars_coordination
        path = self.folder / "coord.sqlite3"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE resources(guild INTEGER,key TEXT,object_id INTEGER,kind TEXT,owner TEXT,baseline TEXT,desired TEXT,state TEXT)")
        for n, owner in enumerate(("ripcars-gate", "ripcars-crew", "ripcars-raffle", "ripcars-verifier")):
            db.execute("INSERT INTO resources VALUES(1,?,?,?,?,'{}','{}','pinned')", ("role:" + str(n), n + 100, "role", owner))
        ripcars_coordination.initialize(db)
        db.commit()
        before = list(db.execute("SELECT * FROM resources"))
        report = ripcars_coordination.inspect(path)
        self.assertTrue(report["ok"])
        self.assertEqual(list(db.execute("SELECT * FROM resources")), before)
        db.close()

    async def test_unit_templates_keep_root_broker_separate_from_frontend(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        agent = (root / "maestro-agent.service").read_text()
        frontend = (root / "maestro-discord.service").read_text()
        self.assertIn("User=root", agent)
        self.assertIn("KillMode=control-group", agent)
        self.assertIn("User=maestro", frontend)
        self.assertIn("NoNewPrivileges=true", frontend)
        self.assertIn("WatchdogSec=120", agent)

    async def test_own_backend_is_never_an_automatic_restart_target(self):
        with self.assertRaises(Denied):
            await self.engine.dispatch(OWNER, "save_target", {"id": "own-agent", "unit": "maestro-agent.service", "auto_restart": True})
