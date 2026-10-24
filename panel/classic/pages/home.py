"""主页：仪表卡、群聊/模型用量统计图、今日聊天详情（PanelWindow 的 mixin，由 window.py 拆出）。"""
from ..common import *  # noqa: F401,F403
from ..widgets import *  # noqa: F401,F403


class HomePageMixin:
    def _build_home_page(self):
        page = QWidget()
        page.setObjectName("stackPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        layout.addStretch(4)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)  # 与工具按钮行的间距一致
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        chat_total = self._today_chat_total()
        model_total = sum(self._count_model_usage().values())
        group_count = str(self._real_group_count())
        self.start_card = DashCard("", "测试", "芙芙状态", callback=self.test_connection)
        self.start_card.value_label.setStyleSheet("color: #ffd166;")
        self.start_card.setToolTip("点击测试机器人是否已连接")
        self.group_card = DashCard("", group_count, "守护群聊", callback=self.show_group_list)
        self.group_card.value_label.setStyleSheet("color: #f59e0b;")
        self.usage_card = DashCard("", str(model_total), "模型调用", callback=self.show_model_usage)
        self.chat_card = DashCard("", str(chat_total), "今日聊天", callback=self.show_today_chat)
        for card in (self.start_card, self.group_card, self.usage_card, self.chat_card):
            card.title_label.setVisible(False)
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)  # 与下方工具按钮行左右对齐
        grid.addWidget(self.start_card, 0, 0)
        grid.addWidget(self.group_card, 0, 1)
        grid.addWidget(self.usage_card, 1, 0)
        grid.addWidget(self.chat_card, 1, 1)
        layout.addLayout(grid)
        layout.addStretch(1)
        return page

    def refresh_home_cards(self):
        """切换 QQ 后刷新主页数据卡片，使其反映当前选中 QQ 的数据。"""
        try:
            if not hasattr(self, "group_card"):
                return
            self.group_card.value_label.setText(str(self._real_group_count()))
            model_total = sum(self._count_model_usage().values())
            self.usage_card.value_label.setText(str(model_total))
            self.chat_card.value_label.setText(str(self._today_chat_total()))
        except Exception:
            pass

    def _refresh_home_cards(self):
        """首页数据卡片：读文件 / 数据库放到后台线程，主线程只负责改文字，不再每 5 秒卡一下。"""
        if not hasattr(self, "group_card") or getattr(self, "_active_tab", 0) != 0 or not self.isVisible():
            return
        if getattr(self, "_home_cards_inflight", False):
            return
        self._home_cards_inflight = True

        def work():
            try:
                data = (self._real_group_count(), sum(self._count_model_usage().values()), self._today_chat_total())
                self.signals.home_cards.emit(data)
            except Exception:
                pass
            finally:
                self._home_cards_inflight = False
        threading.Thread(target=work, daemon=True).start()

    def _on_home_cards(self, data):
        try:
            groups, model_total, chats = data
            for card, value in ((self.group_card, groups), (self.usage_card, model_total), (self.chat_card, chats)):
                text = str(value)
                if card.value_label.text() != text:
                    card.value_label.setText(text)
        except Exception:
            pass

    def _today_chat_total(self) -> int:
        """今日互动次数。daily_limits 只在机器人运行时才跨天重置，面板是直接读文件，
        必须自己核对 date：文件还停留在昨天 / 机器人今天没跑时，今日次数应为 0。"""
        try:
            data = self._load_json(self._qq_path("daily_limits.json"), {})
            if data.get("date") != time.strftime("%Y-%m-%d"):
                return 0
            stats = data.get("stats", {}) or {}
            if stats:
                try:
                    live = self._napcat_group_cache() or {}
                except Exception:
                    live = {}
                if live:
                    stats = {str(g): kinds for g, kinds in stats.items() if str(g) in live}
                return sum(int(v) for g in stats.values() for v in (g or {}).values())
            return sum(int(v or 0) for v in (data.get("chat", {}) or {}).values())
        except Exception:
            return 0

    def show_group_list(self):
        from PySide6.QtWidgets import QDialog, QPushButton as QPB, QVBoxLayout as QVL
        dialog = QDialog(self)
        _dp = dialog.palette(); _dp.setColor(QPalette.ColorRole.Window, QColor("#0a101e")); dialog.setPalette(_dp); dialog.setAutoFillBackground(True)
        dialog.setWindowTitle("守护群聊")
        dialog.setFixedSize(380, 280)
        dialog.setStyleSheet("background: #0a101e; color: #f4f7ff; font-family: 'Microsoft YaHei';")
        layout = QVL(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)

        for gid in self._available_groups():
            name = self._group_name(gid)
            row = QLabel(f"{name}  ·  {gid}")
            row.setStyleSheet("background: #0d1526; padding: 12px 14px; border-radius: 10px; color: #e2e8f0; font-size: 13px;")
            layout.addWidget(row)

        close_btn = QPB("关闭")
        close_btn.setStyleSheet("background: #1e2540; color: #e2e8f0; border: none; border-radius: 8px; padding: 8px 14px;")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn, 0, Qt.AlignRight)
        self._show_dialog(dialog)

    def _count_model_usage(self):
        """今日各模型调用次数（机器人按 model:<名称> 记入 usage 表）。"""
        try:
            return _v2.model_usage_today()
        except Exception:
            return {}

    def show_model_usage(self):
        from PySide6.QtWidgets import QDialog, QPushButton as QPB, QVBoxLayout as QVL
        counts = self._count_model_usage()
        total = sum(counts.values())
        dialog = QDialog(self)
        _dp = dialog.palette(); _dp.setColor(QPalette.ColorRole.Window, QColor("#0a101e")); dialog.setPalette(_dp); dialog.setAutoFillBackground(True)
        dialog.setWindowTitle("今日模型用量")
        dialog.setFixedSize(380, 320)
        dialog.setStyleSheet("background: #0a101e; color: #f4f7ff; font-family: 'Microsoft YaHei';")
        layout = QVL(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)

        header = QLabel(f"今日 AI 调用：{total} 次")
        header.setStyleSheet("font-size: 15px; font-weight: bold; color: #ffd166;")
        layout.addWidget(header)

        if not counts:
            empty = QLabel("今天还没有模型调用记录")
            empty.setStyleSheet("color: #8a94a6;")
            layout.addWidget(empty)
        for model, count in sorted(counts.items(), key=lambda x: -x[1]):
            row = QLabel(f"{model}  ·  {count} 次")
            row.setStyleSheet("background: #0d1526; padding: 10px 14px; border-radius: 10px; color: #e2e8f0; font-size: 13px;")
            layout.addWidget(row)

        close_btn = QPB("关闭")
        close_btn.setStyleSheet("background: #1e2540; color: #e2e8f0; border: none; border-radius: 8px; padding: 8px 14px;")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn, 0, Qt.AlignRight)
        self._show_dialog(dialog)

    def _todo_click(self):
        self.add_log("info", "[待开发] 这个模块还没做好，之后可以放模型用量统计、表情包管理或指令大全")

    def _parse_all_daily_stats(self):
        """解析 daily_limits.json 中的所有日期统计，返回 (all_days_map, live_groups)。
        all_days_map 格式为 { 'YYYY-MM-DD': { 'group_id': { 'kind': count } } }。
        无论当前活跃数据还是 history 历史记录，都统一归档到其真实所属日期中。"""
        data = self._load_json(self._qq_path("daily_limits.json"), {})
        all_days = {}

        # 1. 解析 history 列表
        for entry in data.get("history", []) or []:
            if not isinstance(entry, dict):
                continue
            d = entry.get("date")
            st = entry.get("stats")
            if d and isinstance(st, dict):
                day_bucket = all_days.setdefault(str(d), {})
                for gid, kinds in st.items():
                    if isinstance(kinds, dict):
                        g_bucket = day_bucket.setdefault(str(gid), {})
                        for k, v in kinds.items():
                            g_bucket[str(k)] = g_bucket.get(str(k), 0) + int(v or 0)

        # 2. 解析当前活跃天的 stats（归入其记录的实际 date 中）
        curr_date = data.get("date")
        curr_stats = data.get("stats")
        if curr_date and isinstance(curr_stats, dict):
            day_bucket = all_days.setdefault(str(curr_date), {})
            for gid, kinds in curr_stats.items():
                if isinstance(kinds, dict):
                    g_bucket = day_bucket.setdefault(str(gid), {})
                    for k, v in kinds.items():
                        g_bucket[str(k)] = g_bucket.get(str(k), 0) + int(v or 0)

        # 3. 获取 NapCat 实时在的群（用于过滤已退出的群）
        try:
            live = self._napcat_group_cache() or {}
        except Exception:
            live = {}

        return all_days, live

    def _load_stats_range(self, days):
        all_days, live = self._parse_all_daily_stats()
        today = datetime.date.today()
        start_date = today - datetime.timedelta(days=max(1, days) - 1)
        start_str = start_date.isoformat()
        today_str = today.isoformat()

        agg = {}
        for d_str, day_stats in all_days.items():
            if start_str <= d_str <= today_str:
                for gid, kinds in day_stats.items():
                    if live and str(gid) not in live:
                        continue
                    b = agg.setdefault(str(gid), {})
                    for kind, count in kinds.items():
                        b[str(kind)] = b.get(str(kind), 0) + int(count or 0)

        total = sum(v for g in agg.values() for v in g.values())
        return agg, total

    def _daily_totals(self, days):
        """返回最近 days 天（含今天）的 (日期标签列表, 每日总触发数列表)。"""
        all_days, live = self._parse_all_daily_stats()
        today = datetime.date.today()
        dates, totals = [], []

        for i in range(max(1, days) - 1, -1, -1):
            d = today - datetime.timedelta(days=i)
            ds = d.isoformat()
            day_stats = all_days.get(ds, {})
            cnt = 0
            for gid, kinds in day_stats.items():
                if live and str(gid) not in live:
                    continue
                cnt += sum(int(v or 0) for v in kinds.values())
            dates.append(ds[5:])  # MM-DD
            totals.append(cnt)

        return dates, totals

    def _kind_totals(self, days):
        agg, _ = self._load_stats_range(days)
        kinds = ["chat", "auto", "search", "upscale", "cmd_image", "poke"]
        labels = ["艾特回复", "自动接话", "搜图", "强化", "指令发图", "戳一戳"]
        totals = [sum(int(agg.get(gid, {}).get(k, 0)) for gid in agg) for k in kinds]
        return labels, totals

    def _make_bar_chart(self, categories, values, color):
        from PySide6.QtCharts import QBarSeries, QBarSet, QBarCategoryAxis, QChart, QChartView, QValueAxis
        from PySide6.QtGui import QColor, QBrush, QFont, QPainter
        series = QBarSeries()
        bset = QBarSet("")
        for v in values:
            bset.append(int(v))
        series.append(bset)
        chart = QChart()
        chart.addSeries(series)
        chart.setAnimationOptions(QChart.SeriesAnimations)
        axis_x = QBarCategoryAxis()
        axis_x.append(categories)
        axis_x.setLabelsFont(QFont("Microsoft YaHei", 11))
        axis_x.setLabelsAngle(-45)
        axis_x.setGridLineVisible(False)
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)
        axis_y = QValueAxis()
        axis_y.setLabelFormat("%d")
        axis_y.setLabelsFont(QFont("Microsoft YaHei", 11))
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)
        chart.legend().hide()
        chart.setBackgroundBrush(QBrush(QColor("#0a101e")))
        chart.setTitleBrush(QColor("#cbd5e1"))
        axis_x.setLabelsColor(QColor("#e2e8f0"))
        axis_y.setLabelsColor(QColor("#e2e8f0"))
        axis_x.setLinePenColor(QColor("#334155"))
        axis_y.setLinePenColor(QColor("#334155"))
        axis_x.setGridLineColor(QColor("#1e293b"))
        axis_y.setGridLineColor(QColor("#1e293b"))
        bset.setColor(QColor(color))
        view = QChartView(chart)
        view.setRenderHint(QPainter.Antialiasing)
        view.setMinimumHeight(240)
        return view

    def show_today_chat(self):
        from PySide6.QtWidgets import (
            QDialog, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
            QScrollArea, QWidget, QFrame, QPushButton,
        )
        dialog = QDialog(self)
        _dp = dialog.palette(); _dp.setColor(QPalette.ColorRole.Window, QColor("#0f172a"))
        dialog.setPalette(_dp); dialog.setAutoFillBackground(True)
        dialog.setWindowTitle("功能使用统计")
        dialog.setMinimumSize(680, 760)
        dialog.resize(800, 920)
        dialog.setStyleSheet(
            "QDialog{background:#0f172a;}"
            "QLabel{background:transparent;color:#f4f7ff;font-family:'Microsoft YaHei';}"
        )
        root = QVBoxLayout(dialog)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(16)

        # —— 顶部：标题 + 时间分段 ——
        top = QWidget(); tl = QHBoxLayout(top); tl.setContentsMargins(0, 0, 0, 0)
        title = QLabel("功能使用统计")
        title.setStyleSheet("font-size:20px;font-weight:bold;color:#ffd166;")
        tl.addWidget(title); tl.addStretch(1)
        seg = QFrame()
        seg.setStyleSheet("QFrame{background:rgba(255,255,255,0.06);border-radius:12px;}")
        sl = QHBoxLayout(seg); sl.setContentsMargins(4, 4, 4, 4); sl.setSpacing(2)
        tl.addWidget(seg)
        root.addWidget(top)

        # —— 总触发大卡 ——
        hero = QFrame()
        hero.setStyleSheet(
            "QFrame#hero{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 rgba(255,209,102,0.18),stop:1 rgba(106,212,255,0.10));"
            "border:1px solid rgba(255,209,102,0.35);border-radius:18px;}"
        )
        hero.setObjectName("hero")
        hl = QHBoxLayout(hero); hl.setContentsMargins(22, 18, 22, 18)
        cap = QLabel("总触发"); cap.setStyleSheet("color:#c6d2e6;font-size:14px;")
        total_lbl = QLabel("0")
        total_lbl.setStyleSheet("color:#ffd166;font-size:36px;font-weight:bold;")
        unit = QLabel("次"); unit.setStyleSheet("color:#c6d2e6;font-size:14px;")
        range_lbl = QLabel("")
        range_lbl.setStyleSheet("color:#91a2bc;font-size:13px;")
        hl.addWidget(cap); hl.addSpacing(10); hl.addWidget(total_lbl)
        hl.addSpacing(4); hl.addWidget(unit); hl.addStretch(1)
        hl.addWidget(range_lbl, 0, Qt.AlignRight | Qt.AlignVCenter)
        root.addWidget(hero)

        # —— 滚动内容 ——
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        holder = QWidget(); holder.setStyleSheet("background:transparent;")
        content = QVBoxLayout(holder)
        content.setContentsMargins(2, 2, 2, 2); content.setSpacing(14)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        # —— 关闭按钮 ——
        close_btn = QPushButton("关闭"); close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFixedHeight(40); close_btn.setMinimumWidth(110)
        close_btn.setStyleSheet(
            "QPushButton{background:#ffd166;color:#1a1408;border:none;border-radius:12px;"
            "font-size:14px;font-weight:bold;padding:0 22px;}"
            "QPushButton:hover{background:#ffdc84;}"
        )
        close_btn.clicked.connect(dialog.accept)
        crow = QWidget(); cl = QHBoxLayout(crow); cl.setContentsMargins(0, 0, 0, 0)
        cl.addStretch(1); cl.addWidget(close_btn)
        root.addWidget(crow)

        KINDS = [
            ("chat", "艾特回复", "#ffd166"), ("auto", "自动接话", "#6ad4ff"),
            ("search", "搜图", "#b48cf2"), ("upscale", "强化", "#3ecf8e"),
            ("cmd_image", "指令发图", "#f59e0b"), ("poke", "戳一戳", "#fb7eb8"),
        ]

        def section(name):
            c = QFrame(); c.setObjectName("sc")
            c.setStyleSheet(
                "QFrame#sc{background:rgba(255,255,255,0.04);"
                "border:1px solid rgba(255,255,255,0.08);border-radius:16px;}"
            )
            v = QVBoxLayout(c); v.setContentsMargins(18, 15, 18, 15); v.setSpacing(8)
            t = QLabel(name)
            t.setStyleSheet("color:#ffd166;font-size:14px;font-weight:bold;")
            v.addWidget(t)
            return c, v

        def bar(name, value, maxv, color, name_w=78, sub_txt="", delay=0):
            row = QWidget(); h = QHBoxLayout(row)
            h.setContentsMargins(0, 1, 0, 1); h.setSpacing(10)
            nm = QLabel(name); nm.setFixedWidth(name_w)
            nm.setStyleSheet("color:#c6d2e6;font-size:13px;")
            ratio = (value / maxv) if maxv > 0 else 0.0
            bar_widget = AnimatedBar(color, ratio)
            txt = f"{value} 次" if not sub_txt else f"{value} 次  ({sub_txt})"
            num = QLabel(txt); num.setMinimumWidth(95)
            num.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            num.setStyleSheet("color:#f4f7ff;font-size:13px;font-weight:bold;")
            h.addWidget(nm); h.addWidget(bar_widget, 1); h.addWidget(num)
            bar_widget.start_animation(duration=420, delay=delay)
            return row

        seg_btns = []
        def make_seg(txt, days):
            b = QPushButton(txt); b.setCheckable(True); b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{border:none;background:transparent;color:#aebbd2;"
                "padding:7px 16px;border-radius:9px;font-size:13px;}"
                "QPushButton:checked{background:#ffd166;color:#1a1408;font-weight:bold;}"
            )
            b.clicked.connect(lambda: render(days))
            sl.addWidget(b); seg_btns.append((b, days))
        make_seg("今天", 1); make_seg("近一周", 7); make_seg("近一个月", 30)

        def clear():
            while content.count():
                it = content.takeAt(0); w = it.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()

        def render(days):
            for b, d in seg_btns:
                b.setChecked(d == days)
            clear()
            stats, total = self._load_stats_range(days)
            # 总触发大数字滚动动画
            if total > 0:
                tot_anim = QVariantAnimation(dialog)
                tot_anim.setStartValue(0)
                tot_anim.setEndValue(total)
                tot_anim.setDuration(400)
                tot_anim.setEasingCurve(QEasingCurve.OutCubic)
                tot_anim.valueChanged.connect(lambda v: total_lbl.setText(str(int(v))))
                tot_anim.start()
                total_lbl._anim = tot_anim
            else:
                total_lbl.setText("0")
            range_lbl.setText({1: "今天", 7: "近 7 天", 30: "近 30 天"}.get(days, ""))

            # 每日趋势：
            # 若选择「今天」，展示近 7 天趋势对比并高亮「今日」；
            # 若选择「近一周」或「近一个月」，展示对应周期的每日走势。
            trend_days = 7 if days == 1 else days
            dates, tots = self._daily_totals(trend_days)
            if dates:
                # 若是近一个月且前面有连续全 0 的无记录天数（机器人尚未部署前），修剪掉开头的连续 0（至少保留 7 天）
                if trend_days == 30:
                    first_nz = 0
                    while first_nz < len(tots) - 7 and tots[first_nz] == 0:
                        first_nz += 1
                    dates = dates[first_nz:]
                    tots = tots[first_nz:]

                sec_title = "近期趋势（近 7 天对比 · 今日高亮）" if days == 1 else f"每日趋势（{len(dates)} 天走势）"
                c, v = section(sec_title)
                mx = max(tots) if tots else 0
                today_mmdd = datetime.date.today().strftime("%m-%d")
                for i, (dt, val) in enumerate(zip(dates, tots)):
                    is_today = (dt == today_mmdd)
                    is_peak = (mx > 0 and val == mx)
                    if is_today:
                        color = "#ffd166"
                        label_name = f"{dt} (今日)" if days == 1 else dt
                    elif is_peak:
                        color = "#ffd166"
                        label_name = dt
                    else:
                        color = "#6ad4ff"
                        label_name = dt
                    sub = "峰值" if (is_peak and mx > 0 and len(dates) > 1) else ""
                    delay = min(220, i * 20)
                    v.addWidget(bar(label_name, val, mx, color, name_w=92 if days == 1 else 60, sub_txt=sub, delay=delay))
                content.addWidget(c)

            # 功能分布（按次数降序）
            pairs = []
            for k, name, col in KINDS:
                n = sum(int((g or {}).get(k, 0)) for g in stats.values())
                pairs.append((name, n, col))
            pairs.sort(key=lambda x: -x[1])
            c, v = section("功能分布")
            if total == 0:
                e = QLabel("所选时间段内暂无功能触发记录")
                e.setStyleSheet("color:#8a94a6;font-size:13px;padding:4px 0;")
                v.addWidget(e)
            else:
                kmx = max(p[1] for p in pairs)
                for i, (name, n, col) in enumerate(pairs):
                    pct = f"{(n / total * 100):.1f}%" if total > 0 else "0%"
                    delay = min(180, i * 30)
                    v.addWidget(bar(name, n, kmx, col, name_w=78, sub_txt=pct, delay=delay))
            content.addWidget(c)

            # 各群明细（每群一张卡片，按触发总数降序，右上角展示群总计与占比）
            c, v = section("各群明细")
            if not stats or total == 0:
                e = QLabel("所选时间段内各群暂无触发记录")
                e.setStyleSheet("color:#8a94a6;font-size:13px;padding:4px 0;")
                v.addWidget(e)
            else:
                group_totals = {}
                for gid, bucket in stats.items():
                    group_totals[gid] = sum(int(bucket.get(k, 0)) for k, _, _ in KINDS)

                active_groups = [gid for gid, g_tot in group_totals.items() if g_tot > 0]
                active_groups.sort(key=lambda gid: (-group_totals[gid], gid))

                if not active_groups:
                    e = QLabel("所选时间段内各群暂无触发记录")
                    e.setStyleSheet("color:#8a94a6;font-size:13px;padding:4px 0;")
                    v.addWidget(e)
                else:
                    for gid in active_groups:
                        gname = self._group_name(gid)
                        gtot = group_totals[gid]
                        gpct = f"{(gtot / total * 100):.1f}%" if total > 0 else "0%"
                        bucket = stats[gid] or {}
                        items = [(name, int(bucket.get(k, 0)), col)
                                 for k, name, col in KINDS if int(bucket.get(k, 0)) > 0]
                        items.sort(key=lambda x: -x[1])

                        gc = QFrame(); gc.setObjectName("gc")
                        gc.setStyleSheet(
                            "QFrame#gc{background:rgba(255,255,255,0.03);"
                            "border:1px solid rgba(255,255,255,0.07);border-radius:12px;}"
                        )
                        gv = QVBoxLayout(gc)
                        gv.setContentsMargins(14, 12, 14, 12); gv.setSpacing(10)

                        hdr = QWidget(); hl_g = QHBoxLayout(hdr)
                        hl_g.setContentsMargins(0, 0, 0, 0)
                        gt = QLabel(f"{gname}  ·  {gid}")
                        gt.setStyleSheet("color:#f4f7ff;font-size:13px;font-weight:bold;")
                        hl_g.addWidget(gt); hl_g.addStretch(1)
                        badge = QLabel(f"共 {gtot} 次 · 占比 {gpct}")
                        badge.setStyleSheet(
                            "color:#ffd166;font-size:12px;font-weight:bold;"
                            "background:rgba(255,209,102,0.12);padding:2px 8px;border-radius:6px;"
                        )
                        hl_g.addWidget(badge)
                        gv.addWidget(hdr)

                        grid = QGridLayout()
                        grid.setHorizontalSpacing(8); grid.setVerticalSpacing(8)
                        grid.setContentsMargins(0, 0, 0, 0)
                        for i, (name, n, col) in enumerate(items):
                            chip = QLabel(f"{name}  ×{n}")
                            chip.setAlignment(Qt.AlignCenter)
                            chip.setStyleSheet(
                                f"background:rgba(255,255,255,0.05);color:{col};"
                                f"border:1px solid {col}44;border-radius:10px;"
                                "padding:5px 12px;font-size:12px;font-weight:bold;"
                            )
                            grid.addWidget(chip, i // 3, i % 3)
                        gv.addLayout(grid)
                        v.addWidget(gc)
            content.addWidget(c)
            content.addStretch(1)

        render(1)
        self._show_dialog(dialog)
