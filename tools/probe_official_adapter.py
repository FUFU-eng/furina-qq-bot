"""Exercise the real upstream NoneBot connector with an in-memory OneBot transport.
No QQ API endpoint is contacted; all outgoing Bot.call_api calls are captured.
"""
from __future__ import annotations
import asyncio, importlib, json, os, pathlib, tempfile, time
ROOT=pathlib.Path(__file__).resolve().parents[1]
RESULT=ROOT/'integrations/adapter-probe.json'
os.environ.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
TEMP=ROOT/'integrations/adapter-probe-work';TEMP.mkdir(exist_ok=True);os.chdir(TEMP)
import nonebot
from nonebot.adapters.onebot.v11 import Adapter, Bot, PrivateMessageEvent
nonebot.init(gsuid_core_host='127.0.0.1',gsuid_core_port='8765',gsuid_core_botid='MigrationAdapterProbe',gsuid_core_repeat=False)
nonebot.get_driver().register_adapter(Adapter)
plugin=nonebot.load_plugin('GenshinUID')
if plugin is None:raise RuntimeError('Official connector did not load')
connector=importlib.import_module('GenshinUID')
sent=asyncio.Event();calls=[]
class MemoryBot(Bot):
 async def call_api(self,api,**data):
  if api in ('send_private_msg','send_msg','send_group_msg','send_private_forward_msg'):
   message=data.get('message') or data.get('messages') or []
   kinds=[getattr(x,'type',None) or (x.get('type') if isinstance(x,dict) else type(x).__name__) for x in message]
   calls.append({'api':api,'types':kinds});sent.set()
   return {'message_id':1}
  if api=='get_login_info':return {'user_id':0,'nickname':'Migration verification'}
  if api=='get_stranger_info':return {'user_id':1,'nickname':'Migration verification','sex':'unknown','age':0}
  raise RuntimeError('Unexpected API in offline transport: '+api)
async def main():
 RESULT.write_text(json.dumps({'state':'running','pid':os.getpid()}))
 bot=MemoryBot(nonebot.get_adapter(Adapter),'0')
 # Test fixture registration, not a real OneBot connection.
 nonebot.get_bots()['0']=bot
 await connector.connect()
 event=PrivateMessageEvent.model_validate({'time':int(time.time()),'self_id':0,'post_type':'message','message_type':'private','sub_type':'friend','message_id':1,'user_id':1,'message':[{'type':'text','data':{'text':'gs帮助'}}],'original_message':[{'type':'text','data':{'text':'gs帮助'}}],'raw_message':'gs帮助','font':0,'sender':{'user_id':1,'nickname':'Migration verification'}})
 try:
  await connector.get_all_message(bot,event)
  await asyncio.wait_for(sent.wait(),90)
  passed=any('image' in x['types'] for x in calls)
  RESULT.write_text(json.dumps({'state':'completed','passed':passed,'captured':calls,'real_qq_messages_sent':0},ensure_ascii=False,indent=2))
 finally:
  await connector.stop_client()
  nonebot.get_bots().pop('0',None)
if __name__=='__main__':
 try:asyncio.run(main())
 except Exception as exc:
  RESULT.write_text(json.dumps({'state':'failed','error_type':type(exc).__name__,'error':str(exc)},ensure_ascii=False,indent=2))
  raise
