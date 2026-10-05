import base64
import hashlib
import io
import json
import os
import sqlite3
import tarfile
import zipfile

from maestro.files import MAX_UPLOAD, safe_name
from maestro.security import Denied, Policy, Redactor, unit_name
from scripts.restore_backup import restore
from .helpers import EngineCase, OTHER, OWNER, TOKEN, policy


class SecurityTests(EngineCase):
    async def test_frontend_peer_uid_token_and_transport_all_required(self):
        p = policy()
        self.assertEqual(p.authenticate(os.getuid(), TOKEN, OWNER), OWNER)
        for uid, token, actor in ((os.getuid() + 1, TOKEN, OWNER), (os.getuid(), "wrong", OWNER), (os.getuid(), TOKEN, "telegram:123456789"), (os.getuid(), TOKEN, "discord:111111111")):
            with self.subTest(uid=uid, actor=actor), self.assertRaises(Denied):
                p.authenticate(uid, token, actor)

    async def test_discord_roles_are_not_an_authorization_source(self):
        with self.assertRaises(Denied):
            await self.engine.dispatch("discord:999999999", "status", {})

    async def test_invalid_owner_enrollment_rejected(self):
        with self.assertRaises(Denied):
            Policy({"owners": ["Admin"], "frontends": policy().frontends})

    async def test_unit_names_never_become_shell_code(self):
        self.assertEqual(unit_name("nginx.service"), "nginx.service")
        for value in ("nginx;reboot.service", "../nginx.service", "--help.service", "$(id).service", "nginx", "x y.service"):
            with self.subTest(value=value), self.assertRaises(Denied):
                unit_name(value)

    async def test_known_and_partial_secrets_redacted(self):
        value = "secret-split-across-read-boundaries"
        redactor = Redactor([value])
        self.assertNotIn(value, redactor.clean(value))
        self.assertNotIn(value[:15], redactor.clean("prompt " + value[:15]))
        self.assertNotIn("abcdefg", redactor.clean("PASSWORD=abcdefg"))

    async def test_ansi_and_control_sequences_removed(self):
        result = Redactor().clean("\x1b[31mred\x1b[0m\x00\u202e")
        self.assertEqual(result, "red")

    async def test_settings_revision_prevents_stale_panel_overwrite(self):
        old = self.engine.store.settings()
        await self.engine.dispatch(OWNER, "save_settings", {"revision": old["revision"], "changes": {"daily_hour": 10}})
        with self.assertRaises(ValueError):
            await self.engine.dispatch(OWNER, "save_settings", {"revision": old["revision"], "changes": {"daily_hour": 20}})
        self.assertEqual(self.engine.store.settings()["daily_hour"], 10)

    async def test_owner_and_credentials_cannot_be_changed_from_discord(self):
        with self.assertRaises(Denied):
            await self.engine.dispatch(OWNER, "save_settings", {"revision": 0, "changes": {"owners": [OTHER]}})

    async def test_private_data_is_not_in_operational_export(self):
        await self.execute(code="printf harmless")
        data = json.dumps(self.engine.export())
        self.assertNotIn(TOKEN, data)
        self.assertNotIn("printf harmless", data)

    async def test_encrypted_backup_roundtrip_and_wrong_password(self):
        result = self.engine.encrypted_backup("correct-passphrase-123")
        path = self.folder / "backup.rca"
        path.write_bytes(base64.b64decode(result["data"]))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), result["sha256"])
        with self.assertRaises(Exception):
            restore(path, self.folder / "wrong.sqlite3", "wrong-passphrase")
        output = self.folder / "verified.sqlite3"
        restore(path, output, "correct-passphrase-123")
        db = sqlite3.connect(output)
        self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
        db.close()

    async def test_backup_password_not_saved(self):
        self.engine.encrypted_backup("unique-secret-passphrase")
        self.assertNotIn(b"unique-secret-passphrase", self.engine.store.path.read_bytes())

    async def test_root_and_reboot_templates_follow_local_policy(self):
        self.engine.policy.data["root_commands"] = False
        with self.assertRaises(Denied):
            self.engine.prepare(OWNER, {"kind": "shell", "payload": {"code": "id", "profile": "root"}})
        with self.assertRaises(Denied):
            self.engine.prepare(OWNER, {"kind": "reboot", "payload": {"phrase": "REBOOT anything"}})


class UploadTests(EngineCase):
    async def test_python_file_staged_and_syntax_checked_without_execution(self):
        meta = self.engine.files.stage(OWNER, "file.py", b"raise RuntimeError('never executed at upload')")
        self.assertIn("not executed", meta["files"][0]["syntax"])
        self.assertEqual(self.engine.files.preview(OWNER, meta["id"], "file.py"), "raise RuntimeError('never executed at upload')")

    async def test_upload_actor_is_enforced(self):
        meta = self.engine.files.stage(OWNER, "file.txt", b"hello")
        with self.assertRaises(Denied):
            self.engine.files.metadata(OTHER, meta["id"])

    async def test_all_unsafe_archive_paths_rejected(self):
        for name in ("../escape", "/etc/file", "C:/file", "a\\b", "a/./b", "a//b", "bad\nname"):
            with self.subTest(name=name), self.assertRaises(Denied):
                safe_name(name)

    async def test_zip_traversal_rejected_and_staging_removed(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            z.writestr("../escape.txt", "bad")
        with self.assertRaises(Denied):
            self.engine.files.stage(OWNER, "bad.zip", data.getvalue())
        self.assertEqual(list(self.engine.files.root.iterdir()), [])

    async def test_zip_symlink_rejected(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            info = zipfile.ZipInfo("link")
            info.external_attr = 0o120777 << 16
            z.writestr(info, "/etc/passwd")
        with self.assertRaises(Denied):
            self.engine.files.stage(OWNER, "bad.zip", data.getvalue())

    async def test_tar_hardlink_rejected(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as t:
            info = tarfile.TarInfo("link")
            info.type, info.linkname = tarfile.LNKTYPE, "/etc/passwd"
            t.addfile(info)
        with self.assertRaises(Denied):
            self.engine.files.stage(OWNER, "bad.tar", data.getvalue())

    async def test_duplicate_zip_entries_rejected(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            z.writestr("a.txt", "one")
            z.writestr("a.txt", "two")
        with self.assertRaises(Denied):
            self.engine.files.stage(OWNER, "duplicate.zip", data.getvalue())

    async def test_archive_expansion_limit_checked_before_member_read(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("big.txt", b"a" * (MAX_UPLOAD + 1))
        with self.assertRaises(Denied):
            self.engine.files.stage(OWNER, "large.zip", data.getvalue())

    async def test_changed_staged_file_is_not_executed(self):
        meta = self.engine.files.stage(OWNER, "script.py", b"print('original')")
        (self.engine.files.root / meta["id"] / "script.py").write_text("print('changed')")
        with self.assertRaises(Denied):
            self.engine.files.materialize(OWNER, meta["id"], self.folder / "out")

    async def test_zip_safe_directories_and_hashes(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            z.writestr("project/", "")
            z.writestr("project/main.py", "print('ok')")
        meta = self.engine.files.stage(OWNER, "project.zip", data.getvalue())
        self.assertEqual(meta["files"][0]["name"], "project/main.py")
        self.assertEqual(meta["files"][0]["sha256"], hashlib.sha256(b"print('ok')").hexdigest())
