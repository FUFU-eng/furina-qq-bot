"""Loopback-only upstream command smoke test. Never connects to QQ or sends real messages."""
from __future__ import annotations
import asyncio, base64, io, json, os, pathlib, sys, time
import msgspec
from PIL import Image
from websockets.asyncio.client import connect
ROOT=pathlib.Path(__file__).resolve().parents[1]
RESULT=ROOT/'integrations/core-probe.json'
COMMANDS=['gs帮助','sr帮助','zzz帮助','bbb帮助','#原神帮助','#星铁帮助','#绝区零帮助']
async def check(i,command):
 summary={'command':command,'passed':False,'types':[]}
 async with connect('ws://127.0.0.1:8765/ws/MigrationProbe'+str(i),max_size=2**26,open_timeout=10,ping_timeout=60) as ws:
  packet={'bot_id':'onebot','bot_self_id':'0','msg_id':'migration-probe-'+str(i),'user_type':'direct','user_id':'0','user_pm':6,'content':[{'type':'text','data':command}]}
  await ws.send(msgspec.json.encode(packet))
  deadline=time.monotonic()+90
  while time.monotonic()<deadline:
   try:data=msgspec.json.decode(await asyncio.wait_for(ws.recv(),deadline-time.monotonic()))
   except asyncio.TimeoutError:break
   if str(data.get('target_id',''))!='0':continue
   for part in data.get('content',[]):
    kind=part.get('type');value=part.get('data');summary['types'].append(kind)
    if kind=='image' and isinstance(value,str):
     if value.startswith('base64://'):raw=base64.b64decode(value[9:])
     elif value.startswith('data:'):raw=base64.b64decode(value.split(',',1)[1])
     else:continue
     with Image.open(io.BytesIO(raw)) as im:summary.update(passed=True,image_size=list(im.size),image_bytes=len(raw))
    elif kind=='text' and isinstance(value,str) and ('帮助' in value or '签到' in value) and len(value)>60:
     summary.update(passed=True,text_length=len(value))
   if summary['passed']:break
 return summary
async def main():
 RESULT.write_text(json.dumps({'state':'running','pid':os.getpid()}),encoding='utf-8')
 results=[]
 for start in range(0,len(COMMANDS),1):
  batch=await asyncio.gather(*(check(i,c) for i,c in list(enumerate(COMMANDS))[start:start+1]),return_exceptions=True)
  for entry in batch:
   results.append({'passed':False,'error_type':type(entry).__name__} if isinstance(entry,BaseException) else entry)
 RESULT.write_text(json.dumps({'state':'completed','results':results,'passed':all(x['passed'] for x in results)},ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':asyncio.run(main())
