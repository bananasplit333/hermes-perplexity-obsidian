import json, os, tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import obsidian_vault as v

class VaultTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name)
  self.vault=self.root/'vault'; self.vault.mkdir(); (self.vault/'.obsidian').mkdir()
  self.home=self.root/'hermes'; os.environ['HERMES_HOME']=str(self.home); v.HOME=self.home; v.SETTINGS=self.home/'state/perplexity-obsidian/settings.json'
 def test_config_requires_confirm_and_vault(self):
  with self.assertRaises(v.VaultError): v.configure(self.vault,'Research','flat',False)
  with self.assertRaises(v.VaultError): v.configure(self.root/'notvault','Research','flat',True)
 def test_configuration_validation(self):
  for folder in ('../bad','.obsidian','a/.git','a/.trash'):
   with self.subTest(folder=folder), self.assertRaises(v.VaultError): v.configure(self.vault,folder,'flat',True)
  d=v.configure(self.vault,'Research','flat',True); self.assertEqual(v.load(),d)
 def test_save_exclusive_and_traversal(self):
  d=v.configure(self.vault,'Research','flat',True); target,fd=v.safe_target(d,'note.md')
  os.write(fd,b'one'); os.close(fd)
  with self.assertRaises(FileExistsError): v.safe_target(d,'note.md')
  self.assertEqual(target.read_bytes(),b'one')
  with self.assertRaises(v.VaultError): v.safe_target(d,'../bad')
 def test_topic_folder_and_symlink_rejected(self):
  d=v.configure(self.vault,'Research','topic',True); target,fd=v.safe_target(d,'AI--Paper.md'); os.close(fd)
  self.assertEqual(target.parent,self.vault/'Research'/'AI')
  outside=self.root/'outside'; outside.mkdir(); (self.vault/'Research'/'Link').symlink_to(outside,target_is_directory=True)
  with self.assertRaises(v.VaultError): v.safe_target(d,'Link--escape.md')
 def test_discovery_only_existing_vault(self):
  old=v.registries; v.registries=lambda: []
  try:
   (self.root/'Documents').mkdir(); (self.root/'Documents'/'candidate').mkdir(); (self.root/'Documents'/'candidate'/'.obsidian').mkdir()
   oldhome=Path.home
   # test bounded directory discovery via patched home accessor
   from unittest.mock import patch
   with patch.object(v.Path,'home',return_value=self.root): found=v.discover()
   self.assertTrue(any(x['path']==str(self.root/'Documents'/'candidate') for x in found))
  finally: v.registries=old
 def test_topic_names_reject_traversal_and_reserved_directories(self):
  d=v.configure(self.vault,'Research','topic',True)
  for name in ('..--escape.md','.git--config.md','.obsidian--x.md','Bad:Name--x.md','topic\\sub--x.md'):
   with self.subTest(name=name), self.assertRaises(v.VaultError): v.safe_target(d,name)
  self.assertFalse((self.vault/'Research'/'..').exists())
 def test_corrupt_and_wrong_typed_settings_fail_closed(self):
  v.SETTINGS.parent.mkdir(parents=True)
  for raw in ('{', '[]', '{"version":true,"layout":"flat","vault_path":"x","notes_folder":"Research"}', '{"version":1,"layout":"flat","vault_path":[],"notes_folder":"Research"}'):
   v.SETTINGS.write_text(raw)
   with self.subTest(raw=raw), self.assertRaises(v.VaultError): v.load()
 def test_symlink_settings_refused(self):
  v.SETTINGS.parent.mkdir(parents=True)
  outside=self.root/'settings.json'; outside.write_text('{}')
  v.SETTINGS.symlink_to(outside)
  with self.assertRaises(v.VaultError): v.load()
 def test_wsl_path_translation_and_detection(self):
  from unittest.mock import patch
  with patch.dict(os.environ, {'WSL_DISTRO_NAME':'Ubuntu'}):
   self.assertEqual(v.windows_to_wsl(r'C:\Users\me\Vault'), '/mnt/c/Users/me/Vault')
   self.assertIn(Path('/mnt/c/Users'), v.registries()[2].parents)
 def test_installer_copies_allowlist_without_caches(self):
  import subprocess
  repo=Path(__file__).resolve().parents[1]; cache=repo/'scripts'/'__pycache__'; cache.mkdir(exist_ok=True)
  (cache/'ignore.pyc').write_bytes(b'cache')
  try:
   target=self.root/'install-target'; result=subprocess.run([sys.executable,str(repo/'install.py'),'--hermes-home',str(target)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stderr)
   installed=target/'skills/perplexity-obsidian-research'
   expected={'SKILL.md'}
   for directory in ('scripts','references','templates'):
    source=repo/directory
    if source.exists(): expected.update(str(x.relative_to(repo)) for x in source.rglob('*') if x.is_file() and '__pycache__' not in x.parts and x.suffix!='.pyc')
   actual={str(x.relative_to(installed)) for x in installed.rglob('*') if x.is_file()}
   self.assertEqual(actual,expected)
  finally:
   (cache/'ignore.pyc').unlink(missing_ok=True)
   try: cache.rmdir()
   except OSError: pass
 def test_installer_refuses_overwrite(self):
  import subprocess
  repo=Path(__file__).resolve().parents[1]; hermes=self.root/'target'
  r=subprocess.run([sys.executable,str(repo/'install.py'),'--hermes-home',str(hermes)],capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stderr); installed=hermes/'skills/perplexity-obsidian-research'
  self.assertTrue((installed/'SKILL.md').is_file())
  r=subprocess.run([sys.executable,str(repo/'install.py'),'--hermes-home',str(hermes)],capture_output=True,text=True)
  self.assertNotEqual(r.returncode,0); self.assertIn('refusing to overwrite',r.stderr)
if __name__=='__main__': unittest.main()
