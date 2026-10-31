"""「B站生活」页（PanelWindow 的 mixin）：她此刻在 B 站干嘛、今天做了多少、看过的视频、发过的内容、学到的梗，
以及扫码登录 / 让她现在去刷 / 发动态 / 看回复 / 暂停。数据和网页面板一样来自 panel/services/bili_panel.py。"""
import html
import threading
import time
import base64

from PySide6.QtWidgets import QScrollArea, QDialog, QMessageBox, QGridLayout

from ..common import *  # noqa: F401,F403
from ..widgets import *  # noqa: F401,F403
from .bili_pics import BiliPicsMixin

_STATE = {"idle": ("摸鱼中", "#8193b2"), "browsing": ("在找视频", "#6ad4ff"), "watching": ("正在看", "#3ecf8e"),
          "thinking": ("在回味", "#ffd166"), "acting": ("在动手", "#fb7299")}
_FEED_C = {"start": "#3ecf8e", "end": "#8193b2", "watch": "#6ad4ff", "like": "#f472b6", "danmaku": "#3ecf8e",
           "comment": "#6ad4ff", "reply": "#a78bfa", "dynamic": "#ffd166", "risk": "#ef4444", "error": "#ef4444"}
_POST_KIND = {"comment": "评论", "danmaku": "弹幕", "dynamic": "动态", "reply": "回复", "like": "点赞"}
_STATS = (("rounds", "刷了几轮", "#fb7299"), ("likes", "点赞", "#f472b6"), ("danmaku", "弹幕", "#3ecf8e"),
          ("comments", "评论", "#6ad4ff"), ("replies", "回复", "#a78bfa"), ("dynamics", "动态", "#ffd166"))
_E = html.escape
ACTS_OPEN = "<br><span style='color:#a78bfa'>"


def _ago(ts, now=None):
    try:
        sec = max(0, int((now or time.time()) - float(ts or 0)))
    except (TypeError, ValueError):
        return ""
    if sec < 60:
        return "刚刚"
    if sec < 3600:
        return f"{sec // 60} 分钟前"
    if sec < 86400:
        return f"{sec // 3600} 小时前"
    return f"{sec // 86400} 天前"


def _hm(ts):
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return ""


_GRAY = "<span style='color:#8193b2'>{}</span>"


def _annoy_color(v):
    return "#ef4444" if v >= 60 else "#ffb347" if v >= 35 else "#ffd166" if v >= 15 else "#3ecf8e"


def _inner_html(inner, now=None):
    """心情一行 + 烦躁值（群里整体 + 对谁烦、为什么）。"""
    mv = int(inner.get("mood") or 0)
    out = [f"心情 <b style='color:#6ad4ff'>{_E(str(inner.get('mood_label') or ''))}</b> {mv}/100"
           + (f"　{_E('、'.join(inner['feelings']))}" if inner.get("feelings") else "")]
    if inner.get("mood_reason"):
        out.append(_GRAY.format("　因为：" + _E(inner["mood_reason"])))
    a = inner.get("annoy") or {}
    if not a.get("enabled"):
        out.append(_GRAY.format("烦躁值关着（设置里能开）"))
        return "<br>".join(out)
    g = float(a.get("global") or 0)
    out.append(f"群里吵不吵 <b style='color:{_annoy_color(g)}'>{_E(a.get('global_band') or '还好')}</b> {g:.0f}/100")
    users = [u for u in a.get("users") or [] if float(u.get("level") or 0) >= 5]
    for u in users[:5]:
        v = float(u.get("level") or 0)
        out.append(f"对 <b>{_E(str(u.get('name') or ''))}</b> <b style='color:{_annoy_color(v)}'>{_E(u.get('band') or '还行')}</b> {v:.0f}"
                   + (_GRAY.format("　" + _E(str(u.get("why") or ""))) if u.get("why") else ""))
    if not users:
        out.append(_GRAY.format("谁也没惹她，心平气和"))
    return "<br>".join(out)


