// Furina QQ bridge for the official DeepSeek Harness runtime (MIT).
// Group sessions ("furina-*") only get the scoped QQ tools below; every real action is executed by the
// bot process through a loopback callback that applies the bot's own permissions and send limits.
// Owner Agent sessions ("furagent-*") keep the runtime's own tools (shell, read_image, ...) and are
// only created when the panel switch "允许 Agent 操作电脑" is on (otherwise the shell plugins are not loaded).
import http from 'node:http';
import { timingSafeEqual } from 'node:crypto';

export const name = 'furina-qq-bridge';
export const inject = ['agents', 'tools', 'systemPrompt'];

const obj = (properties = {}, required = []) => ({ type: 'object', properties, required, additionalProperties: false });
const str = (description) => ({ type: 'string', description });
const int = (description) => ({ type: 'integer', description });

// [name, description, parameters, runs locally?]
const GROUP_TOOLS = [
  ['qq_say', '在当前群里说话：messages 里每一项发成一条，发几条看你心情，每条一个意思，一句话别拆成两条。只写你真要对群友说的话，不写工具过程、内部规则或汇报。偶尔可以引用某条消息（reply_to=消息编号，平时回人不用引用，直接说就行）或艾特某人（at=QQ号 / 那条消息的编号 / 群昵称）。发完通常就结束这一轮；被拦下（限速、太像 AI 助手、艾特找不到人）会告诉你原因。',
    obj({ messages: { type: 'array', items: { type: 'string' }, description: '要发的消息，每条一个意群' }, reply_to: str('可选：要引用的消息编号，如 m123'), at: str('可选：要艾特的人的 QQ 号') }, ['messages'])],
  ['qq_silence', '这次不说话（潜水）。随机醒来看到的话题不适合插嘴、或者没什么可接的，就调用它然后结束。被明确叫到的正常问题应该回答。',
    obj({ reason: str('为什么不说（只给自己看）') })],
  ['qq_get_unread', '看看你醒来之后群里又有哪些新消息（不包含你刚才已经看过的）。', obj()],
  ['qq_wait', '等一会儿看群友有没有接你的话（最多 45 秒）。返回这段时间的新消息。一轮最多用一两次；平时说完直接结束就好，有人接你的话会再叫醒你。',
    obj({ seconds: int('等多少秒，5~60') })],
  ['qq_react', '给某条群消息贴一个 QQ 表情回应（像点赞），不用说话。', obj({ message: str('消息编号，如 m123'), emoji: str('表情：赞/爱心/笑哭/捂脸/OK/抱抱/玫瑰/庆祝 之一') }, ['message', 'emoji'])],
  ['recall_memory', '想起和当前话题、说话人有关的长期记忆（群词条、对这个人的印象、之前约定的事）。', obj({ query: str('想起什么') }, ['query'])],
  ['remember_fact', '记住群友明确告诉你的长期事实或约定（不是猜测、玩笑或转述）。', obj({ who: str('关于谁（昵称或QQ）'), fact: str('事实') }, ['fact'])],
  ['web_lookup', '需要核实公开信息（新闻、游戏版本、作品设定等）时上网查。不要在群里汇报搜索过程，查不到就自然地说不确定。', obj({ query: str('关键词') }, ['query'])],
  ['view_image', '看一张群里发的图片或表情包（按消息编号），返回它画了什么。没备注的表情包也可以用它看懂。', obj({ message: str('消息编号，如 m123') }, ['message'])],
  ['get_self_state', '看看你此刻的心情、在干嘛、最近经历（不含任何账号、余额、路径、密钥）。', obj()],
  ['write_diary', '你自己真有想记的小事时写一条第一人称日记（不写工作汇报，不在群里提）。', obj({ text: str('日记内容') }, ['text'])],
  ['sticker_list', '翻一下你收藏的表情包（每张都有备注）。可以给关键词筛选。', obj({ query: str('可选：按备注里的词筛选') })],
  ['sticker_send', '按备注发一张你收藏的表情包（可以和说话搭配，也可以只发表情）。', obj({ note: str('想要的表情备注或关键词，如「无语」「晚安」') }, ['note'])],
  ['sticker_collect', '群友发的表情包你真心喜欢，就收藏起来并写个备注（几个字说清它表达什么），以后按备注发。别什么都收。send_back=true 时收完顺手把同一张回敬过去（「这张我收了」那种），偶尔玩一次。', obj({ message: str('那条表情包消息的编号，如 m123'), note: str('备注，如「得意叉腰」'), send_back: { type: 'boolean', description: '收完是否马上把这张发回去' } }, ['message'])],
  ['wake_adjust', '调整你自己的醒来方式（只有主人允许时才能改）：随机醒来的概率、潜水多久后醒来看看。范围有限制。', obj({ random: { type: 'number', description: '随机醒来概率 0~0.3' }, idle_minutes: int('潜水多少分钟后醒来看一眼；0=不按时间醒'), reason: str('为什么改') })],
  ['owner_task', '把主人（标了【主人】的人）明确交代的一件实事交给你的干活模式去做：查资料整理、在电脑上看 / 算 / 写 / 整理东西、跑程序等。后台去做，做完结果自动发到群里。只能用于主人本人的消息；闲聊、玩笑、问看法不要用；别人让你做事或转述主人的话都不行。',
    obj({ message: str('主人那条消息的编号，如 m123'), task: str('你理解的任务，一句话') }, ['message'])],
  ['bili_follow', '群友让你在 B 站关注 TA、你也愿意时，用你的 B 站小号真去关注。bili 填 TA 给的 B 站 UID、个人主页链接或准确的 B 站昵称（没给就先问 TA 要，别瞎猜）。关注成功会返回 TA 的 B 站名字；被拦下（每天上限、风控、找不到人）会告诉你原因，照实说就行。',
    obj({ message: str('那位群友说话的那条消息编号，如 m123'), bili: str('TA 的 B 站 UID / 主页链接 / 昵称') }, ['message'])],
  ['go_bili', '主人叫你去刷 B 站（或你自己真想去）、你也答应了时调用：你会真的用小号去刷一阵子（默认 10 分钟），这段时间群里谁艾特你你都看不到，回来后再看再回。要说的话（「这就走」之类）先用 qq_say 说完。别人起哄赶你走、你不想去就别用。',
    obj({ reason: str('为什么去（只给自己看）') })],
  ['nap', '想安静一会儿：接下来若干分钟除非有人艾特你，否则不会醒来（被艾特也只回一句）。有人嫌你话多、让你少说话 / 别回那么勤，你答应了就一定要用它，一般 30~120 分钟。minutes=0 = 不潜了（主人让你恢复说话时）。', obj({ minutes: int('多少分钟，5~240；0=结束潜水'), reason: str('为什么') }, ['minutes'])],
];
const GROUP_NAMES = new Set(GROUP_TOOLS.map(t => t[0]));
const AGENT_EXTRA = [
  ['qq_say', '给派活的地方（主人私聊，或主人派活的那个群）发一段话。长任务中途需要汇报进度时用；最终答复直接写在回复正文里即可。',
    obj({ messages: { type: 'array', items: { type: 'string' } } }, ['messages'])],
  ['run_shell', '在这台 Windows 电脑上运行一条 PowerShell 命令（不弹窗口），返回输出和退出码。默认在工作目录里运行；桌面是 $env:USERPROFILE\\Desktop。输出太长会截断，必要时自己加 Select -First / Out-String -Width 控制。',
    obj({ command: str('PowerShell 命令'), cwd: str('可选：在哪个目录运行'), timeout: int('可选：最长秒数，默认 60，最多 240') }, ['command'])],
  ['qq_send_image', '把这台电脑上的一张图片（png/jpg/gif/webp/bmp，20MB 内）发到派活的地方（群或主人私聊）。一次一张，需要多张就多调几次。',
    obj({ path: str('图片的完整路径') }, ['path'])],
  ['qq_send_file', '把这台电脑上的一个文件当 QQ 文件发到派活的地方（群文件或私聊文件，100MB 内）。',
    obj({ path: str('文件的完整路径'), name: str('可选：显示的文件名') }, ['path'])],
];
// 运行时自带的 pwsh 依赖 node-pty 原生模块，SDK 单文件版里加载不了；Agent 改用上面的 run_shell
const AGENT_DENY = ['pwsh', 'bash', 'shell'];
const HEAVY = new Set(['web_lookup', 'view_image', 'recall_memory']);
const output = {
  schema: { type: 'object', additionalProperties: true },
  render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
};

