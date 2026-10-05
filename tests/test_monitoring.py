import os
import sqlite3
import time
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from aiohttp import web

from maestro.probes import backup_check, database_check, duration_seconds, suite_targets, validate_target
from maestro.runbooks import classify
from maestro.security import Denied
from .helpers import EngineCase, OWNER


class MonitorTests(EngineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake = self.use_fake_probes()
        self.target = self.engine.store.target("gate")
        self.target.update(enabled=True, expected_running=True, auto_restart=True, database="", backup_dir="", env_file="", repo="")
        self.engine.store.save_target(self.target)
        value = self.engine.store.settings()
        value.update(enabled=True, auto_recovery=True)
        self.engine.store.put("settings", value)

    async def twice(self):
        await self.engine.check_cycle("watch")
        self.engine.store.run("UPDATE incidents SET last_seen=last_seen-61")
        return await self.engine.check_cycle("watch")

    def restarts(self):
        return [c for c in self.fake.calls if c[:2] == ["systemctl", "restart"]]

    async def test_four_targets_are_independent_and_disabled_until_enrollment(self):
        ids = {t["id"] for t in suite_targets()}
        self.assertEqual(ids, {"gate", "crew", "raffle", "verifier"})
        self.assertTrue(all(not t["enabled"] and not t["auto_restart"] for t in suite_targets()))

    async def test_disabled_companion_bots_are_not_started_or_reported_as_missing(self):
        result = await self.engine.check_cycle("hourly")
        self.assertEqual([t["id"] for t in result["targets"]], ["gate"])
        self.assertEqual(self.restarts(), [])

    async def test_two_separate_failure_observations_then_restart_and_verify(self):
        self.fake.states[self.target["unit"]] = "failed"
        await self.engine.check_cycle("watch")
        self.assertEqual(self.restarts(), [])
        self.engine.store.run("UPDATE incidents SET last_seen=last_seen-61")
        result = await self.engine.check_cycle("watch")
        self.assertEqual(len(self.restarts()), 1)
        self.assertTrue(result["targets"][0]["recovery"]["verified_ok"])

    async def test_adjacent_watch_and_hourly_passes_do_not_fake_two_observations(self):
        self.fake.states[self.target["unit"]] = "failed"
        await self.engine.check_cycle("watch")
        await self.engine.check_cycle("hourly")
        self.assertEqual(self.restarts(), [])

    async def test_intentionally_stopped_unit_stays_stopped(self):
        self.fake.states[self.target["unit"]] = "inactive"
        await self.twice()
        self.assertEqual(self.restarts(), [])

    async def test_global_recovery_off_overrides_target_opt_in(self):
        value = self.engine.store.settings()
        value["auto_recovery"] = False
        self.engine.store.put("settings", value)
        self.fake.states[self.target["unit"]] = "failed"
        await self.twice()
        self.assertEqual(self.restarts(), [])

    async def test_target_recovery_off_overrides_global_opt_in(self):
        self.target["auto_restart"] = False
        self.engine.store.save_target(self.target)
        self.fake.states[self.target["unit"]] = "failed"
        await self.twice()
        self.assertEqual(self.restarts(), [])

    async def test_maintenance_blocks_automatic_changes(self):
        value = self.engine.store.settings()
        value["maintenance_until"] = time.time() + 3600
        self.engine.store.put("settings", value)
        self.fake.states[self.target["unit"]] = "failed"
        await self.twice()
        self.assertEqual(self.restarts(), [])

    async def test_invalid_token_or_corrupt_database_is_escalated_not_restarted(self):
        for log in ("LoginFailure: Improper token", "database disk image is malformed", "Missing Permissions (50013)"):
            with self.subTest(log=log):
                self.fake.states[self.target["unit"]] = "failed"
                self.fake.logs[self.target["unit"]] = log
                await self.twice()
                self.assertEqual(self.restarts(), [])

    async def test_port_conflict_runs_automatic_read_only_diagnostic(self):
        self.fake.states[self.target["unit"]] = "failed"
        self.fake.logs[self.target["unit"]] = "OSError: Address already in use"
        result = await self.twice()
        self.assertIn(["ss", "-lntup"], self.fake.calls)
        self.assertIn("ports", result["targets"][0]["diagnostics"])
        self.assertEqual(self.restarts(), [])

    async def test_cooldown_and_daily_budget_are_persistent(self):
        self.fake.states[self.target["unit"]] = "failed"
        self.engine.store.run("INSERT INTO repairs(at,target,action,result) VALUES(?,?,?,?)", (time.time(), "gate", "restart", "test"))
        await self.twice()
        self.assertEqual(self.restarts(), [])
        self.engine.store.run("DELETE FROM repairs")
        for ago in (4000, 8000, 12000):
            self.engine.store.run("INSERT INTO repairs(at,target,action,result) VALUES(?,?,?,?)", (time.time() - ago, "gate", "restart", "test"))
        await self.twice()
        self.assertEqual(self.restarts(), [])

    async def test_live_shared_setup_lease_defers_restart_without_writing_registry(self):
        path = self.folder / "coord.sqlite3"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE leases(guild INTEGER,key TEXT,token TEXT,expires REAL)")
        db.execute("INSERT INTO leases VALUES(1,'server-setup','token',?)", (time.time() + 180,))
        db.commit()
        before = path.read_bytes()
        db.close()
        self.engine.policy.data["coordination_path"] = str(path)
        self.fake.states[self.target["unit"]] = "failed"
        await self.twice()
        self.assertEqual(self.restarts(), [])
        self.assertEqual(path.read_bytes(), before)

    async def test_manual_probe_does_not_trigger_repairs(self):
        self.fake.states[self.target["unit"]] = "failed"
        await self.engine.check_cycle("daily", "gate", manual=True)
        self.assertEqual(self.restarts(), [])

    async def test_incident_open_dedup_and_resolve(self):
        self.fake.states[self.target["unit"]] = "inactive"
        await self.twice()
        opened = self.engine.store.rows("SELECT * FROM outbox WHERE kind='incident_open'")
        self.assertEqual(len(opened), 1)
        self.fake.states[self.target["unit"]] = "active"
        await self.engine.check_cycle("watch")
        self.assertEqual(self.engine.store.one("SELECT state FROM incidents WHERE target='gate'")["state"], "resolved")

    async def test_daily_only_failures_not_falsely_resolved_by_hourly_check(self):
        self.target["database"] = str(self.folder / "missing.sqlite3")
        self.engine.store.save_target(self.target)
        await self.engine.check_cycle("daily")
        await self.engine.check_cycle("hourly")
        self.assertEqual(self.engine.store.one("SELECT state FROM incidents WHERE code='database_failed'")["state"], "open")

    async def test_unresolved_daily_database_error_blocks_later_watch_restart(self):
        self.target["database"] = str(self.folder / "missing.sqlite3")
        self.engine.store.save_target(self.target)
        await self.engine.check_cycle("daily")
        self.fake.states[self.target["unit"]] = "failed"
        await self.twice()
        self.assertEqual(self.restarts(), [])

    async def test_hourly_and_daily_schedule_in_owner_timezone(self):
        self.engine.check_cycle = AsyncMock(return_value={})
        now = datetime(2026, 10, 5, 18, 1, tzinfo=ZoneInfo("Asia/Tehran")).timestamp()
        await self.engine.scheduler_step(now)
        self.assertEqual([c.args[0] for c in self.engine.check_cycle.await_args_list], ["watch", "hourly", "daily"])
        self.engine.check_cycle.reset_mock()
        await self.engine.scheduler_step(now + 15)
        self.engine.check_cycle.assert_not_called()

    async def test_report_queue_requires_ack_and_keeps_failed_delivery(self):
        self.engine._event("test", {"detail": "TOKEN=not-public"})
        events = await self.engine.dispatch(OWNER, "events", {})
        self.assertEqual(len(events), 1)
        self.assertNotIn("not-public", str(events))
        await self.engine.dispatch(OWNER, "ack", {"id": events[0]["id"]})
        self.assertEqual(await self.engine.dispatch(OWNER, "events", {}), [])

    async def test_stale_target_form_is_rejected(self):
        old = dict(self.target)
        updated = await self.engine.dispatch(OWNER, "save_target", old)
        self.assertEqual(updated["revision"], old.get("revision", 0) + 1)
        with self.assertRaises(Denied):
            await self.engine.dispatch(OWNER, "save_target", old)

    async def test_discovery_enrolls_installed_units_without_starting_them(self):
        self.fake.states["ripcars-crew.service"] = "inactive"
        await self.engine.dispatch(OWNER, "discover", {})
        self.assertFalse(self.engine.store.target("crew")["expected_running"])
        self.assertEqual(self.restarts(), [])


class ProbeTests(EngineCase):
    async def test_read_only_sqlite_probe_never_creates_missing_database(self):
        p = self.folder / "missing.sqlite3"
        self.assertFalse(database_check(p)["ok"])
        self.assertFalse(p.exists())

    async def test_read_only_sqlite_probe_reads_live_wal_database(self):
        p = self.folder / "db.sqlite3"
        db = sqlite3.connect(p)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE marker(value)")
        db.execute("INSERT INTO marker VALUES(123)")
        db.commit()
        report = database_check(p)
        self.assertTrue(report["ok"])
        self.assertGreater(report["wal_bytes"], 0)
        self.assertEqual(db.execute("SELECT value FROM marker").fetchone()[0], 123)
        db.close()

    async def test_corrupt_sqlite_probe_is_not_reported_healthy(self):
        p = self.folder / "bad.sqlite3"
        p.write_bytes(b"not a sqlite database")
        with self.assertRaises(sqlite3.DatabaseError):
            database_check(p)

    async def test_backup_age_and_symlink_handling(self):
        folder = self.folder / "backups"
        folder.mkdir()
        backup = folder / "snapshot.rca"
        backup.write_bytes(b"x")
        (folder / "link").symlink_to(backup)
        self.assertEqual(backup_check(folder, 36)["count"], 1)
        os.utime(backup, (time.time() - 40 * 3600,) * 2)
        self.assertFalse(backup_check(folder, 36)["ok"])

    async def test_watchdog_unit_duration_formats(self):
        self.assertEqual(duration_seconds("1min 30s"), 90)
        self.assertEqual(duration_seconds("90000000"), 90)
        self.assertEqual(duration_seconds("0"), 0)

    async def test_target_rejects_credentials_plain_http_and_shell_unit(self):
        for change in ({"url": "https://user:secret@example.com/health"}, {"url": "http://example.com/health"}, {"unit": "x;id.service"}, {"auto_restart": "yes"}):
            with self.subTest(change=change), self.assertRaises(Denied):
                validate_target({**suite_targets()[0], **change})

    async def test_real_http_health_flag_redirect_and_malformed_json(self):
        app = web.Application()
        async def ok(request):
            return web.json_response({"ok": True})
        async def failed(request):
            return web.json_response({"ok": False})
        async def bad(request):
            return web.Response(text="not json")
        async def redirect(request):
            raise web.HTTPFound("/ok")
        app.add_routes([web.get("/ok", ok), web.get("/false", failed), web.get("/bad", bad), web.get("/redirect", redirect)])
        server = web.AppRunner(app)
        await server.setup()
        site = web.TCPSite(server, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            for path, expected in (("ok", True), ("false", False), ("bad", False), ("redirect", False)):
                with self.subTest(path=path):
                    report = await self.engine.probes.endpoint(f"http://127.0.0.1:{port}/{path}", "ok")
                    self.assertEqual(report["ok"], expected)
        finally:
            await server.cleanup()

    async def test_runbook_classification_reports_multiple_causes(self):
        codes = {r["code"] for r in classify("Missing Permissions; Address already in use; No space left on device")}
        self.assertEqual(codes, {"discord_permissions", "port_conflict", "disk_full"})