def _taste_html(t, now=None):
    if not t.get("enabled"):
        return _GRAY.format("口味记录关着（B站设置里能开）")
    out = []
    for key, name, col in (("likes", "在迷", "#fb7299"), ("bored", "看腻了", "#ffd166"), ("dislikes", "不爱看", "#8193b2")):
        items = t.get(key) or []
        if items:
            out.append(f"<span style='color:{col}'>{name}</span>　" + "、".join(
                _E(("UP主 " if x.get("kind") == "up" else "") + str(x.get("term") or "")) for x in items[:6]))
    ops = t.get("opinions") or []
    if ops:
        o = ops[0]
        out.append(_GRAY.format(f"最近记住的：{_ago(o.get('ts'), now)}{'很喜欢' if o.get('love') else '不喜欢'}《"
                                + _E(str(o.get('title') or ''))[:30] + "》"
                                + (("：" + _E(str(o.get("thought") or ""))[:40]) if o.get("thought") else "")))
    return "<br>".join(out) or _GRAY.format("还没形成口味：多刷几轮，她喜欢什么、腻了什么会慢慢显出来")


def _livestream_html(ls):
    if not ls.get("enabled"):
        return ""
    if ls.get("error"):
        state = f"<b style='color:#ff6b6b'>直播没法上场</b>　{_E(ls['error'])}"
    elif ls.get("live"):
        s = ls.get("stats") or {}
        state = (f"<b style='color:#fb7299'>🎙 正在直播</b>" + ("（闭麦中）" if ls.get("muted") else "")
                 + f"　弹幕 {int(s.get('danmaku') or 0)} · 她回了 {int(s.get('replies') or 0)} 次 · 挡掉 {int(s.get('blocked') or 0)}"
                 + ("" if ls.get("stage_clients") else "　<span style='color:#ffd166'>直播姬里没加舞台画面</span>")
                 + (f"<br><span style='color:#8193b2'>刚说：{_E(ls['last_line'])[:60]}</span>" if ls.get("last_line") else ""))
    else:
        state = "<b style='color:#8193b2'>🎙 等你开播</b>　在直播姬里点开始直播，她半分钟内上场"
    return (f"<br><br>{state}<br><span style='color:#8193b2'>直播姬素材地址：</span>"
            f"<span style='color:#6ad4ff'>{_E(ls.get('stage_url') or '')}</span>")


