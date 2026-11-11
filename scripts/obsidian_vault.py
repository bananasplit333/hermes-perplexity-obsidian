#!/usr/bin/env python3
"""Discover, configure, inspect, and safely save into an existing Obsidian vault."""
from __future__ import annotations
import argparse, json, os, re, sys, ntpath
from pathlib import Path

HOME = Path(os.environ.get('HERMES_HOME', Path.home()/'.hermes')).expanduser()
SETTINGS = HOME/'state'/'perplexity-obsidian'/'settings.json'

class VaultError(ValueError): pass

def is_wsl():
    if not sys.platform.startswith('linux'):
        return False
    if os.environ.get('WSL_DISTRO_NAME'):
        return True
    try:
        return 'microsoft' in Path('/proc/version').read_text(errors='ignore').lower()
    except OSError:
        return False


def registries():
    home=Path.home(); out=[]
    if sys.platform == 'win32':
        base=Path(os.environ.get('APPDATA', home/'AppData/Roaming'))/'obsidian'
        out += [base/'obsidian.json']
    elif sys.platform == 'darwin':
        out += [home/'Library/Application Support/obsidian/obsidian.json']
    else:
        out += [home/'.config/obsidian/obsidian.json', home/'.var/app/md.obsidian.Obsidian/config/obsidian/obsidian.json']
        if is_wsl():
            appdata=os.environ.get('APPDATA')
            if appdata: out += [Path(appdata)/'obsidian/obsidian.json']
            out += [Path('/mnt/c/Users')/u/'AppData/Roaming/obsidian/obsidian.json' for u in os.listdir('/mnt/c/Users') if (Path('/mnt/c/Users')/u).is_dir()] if Path('/mnt/c/Users').is_dir() else []
    return out

def discover():
    candidates={}
    def add(p,evidence):
        try:
            p=Path(windows_to_wsl(p)).expanduser().resolve(strict=True)
            if p.is_dir() and (p/'.obsidian').is_dir(): candidates[str(p)] = sorted(set(candidates.get(str(p),[])+[evidence]))
        except (OSError, RuntimeError, TypeError): pass
    for cfg in registries():
        try:
            data=json.loads(cfg.read_text(encoding='utf-8'))
            if isinstance(data,dict):
                for key,v in data.items():
                    if key=='vaults' and isinstance(v,dict):
                        for entry in v.values():
                            if isinstance(entry,dict) and entry.get('path'): add(entry['path'],f'Obsidian registry: {cfg}')
                    elif isinstance(v,dict) and v.get('path'): add(v['path'],f'Obsidian registry: {cfg}')
        except (OSError, ValueError): pass
    roots=[Path.home()/'Documents',Path.home()/'Obsidian',Path.home()/'Notes',Path.home()/'Documents/Obsidian',Path.home()/'Sync/Obsidian']
    if sys.platform=='win32': roots += [Path.home()/'OneDrive/Documents',Path.home()/'OneDrive/Obsidian']
    if sys.platform=='darwin': roots += [Path.home()/'Library/Mobile Documents/iCloud~md~obsidian/Documents']
    for root in roots:
        if root.is_dir():
            add(root,'common folder')
            try:
                for child in list(root.iterdir())[:250]:
                    if child.is_dir() and not child.is_symlink(): add(child,'bounded common-folder scan')
            except OSError: pass
    return [{'path':p,'evidence':e} for p,e in sorted(candidates.items())]

def validate_folder(value):
    if not isinstance(value, str) or not value.strip(): raise VaultError('folder must be a non-empty relative path')
    parts=value.replace('\\','/').split('/')
    if value.startswith('/') or any(x in ('','.','..') for x in parts): raise VaultError('folder must be a non-empty relative path without dot/traversal segments')
    if any(any(c in x for c in '<>:"|?*') or any(ord(c)<32 for c in x) or x.endswith((' ','.')) for x in parts): raise VaultError('folder contains a reserved Windows name character')
    if any(x.lower() in {'.obsidian','.git','.trash'} for x in parts): raise VaultError('folder cannot enter reserved .obsidian, .git, or .trash directories')
    return Path(*parts)

def windows_to_wsl(value):
    if not is_wsl(): return value
    drive, tail=ntpath.splitdrive(value)
    if not re.fullmatch(r'[A-Za-z]:', drive) or not tail.startswith(('\\','/')): return value
    return '/mnt/'+drive[0].lower()+tail.replace('\\','/')

