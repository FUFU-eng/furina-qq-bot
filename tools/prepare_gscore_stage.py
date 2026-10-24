"""Prepare and verify a reversible code candidate without changing the running project."""
from __future__ import annotations
import base64, hashlib, json, os, pathlib, shutil, subprocess, sys, zlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
BASE=ROOT/'integrations'
STAGE=BASE/'migration-stage/project'
plan=json.loads(zlib.decompress(base64.b64decode((BASE/'migration-candidate.b64').read_text(encoding='utf-8'))))
for name,expected in plan['expected_versions'].items():
 path=ROOT/name
 actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
 if actual!=expected:raise RuntimeError('Source changed since inspection: '+name)
if STAGE.exists():raise RuntimeError('Stage exists; inspect instead of replacing.')
STAGE.mkdir(parents=True)
for directory in ('furina','panel','tests','tools'):
 shutil.copytree(ROOT/directory,STAGE/directory,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
for filename in ('bot.py','requirements.txt','pyproject.toml'):
 shutil.copy2(ROOT/filename,STAGE/filename)
for name,content in plan['files'].items():
 target=STAGE/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content,encoding='utf-8')
(BASE/'stage-status.json').write_text(json.dumps({'state':'prepared','files':len(plan['files']),'stage':str(STAGE)},ensure_ascii=False),encoding='utf-8')
print('STAGED',len(plan['files']))
