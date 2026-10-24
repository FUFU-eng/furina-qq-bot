"""Authorized, checkpointed cutover. Never stops NapCat or modifies legacy accounts."""
from __future__ import annotations
import datetime, hashlib, importlib, json, os, pathlib, shutil, sqlite3, subprocess, sys, time
ROOT=pathlib.Path(__file__).resolve().parents[1];os.chdir(ROOT);sys.path.insert(0,str(ROOT))
BASE=ROOT/'integrations';STAGE=BASE/'migration-stage/project';STATUS=BASE/'activation-status.json'
PLAN=json.loads((BASE/'activation-plan.json').read_text(encoding='utf-8'))
state={'state':'running','phase':'precheck','pid':os.getpid()}
def save(phase,**fields):
 state.update(phase=phase,**fields);STATUS.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8');print(phase,flush=True)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
def run_importer(*args):
 p=subprocess.run([str(BASE/'gsuid_core/.venv/Scripts/python.exe'),str(ROOT/'tools/migrate_accounts_to_gscore.py'),*args],cwd=ROOT,capture_output=True,timeout=180,env=dict(os.environ,PYTHONUTF8='1',PYTHONIOENCODING='utf-8'))
 if p.returncode:raise RuntimeError('Upstream migration/configuration failed; inspect phase and result without logging credentials')
 lines=p.stdout.decode('utf-8',errors='replace').splitlines()
 return json.loads(next(x for x in reversed(lines) if x.startswith('{')))
def close_panel():
 from panel.services import winproc
 # Known application PID freshly revalidated; graceful WM_CLOSE only, no focus/mouse/key action.
 result=winproc.powershell("$p=Get-Process -Id 63548 -ErrorAction SilentlyContinue; if($p){if($p.ProcessName -ne 'pythonw' -or $p.MainWindowTitle -notlike '*芙宁娜*'){throw 'Panel identity changed'}; $null=$p.CloseMainWindow(); if(-not $p.WaitForExit(12000)){throw 'Panel remained open; no force close attempted'}}; 'PANEL_CLOSED'",timeout=18)
 if 'PANEL_CLOSED' not in result:raise RuntimeError('Could not close the saved native panel safely')