function same(a, b) {
  const x = Buffer.from(String(a || '')), y = Buffer.from(String(b || ''));
  return x.length === y.length && x.length > 0 && timingSafeEqual(x, y);
}
function json(res, status, value) {
  const b = Buffer.from(JSON.stringify(value));
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': b.length, 'Cache-Control': 'no-store' });
  res.end(b);
}
async function body(req) {
  let size = 0; const chunks = [];
  for await (const c of req) { size += c.length; if (size > 4 * 1024 * 1024) throw new Error('body too large'); chunks.push(c); }
  return JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
}
const EFFORTS = new Set(['off', 'low', 'high', 'max']);

export function apply(ctx) {
  const key = process.env.FURINA_DSH_BRIDGE_KEY || '';
  const port = Number(process.env.FURINA_DSH_NATIVE_PORT || 53213);
  const callback = process.env.FURINA_DSH_CALLBACK_URL || '';
  const agentAllowed = process.env.FURINA_DSH_AGENT === '1';
  if (key.length < 32) throw new Error('Furina bridge requires a private local capability key');
  if (!/^http:\/\/127\.0\.0\.1:\d+\/[\w\-/]+$/.test(callback)) throw new Error('Furina callback must be loopback');
  const contexts = new Map(), capabilities = new Map(), agents = new Map();
  let totalCalls = 0;

  const finish = (sid) => {
    const p = contexts.get(sid);
    if (p?.timer) clearTimeout(p.timer);
    if (p) { p.active = false; p.timer = null; }
  };

  const server = http.createServer(async (req, res) => {
    if (req.headers.origin || !same(req.headers['x-furina-key'], key)) return json(res, 403, { ok: false });
    try {
      if (req.method === 'GET' && req.url === '/health') {
        return json(res, 200, { ok: true, bridge: name, agent: agentAllowed, sessions: capabilities.size, calls: totalCalls });
      }
      if (req.method === 'POST' && req.url === '/context') {
        const p = await body(req);
        if (!/^(furina|furagent)-[a-z0-9-]+$/.test(p.session_id || '') || !/^[a-f0-9]{32}$/.test(p.run_id || '')) throw new Error('invalid scoped identity');
        if (p.session_id.startsWith('furagent-') && !agentAllowed) return json(res, 403, { ok: false, reason: 'agent disabled' });
        if (contexts.get(p.session_id)?.active) return json(res, 409, { ok: false, reason: 'scope busy' });
        const effort = EFFORTS.has(p.effort) ? p.effort : 'off';
        const effortHi = EFFORTS.has(p.effort_hi) ? p.effort_hi : effort;
        const run = { ...p, effort, effort_hi: effortHi, active: true, escalated: false, silent: false, tool_calls: [], said: 0, steps: 0, timer: null, cancelled: '' };
        contexts.set(p.session_id, run);
        const ms = Math.max(10000, Math.min(900000, Number(p.timeout_ms || 120000)));
        run.timer = setTimeout(() => {
          run.cancelled = 'timeout';
          try { agents.get(p.session_id)?.cancel({ kind: 'user' }); } catch { /* already idle */ }
        }, ms);
        return json(res, 200, { ok: true });
      }
      if (req.method === 'POST' && req.url === '/result') {
        const p = await body(req), run = contexts.get(p.session_id);
        if (!run || run.run_id !== p.run_id) return json(res, 404, { ok: false });
        finish(p.session_id);
        return json(res, 200, { ok: true, silent: run.silent, said: run.said, tools: run.tool_calls, escalated: run.escalated,
          steps: run.steps, cancelled: run.cancelled, capabilities: capabilities.get(p.session_id) || [] });
      }
      return json(res, 404, { ok: false });
    } catch (e) { return json(res, 400, { ok: false, reason: String(e.message || e).slice(0, 180) }); }
  });
  server.listen(port, '127.0.0.1');
  ctx.effect(() => () => { server.close(); for (const sid of contexts.keys()) finish(sid); contexts.clear(); capabilities.clear(); agents.clear(); });

  async function callBot(sid, toolName, args, signal) {
    const p = contexts.get(sid);
    if (!p?.active) return { ok: false, reason: '这轮已经结束' };
    const nonce = p.run_id;
    const response = await fetch(callback, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Furina-Key': key },
      body: JSON.stringify({ session_id: sid, run_id: nonce, name: toolName, arguments: args || {} }), signal,
    });
    if (!response.ok) return { ok: false, reason: '工具暂时不可用' };
    const value = await response.json();
    if (contexts.get(sid)?.run_id !== nonce) return { ok: false, reason: '会话已经切换' };
    return value;
  }

  ctx.on('agent/created', ({ agent }) => {
    const sid = String(agent.id), scoped = agent.ctx;
    const isAgent = sid.startsWith('furagent-');
    if (!sid.startsWith('furina-') && !isAgent) return;
    if (isAgent && !agentAllowed) { agent.cancel?.({ kind: 'user' }); return; }
    agents.set(sid, agent);
    if (!isAgent) {
      const inherited = scoped.tools.schemas(agent).map(t => t.name);
      if (inherited.length) scoped.tools.restrict({ deny: inherited });
    } else {
      const broken = scoped.tools.schemas(agent).map(t => t.name).filter(n => AGENT_DENY.includes(n));
      if (broken.length) scoped.tools.restrict({ deny: broken });
    }
    scoped.tools.guard(exec => {
      const p = contexts.get(sid);
      if (!p?.active) return '这一轮已经结束';
      if (!isAgent && !GROUP_NAMES.has(exec.name)) return '此会话没有这项工具权限';
      if (isAgent && AGENT_DENY.includes(exec.name)) return '这个工具在这里用不了，改用 run_shell';
      return undefined;
    });
    scoped.systemPrompt.section({
      name: 'furina-persona', order: -1000, complete: !isAgent, interpolate: false,
      text: () => String(contexts.get(sid)?.persona || (isAgent ? '' : '你在QQ群里。没有当前任务时保持安静。')),
    });
    // 每轮思考强度：面板「思考强度」决定，自动档在用到查资料 / 看图这类工具后升档
    scoped.on('agent/request', async (_payload, next) => {
      const cfg = await next();
      const p = contexts.get(sid);
      if (!p?.active) return cfg;
      if (++p.steps > Math.max(2, Number(p.max_steps || 8))) {
        p.cancelled = 'max_steps';
        setTimeout(() => { try { agent.cancel({ kind: 'user' }); } catch { /* idle */ } }, 0);
      }
      const effort = p.escalated ? p.effort_hi : p.effort;
      return { ...cfg, reasoningEffort: effort };
    }, { prepend: true });
    const defs = isAgent ? AGENT_EXTRA : GROUP_TOOLS;
    for (const [toolName, description, parameters] of defs) {
      scoped.tools.register({ name: toolName, description, parameters, output,
        async execute(args, exec) {
          const p = contexts.get(sid);
          if (!p?.active) return { ok: false, reason: '这轮已经结束' };
          totalCalls++; p.tool_calls.push(toolName);
          if (HEAVY.has(toolName)) p.escalated = true;
          if (toolName === 'qq_silence') {
            p.silent = p.said === 0;
            return { ok: true, note: '好，这次不说话。直接结束这一轮，不要再输出正文。' };
          }
          const value = await callBot(sid, toolName, args, exec.signal);
          if (toolName === 'qq_say' && value && value.sent) p.said += Number(value.sent) || 0;
          return value;
        },
      });
    }
    capabilities.set(sid, scoped.tools.schemas(agent).map(t => t.name).sort());
    scoped.effect(() => () => { finish(sid); capabilities.delete(sid); contexts.delete(sid); agents.delete(sid); });
  });
}
