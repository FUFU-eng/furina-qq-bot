"""「模型花费」页（PanelWindow 的 mixin）：DeepSeek 余额、每天花了多少、钱花在哪个功能上。

数据来自 furina/services/llm_spend.py：余额走官方接口（用聊天的 API key），每日账单走 DeepSeek 网站的用量接口
（要在本页填一次「网站令牌」），按功能的明细是机器人自己记的账。网络请求都在后台线程，不卡界面。
"""
import html
import threading
import time
from datetime import date, timedelta

from PySide6.QtCore import QMargins, QObject
from PySide6.QtWidgets import QDialog, QGridLayout, QMessageBox, QScrollArea

from ..common import *  # noqa: F401,F403
from ..widgets import *  # noqa: F401,F403

_E = html.escape
_MUTED = "#8193b2"


class _SpendBus(QObject):
    done = Signal(object)


def _yuan(v, nd=2):
    try:
        return f"¥{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "--"


def _big(n):
    n = float(n or 0)
    if n >= 1e8:
        return f"{n / 1e8:.2f} 亿"
    if n >= 1e4:
        return f"{n / 1e4:.1f} 万"
    return f"{int(n)}"


class SpendPageMixin:
    def _build_spend_page(self):
        page = QWidget()
        page.setObjectName("stackPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(14)

        head = QHBoxLayout()
        self.spend_status = self._label("打开本页时自动读取…", "pageSubLabel", 11)
        self.spend_status.setTextFormat(Qt.RichText)
        self.spend_status.setWordWrap(True)
        head.addWidget(self.spend_status, 1)
        self.spend_refresh_btn = self._tool_button("刷新", lambda: self.refresh_spend(force=True))
        head.addWidget(self.spend_refresh_btn)
        head.addWidget(self._tool_button("网站令牌", self._spend_token_dialog))
        head.addWidget(self._tool_button("省钱设置", lambda: self._open_settings("huoren")))
        layout.addLayout(head)

        tiles = QGridLayout()
        tiles.setSpacing(10)
        self.spend_tiles = {}
        for i, (key, title) in enumerate((("balance", "账户余额"), ("today", "今天"), ("month", "近 30 天"), ("total", "累计消费"))):
            frame = QFrame()
            frame.setObjectName("contentSurface")
            lay = QVBoxLayout(frame)
            lay.setContentsMargins(16, 14, 16, 14)
            lay.setSpacing(4)
            cap = self._label(title, "pageSubLabel", 11)
            lay.addWidget(cap)
            val = QLabel("--")
            val.setTextFormat(Qt.RichText)
            val.setStyleSheet("background:transparent; color:#f1f5f9; font-size:24px; font-weight:bold;")
            sub = QLabel("")
            sub.setTextFormat(Qt.RichText)
            sub.setWordWrap(True)
            sub.setStyleSheet(f"background:transparent; color:{_MUTED}; font-size:11px;")
            lay.addWidget(val)
            lay.addWidget(sub)
            tiles.addWidget(frame, 0, i)
            tiles.setColumnStretch(i, 1)
            self.spend_tiles[key] = (val, sub)
        layout.addLayout(tiles)

        chart_box = QFrame()
        chart_box.setObjectName("contentSurface")
        cl = QVBoxLayout(chart_box)
        cl.setContentsMargins(16, 14, 16, 12)
        cl.setSpacing(4)
        self.spend_chart_title = self._label("每天花了多少（元）", "sectionLabel", 11)
        cl.addWidget(self.spend_chart_title)
        self.spend_chart_holder = QVBoxLayout()
        cl.addLayout(self.spend_chart_holder, 1)
        chart_box.setMinimumHeight(300)
        chart_box.setMaximumHeight(400)
        layout.addWidget(chart_box)

        feat_box = QFrame()
        feat_box.setObjectName("contentSurface")
        fl = QVBoxLayout(feat_box)
        fl.setContentsMargins(16, 14, 16, 14)
        fl.setSpacing(6)
        fl.addWidget(self._label("今天钱花在哪（机器人自己记的账，按功能）", "sectionLabel", 11))
        self.spend_features = QLabel("…")
        self.spend_features.setTextFormat(Qt.RichText)
        self.spend_features.setWordWrap(True)
        self.spend_features.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.spend_features.setStyleSheet("background:transparent; color:#dbe4f3; font-size:12px;")
        fl.addWidget(self.spend_features)
        layout.addWidget(feat_box)

        # 二代仿真花费看板：按群 / 每一轮 / 积极性（参考 bluefishovo「令牌与花费」、fatfish 的花钱感知）
        board_box = QFrame()
        board_box.setObjectName("contentSurface")
        bl = QVBoxLayout(board_box)
        bl.setContentsMargins(16, 14, 16, 14)
        bl.setSpacing(6)
        bh = QHBoxLayout()
        bh.addWidget(self._label("二代仿真花费看板（按群 · 每一轮）", "sectionLabel", 11), 1)
        bh.addWidget(self._tool_button("每一轮明细", self._spend_rounds_dialog))
        bh.addWidget(self._tool_button("花钱感知设置", lambda: self._open_settings("brain")))
        bl.addLayout(bh)
        self.spend_board = QLabel("…")
        self.spend_board.setTextFormat(Qt.RichText)
        self.spend_board.setWordWrap(True)
        self.spend_board.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.spend_board.setStyleSheet("background:transparent; color:#dbe4f3; font-size:12px;")
        bl.addWidget(self.spend_board)
        layout.addWidget(board_box)
        layout.addStretch(1)
        self._spend_board_data = {}

        self._spend_bus = _SpendBus(page)
        self._spend_bus.done.connect(self._on_spend_data)
        self._spend_busy = False
        self._spend_last_fetch = 0.0
        self._spend_chart_view = None
        # 先把上次读到的显示出来（不联网）
        try:
            from furina.services import llm_spend
            last = llm_spend.last_snapshot()
            if last:
                last = dict(last, errors={}, has_token=bool(llm_spend.web_token()), stale=True,
                            local_days=llm_spend.local_days(), features=llm_spend.local_features())
                self._on_spend_data(last)
        except Exception:  # noqa: BLE001
            pass
        return page

    # ---------------- 数据 ----------------
    def refresh_spend(self, force=False):
        if self._spend_busy:
            return
        if not force and time.time() - self._spend_last_fetch < 120:
            return
        self._spend_busy = True
        self._spend_last_fetch = time.time()
        self.spend_refresh_btn.setText("读取中…")
        self.spend_refresh_btn.setEnabled(False)

        def work():
            try:
                from furina.services import llm_spend
                snap = llm_spend.snapshot()
            except Exception as e:  # noqa: BLE001
                snap = {"ts": time.time(), "errors": {"all": f"读取失败：{e}"}}
            self._spend_bus.done.emit(snap)

        threading.Thread(target=work, daemon=True).start()

    def _on_spend_data(self, snap):
        self._spend_busy = False
        self.spend_refresh_btn.setText("刷新")
        self.spend_refresh_btn.setEnabled(True)
        snap = snap or {}
        errs = snap.get("errors") or {}
        web = snap.get("web") or {}
        bal = snap.get("balance") or {}
        local = snap.get("local_days") or {}
        summary = web.get("summary") or {}
        today = date.today().isoformat()

        # 余额：官方接口优先，没有就用网站钱包
        v, s = self.spend_tiles["balance"]
        if bal:
            v.setText(_yuan(bal.get("total")))
            extra = f"充值 {_yuan(bal.get('topped_up'))} · 赠送 {_yuan(bal.get('granted'))}"
            if not bal.get("available", True):
                extra = "<span style='color:#ef4444'>余额不足，已不能调用</span> · " + extra
            s.setText(extra)
        elif summary:
            v.setText(_yuan(summary.get("balance", 0) + summary.get("bonus", 0)))
            s.setText(f"赠送 {_yuan(summary.get('bonus'))}")
        else:
            v.setText("--")
            s.setText(_E(errs.get("balance", "")))

        days = web.get("days") or {}
        amounts = web.get("amounts") or {}
        local_today = (local.get(today) or {}).get("cost", 0.0)
        v, s = self.spend_tiles["today"]
        if web:
            v.setText(_yuan(days.get(today, 0.0)))
            req = (amounts.get(today) or {}).get("requests")
            s.setText((f"{req} 次请求 · " if req else "") + f"本地估算 {_yuan(local_today, 3)}（网站账单有延迟）")
        else:
            v.setText(_yuan(local_today, 3))
            calls = (local.get(today) or {}).get("calls", 0)
            s.setText(f"本地估算 · {calls} 次调用")

        v, s = self.spend_tiles["month"]
        if web:
            v.setText(_yuan(sum(days.values())))
            req = sum(int(a.get("requests") or 0) for a in amounts.values())
            tok = sum(int(a.get("hit", 0)) + int(a.get("miss", 0)) + int(a.get("out", 0)) for a in amounts.values())
            hit = sum(int(a.get("hit", 0)) for a in amounts.values())
            inp = hit + sum(int(a.get("miss", 0)) for a in amounts.values())
            parts = []
            if req:
                parts.append(f"{_big(req)} 次请求")
            if tok:
                parts.append(f"{_big(tok)} tokens")
            if inp:
                parts.append(f"缓存命中 {hit * 100 / inp:.0f}%")
            if web.get("source") == "monthly":
                parts.append("（按月接口）")
            s.setText(" · ".join(parts))
        else:
            v.setText(_yuan(sum(d.get("cost", 0.0) for d in local.values()), 3))
            s.setText("本地估算（记账开始后才有）")

        v, s = self.spend_tiles["total"]
        costs = summary.get("costs") or {}
        total_key = next((k for k in costs if "total" in k.lower()), None) or next(iter(costs), None)
        if total_key is not None:
            v.setText(_yuan(costs[total_key]))
            s.setText("DeepSeek 网站上的累计消费")
        else:
            v.setText("--")
            s.setText("填「网站令牌」后显示" if not snap.get("has_token") else "网站没给这一项")

        self._spend_draw_chart(days if web else {d: x.get("cost", 0.0) for d, x in local.items()}, bool(web))
        self._spend_render_features(snap.get("features") or [], local_today)
        self._spend_render_board(snap.get("board") or {})

        when = time.strftime("%H:%M", time.localtime(float(snap.get("ts") or time.time())))
        bits = [f"更新于 {when}" + ("（上次的数据，正在重新读取）" if snap.get("stale") else "")]
        if web:
            bits.append("<span style='color:#3ecf8e'>● 官方账单</span>")
        elif not snap.get("has_token"):
            bits.append("<span style='color:#ffb347'>● 没填网站令牌：每日账单只能用本地估算</span>")
        for k in ("web", "local", "all"):
            if errs.get(k):
                bits.append(f"<span style='color:#ef4444'>{_E(errs[k])}</span>")
        self.spend_status.setText(" · ".join(bits))

    def _spend_draw_chart(self, per_day, official):
        from PySide6.QtCharts import QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView, QValueAxis
        from PySide6.QtGui import QBrush
        t = date.today()
        dates = [(t - timedelta(days=i)) for i in range(29, -1, -1)]
        vals = [float(per_day.get(d.isoformat(), 0.0)) for d in dates]
        bset = QBarSet("")
        for x in vals:
            bset.append(x)
        bset.setColor(QColor("#6ad4ff" if official else "#ffd166"))
        series = QBarSeries()
        series.setBarWidth(0.7)
        series.append(bset)
        chart = QChart()
        chart.addSeries(series)
        ax = QBarCategoryAxis()
        # 每 3 天标一个日期、横着放（类目名必须唯一：不标的那天用不同长度的空白占位）
        ax.append([f"{d.month}/{d.day}" if (len(dates) - 1 - i) % 3 == 0 else " " * (i + 1) for i, d in enumerate(dates)])
        ax.setLabelsFont(QFont("Microsoft YaHei", 8))
        if hasattr(ax, "setTruncateLabels"):
            ax.setTruncateLabels(False)  # 不然会被省略成「1…」
        ax.setGridLineVisible(False)
        chart.addAxis(ax, Qt.AlignBottom)
        series.attachAxis(ax)
        ay = QValueAxis()
        top = max(vals) if vals else 0
        step = next((x for x in (0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100)
                     if top / x <= 5), 200)
        ticks = max(1, -(-top // step)) if top > 0 else 1
        ay.setRange(0, step * ticks)
        ay.setTickCount(int(ticks) + 1)
        ay.setLabelFormat("%.2f" if step < 0.1 else "%.1f" if step < 1 else "%.0f")
        ay.setLabelsFont(QFont("Microsoft YaHei", 9))
        chart.addAxis(ay, Qt.AlignLeft)
        series.attachAxis(ay)
        chart.legend().hide()
        chart.setBackgroundBrush(QBrush(QColor(SURFACE)))
        chart.setBackgroundRoundness(0)
        chart.setMargins(QMargins(0, 6, 0, 0))
        for a in (ax, ay):
            a.setLabelsColor(QColor(TEXT_SECONDARY))
            a.setLinePenColor(QColor("#2a3750"))
            a.setGridLineColor(QColor("#222e44"))
        bset.hovered.connect(lambda on, i, ds=dates, vs=vals: self.spend_chart_title.setText(
            f"每天花了多少（元） · {ds[i].month}/{ds[i].day}：{_yuan(vs[i], 3)}" if on else self._spend_chart_caption))
        if self._spend_chart_view is None:
            self._spend_chart_view = QChartView()
            self._spend_chart_view.setRenderHint(QPainter.Antialiasing)
            self._spend_chart_view.setMinimumHeight(200)
            self._spend_chart_view.setStyleSheet("background:transparent;")
            self.spend_chart_holder.addWidget(self._spend_chart_view)
        old = self._spend_chart_view.chart()
        self._spend_chart_view.setChart(chart)  # 同一个视图换图表，不叠出旧图
        if old is not None:
            old.deleteLater()
        src = "DeepSeek 官方账单" if official else "本地估算（只有开始记账后的几天；填网站令牌看完整账单）"
        self._spend_chart_caption = f"每天花了多少（元） · 近 30 天 · {src} · 鼠标指柱子看金额"
        self.spend_chart_title.setText(self._spend_chart_caption)

    def _spend_render_features(self, feats, total):
        if not feats:
            self.spend_features.setText(f"<span style='color:{_MUTED}'>今天还没有记账（机器人没在跑，或者还没调用过模型）。</span>")
            return
        total = sum(f["cost"] for f in feats) or total or 1e-9
        rows = []
        for f in feats:
            inp = f["hit"] + f["miss"]
            hit = f"{f['hit'] * 100 / inp:.0f}%" if inp else "-"
            share = f["cost"] * 100 / total
            bar = f"<span style='color:#6ad4ff'>{'█' * max(1, round(share / 5))}</span>" if share >= 1 else ""
            rows.append(
                f"<tr><td style='padding:3px 12px 3px 0' title='{_E(', '.join(f['tags']))}'>{_E(f['name'])}</td>"
                f"<td style='padding:3px 12px 3px 0;color:{_MUTED}' align='right'>{f['calls']} 次</td>"
                f"<td style='padding:3px 12px 3px 0;color:{_MUTED}' align='right'>输入 {_big(inp)} · 命中 {hit}</td>"
                f"<td style='padding:3px 12px 3px 0;color:{_MUTED}' align='right'>输出 {_big(f['out'])}</td>"
                f"<td style='padding:3px 8px 3px 0' align='right'><b>{_yuan(f['cost'], 3)}</b></td>"
                f"<td style='padding:3px 0;color:{_MUTED}'>{share:.0f}% {bar}</td></tr>")
        tip = ("<br><span style='color:#8193b2'>金额按 DeepSeek 价格表自己折算（工作日高峰翻倍），和官方账单会差一点。"
               "「回复」和「判断要不要接话」多半来自随机接话：想省钱可以在「省钱设置 → 随机接话会变高的时候」调低"
               "「她刚说过话、还留着」「说话的人刚和她聊着」。</span>")
        self.spend_features.setText("<table>" + "".join(rows) + "</table>" + tip)

    # ---------------- 二代仿真花费看板 ----------------
    def _spend_render_board(self, board):
        self._spend_board_data = board or {}
        if not board:
            self.spend_board.setText(f"<span style='color:{_MUTED}'>还没有数据（二代仿真跑过一轮后就有）。</span>")
            return
        b = board.get("budget") or {}
        cap = f"{_yuan(b.get('daily_cap'))}/天" if b.get("daily_cap") else "不限"
        over = {"daily": "<b style='color:#f87171'>今天已到上限：只回被叫到的，不开思考</b>",
                "group": "<b style='color:#f87171'>有群到了单群上限</b>"}.get(b.get("over_cap") or "", "")
        head = (f"今天 <b>{_yuan(b.get('today'), 3)}</b> · 最近 24 小时 {_yuan(b.get('last24h'), 3)} · 每日上限 {cap}"
                f"<br>她现在的积极性 <b>{float(b.get('willingness') or 0) * 100:.0f}%</b>"
                f"（没人叫时接话 {float(b.get('follow_p') or 0) * 100:.0f}% · 随手刷到 {float(b.get('random_p') or 0) * 100:.0f}%；"
                f"被艾特 / 叫名字 / 引用永远照回）" + (f"<br>{over}" if over else "")
                + ("" if b.get("enabled", True) else f"<br><span style='color:{_MUTED}'>花钱感知已关</span>"))
        rows = []
        for r in board.get("today") or []:
            rows.append(
                f"<tr><td style='padding:3px 12px 3px 0'>{_E(r.get('name', ''))}</td>"
                f"<td style='padding:3px 12px 3px 0;color:{_MUTED}' align='right'>{r.get('rounds', 0)} 轮 / {r.get('calls', 0)} 次</td>"
                f"<td style='padding:3px 12px 3px 0;color:{_MUTED}' align='right'>输入 {_big(r.get('hit', 0) + r.get('miss', 0))} · 命中 {r.get('hit_rate', 0) * 100:.0f}%</td>"
                f"<td style='padding:3px 12px 3px 0;color:{_MUTED}' align='right'>每轮 {_yuan(r.get('per_round'), 4)}</td>"
                f"<td style='padding:3px 0' align='right'><b>{_yuan(r.get('cost'), 3)}</b></td></tr>")
        table = ("<table>" + "".join(rows) + "</table>") if rows else f"<span style='color:{_MUTED}'>今天二代仿真还没跑过。</span>"
        hours = board.get("hourly") or []
        top = max((h.get("cost", 0) for h in hours), default=0) or 1e-9
        hot = " ".join(f"{h['hour']}点 {_yuan(h['cost'], 2)}" for h in sorted(hours, key=lambda h: -h.get("cost", 0))[:4] if h.get("cost"))
        days = {}
        for r in board.get("days") or []:
            days[r["day"]] = days.get(r["day"], 0.0) + float(r.get("cost") or 0)
        trend = " · ".join(f"{d[5:]} {_yuan(v, 2)}" for d, v in sorted(days.items())[-7:])
        self.spend_board.setText(head + "<br><br>" + table
                                 + (f"<br><span style='color:{_MUTED}'>今天最烧钱的几个小时：{hot}</span>" if hot and top else "")
                                 + (f"<br><span style='color:{_MUTED}'>最近几天（二代仿真）：{trend}</span>" if trend else ""))

    def _spend_rounds_dialog(self):
        rec = (self._spend_board_data or {}).get("recent") or []
        dlg = QDialog(self)
        dlg.setWindowTitle("二代仿真 · 每一轮花了多少（今天，最近 80 轮）")
        dlg.resize(980, 620)
        lay = QVBoxLayout(dlg)
        why = {"at": "被艾特", "reply": "被引用", "name": "叫名字", "follow": "接话", "random": "随手刷到",
               "idle": "潜水久了", "back": "刷完B站", "limited": "次数到了", "promise": "答应的事到点"}
        rows = []
        for r in rec:
            t = time.strftime("%H:%M:%S", time.localtime(float(r.get("t") or 0)))
            act = "说了话" if r.get("said") else ("潜水" if r.get("silent") else ("失败" if not r.get("ok") else "没说"))
            rows.append(
                f"<tr><td style='padding:2px 10px 2px 0'>{t}</td><td style='padding:2px 10px 2px 0'>{_E(r.get('name', ''))}</td>"
                f"<td style='padding:2px 10px 2px 0'>{why.get(r.get('why'), _E(str(r.get('why'))))}</td>"
                f"<td style='padding:2px 10px 2px 0;color:{_MUTED}'>{_E(str(r.get('eff') or ''))}</td>"
                f"<td style='padding:2px 10px 2px 0' align='right'>{r.get('calls', 0)} 次 / {r.get('steps', 0)} 步</td>"
                f"<td style='padding:2px 10px 2px 0;color:{_MUTED}' align='right'>最长 {_big(r.get('max_in', 0))} · 未命中 {_big(r.get('miss', 0))} · 输出 {_big(r.get('out', 0))}</td>"
                f"<td style='padding:2px 10px 2px 0' align='right'><b>{_yuan(r.get('cost'), 4)}</b></td>"
                f"<td style='padding:2px 10px 2px 0'>{act}</td>"
                f"<td style='padding:2px 0;color:{_MUTED}'>{_E(','.join(r.get('tools') or []))}</td></tr>")
        lbl = QLabel(("<table>" + "".join(rows) + "</table>") if rows else "今天还没有记录。")
        lbl.setTextFormat(Qt.RichText)
        lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl.setStyleSheet("background:transparent; color:#dbe4f3; font-size:12px;")
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(lbl)
        lay.addWidget(area)
        dlg.exec()

    # ---------------- 网站令牌 ----------------
    def _spend_token_dialog(self):
        from ...services import envfile
        dlg = QDialog(self)
        dlg.setWindowTitle("DeepSeek 网站令牌")
        pal = dlg.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#0f172a"))
        dlg.setPalette(pal)
        dlg.setAutoFillBackground(True)
        dlg.setMinimumWidth(560)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        tip = QLabel(
            "DeepSeek 只给网站登录后的账号看每日账单（API key 只能查余额），所以要借一下网站的登录令牌：<br><br>"
            "1. 用浏览器打开 <b>platform.deepseek.com</b> 并登录；<br>"
            "2. 按 <b>F12</b> → 切到「控制台 / Console」；<br>"
            "3. 输入 <code style='color:#ffd166'>JSON.parse(localStorage.userToken).value</code> 回车；<br>"
            "4. 把打出来的那串字（不要引号）粘到下面，保存。<br><br>"
            "<span style='color:#8193b2'>令牌只存在本机 .env（DEEPSEEK_WEB_TOKEN），只用来读用量；在网站上退出登录后会失效，到时重新复制一次。"
            "清空再保存 = 不用网站账单。</span>")
        tip.setTextFormat(Qt.RichText)
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#dbe6f5; font-size:12px;")
        tip.setTextInteractionFlags(Qt.TextSelectableByMouse)  # 可以用鼠标选中复制
        lay.addWidget(tip)
        cmd = "JSON.parse(localStorage.userToken).value"
        copy_btn = QPushButton("复制第 3 步的命令")
        copy_btn.setCursor(Qt.PointingHandCursor)

        def _copy():
            QApplication.clipboard().setText(cmd)
            copy_btn.setText("已复制 ✓")

        copy_btn.clicked.connect(_copy)
        lay.addWidget(copy_btn, 0, Qt.AlignLeft)
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.Password)
        edit.setPlaceholderText("已填过" if envfile.get("DEEPSEEK_WEB_TOKEN") else "粘贴令牌")
        edit.setStyleSheet(LINE_EDIT_QSS)
        lay.addWidget(edit)
        row = QHBoxLayout()
        row.addStretch(1)
        clear = QPushButton("清空")
        save = QPushButton("保存")
        save.setObjectName("primaryBtn")
        for b in (clear, save):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        lay.addLayout(row)

        def _save(value):
            value = value.strip().strip('"').strip("'")
            try:
                envfile.set_values({"DEEPSEEK_WEB_TOKEN": value})
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(dlg, "保存失败", str(e))
                return
            dlg.accept()
            self.add_log("info", "[花费] 网站令牌已" + ("保存" if value else "清空"))
            self.refresh_spend(force=True)

        save.clicked.connect(lambda: _save(edit.text()) if edit.text().strip() else dlg.reject())
        clear.clicked.connect(lambda: _save(""))
        show = getattr(self, "_show_dialog", None)
        if callable(show):
            show(dlg)
        else:
            dlg.exec()
        dlg.deleteLater()