class BiliPageMixin(BiliPicsMixin):
    def _build_bili_page(self):
        page = QWidget()
        page.setObjectName("stackPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(14)

        head = QHBoxLayout()
        self.bili_status = self._label("正在读取 B 站状态…", "pageSubLabel", 11)
        self.bili_status.setTextFormat(Qt.RichText)
        head.addWidget(self.bili_status, 1)
        head.addWidget(self._tool_button("刷新", self.refresh_bili))
        head.addWidget(self._tool_button("B站设置", lambda: self._open_settings("bili")))
        self.bili_login_btn = self._tool_button("扫码登录", self._bili_login_or_out)
        head.addWidget(self.bili_login_btn)
        layout.addLayout(head)

        bar = QFrame()
        bar.setObjectName("statToolbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)
        self.bili_kw = QLineEdit()
        self.bili_kw.setObjectName("searchInput")
        self.bili_kw.setPlaceholderText("想让她搜什么（可留空，留空就按她自己的兴趣刷）")
        self.bili_kw.setClearButtonEnabled(True)
        row.addWidget(self.bili_kw, 1)
        self.bili_btns = {}
        for action, text in (("surf", "刷一下"), ("dynamic", "发条动态"), ("replies", "看回复")):
            b = self._tool_button(text, lambda _=False, a=action: self._bili_request(a))
            self.bili_btns[action] = b
            row.addWidget(b)
        self.bili_pause_btn = self._tool_button("暂停", self._bili_toggle_pause)
        row.addWidget(self.bili_pause_btn)
        layout.addWidget(bar)

        layout.addWidget(self._build_bili_pics())
        self.bili_live = self._bili_box("此刻", min_h=86)
        layout.addWidget(self.bili_live[0])
        inner = QHBoxLayout()
        inner.setSpacing(12)
        self.bili_inner = self._bili_box("她此刻的心情和烦躁", min_h=70)
        self.bili_taste = self._bili_box("她的口味（看得越多越会变）", min_h=70)
        inner.addWidget(self.bili_inner[0], 1)
        inner.addWidget(self.bili_taste[0], 1)
        layout.addLayout(inner)
        stats_box = QFrame()
        stats_box.setObjectName("contentSurface")
        sl = QVBoxLayout(stats_box)
        sl.setContentsMargins(16, 12, 16, 12)
        sl.setSpacing(6)
        sl.addWidget(self._label("今天做了多少", "sectionLabel", 12))
        self.bili_stats = QLabel()
        self.bili_stats.setTextFormat(Qt.RichText)
        self.bili_stats.setStyleSheet("background:transparent;")
        sl.addWidget(self.bili_stats)
        layout.addWidget(stats_box)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.bili_feed = self._bili_box("最近的动静")
        self.bili_watched = self._bili_box("看过的视频")
        self.bili_posts = self._bili_box("发过的内容")
        self.bili_learned = self._bili_box("在 B 站学到的梗")
        grid.addWidget(self.bili_feed[0], 0, 0)
        grid.addWidget(self.bili_watched[0], 0, 1)
        grid.addWidget(self.bili_posts[0], 1, 0)
        grid.addWidget(self.bili_learned[0], 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        layout.addStretch(1)

        self._bili_data = None
        self._bili_timer = QTimer(page)
        self._bili_timer.setInterval(10000)
        self._bili_timer.timeout.connect(self._bili_tick)
        self._bili_timer.start()
        return page

    def _bili_box(self, title, scroll=False, min_h=0):
        frame = QFrame()
        frame.setObjectName("contentSurface")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        lay.addWidget(self._label(title, "sectionLabel", 12))
        body = QLabel("…")
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(Qt.TextBrowserInteraction)
        body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        body.setStyleSheet("background:transparent; color:#d5deea; font-size:13px;")
        if min_h:
            body.setMinimumHeight(min_h)
        if scroll:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.NoFrame)
            area.setStyleSheet("QScrollArea{background:transparent;border:none;} QScrollArea > QWidget > QWidget{background:transparent;}")
            area.setWidget(body)
            lay.addWidget(area, 1)
        else:
            lay.addWidget(body, 1)  # 同一行两张卡片等高时，多出来的空间给正文，标题始终贴顶
        return frame, body

    # ---------------- 数据 ----------------
    def _bili_tick(self):
        idx = getattr(self, "_active_tab", -1)
        if 0 <= idx < len(self.rail_kinds) and self.rail_kinds[idx] == "bili" and self.isVisible():
            self.refresh_bili()

    def refresh_bili(self):
        try:
            from ...services import bili_panel
            d = bili_panel.overview()
        except Exception as e:  # noqa: BLE001
            self.bili_status.setText(f"<span style='color:#ef4444'>读不到 B 站数据：{_E(str(e))[:120]}</span>")
            return
        self._bili_data = d
        self._refresh_bili_pics(d.get("pics") or {})
        st = d.get("status") or {}
        now = d.get("now") or time.time()
        if not st.get("logged_in"):
            s = "<span style='color:#ffb347'>● 还没登录 B 站小号</span> · 点右上角「扫码登录」"
        elif st.get("halted"):
            s = f"<span style='color:#ef4444'>● 已暂停</span> · {_E(st.get('halt_reason') or '')}"
        else:
            s = f"<span style='color:#3ecf8e'>● 已登录</span> {_E(st.get('uname') or '')}"
        s += f" · 自动时段 {_E(str(d.get('hours') or ''))}"
        if not d.get("enabled"):
            s += " · <span style='color:#ffb347'>总开关关着</span>"
        if d.get("dry_run"):
            s += " · <span style='color:#ffb347'>演练模式（不真发）</span>"
        if d.get("mention"):
            s += f" · 主人：{_E(d['mention'])}"
        self.bili_status.setText(s)
        logged = bool(st.get("logged_in"))
        self.bili_login_btn.setText("退出登录" if logged else "扫码登录")
        self.bili_pause_btn.setText("恢复" if st.get("halted") else "暂停")
        for b in self.bili_btns.values():
            b.setEnabled(logged)

        lv = d.get("live") or {}
        label, col = _STATE.get(lv.get("state"), _STATE["idle"])
        v = lv.get("video") or {}
        pend = d.get("pending")
        req = ""
        if pend:
            names = {"surf": "刷一下", "dynamic": "发动态", "replies": "看回复", "trending": "取热搜"}
            req = (f"<br><span style='color:#ffd166'>等机器人领取：{names.get(pend.get('action'), _E(str(pend.get('action'))))}"
                   f"{('「' + _E(pend.get('keyword')) + '」') if pend.get('keyword') else ''}</span>")
        if lv.get("state") not in (None, "idle") and v:
            url = f"https://www.bilibili.com/video/{_E(v.get('bvid') or '')}"
            live = (f"<b style='color:{col}'>{label}</b>　<a style='color:#6ad4ff' href='{url}'>{_E(v.get('title') or v.get('bvid') or '')}</a>"
                    f"<br><span style='color:#8193b2'>UP主：{_E(v.get('up') or '-')}{(' · ' + _E(v['tname'])) if v.get('tname') else ''}</span>"
                    f"<br>{_E(lv.get('step') or '')}")
        elif lv.get("state") not in (None, "idle"):
            live = f"<b style='color:{col}'>{label}</b>　{_E(lv.get('step') or '')}"
        else:
            last = (d.get("watched") or [None])[0]
            live = ("<b style='color:#8193b2'>💤 没在刷</b>　" +
                    (f"上次 {_ago(last.get('ts'), now)} 看了《{_E(last.get('title') or '')}》" if last else "还没刷过视频") +
                    "<br><span style='color:#8193b2'>点「刷一下」让她现在就去；输入关键词可以指定她搜什么。</span>")
        self.bili_live[1].setText(live + req + _livestream_html(d.get("livestream") or {}))

        self.bili_inner[1].setText(_inner_html(d.get("inner") or {}, now))
        self.bili_taste[1].setText(_taste_html(d.get("taste") or {}, now))

        t, c = st.get("today") or {}, d.get("caps") or {}
        cells = [
            f"<td style='padding:2px 26px 2px 0'><span style='color:{col}'>●</span> {label} "
            f"<b style='font-size:15px'>{int(t.get(k) or 0)}</b>"
            f"<span style='color:#8193b2'>{(' / ' + str(c[k])) if int(c.get(k) or 0) > 0 else ' · 不限'}</span></td>"
            for k, label, col in _STATS]
        self.bili_stats.setText("<table><tr>" + "".join(cells) + "</tr></table>")

        feed = d.get("activity") or []
        self.bili_feed[1].setText("<br>".join(
            f"<span style='color:{_FEED_C.get(x.get('kind'), '#8193b2')}'>●</span> "
            f"<span style='color:#8193b2'>{_hm(x.get('ts'))}</span>　{_E(str(x.get('text') or ''))}" for x in feed[:15])
            or "<span style='color:#8193b2'>还没有动静</span>")

        watched = d.get("watched") or []
        self.bili_watched[1].setText("<br><br>".join(
            f"<a style='color:#6ad4ff' href='https://www.bilibili.com/video/{_E(x.get('bvid') or '')}'>{_E(x.get('title') or x.get('bvid') or '')}</a>"
            f" <span style='color:#8193b2'>· {_E(x.get('up') or '')} · {_ago(x.get('ts'), now)}"
            f"{(' · 兴趣 ' + str(x['interest'])) if x.get('interest') is not None else ''}</span>"
            f"{('<br>💭 ' + _E(str(x['thought']))) if x.get('thought') else ''}"
            f"{(ACTS_OPEN + _E('、'.join(map(str, x['acts']))) + '</span>') if x.get('acts') else ''}"
            for x in watched[:8]) or "<span style='color:#8193b2'>还没看过视频</span>")

        posts = d.get("posts") or []
        self.bili_posts[1].setText("<br><br>".join(
            f"<span style='color:#ffd166'>[{_POST_KIND.get(p.get('kind'), _E(str(p.get('kind') or '')))}]</span> "
            f"<span style='color:#8193b2'>{_hm(p.get('ts'))}</span>　"
            + (f"<a style='color:#dbe4f3' href='{_E(p['url'])}'>{_E(str(p.get('text') or ''))}</a>" if p.get("url") else _E(str(p.get("text") or "")))
            for p in posts[:10]) or "<span style='color:#8193b2'>还没发过东西</span>")

        learned = d.get("learned") or []
        trend = (d.get("trending") or {}).get("words") or []
        txt = "<br>".join(f"<b>{_E(str(x.get('term') or ''))}</b>　<span style='color:#8193b2'>{_E(str(x.get('meaning') or ''))}</span>"
                          for x in learned[:20]) or "<span style='color:#8193b2'>还没学到新梗</span>"
        if trend:
            txt += "<br><br><span style='color:#fb7299'>B站热词：</span>" + _E("、".join(map(str, trend[:20])))
        self.bili_learned[1].setText(txt)

    # ---------------- 操作 ----------------
    def _bili_request(self, action):
        try:
            from ...services import bili_panel
            bili_panel.request(action, self.bili_kw.text() if action == "surf" else "")
            self.add_log("info", f"[B站] 已经告诉她：{ {'surf': '去刷一下', 'dynamic': '发条动态', 'replies': '看看回复'}.get(action, action) }（机器人 10 秒内领取）")
        except Exception as e:  # noqa: BLE001
            self.add_log("error", f"[B站] 操作失败：{e}")
        self.refresh_bili()

    def _bili_toggle_pause(self):
        st = (self._bili_data or {}).get("status") or {}
        self._bili_request("resume" if st.get("halted") else "pause")

    def _bili_login_or_out(self):
        st = (self._bili_data or {}).get("status") or {}
        if st.get("logged_in"):
            if QMessageBox.question(self, "退出 B 站登录", "确定退出 B 站小号的登录吗？之后要重新扫码。") == QMessageBox.Yes:
                self._bili_request("logout")
            return
        self._bili_login_dialog()

    def _bili_login_dialog(self):
        from ...services import bili_panel
        dlg = QDialog(self)
        dlg.setWindowTitle("扫码登录 B 站小号")
        dlg.setStyleSheet("QDialog{background:#0f1b30;} QLabel{color:#dbe4f3;font-size:13px;}")
        lay = QVBoxLayout(dlg)
        qr = QLabel("正在获取二维码…")
        qr.setAlignment(Qt.AlignCenter)
        qr.setMinimumSize(280, 280)
        tip = QLabel("用 B 站 App 扫一扫（用给她准备的小号）")
        tip.setAlignment(Qt.AlignCenter)
        lay.addWidget(qr)
        lay.addWidget(tip)
        box = {"start": None, "poll": None, "busy": False, "err": None}

        def _start():
            try:
                box["start"] = bili_panel.login_start()
            except Exception as e:  # noqa: BLE001
                box["err"] = str(e)

        def _poll():
            try:
                box["poll"] = bili_panel.login_poll()
            except Exception as e:  # noqa: BLE001
                box["poll"] = {"state": "error", "msg": str(e)}
            box["busy"] = False

        threading.Thread(target=_start, daemon=True).start()
        timer = QTimer(dlg)

        def _tick():
            if box["err"]:
                qr.setText(f"拿不到二维码：{box['err'][:80]}")
                timer.stop()
                return
            if box["start"] and qr.pixmap().isNull():
                raw = base64.b64decode(box["start"]["qr"].split(",", 1)[1])
                pm = QPixmap()
                pm.loadFromData(raw)
                qr.setPixmap(pm.scaled(280, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            if not box["start"]:
                return
            res = box["poll"] or {}
            state = res.get("state")
            if state == "scanned":
                tip.setText("已扫码，请在手机上点确认")
            elif state == "ok":
                tip.setText(f"登录成功：{res.get('uname') or ''}，机器人下一轮就会用这个号")
                timer.stop()
                self.add_log("success", f"[B站] 小号登录成功：{res.get('uname') or ''}")
                QTimer.singleShot(1500, dlg.accept)
                self.refresh_bili()
                return
            elif state in ("expired", "error"):
                tip.setText("二维码过期了，关掉再点一次「扫码登录」" if state == "expired" else f"出错了：{res.get('msg', '')[:80]}")
                timer.stop()
                return
            if not box["busy"]:
                box["busy"] = True
                threading.Thread(target=_poll, daemon=True).start()

        timer.timeout.connect(_tick)
        timer.start(1500)
        dlg.exec()
        timer.stop()