def main():
 if STATUS.exists():raise RuntimeError('Activation already has a checkpoint; inspect instead of replaying')
 for name,expected in PLAN['expected_versions'].items():
  if digest(ROOT/name)!=expected:raise RuntimeError('Source changed: '+name)
 for name,expected in PLAN['candidate_hashes'].items():
  if digest(STAGE/name)!=expected:raise RuntimeError('Candidate differs from reviewed source: '+name)
  if name.endswith('.py'):compile((STAGE/name).read_text(encoding='utf-8'),name,'exec')
 for name in ('stage-tests','core-probe','adapter-probe','native-preflight'):
  result=json.loads((BASE/(name+'.json')).read_text(encoding='utf-8'))
  if name=='stage-tests':assert result.get('exit_code')==0
  elif name=='native-preflight':assert result.get('loaded') and result.get('rendered')
  else:assert result.get('passed')
 from panel.services import control,winproc,napcat
 if not napcat.is_running():raise RuntimeError('Existing NapCat is not running; cutover will not restart it')
 backup=ROOT/'backups'/('gscore-cutover-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));backup.mkdir(parents=True)
 for name in PLAN['expected_versions']:
  if (ROOT/name).is_file():
   dst=backup/'source'/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,dst)
 with sqlite3.connect((ROOT/'data/furina.db').as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(backup/'furina.db') as dst:src.backup(dst)
 (backup/'deployment-plan.json').write_text(json.dumps(PLAN,ensure_ascii=False,indent=2),encoding='utf-8')
 save('backed_up',backup=str(backup),old_bot_pids=[x[0] for x in control.find_bot_processes()])
 close_panel();save('panel_closed')
 control.controller._kill_bot()
 if control.find_bot_processes():raise RuntimeError('Old scheduler is still running')
 save('old_bot_stopped')
 # The preflight process was started by this migration, and has no real user account yet.
 pid=json.loads((BASE/'preflight-runtime.json').read_text())['pid']
 expected=str(BASE/'gsuid_core/.venv/Scripts/python.exe')
 check=winproc.powershell(f"Get-CimInstance Win32_Process -Filter 'ProcessId = {int(pid)}' | Where-Object {{ $_.ExecutablePath -eq {winproc.ps_quote(expected)} -and $_.CommandLine -like '*-m gsuid_core.core*' }} | ForEach-Object {{ $_.ProcessId }}")
 if str(pid) not in check.split():raise RuntimeError('Preflight identity changed; no unrelated process was stopped')
 winproc.kill_tree([int(pid)])
 for _ in range(30):
  if not winproc.port_open(8765):break
  time.sleep(.3)
 if winproc.port_open(8765):raise RuntimeError('Core port remains occupied')
 save('preflight_stopped')
 activate_after_stop()

def activate_after_stop():
 from panel.services import control,winproc,napcat
 # All readers/writers being replaced are now stopped. Recheck before each atomic replace.
 for name,expected in PLAN['expected_versions'].items():
  target=ROOT/name
  if digest(target)!=expected:raise RuntimeError('Concurrent source change: '+name)
  target.parent.mkdir(parents=True,exist_ok=True);temp=target.with_name(target.name+'.gscore-new')
  with temp.open('xb') as f:f.write((STAGE/name).read_bytes())
  os.replace(temp,target)
 save('source_applied',files=len(PLAN['candidate_hashes']))
 run_importer('--configure-only');save('schedule_time_fixed')
 summary=run_importer();save('accounts_migrated',migration=summary)
 # The old scheduler is stopped; the new core will first boot with the preserved clock/flags.
 run_importer('--activate-schedules');save('schedules_configured')
 importlib.invalidate_caches();control=importlib.reload(control)
 # Do not use _start_work: that method may start NapCat if it has gone away.
 if not napcat.is_running():raise RuntimeError('NapCat went away; it was not restarted')
 if winproc.port_open(control.bot_port()):raise RuntimeError('Bot port occupied')
 with (ROOT/'logs/console.log').open('ab') as log:
  bot=subprocess.Popen([control.python_exe(),str(ROOT/'bot.py')],cwd=ROOT,env=dict(os.environ,PYTHONUTF8='1',PYTHONIOENCODING='utf-8'),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,creationflags=winproc.NO_WINDOW)
 save('new_bot_started',bot_launcher_pid=bot.pid)
 for _ in range(90):
  if bot.poll() is not None:raise RuntimeError('New bot exited; inspect redacted log')
  if winproc.port_open(control.bot_port()) and winproc.port_open(8765):break
  time.sleep(1)
 else:raise RuntimeError('New services not listening within 90 seconds')
 from furina.services import gscore
 core=gscore.snapshot()
 if not core['ready']:raise RuntimeError('New core not ready')
 panel_python=pathlib.Path(sys.executable)
 if panel_python.name.lower()=='python.exe':panel_python=panel_python.with_name('pythonw.exe')
 with (ROOT/'logs/panel-gscore-launch.log').open('ab') as log:
  panel=subprocess.Popen([str(panel_python),'-m','panel'],cwd=ROOT,env=dict(os.environ,PYTHONUTF8='1',PYTHONIOENCODING='utf-8'),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,creationflags=winproc.NO_WINDOW)
 save('panel_restarted',panel_pid=panel.pid,core_pids=core['pids'])
 health=control.controller.check_once()
 save('awaiting_live_verification',state='activated',health=health.get('health',{}).get('state'),napcat_preserved=bool(health.get('napcat')),nonebot=bool(health.get('nonebot')))
if __name__=='__main__':
 try:
  if '--resume-after-stop' in sys.argv:
   saved=json.loads(STATUS.read_text(encoding='utf-8'))
   assert saved['state']=='failed' and saved['error']=='Core port remains occupied'
   from panel.services import control,winproc
   assert not control.find_bot_processes() and not winproc.port_open(8765)
   state.update(saved);state.pop('error',None);state.pop('error_type',None)
   save('preflight_stopped',state='running',pid=os.getpid())
   activate_after_stop()
  else:main()
 except Exception as exc:
  save('failed',state='failed',error_type=type(exc).__name__,error=str(exc));raise
