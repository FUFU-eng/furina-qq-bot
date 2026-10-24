"""One-shot data migration using upstream GsBind, deal_ck and subscription APIs.
The legacy SQLite database is read-only. Cookies never leave the machine in logs/files.
Only upstream's normal MiHoYo authentication requests are made.
"""
from __future__ import annotations
import argparse, asyncio, contextlib, importlib.util, json, logging, os, pathlib, sqlite3, sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
CORE=ROOT/'integrations/gsuid_core';RESULT=ROOT/'integrations/account-migration-result.json'
os.chdir(CORE);sys.path.insert(0,str(CORE));logging.disable(logging.CRITICAL)
GAMES={'genshin':('gs','GenshinUID','[原神] 自动签到','genshinuid_config','CONIFG_DEFAULT'),
       'sr':('sr','StarRailUID','[星铁] 自动签到','starrailuid_config','CONIFG_DEFAULT'),
       'zzz':('zzz','ZZZeroUID','[绝区零] 自动签到','zzzerouid_config','CONFIG_DEFAULT'),
       'bh3':('bbb','BBBUID','[崩坏3] 自动签到','bbb_config','CONFIG_DEFAULT')}

def read_legacy():
 path=ROOT/'data/furina.db'
 with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as con:
  values={key:json.loads(value) for key,value in con.execute("SELECT key,value FROM docs WHERE ns='mihoyo' AND key IN ('accounts','config')")}
  perms={key:json.loads(value) for key,value in con.execute("SELECT key,value FROM docs WHERE ns='perm'")}
 return values.get('accounts') or {},values.get('config') or {},perms

