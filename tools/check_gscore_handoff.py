"""Read-only runtime/source verification. No restart, migration, sign-in, or QQ sends."""
from __future__ import annotations
import ast,datetime,hashlib,json,os,pathlib,sqlite3,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
os.chdir(ROOT);sys.path.insert(0,str(ROOT))
BASE=ROOT/'integrations'
def main():
 from panel.services import control,winproc
 from furina.services import gscore
 activation=json.loads((BASE/'activation-status.json').read_text(encoding='utf-8'))
 plan=json.loads((BASE/'activation-plan.json').read_text(encoding='utf-8'))
 previous=json.loads((BASE/'final-verification.json').read_text(encoding='utf-8'))
 r={'checked_at':datetime.datetime.now().astimezone().isoformat(),'actions':'read_only_checks_no_restart_no_migration_no_sign_no_qq_send'}
 def mismatches(expected):
  return [name for name,digest in expected.items() if not (ROOT/name).is_file() or hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest]
 expected=dict(plan['candidate_hashes'])
 baseline_changes=mismatches(expected)
 ui_path=BASE/'ui-fix-deployment.json'
 if ui_path.exists():
  ui=json.loads(ui_path.read_text(encoding='utf-8'))
  allowed={'panel/classic/window.py','panel/classic/games.py','tests/test_game_management.py','tools/check_gscore_handoff.py','docs/07-GsCore官方迁移.md'}
  assert ui['state']=='installed_waiting_panel_reload' and set(ui['deployed_hashes'])==allowed
  for name,digest in ui['deployed_hashes'].items():
   if name in expected:assert ui['previous_hashes'][name]==expected[name]
   expected[name]=digest
  r['ui_update']={'tests':ui['tests']['tests'],'test_exit_code':ui['tests']['exit_code'],'offscreen_interaction_passed':ui['interaction']['passed'],'desktop_click_verified':ui['desktop_click_verified'],'installation_state':ui['state']}
 r['source_changes']=mismatches(expected)
 r['previous_regression']={'tests':previous.get('tests'),'exit_code':previous.get('test_exit_code'),'source_still_matches':not baseline_changes}
 if 'ui_update' in r:r['ui_update']['source_still_matches']=not r['source_changes']
 r['core']=gscore.snapshot()
 h=control.controller.check_once();r['qq_health']=h.get('health',{}).get('state');r['nonebot']=h.get('nonebot');r['napcat']=h.get('napcat')
 bot_pids=[pid for pid,_ in control.find_bot_processes()];r['bot_process_tree']=bot_pids
 tcp=winproc.powershell("Get-NetTCPConnection -State Established -RemotePort 8765 -ErrorAction SilentlyContinue | Where-Object {$_.RemoteAddress -eq '127.0.0.1'} | ForEach-Object {$_.OwningProcess}")
 r['live_adapter_tcp']=any(str(pid) in tcp.split() for pid in bot_pids)
 panel=winproc.powershell(f"Get-Process -Id {int(activation['panel_pid'])} -ErrorAction SilentlyContinue | Where-Object {{$_.ProcessName -eq 'pythonw' -and $_.MainWindowTitle -like '*芙宁娜*'}} | ForEach-Object {{$_.Id}}")
 r['original_restarted_panel_running']=str(activation['panel_pid']) in panel.split()
 r['upstream']={}
 repos={'core':BASE/'gsuid_core','adapter':BASE/'nonebot-genshinuid'}
 repos.update({n:BASE/'gsuid_core/gsuid_core/plugins'/n for n in ('GenshinUID','StarRailUID','ZZZeroUID','BBBUID')})
 for name,path in repos.items():
  head=subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],creationflags=subprocess.CREATE_NO_WINDOW).decode().strip()
  clean=subprocess.run(['git','-C',str(path),'diff','--quiet','HEAD'],creationflags=subprocess.CREATE_NO_WINDOW).returncode==0
  r['upstream'][name]={'commit_matches_verified':head==previous['upstream'][name]['commit'],'tracked_source_clean':clean}
 def legacy_values(path):
  with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as con:return dict(con.execute("SELECT key,value FROM docs WHERE ns='mihoyo' AND key IN ('accounts','config')"))
 r['legacy_game_data_unchanged']=legacy_values(ROOT/'data/furina.db')==legacy_values(pathlib.Path(activation['backup'])/'furina.db')
 r['schedules']={}
 for game,name in [('genshin','GenshinUID'),('sr','StarRailUID'),('zzz','ZZZeroUID'),('bh3','BBBUID')]:
  raw=json.loads((BASE/'gsuid_core/data'/name/'config.json').read_text(encoding='utf-8'))
  def value(key):return raw[key].get('data') if isinstance(raw[key],dict) else raw[key]
  r['schedules'][game]={'enabled':value('SchedSignin'),'time':value('SignTime')}
 r['old_service_archived']=not (ROOT/'furina/services/mihoyo').exists() and (pathlib.Path(activation['backup'])/'retired/furina/services/mihoyo').is_dir()
 r['runtime_passed']=bool(r['core']['ready'] and r['qq_health']=='online' and r['live_adapter_tcp'] and not r['source_changes'] and all(x['commit_matches_verified'] and x['tracked_source_clean'] for x in r['upstream'].values()))
 print(json.dumps(r,ensure_ascii=True))
if __name__=='__main__':
 try:main()
 except Exception as e:print(json.dumps({'verification_error_type':type(e).__name__}));sys.exit(1)
