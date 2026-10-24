"""Readback, isolated regression, and narrowly scoped archival/cleanup."""
from __future__ import annotations
import ast,hashlib,json,os,pathlib,re,shutil,sqlite3,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parents[1];os.chdir(ROOT);sys.path.insert(0,str(ROOT))
BASE=ROOT/'integrations';RESULT=BASE/'final-verification.json'
state={'state':'running','pid':os.getpid()}
def save():RESULT.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
def main():
 activation=json.loads((BASE/'activation-status.json').read_text(encoding='utf-8'));assert activation['state']=='activated'
 backup=pathlib.Path(activation['backup']);plan=json.loads((BASE/'activation-plan.json').read_text(encoding='utf-8'))
 assert all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==v for n,v in plan['candidate_hashes'].items())
 state['deployed_hashes_match']=True;save()
 # No deletion of former game source: move it and its obsolete tests/tool into the rollback backup.
 retired={}
 for name in ('furina/services/mihoyo','tests/test_mihoyo_service.py','tools/verify_game_management.py'):
  src=ROOT/name;dst=backup/'retired'/name
  assert src.exists() and not dst.exists()
  files=[x for x in src.rglob('*') if x.is_file() and '__pycache__' not in x.parts] if src.is_dir() else [src]
  retired.update({str(x.relative_to(ROOT)):hashlib.sha256(x.read_bytes()).hexdigest() for x in files})
  dst.parent.mkdir(parents=True,exist_ok=True);src.rename(dst)
 (backup/'retired-source-hashes.json').write_text(json.dumps(retired,indent=2),encoding='utf-8')
 state['retired_files']=len(retired);save()
 references=[]
 for base in ('furina','panel','tests'):
  for p in (ROOT/base).rglob('*.py'):
   tree=ast.parse(p.read_text(encoding='utf-8-sig'))
   for n in ast.walk(tree):
    if isinstance(n,ast.ImportFrom) and (n.module or '').startswith('furina.services.mihoyo') or isinstance(n,ast.Import) and any(x.name.startswith('furina.services.mihoyo') for x in n.names):references.append(str(p.relative_to(ROOT)))
 assert not references;state['legacy_runtime_imports']=references;save()
 # Test an exact fresh copy, never the production .env or production database.
 testroot=BASE/'current-verification/project';testroot.mkdir(parents=True,exist_ok=False)
 for name in ('furina','panel','tests','tools'):shutil.copytree(ROOT/name,testroot/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
 for name in ('bot.py','requirements.txt','pyproject.toml'):shutil.copy2(ROOT/name,testroot/name)
 shutil.copy2(BASE/'migration-stage/project/.env',testroot/'.env')
 temp=BASE/'current-verification/temp';temp.mkdir()
 env=dict(os.environ,PYTHONUTF8='1',PYTHONIOENCODING='utf-8',TEMP=str(temp),TMP=str(temp))
 python=BASE/'nonebot-env/Scripts/python.exe'
 with (ROOT/'logs/gscore-current-tests.log').open('wb') as log:
  compiled=subprocess.run([str(python),'-m','compileall','-q','bot.py','furina','panel','tools','tests'],cwd=testroot,env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
  result=subprocess.run([str(python),'-m','unittest','discover','-s','tests','-t','.'],cwd=testroot,env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW) if compiled.returncode==0 else compiled
 text=(ROOT/'logs/gscore-current-tests.log').read_text(encoding='utf-8',errors='replace')
 match=re.search(r'Ran (\d+) tests? in',text)
 state.update(tests=int(match.group(1)) if match else None,test_exit_code=result.returncode,compiled=compiled.returncode==0);save()
 assert result.returncode==0,'Isolated regression failed; read its log before continuing'
 finish_readback(activation,backup)

def finish_readback(activation,backup):
 python=BASE/'nonebot-env/Scripts/python.exe'
 from panel.services import control,winproc
 from furina.services import gscore
 state['core']=gscore.snapshot();health=control.controller.check_once();state['qq_health']=health.get('health',{}).get('state');state['nonebot']=health.get('nonebot');state['napcat']=health.get('napcat')
 bot_pids=[x[0] for x in control.find_bot_processes()];state['bot_process_tree']=bot_pids
 tcp=winproc.powershell("Get-NetTCPConnection -State Established -RemotePort 8765 -ErrorAction SilentlyContinue | Where-Object {$_.RemoteAddress -eq '127.0.0.1'} | ForEach-Object {$_.OwningProcess}")
 state['live_adapter_tcp']=any(str(pid) in tcp.split() for pid in bot_pids)
 panel=winproc.powershell(f"Get-Process -Id {int(activation['panel_pid'])} -ErrorAction SilentlyContinue | Where-Object {{$_.ProcessName -eq 'pythonw' -and $_.MainWindowTitle -like '*芙宁娜*'}} | ForEach-Object {{$_.Id}}")
 state['native_panel_running']=str(activation['panel_pid']) in panel.split()
 state['upstream']={}
 repos={'core':BASE/'gsuid_core','adapter':BASE/'nonebot-genshinuid'}
 repos.update({name:BASE/'gsuid_core/gsuid_core/plugins'/name for name in ('GenshinUID','StarRailUID','ZZZeroUID','BBBUID')})
 for name,path in repos.items():
  head=subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],creationflags=subprocess.CREATE_NO_WINDOW).decode().strip()
  clean=subprocess.run(['git','-C',str(path),'diff','--quiet','HEAD'],creationflags=subprocess.CREATE_NO_WINDOW).returncode==0
  state['upstream'][name]={'commit':head,'tracked_source_clean':clean}
 with (BASE/'adapter-resolved.txt').open('wb') as out:subprocess.run(['uv','pip','freeze','--python',str(python)],stdout=out,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
 def legacy_values(path):
  with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as con:return dict(con.execute("SELECT key,value FROM docs WHERE ns='mihoyo' AND key IN ('accounts','config')"))
 state['legacy_game_data_unchanged']=legacy_values(ROOT/'data/furina.db')==legacy_values(backup/'furina.db')
 with sqlite3.connect((ROOT/'data/furina.db').as_uri()+'?mode=ro',uri=True) as con:
  row=con.execute("SELECT value FROM docs WHERE ns='mihoyo' AND key='config'").fetchone();cfg=json.loads(row[0]) if row else {}
 state['schedules']={}
 for game,name in [('genshin','GenshinUID'),('sr','StarRailUID'),('zzz','ZZZeroUID'),('bh3','BBBUID')]:
  raw=json.loads((BASE/'gsuid_core/data'/name/'config.json').read_text(encoding='utf-8'))
  raw={key:(value.get('data') if isinstance(value,dict) else value) for key,value in raw.items()}
  state['schedules'][game]={'enabled':raw['SchedSignin'],'time':raw['SignTime']}
  assert raw['SchedSignin']==bool(cfg.get('auto_sign',True) and (cfg.get('games') or {}).get(game,True))
  assert [int(x) for x in raw['SignTime']]==[int(x) for x in str(cfg.get('sign_time') or '10:00').split(':')]
 assert state['core']['ready'] and state['qq_health']=='online' and state['live_adapter_tcp'] and state['native_panel_running']
 assert all(x['tracked_source_clean'] for x in state['upstream'].values())
 state['state']='passed';save()
 # Only failed transfers and disposable test working directories created by this migration.
 removed=[]
 for name in ('bundle-transfer','failed-fetches','adapter-probe-work','gsuid_core-1153548720a7c04e8f29a0299e90131c81bc0521.zip','gsuid_core-direct-download.zip','nonebot-genshinuid-e57b76a089365357b3581bf605127842d1bd31dc.zip'):
  p=BASE/name
  if p.is_dir():shutil.rmtree(p);removed.append(name)
  elif p.is_file():p.unlink();removed.append(name)
 shutil.rmtree(BASE/'current-verification');removed.append('current-verification')
 # Retain the verified staging snapshot as one meaningful historical record, not a misleading live candidate.
 for name in ('migration-stage','migration-candidate.b64','activation-precheck-1.json','preflight-runtime.json','stage-status.json'):
  src=BASE/name;dst=backup/'migration-history'/name
  if src.exists():dst.parent.mkdir(parents=True,exist_ok=True);src.rename(dst)
 state['cleaned_scratch']=removed;save()
 print(json.dumps({k:state[k] for k in ('state','tests','test_exit_code','live_adapter_tcp','native_panel_running','qq_health','legacy_game_data_unchanged')},ensure_ascii=True))
if __name__=='__main__':
 try:
  if '--resume-readback' in sys.argv:
   prior=json.loads(RESULT.read_text(encoding='utf-8'))
   assert prior['state']=='failed' and prior['test_exit_code']==0 and prior['retired_files']==8
   state.update(prior);state.pop('error',None);state.pop('error_type',None);state['state']='running';state['pid']=os.getpid()
   activation=json.loads((BASE/'activation-status.json').read_text(encoding='utf-8'))
   finish_readback(activation,pathlib.Path(activation['backup']))
  else:main()
 except Exception as exc:state.update(state='failed',error_type=type(exc).__name__,error=str(exc));save();raise
