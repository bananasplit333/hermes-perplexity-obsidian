import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import obsidian_vault as vault


class VaultPlatformTests(unittest.TestCase):
    def test_macos_registry_and_configuration_without_proc(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            notes = home / 'Documents' / 'Personal'
            (notes / '.obsidian').mkdir(parents=True)
            registry = home / 'Library/Application Support/obsidian/obsidian.json'
            registry.parent.mkdir(parents=True)
            registry.write_text(json.dumps({'vaults': {'one': {'path': str(notes)}}}))
            settings = home / 'profile/state/settings.json'
            with patch.object(vault.sys, 'platform', 'darwin'), patch.object(Path, 'home', return_value=home), patch.object(vault, 'SETTINGS', settings):
                self.assertFalse(vault.is_wsl())
                self.assertEqual(vault.registries(), [registry])
                self.assertEqual(vault.discover()[0]['path'], str(notes))
                vault.configure(str(notes), 'Research', 'flat', True)
                config = vault.load()
                target, fd = vault.safe_target(config, 'Example.md')
                with os.fdopen(fd, 'w') as stream:
                    stream.write('Verified local fixture')
                self.assertEqual(target.read_text(), 'Verified local fixture')

    def test_macos_does_not_translate_windows_drive(self):
        with patch.object(vault.sys, 'platform', 'darwin'):
            self.assertEqual(vault.windows_to_wsl('C:\\Notes'), 'C:\\Notes')

    def test_wsl_registry_translates_drive_path(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            notes = home / 'Mounted notes'
            (notes / '.obsidian').mkdir(parents=True)
            registry = home / 'obsidian.json'
            registry.write_text(json.dumps({'vaults': {'one': {'path': 'C:\\Notes'}}}))
            with patch.object(vault, 'registries', return_value=[registry]), patch.object(Path, 'home', return_value=home), patch.object(vault, 'windows_to_wsl', side_effect=lambda p: str(notes) if p == 'C:\\Notes' else p):
                self.assertEqual(vault.discover()[0]['path'], str(notes))


if __name__ == '__main__':
    unittest.main()
