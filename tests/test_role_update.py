import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.update_role_raffles import UNITS,activate,snapshot_database


class RoleUpdateTests(unittest.TestCase):
    def test_launcher_rejects_branch_names_before_any_download(self):
        launcher=Path(__file__).resolve().parents[1]/'scripts/launch_role_update.sh'
        for refs in [[],['main','a'*40,'b'*40]]:
            result=subprocess.run(['bash',str(launcher),*refs],capture_output=True,text=True)
            self.assertEqual(result.returncode,2)

    def test_database_snapshot_includes_wal_and_passes_integrity(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'live.sqlite3';target=Path(folder)/'saved.sqlite3'
            writer=sqlite3.connect(source)
            writer.execute('PRAGMA journal_mode=WAL');writer.execute('CREATE TABLE value(n)')
            writer.execute('INSERT INTO value VALUES(42)');writer.commit()
            snapshot_database(source,target)
            with sqlite3.connect(target) as restored:
                self.assertEqual(restored.execute('SELECT n FROM value').fetchone()[0],42)
                self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            writer.close();self.assertEqual(target.stat().st_mode&0o777,0o600)

    def test_start_failure_restores_all_previous_code_without_database_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);releases={};previous={};unit_folder=root/'units';backup=root/'backup'
            unit_folder.mkdir();backup.mkdir()
            for name in ('ripcars-raffle','ripcars-verifier','maestro'):
                base=root/name;base.mkdir();old=base/'old';old.mkdir();new=base/'new';new.mkdir()
                current=base/'current';current.symlink_to(old);previous[current]=old;releases[current]=new
            for unit in UNITS:
                name='maestro' if unit.startswith('maestro-') else unit.removesuffix('.service')
                (root/name/'new'/unit).write_text('new unit')
                (unit_folder/unit).write_text('old unit');(backup/unit).write_text('old unit')
            calls=[]
            def run(*args,**kwargs):
                calls.append(args)
                if args[:2]==('systemctl','start') and kwargs.get('check',True):raise RuntimeError('Readiness failed')
            with patch('scripts.update_role_raffles.command',side_effect=run),patch('scripts.update_role_raffles.time.sleep'):
                with self.assertRaises(RuntimeError):activate(releases,previous,unit_folder,backup)
            self.assertTrue(all(current.resolve()==old for current,old in previous.items()))
            self.assertTrue(all((unit_folder/u).read_text()=='old unit' for u in UNITS))
            self.assertFalse(any('ripcars-gate.service' in call or 'ripcars-crew.service' in call for call in calls))
