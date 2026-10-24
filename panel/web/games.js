/* A service entry point only. The upstream console owns all game operations. */
"use strict";
pages.games = {
  mount(el) {
    this.el = el;
    el.innerHTML = `<div class="page-head"><h1>游戏管理</h1><span class="sub">GsCore 官方生态</span></div><div class="page-body scroll"><div class="panel panel-pad"><h2>GenshinUID · StarRailUID · ZZZeroUID · BBBUID</h2><p>账号、扫码、签到订阅、战绩图片及指令均由上游项目提供，不再使用自制游戏实现。</p><p id="gsState" role="status">正在检查本机核心…</p><div class="row"><button class="btn primary" id="gsStart">启动核心</button><button class="btn" id="gsRefresh">刷新状态</button><a class="btn" href="http://127.0.0.1:8765/app" target="_blank" rel="noopener noreferrer">打开本机官方控制台</a></div><p class="muted">仅适用于在机器人所在电脑访问。首次注册请在桌面「游戏管理」点击「本机注册码」，密码由你自行设置。核心的重启、停止及细分权限使用官方控制台。</p><p>官方帮助：<code>ys帮助 / bt帮助 / zzz帮助 / bbb帮助</code><br>先绑定对应 UID，再私聊发送 <code>扫码登录</code>。自动签到按游戏开启订阅。</p><p class="muted">原有游戏/签到两个权限开关均须放行，禁用群和群友继续拦截。旧账号数据保留。旧版设置、账号写入与卡片预览接口已停用。</p></div></div>`;
    $("#gsRefresh",el).onclick=()=>this.show();
    $("#gsStart",el).onclick=async()=>{const b=$("#gsStart",el);b.disabled=true;try{await post("/api/games/start",{});await this.show();}catch(e){toastErr(e);b.disabled=false;}};
  },
  async show(){try{const d=await api("/api/games");$("#gsState",this.el).textContent=d.ready?"GsCore 在线 · 仅监听本机":d.port_conflict?"8765 端口被其他进程占用（未操作该进程）":d.running?"核心正在初始化，请稍后刷新":"核心未运行";$("#gsStart",this.el).disabled=d.running;}catch(e){toastErr(e);}},
  async leave(){return true;},
};
