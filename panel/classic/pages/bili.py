"""「B站生活」页（PanelWindow 的 mixin）：她此刻在 B 站干嘛、今天做了多少、看过的视频、发过的内容、学到的梗，
以及扫码登录 / 让她现在去刷 / 发动态 / 看回复 / 暂停。数据和网页面板一样来自 panel/services/bili_panel.py。"""
import html
import threading
import time
import base64

from PySide6.QtWidgets import QScrollArea, QDialog, QMessageBox, QGridLayout

from ..common import *  # noqa: F401,F403
from ..widgets import *  # noqa: F401,F403

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


class BiliPageMixin:
    def _build_bili_page(self):
        page = QWidget()
        page.setObjectName("stackPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)

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

        self.bili_live = self._bili_box("此刻", min_h=86)
        layout.addWidget(self.bili_live[0])
        self.bili_stats = QLabel()
        self.bili_stats.setTextFormat(Qt.RichText)
        self.bili_stats.setStyleSheet("background:transparent;")
        layout.addWidget(self.bili_stats)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.bili_feed = self._bili_box("最近的动静", scroll=True)
        self.bili_watched = self._bili_box("看过的视频", scroll=True)
        self.bili_posts = self._bili_box("发过的内容", scroll=True)
        self.bili_learned = self._bili_box("在 B 站学到的梗", scroll=True)
        grid.addWidget(self.bili_feed[0], 0, 0)
        grid.addWidget(self.bili_watched[0], 0, 1)
        grid.addWidget(self.bili_posts[0], 1, 0)
        grid.addWidget(self.bili_learned[0], 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid, 1)

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
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(6)
        lay.addWidget(self._label(title, "sectionLabel", 12))
        body = QLabel("…")
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(Qt.TextBrowserInteraction)
        body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        body.setStyleSheet("background:transparent; color:#dbe4f3; font-size:12px;")
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
            lay.addWidget(body)
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
        self.bili_live[1].setText(live + req)

        t, c = st.get("today") or {}, d.get("caps") or {}
        cells = [
            f"<td style='padding:6px 14px 6px 0'><span style='color:{col}'>●</span> 今天{label} "
            f"<b style='font-size:15px'>{int(t.get(k) or 0)}</b>"
            f"<span style='color:#8193b2'>{(' / ' + str(c[k])) if int(c.get(k) or 0) > 0 else ' · 不限'}</span></td>"
            for k, label, col in _STATS]
        self.bili_stats.setText("<table><tr>" + "".join(cells[:3]) + "</tr><tr>" + "".join(cells[3:]) + "</tr></table>")

        feed = d.get("activity") or []
        self.bili_feed[1].setText("<br>".join(
            f"<span style='color:{_FEED_C.get(x.get('kind'), '#8193b2')}'>●</span> "
            f"<span style='color:#8193b2'>{_hm(x.get('ts'))}</span>　{_E(str(x.get('text') or ''))}" for x in feed[:40])
            or "<span style='color:#8193b2'>还没有动静</span>")

        watched = d.get("watched") or []
        self.bili_watched[1].setText("<br><br>".join(
            f"<a style='color:#6ad4ff' href='https://www.bilibili.com/video/{_E(x.get('bvid') or '')}'>{_E(x.get('title') or x.get('bvid') or '')}</a>"
            f" <span style='color:#8193b2'>· {_E(x.get('up') or '')} · {_ago(x.get('ts'), now)}"
            f"{(' · 兴趣 ' + str(x['interest'])) if x.get('interest') is not None else ''}</span>"
            f"{('<br>💭 ' + _E(str(x['thought']))) if x.get('thought') else ''}"
            f"{(ACTS_OPEN + _E('、'.join(map(str, x['acts']))) + '</span>') if x.get('acts') else ''}"
            for x in watched[:30]) or "<span style='color:#8193b2'>还没看过视频</span>")

        posts = d.get("posts") or []
        self.bili_posts[1].setText("<br><br>".join(
            f"<span style='color:#ffd166'>[{_POST_KIND.get(p.get('kind'), _E(str(p.get('kind') or '')))}]</span> "
            f"<span style='color:#8193b2'>{_hm(p.get('ts'))}</span>　"
            + (f"<a style='color:#dbe4f3' href='{_E(p['url'])}'>{_E(str(p.get('text') or ''))}</a>" if p.get("url") else _E(str(p.get("text") or "")))
            for p in posts[:40]) or "<span style='color:#8193b2'>还没发过东西</span>")

        learned = d.get("learned") or []
        trend = (d.get("trending") or {}).get("words") or []
        txt = "<br>".join(f"<b>{_E(str(x.get('term') or ''))}</b>　<span style='color:#8193b2'>{_E(str(x.get('meaning') or ''))}</span>"
                          for x in learned[:60]) or "<span style='color:#8193b2'>还没学到新梗</span>"
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
