"""直播画面（「舞台」）：一个本机网页，直播姬把它当素材加进去就能播出去。

地址：http://127.0.0.1:<机器人端口>/live/stage （只在本机能打开）
- /live/events  服务器推送（字幕、弹幕、状态、要播的语音）
- /live/audio/x 合成好的语音
- /live/avatar  形象图（BILI_LIVE_AVATAR 填的图片；不填就显示名字牌）
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from pathlib import Path

from ...core import env
from . import voice

_clients: set[asyncio.Queue] = set()
_recent: deque = deque(maxlen=14)
_state: dict = {"status": "idle", "subtitle": "", "viewers": 0, "label": ""}


def label() -> str:
    return env.get("BILI_LIVE_LABEL", "AI 虚拟主播 · 同人非官方") or "AI 虚拟主播"


def clients() -> int:
    return len(_clients)


def push(event: dict) -> None:
    """推给所有打开着的舞台页面。"""
    kind = event.get("type")
    if kind == "danmaku":
        _recent.append(event)
    elif kind == "say":
        _state["subtitle"] = event.get("text", "")
    elif kind == "status":
        _state.update({k: v for k, v in event.items() if k != "type"})
    for q in list(_clients):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


def snapshot() -> dict:
    return {"type": "hello", "label": label(), "danmaku": list(_recent), "state": dict(_state),
            "avatar": bool(_avatar_path())}


def _avatar_path() -> Path | None:
    p = (env.get("BILI_LIVE_AVATAR", "") or "").strip().strip('"')
    if p and Path(p).is_file() and Path(p).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        return Path(p)
    return None


def mount(app) -> None:
    """挂到机器人自带的网页服务上（用 Starlette 原生路由，避开 FastAPI 对字符串注解的误判）。"""
    from starlette.responses import FileResponse, HTMLResponse, Response, StreamingResponse

    async def _stage(request):  # noqa: ANN001, ANN202
        return HTMLResponse(STAGE_HTML, headers={"Cache-Control": "no-store"})

    async def _events(request):  # noqa: ANN001, ANN202
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        _clients.add(q)

        async def gen():
            try:
                yield "data: " + json.dumps(snapshot(), ensure_ascii=False) + "\n\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                        yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                _clients.discard(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    async def _audio(request):  # noqa: ANN001, ANN202
        name = str(request.path_params.get("name") or "")
        f = voice.cache_dir() / name
        if not re.fullmatch(r"[\w-]{6,40}\.mp3", name) or not f.is_file():
            return Response(status_code=404)
        return FileResponse(f, media_type="audio/mpeg")

    async def _avatar(request):  # noqa: ANN001, ANN202
        p = _avatar_path()
        if not p:
            return Response(status_code=404)
        return FileResponse(p, headers={"Cache-Control": "no-store"})

    app.add_route("/live/stage", _stage, methods=["GET"])
    app.add_route("/live/events", _events, methods=["GET"])
    app.add_route("/live/audio/{name}", _audio, methods=["GET"])
    app.add_route("/live/avatar", _avatar, methods=["GET"])


def touch() -> None:
    _state["ts"] = time.time()


STAGE_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>芙宁娜 · 直播舞台</title>
<meta name="viewport" content="width=1280,height=720">
<style>
:root{--ink:#eaf6ff;--soft:#9cc3e6;--accent:#6fd3ff;--gold:#ffd98a}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1280px;height:720px;overflow:hidden;background:#071a33;color:var(--ink);
 font-family:"HarmonyOS Sans SC","Microsoft YaHei UI","PingFang SC",sans-serif}
#bg{position:absolute;inset:0;background:
 radial-gradient(1200px 520px at 78% 110%,rgba(80,190,255,.28),transparent 60%),
 radial-gradient(700px 420px at 12% -10%,rgba(160,120,255,.20),transparent 60%),
 linear-gradient(160deg,#0a2347 0%,#0b2f5c 45%,#0a4a6e 100%)}
.ripple{position:absolute;border:1px solid rgba(140,220,255,.18);border-radius:50%;animation:rip 9s linear infinite}
@keyframes rip{from{transform:scale(.2);opacity:.9}to{transform:scale(2.4);opacity:0}}
#label{position:absolute;left:28px;top:24px;padding:12px 20px;border-radius:14px;background:rgba(5,16,34,.72);
 border:1.5px solid rgba(111,211,255,.55);font-size:26px;font-weight:700;letter-spacing:2px;color:#fff}
#label small{display:block;font-size:14px;font-weight:400;letter-spacing:1px;color:var(--soft);margin-top:4px}
#status{position:absolute;right:28px;top:28px;font-size:18px;color:var(--soft);text-align:right}
#status b{color:var(--gold);font-weight:600}
#feed{position:absolute;left:28px;top:132px;width:380px;height:400px;display:flex;flex-direction:column;
 justify-content:flex-end;gap:8px;overflow:hidden;-webkit-mask-image:linear-gradient(to bottom,transparent,#000 22%)}
.dm{background:rgba(6,20,42,.62);border-radius:12px;padding:8px 12px;font-size:19px;line-height:1.35;
 animation:pop .35s ease-out;word-break:break-all}
.dm .n{color:var(--accent);margin-right:6px}
.dm.gift .n,.dm.sc .n,.dm.guard .n{color:var(--gold)}
@keyframes pop{from{transform:translateY(14px);opacity:0}to{transform:none;opacity:1}}
#stagebox{position:absolute;right:40px;bottom:150px;width:520px;height:520px;display:flex;align-items:flex-end;justify-content:center}
#avatar{max-width:520px;max-height:540px;transform-origin:50% 100%;filter:drop-shadow(0 12px 30px rgba(0,0,0,.45));
 transition:transform .08s linear;animation:breathe 4.2s ease-in-out infinite}
@keyframes breathe{0%,100%{translate:0 0}50%{translate:0 -6px}}
#plate{width:360px;height:360px;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;
 background:radial-gradient(circle at 35% 30%,rgba(255,255,255,.18),rgba(80,170,255,.10) 55%,rgba(10,40,80,.2));
 border:2px solid rgba(140,220,255,.45);box-shadow:0 0 60px rgba(111,211,255,.25) inset;transition:transform .08s linear;
 animation:breathe 4.2s ease-in-out infinite}
#plate .nm{font-size:64px;font-weight:800;letter-spacing:8px}
#plate .sub{font-size:18px;color:var(--soft);margin-top:10px;letter-spacing:3px}
#sub{position:absolute;left:50%;bottom:36px;transform:translateX(-50%);width:1080px;min-height:92px;padding:18px 30px;
 border-radius:20px;background:rgba(4,14,30,.78);border:1.5px solid rgba(111,211,255,.35);font-size:32px;line-height:1.45;
 text-align:center;text-shadow:0 2px 6px rgba(0,0,0,.6);transition:opacity .3s}
#sub.empty{opacity:0}
#reply{font-size:17px;color:var(--soft);margin-bottom:4px}
#gate{position:absolute;inset:0;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.55);
 font-size:28px;cursor:pointer;z-index:9}
#think{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--accent);margin-left:8px;opacity:0}
#think.on{animation:blink 1s infinite}@keyframes blink{50%{opacity:1}}
</style></head><body>
<div id="bg"></div>
<div id="label">AI 虚拟主播<small>同人非官方 · 内容由 AI 生成</small></div>
<div id="status">观看 <b id="viewers">-</b><br><span id="st">等开播</span><span id="think"></span></div>
<div id="feed"></div>
<div id="stagebox"><div id="plate"><div class="nm">芙宁娜</div><div class="sub">FURINA · LIVE</div></div></div>
<div id="sub" class="empty"><div id="reply"></div><div id="line"></div></div>
<div id="gate">点一下这里开始（浏览器要点一下才肯放声音）</div>
<audio id="au"></audio>
<script>
const $=id=>document.getElementById(id);
for(let i=0;i<5;i++){const r=document.createElement('div');r.className='ripple';const s=260+i*90;
 Object.assign(r.style,{width:s+'px',height:s+'px',right:(120-i*30)+'px',bottom:(-80-i*30)+'px',animationDelay:(i*1.8)+'s'});$('bg').appendChild(r)}
let face=$('plate'),ctx=null,an=null,queue=[],playing=false,hideT=null;
function setAvatar(on){if(!on)return;const img=new Image();img.id='avatar';img.src='/live/avatar?'+Date.now();
 img.onload=()=>{$('stagebox').innerHTML='';$('stagebox').appendChild(img);face=img}}
function esc(s){return String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function addDm(d){const el=document.createElement('div');el.className='dm '+(d.kind||'');
 el.innerHTML='<span class="n">'+esc(d.name)+'</span>'+esc(d.text);$('feed').appendChild(el);
 while($('feed').children.length>14)$('feed').firstChild.remove()}
function setLabel(t){if(!t)return;const p=t.split('·');$('label').innerHTML=esc(p[0].trim())+'<small>'+esc((p.slice(1).join('·')||'').trim()||'内容由 AI 生成')+' · 内容由 AI 生成</small>'}
function ensureCtx(){if(ctx)return;try{ctx=new (window.AudioContext||window.webkitAudioContext)();
 const src=ctx.createMediaElementSource($('au'));an=ctx.createAnalyser();an.fftSize=256;src.connect(an);an.connect(ctx.destination);loop()}catch(e){}}
function loop(){requestAnimationFrame(loop);if(!an||!playing){face.style.transform='';return}
 const a=new Uint8Array(an.frequencyBinCount);an.getByteFrequencyData(a);let v=0;for(const x of a)v+=x;v=v/a.length/255;
 face.style.transform='scale('+(1+Math.min(.06,v*.22))+','+(1-Math.min(.03,v*.1))+')'}
function showSub(t,who){clearTimeout(hideT);$('line').textContent=t;$('reply').textContent=who?('回 '+who):'';$('sub').classList.remove('empty')}
function hideSubLater(ms){clearTimeout(hideT);hideT=setTimeout(()=>$('sub').classList.add('empty'),ms)}
function next(){if(playing||!queue.length)return;const ev=queue.shift();showSub(ev.text,ev.reply_to);
 if(!ev.audio){playing=true;setTimeout(()=>{playing=false;hideSubLater(2500);next()},Math.max(1800,(ev.text||'').length*230));return}
 ensureCtx();const au=$('au');au.src=ev.audio;playing=true;
 au.onended=()=>{playing=false;hideSubLater(2500);next()};
 au.onerror=()=>{playing=false;hideSubLater(2500);next()};
 const p=au.play();if(p&&p.catch)p.catch(()=>{playing=false;queue.unshift(ev);$('gate').style.display='flex'})}
$('gate').onclick=()=>{$('gate').style.display='none';ensureCtx();if(ctx&&ctx.resume)ctx.resume();next()};
function status(s){if(s.viewers!==undefined&&s.viewers)$('viewers').textContent=s.viewers;
 const m={idle:'等开播',live:'正在听弹幕',thinking:'想一想…',speaking:'说话中',muted:'主播暂时离开一下',offline:'下播啦'};
 if(s.status)$('st').textContent=m[s.status]||s.status;$('think').classList.toggle('on',s.status==='thinking')}
function connect(){const es=new EventSource('/live/events');
 es.onmessage=e=>{const ev=JSON.parse(e.data);
  if(ev.type==='hello'){setLabel(ev.label);setAvatar(ev.avatar);$('feed').innerHTML='';(ev.danmaku||[]).forEach(addDm);status(ev.state||{})}
  else if(ev.type==='danmaku')addDm(ev);
  else if(ev.type==='say'){queue.push(ev);next()}
  else if(ev.type==='status')status(ev)
  else if(ev.type==='reload')location.reload()};
 es.onerror=()=>{es.close();setTimeout(connect,3000)}}
connect();
</script></body></html>
"""