def settings(name,folder,attribute):
 from gsuid_core.utils.plugins_config.gs_config import StringConfig
 from gsuid_core.data_store import get_res_path
 path=CORE/'gsuid_core/plugins'/name/name/folder/'config_default.py'
 spec=importlib.util.spec_from_file_location('_migration_defaults_'+name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 return StringConfig(name,get_res_path(name)/'config.json',getattr(module,attribute))

def configure_schedules(activate=False):
 _,config,_=read_legacy();clock=str(config.get('sign_time') or '10:00').split(':')
 if len(clock)!=2 or not all(x.isdigit() for x in clock):raise ValueError('Invalid legacy sign time')
 if not 0<=int(clock[0])<=23 or not 0<=int(clock[1])<=59:raise ValueError('Invalid legacy sign time')
 for key,(_,name,_,folder,attribute) in GAMES.items():
  conf=settings(name,folder,attribute)
  assert conf.set_config('SignTime',[int(x) for x in clock] if name=='GenshinUID' else clock)
  assert conf.set_config('SchedSignin',bool(activate and config.get('auto_sign',True) and (config.get('games') or {}).get(key,True)))


def group_allowed(qq,gid,perms,owners):
 # Conservative one-time translation of existing grants; never broaden access.
 data=perms.get(gid) or {}
 if not data.get('enabled',True):return False
 features=data.get('features') or {}
 if not all(features.get(k,True) for k in ('game_query','miyoushe_sign')):return False
 if qq in owners:return True
 if qq in {str(x) for x in data.get('disabled_users',[])}:return False
 user=(data.get('user_features') or {}).get(qq) or {}
 return all(user.get(k,True) for k in ('game_query','miyoushe_sign'))

async def migrate():
 if RESULT.exists():raise RuntimeError('Migration already has a result; inspect it instead of replaying.')
 from dotenv import dotenv_values
 from gsuid_core.utils.database.base_models import init_database
 from gsuid_core.utils.database.models import GsBind,GsUser
 from gsuid_core.utils.cookie_manager.add_ck import deal_ck,persist_cookie_keys
 from gsuid_core.subscribe import gs_subscribe
 from gsuid_core.models import Event
 await init_database()
 accounts,config,perms=read_legacy()
 env=dotenv_values(ROOT/'.env');bot_self_id=str(env.get('BOT_QQ') or '')
 if not bot_self_id.isdigit():raise ValueError('Missing configured bot identity')
 ws_id=str(env.get('GSUID_CORE_BOTID') or 'NoneBot2')
 owners={str(env.get('AI_CHAT_GROUP_OWNER_QQ') or env.get('RELAY_OWNER_QQ') or '')}
 items=list(accounts.items()) if isinstance(accounts,dict) else [(str(x.get('qq','')),x) for x in accounts]
 report={'state':'running','accounts':[],'summary':{'accounts':len(items),'credential_imported':0,'needs_rebind':0,'uid_only':0,'uid_bindings':0,'subscriptions':0,'already_in_upstream':0}}
 RESULT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 for key,account in items:
  qq=str(account.get('qq') or key);gid=str(account.get('group_id') or '')
  row={'user_id':qq,'uid_bindings':0,'credentials':'uid_only','subscriptions':0}
  if not qq.isdigit():row['credentials']='needs_rebind';report['summary']['needs_rebind']+=1;report['accounts'].append(row);continue
  existing=await GsUser.select_data_list(qq,'onebot')
  if existing and any(x.cookie for x in existing):
   row['credentials']='already_in_upstream';report['summary']['already_in_upstream']+=1;report['accounts'].append(row);continue
  roles=account.get('roles') or []
  if isinstance(roles,dict):roles=[dict(role,game_key=g) for g,rs in roles.items() if isinstance(rs,list) for role in rs if isinstance(role,dict)]
  if not isinstance(roles,list):roles=[]
  roles=sorted((x for x in roles if isinstance(x,dict)),key=lambda x:not bool(x.get('is_chosen')))
  for role in roles:
   game=str(role.get('game_key') or '');uid=str(role.get('game_uid') or '')
   if game not in GAMES or not uid.isdigit():continue
   result=await GsBind.insert_uid(qq,'onebot',uid,group_id=gid or None,game_name=GAMES[game][0])
   if result in (0,-2):row['uid_bindings']+=1
  cookies=account.get('cookies') or {}
  imported=False
  if isinstance(cookies,dict) and cookies:
   try:
    raw=';'.join(f'{k}={v}' for k,v in cookies.items() if k in persist_cookie_keys and isinstance(v,(str,int)))
    image,status=await asyncio.wait_for(deal_ck('onebot',raw,qq),120)
    del raw,image
    records=await GsUser.select_data_list(qq,'onebot')
    imported=bool(status and records and any(x.cookie and any(getattr(x,f,None) for f in ('uid','sr_uid','zzz_uid','bbb_uid')) for x in records))
   except Exception as exc:row['error_type']=type(exc).__name__
   row['credentials']='credential_imported' if imported else 'needs_rebind'
  report['summary'][row['credentials']]+=1
  if imported and account.get('auto_sign',True) and (not gid or group_allowed(qq,gid,perms,owners)):
   enabled=account.get('enabled_games',list(GAMES))
   whitelist={str(x) for x in (config.get('notify_groups') or [])}
   target_gid=gid if gid and (not whitelist or gid in whitelist) else None
   event=Event(bot_id='onebot',bot_self_id=bot_self_id,user_type='group' if target_gid else 'direct',group_id=target_gid,user_id=qq,WS_BOT_ID=ws_id)
   for game,(upstream_game,_,task,_,_) in GAMES.items():
    if game not in enabled or not (config.get('games') or {}).get(game,True):continue
    uid=await GsBind.get_uid_by_game(qq,'onebot',upstream_game)
    if not uid:continue
    record=await GsUser.select_data_by_uid(uid,upstream_game)
    if not record or not record.cookie:continue
    await gs_subscribe.add_subscribe('single',task,event,uid=uid)
    row['subscriptions']+=1
  report['summary']['uid_bindings']+=row['uid_bindings'];report['summary']['subscriptions']+=row['subscriptions']
  report['accounts'].append(row);RESULT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 report['state']='completed';RESULT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 return report['summary']

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--configure-only',action='store_true');parser.add_argument('--activate-schedules',action='store_true');args=parser.parse_args()
 try:
  with open(os.devnull,'w') as silent,contextlib.redirect_stdout(silent),contextlib.redirect_stderr(silent):
   if args.configure_only or args.activate_schedules:
    configure_schedules(args.activate_schedules);summary={'schedules_activated':args.activate_schedules}
   else:summary=asyncio.run(migrate())
  print(json.dumps(summary,ensure_ascii=True))
 except Exception as exc:
  print(json.dumps({'failed':True,'error_type':type(exc).__name__},ensure_ascii=True));sys.exit(1)
