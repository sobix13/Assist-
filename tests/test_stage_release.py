import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.stage_release import stage


class StageReleaseTests(unittest.TestCase):
    def test_private_caller_mask_does_not_make_venv_inaccessible(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source';source.mkdir()
            (source/'VERSION').write_text('1.1.2')
            destination=Path(folder)/'release'
            def create(path,**kwargs):
                path.mkdir();(path/'bin').mkdir()
            old=os.umask(0o077)
            try:
                with patch('scripts.stage_release.venv.create',side_effect=create),patch('scripts.stage_release.subprocess.run'):
                    stage(source,destination)
                self.assertEqual(stat.S_IMODE((destination/'.venv/bin').stat().st_mode),0o755)
                self.assertEqual(os.umask(0o077),0o077)
            finally:os.umask(old)

    def test_private_source_stages_code_accessible_to_interface_users(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir(mode=0o700)
            (source / 'VERSION').write_text('1.1.1')
            (source / 'main.py').write_text("print('ready')\n")
            (source / 'frontend.env').write_text('PRIVATE=never-copy\n')
            private = source / 'private-example.txt'
            private.write_text('Retain explicit file permissions')
            private.chmod(0o600)
            destination = root / 'release'
            with patch('scripts.stage_release.venv.create'), patch('scripts.stage_release.subprocess.run'):
                stage(source, destination)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((destination / private.name).stat().st_mode), 0o600)
            self.assertFalse((destination / 'frontend.env').exists())
            self.assertEqual((destination / 'main.py').read_text(), "print('ready')\n")
            self.assertTrue((destination / 'STAGED_OK').is_file())