def load():
    if SETTINGS.is_symlink(): raise VaultError('refusing symlink settings file')
    try:
        fd=os.open(SETTINGS,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,encoding='utf-8') as stream: d=json.load(stream)
    except FileNotFoundError: return None
    except (json.JSONDecodeError, UnicodeDecodeError) as e: raise VaultError('invalid settings JSON') from e
    if not isinstance(d,dict) or type(d.get('version')) is not int or d.get('version')!=1 or d.get('layout') not in ('flat','topic'): raise VaultError('invalid settings')
    if not isinstance(d.get('vault_path'),str) or not isinstance(d.get('notes_folder'),str): raise VaultError('invalid settings types')
    validate_folder(d['notes_folder'])
    try: v=Path(windows_to_wsl(d['vault_path'])).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as e: raise VaultError('configured vault path is invalid') from e
    if not v.is_dir() or not (v/'.obsidian').is_dir(): raise VaultError('configured vault no longer contains .obsidian')
    d['vault_path']=str(v); return d

def configure(vault,folder,layout,confirmed):
    if not confirmed: raise VaultError('configuration requires --confirmed after the user selects the vault and folder layout')
    try: v=Path(windows_to_wsl(vault)).expanduser().resolve(strict=True)
    except OSError as e: raise VaultError(f'selected vault path does not exist: {vault}') from e
    if not v.is_dir() or not (v/'.obsidian').is_dir(): raise VaultError('selected path must be an existing Obsidian vault containing .obsidian')
    f=validate_folder(folder)
    if layout not in ('flat','topic'): raise VaultError('layout must be flat or topic')
    SETTINGS.parent.mkdir(parents=True,exist_ok=True)
    data={'version':1,'vault_path':str(v),'notes_folder':f.as_posix(),'layout':layout}
    tmp=SETTINGS.with_suffix('.tmp'); tmp.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8'); os.chmod(tmp,0o600); tmp.replace(SETTINGS)
    return data

def safe_target(data, name):
    if not isinstance(name,str) or not name or Path(name).name!=name or name in ('.','..') or any(c in name for c in '/\\\0'): raise VaultError('note name must be a simple filename')
    if re.search(r'[<>:\|?*"\x00-]', name) or name.endswith((' ','.')): raise VaultError('note name contains a reserved Windows name character')
    root=Path(data['vault_path']).resolve(strict=True); base=root/data['notes_folder']
    if data['layout']=='topic':
        topic=name.split('--',1)[0]
        if topic==name: raise VaultError('topic layout requires filename Topic--Title.md')
        validate_folder(topic)
        base=base/topic
    filename=name if name.endswith('.md') else name+'.md'
    target=base/filename
    parts=base.relative_to(root).parts
    root_fd=os.open(root,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
    current=root_fd
    try:
        for part in parts:
            try: os.mkdir(part,0o700,dir_fd=current)
            except FileExistsError: pass
            child=os.open(part,os.O_RDONLY|getattr(os,'O_DIRECTORY',0)|getattr(os,'O_NOFOLLOW',0),dir_fd=current)
            if current!=root_fd: os.close(current)
            current=child
        fd=os.open(filename,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600,dir_fd=current)
    except FileExistsError:
        raise
    except OSError as e:
        raise VaultError('refusing unsafe or symlink destination path') from e
    finally:
        if current!=root_fd: os.close(current)
        os.close(root_fd)
    return target,fd

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); s=p.add_subparsers(dest='cmd',required=True)
    s.add_parser('discover'); s.add_parser('status')
    c=s.add_parser('configure'); c.add_argument('--vault',required=True); c.add_argument('--folder',required=True); c.add_argument('--layout',choices=['flat','topic'],required=True); c.add_argument('--confirmed',action='store_true')
    a=s.add_parser('save'); a.add_argument('--name',required=True); a.add_argument('--input',required=True,type=Path)
    args=p.parse_args(argv)
    try:
        if args.cmd=='discover': result={'candidates':discover()}
        elif args.cmd=='status': result=load() or {'configured':False,'settings_path':str(SETTINGS)}
        elif args.cmd=='configure': result=configure(args.vault,args.folder,args.layout,args.confirmed)
        else:
            d=load()
            if not d: raise VaultError('No vault configured. Ask the user to select an existing vault and folder first.')
            content=args.input.read_text(encoding='utf-8'); target,fd=safe_target(d,args.name)
            try:
                with os.fdopen(fd,'w',encoding='utf-8') as f: f.write(content)
            except Exception:
                target.unlink(missing_ok=True); raise
            result={'saved':str(target)}
        print(json.dumps(result,indent=2)); return 0
    except (VaultError,OSError,ValueError) as e: print(f'error: {e}',file=sys.stderr); return 2
if __name__=='__main__': raise SystemExit(main())
