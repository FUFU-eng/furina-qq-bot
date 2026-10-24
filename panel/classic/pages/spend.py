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
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)

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
            lay.setContentsMargins(14, 10, 14, 10)
            lay.setSpacing(2)
            lay.addWidget(self._label(title, "sectionLabel", 11))
            val = QLabel("--")
            val.setTextFormat(Qt.RichText)
            val.setStyleSheet("background:transparent; color:#f4f7ff; font-size:22px; font-weight:bold;")
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
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(4)
        self.spend_chart_title = self._label("每天花了多少（元）", "sectionLabel", 11)
        cl.addWidget(self.spend_chart_title)
        self.spend_chart_holder = QVBoxLayout()
        cl.addLayout(self.spend_chart_holder, 1)
        layout.addWidget(chart_box, 3)

        feat_box = QFrame()
        feat_box.setObjectName("contentSurface")
        fl = QVBoxLayout(feat_box)
        fl.setContentsMargins(14, 10, 14, 12)
        fl.setSpacing(6)
        fl.addWidget(self._label("今天钱花在哪（机器人自己记的账，按功能）", "sectionLabel", 11))
        self.spend_features = QLabel("…")
        self.spend_features.setTextFormat(Qt.RichText)
        self.spend_features.setWordWrap(True)
        self.spend_features.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.spend_features.setStyleSheet("background:transparent; color:#dbe4f3; font-size:12px;")
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setStyleSheet("QScrollArea{background:transparent;border:none;} QScrollArea > QWidget > QWidget{background:transparent;}")
        area.setWidget(self.spend_features)
        fl.addWidget(area, 1)
        layout.addWidget(feat_box, 2)

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
        ax.append([f"{d.month}/{d.day}" for d in dates])
        ax.setLabelsFont(QFont("Microsoft YaHei", 8))
        ax.setLabelsAngle(-50)  # 斜着放，30 天的日期都放得下
        if hasattr(ax, "setTruncateLabels"):
            ax.setTruncateLabels(False)  # 不然会被省略成「1…」
        ax.setGridLineVisible(False)
        chart.addAxis(ax, Qt.AlignBottom)
        series.attachAxis(ax)
        ay = QValueAxis()
        top = max(vals) if vals else 0
        ay.setRange(0, max(0.05, top * 1.15))
        ay.setLabelFormat("%.2f" if top < 10 else "%.0f")
        ay.setLabelsFont(QFont("Microsoft YaHei", 9))
        chart.addAxis(ay, Qt.AlignLeft)
        series.attachAxis(ay)
        chart.legend().hide()
        chart.setBackgroundBrush(QBrush(QColor("#0a101e")))
        chart.setMargins(QMargins(4, 4, 4, 4))
        for a in (ax, ay):
            a.setLabelsColor(QColor("#cbd5e1"))
            a.setLinePenColor(QColor("#334155"))
            a.setGridLineColor(QColor("#1e293b"))
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
