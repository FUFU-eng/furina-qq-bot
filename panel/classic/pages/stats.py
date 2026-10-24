"""群统计与成员页（数据走 v2 数据库）（PanelWindow 的 mixin，由 window.py 拆出）。"""
from ..common import *  # noqa: F401,F403
from ..widgets import *  # noqa: F401,F403


class StatsPageMixin:
    def _build_stats_page(self):
        page = QWidget()
        page.setObjectName("stackPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.addWidget(self._label("查看触发统计，点击群友可展开 QQ 号，双击可设置个人上限", "pageSubLabel", 11), 1)
        # 每日自动回复上限：原本在「设置」里，语义上属于本页，移到页眉右侧，紧凑不突兀
        from PySide6.QtWidgets import QSpinBox, QCheckBox
        from PySide6.QtCore import QTimer
        cap_lbl = QLabel("每日上限")
        cap_lbl.setStyleSheet("color:#a9b7cc; font-size:12px;")
        header.addWidget(cap_lbl)
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 999)
        _unified = self._read_daily_limit()
        self.limit_spin.setValue(_unified if _unified > 0 else 30)
        self.limit_spin.setFixedWidth(76)
        self.limit_spin.setToolTip("每个群友每天最多被机器人自动回复的次数；勾选右侧“无限次”则统一不限")
        self.limit_spin.setStyleSheet(
            "QSpinBox{background:#0d1726;color:#f4f7ff;border:1px solid rgba(255,255,255,0.14);"
            "border-radius:8px;padding:4px 8px;selection-background-color:#6ad4ff;}"
            "QSpinBox:disabled{background:#0a1220;color:#5b6678;}"
        )
        header.addWidget(self.limit_spin)
        self.unlimited_check = QCheckBox("无限次")
        self.unlimited_check.setStyleSheet("color:#a9b7cc; font-size:12px;")
        self.unlimited_check.setCursor(Qt.PointingHandCursor)
        self.unlimited_check.setChecked(_unified <= 0)
        self.unlimited_check.setToolTip("勾选后统一上限为无限次（群主与特殊群友本就不受限）")
        self.unlimited_check.toggled.connect(lambda on: self.limit_spin.setDisabled(on))
        self.limit_spin.setDisabled(_unified <= 0)
        header.addWidget(self.unlimited_check)
        save_limit = QPushButton("保存上限")
        save_limit.setObjectName("primaryBtn")
        save_limit.setCursor(Qt.PointingHandCursor)

        def _on_save_limit():
            self._save_daily_limit(None)
            self.refresh_stats()
            save_limit.setText("已保存 ✓")
            QTimer.singleShot(1500, lambda: save_limit.setText("保存上限"))

        save_limit.clicked.connect(_on_save_limit)
        header.addWidget(save_limit)
        layout.addLayout(header)

        toolbar = QFrame()
        toolbar.setObjectName("statToolbar")
        top_row = QHBoxLayout(toolbar)
        top_row.setContentsMargins(10, 8, 10, 8)
        top_row.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("搜索昵称、QQ 或群名")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.refresh_stats)
        top_row.addWidget(self.search_input, 1)
        self.filter_btn = QPushButton()
        self.filter_btn.setObjectName("filterBtn")
        self.filter_btn.setFixedSize(40, 40)
        self.filter_btn.setIcon(self._make_icon("filter", "#a9b7cc"))
        self.filter_btn.setIconSize(QSize(20, 20))
        self.filter_btn.setToolTip("筛选统计范围")
        self.filter_btn.setCursor(Qt.PointingHandCursor)
        self.filter_btn.clicked.connect(self.open_filter)
        top_row.addWidget(self.filter_btn)
        top_row.addWidget(self._tool_button("刷新数据", self.refresh_stats))
        layout.addWidget(toolbar)

        list_surface = QFrame()
        list_surface.setObjectName("contentSurface")
        list_layout = QVBoxLayout(list_surface)
        list_layout.setContentsMargins(10, 10, 10, 10)
        list_layout.setSpacing(6)
        list_layout.addWidget(self._label("群聊成员统计", "sectionLabel", 11))
        self.stat_list = QListWidget()
        self.stat_list.setObjectName("statList")
        self.stat_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.stat_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stat_list.itemClicked.connect(self._toggle_qq)
        self.stat_list.itemDoubleClicked.connect(self._edit_user_limit)
        list_layout.addWidget(self.stat_list, 1)
        layout.addWidget(list_surface, 1)
        self.refresh_stats()
        return page

    def _load_json(self, path, default):
        try:
            if str(path).startswith(V2DOC):
                data = _v2.read_doc(path)
                return data if isinstance(data, dict) else default
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else default
        except Exception:
            return default

    # ===== 数据：旧文件名映射到 v2 数据库（与机器人同一个库，机器人热读取） =====
    def _qq_path(self, name):
        return V2DOC + name

    def _stat_entry(self, group_id, user_id, nickname, used):
        if str(user_id) in self._unlimited_qq_set():
            return (group_id, user_id, nickname, "∞", "∞")
        limit, _src = self._effective_user_limit(group_id, user_id)
        if limit <= 0:
            return (group_id, user_id, nickname, "∞", "∞")
        remaining = max(0, limit - int(used or 0))
        return (group_id, user_id, nickname, str(remaining), str(limit))

    def _nickname_from_fengshen(self, feng, group_id, user_id):
        try:
            return feng.get(group_id, {}).get("stats", {}).get(user_id, {}).get("nickname", "") or ""
        except Exception:
            return ""

    def _sort_key(self, nickname):
        try:
            initials = lazy_pinyin(nickname or "")
            return (initials[0].upper() if initials else "", (nickname or "").lower())
        except Exception:
            return ((nickname or "").upper(), (nickname or "").lower())

    def _group_name(self, group_id):
        known = {}
        # 优先用NapCat API返回的群名
        napcat_groups = self._napcat_group_cache()
        gid = str(group_id)
        if gid in napcat_groups:
            return napcat_groups[gid]
        group_names = self._load_json(self._qq_path("group_members.json"), {}).get("group_names", {}) or {}
        return group_names.get(gid) or known.get(gid) or f"群 {gid}"

    def _request_napcat_groups(self, force=False):
        """后台请求 NapCat 群列表；绝不让切页或定时器在主线程等待 HTTP。"""
        now = time.time()
        if getattr(self, "_group_refresh_inflight", False):
            return
        if not force and now - getattr(self, "_napcat_groups_ts", 0.0) < 15:
            return
        # UI 创建完成前没有 queued signal，构建页面时只使用本地兜底。
        if not hasattr(self, "signals"):
            return
        self._group_refresh_inflight = True

        def work():
            result = {}
            try:
                result = dict(_v2.refresh_groups())
            except Exception:
                # 失败时也回到主线程释放 inflight，继续使用本地缓存。
                result = {}
            self.signals.groups.emit(result)

        threading.Thread(target=work, daemon=True).start()

    def _on_napcat_groups_updated(self, groups):
        self._group_refresh_inflight = False
        if isinstance(groups, dict) and groups:
            self._napcat_groups_cache = {str(k): str(v) for k, v in groups.items()}
        self._napcat_groups_ts = time.time()
        self._auto_refresh_groups()

    def _napcat_group_cache(self):
        """返回群列表缓存，并在过期时后台刷新，避免主线程卡在 HTTP 请求上。"""
        self._request_napcat_groups()
        return getattr(self, "_napcat_groups_cache", {}) or {}

    def _real_group_count(self):
        """当前已登录 QQ 真实已加入的群数量。

        优先用 NapCat 实时群列表（get_group_list，反映当前登录账号的真实群），
        NapCat 未连接时再回退到本地缓存文件。这样切换/重登账号后数字会显示
        该号实际所在群数，而不是迁移时残留的旧缓存。
        """
        groups = self._napcat_group_cache()
        if groups:
            return len(groups)
        group_data = self._load_json(self._qq_path("group_members.json"), {}).get("groups", {}) or {}
        return len(group_data)

    def _auto_refresh_groups(self):
        """检测群列表变化并自动刷新UI，同时把 group_members.json 与 NapCat 真实群列表同步。"""
        try:
            groups = self._napcat_group_cache()
            sig = ",".join(sorted(groups.keys())) if groups else ""
            if sig and sig != self._last_group_sig:
                self._last_group_sig = sig
                # 把本地缓存里已退出/迁移残留的群清理掉，保证离线兜底也准确
                gm_path = self._qq_path("group_members.json")
                gm = self._load_json(gm_path, {})
                if isinstance(gm, dict):
                    gm_groups = gm.get("groups", {})
                    gm_names = gm.get("group_names", {})
                    if isinstance(gm_groups, dict):
                        gm["groups"] = {gid: users for gid, users in gm_groups.items() if str(gid) in groups}
                    if isinstance(gm_names, dict):
                        gm["group_names"] = {gid: name for gid, name in gm_names.items() if str(gid) in groups}
                    try:
                        _atomic_write_json(gm_path, gm)
                    except Exception:
                        pass
                # 只刷新当前可见内容；非当前页不做整表重建，降低切页时的主线程压力。
                if hasattr(self, "group_card") and getattr(self, "_active_tab", 0) == 0:
                    self.group_card.set_value(str(len(groups)))
                if hasattr(self, "stat_list") and getattr(self, "_active_tab", 0) == 5:
                    self.refresh_stats()
                if hasattr(self, "perm_list") and getattr(self, "_active_tab", 0) == 2:
                    self._refresh_permission_groups()
        except Exception:
            pass

    def _member_text(self, item):
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict):
            return item.text()
        key = data.get("key", "")
        if key in self._revealed_qq:
            return f"{data['nick']}（{data['uid']}）  剩余 {data['remain']}/{data['total']}"
        return f"{data['nick']}  剩余 {data['remain']}/{data['total']}"

    def _toggle_qq(self, item):
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict):
            return
        key = data.get("key", "")
        if key in self._revealed_qq:
            self._revealed_qq.discard(key)
        else:
            self._revealed_qq.add(key)
        item.setText(self._member_text(item))

    def _available_groups(self):
        # 优先用NapCat API实时获取当前登录QQ的群列表
        napcat_groups = self._napcat_group_cache()
        if napcat_groups:
            return sorted(napcat_groups.keys(), key=lambda g: self._sort_key(napcat_groups[g]))
        members = self._load_json(self._qq_path("group_members.json"), {}).get("groups", {}) or {}
        feng = self._load_json(self._qq_path("fengshen_data.json"), {})
        gids = {str(g) for g in members.keys()}
        if not gids:
            gids = {str(g) for g in feng.keys()}
        if not gids:
            gids = set()
        return sorted(gids, key=lambda g: self._sort_key(self._group_name(g)))

    def open_filter(self):
        from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout as QHL, QLabel as QL, QPushButton as QPB, QVBoxLayout as QVL
        dialog = QDialog(self)
        _dp = dialog.palette(); _dp.setColor(QPalette.ColorRole.Window, QColor("#0a101e")); dialog.setPalette(_dp); dialog.setAutoFillBackground(True)
        dialog.setWindowTitle("筛选回复次数")
        dialog.setFixedSize(340, 260)
        dialog.setStyleSheet("background: #0a101e; color: #f4f7ff; font-family: 'Microsoft YaHei';")
        layout = QVL(dialog)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(8)

        layout.addWidget(QL("选择群聊"))
        combo_style = (
            "QComboBox { background: #241f33; color: #f4f7ff; border: 1px solid rgba(255,255,255,0.10); "
            "border-radius: 9px; padding: 7px 12px; font-size: 13px; }"
            "QComboBox:focus { border-color: rgba(74,208,232,0.6); }"
            "QComboBox QAbstractItemView { background: #241f33; color: #f4f7ff; selection-background-color: #4a68d0; "
            "border: 1px solid rgba(255,255,255,0.10); border-radius: 8px; outline: none; padding: 4px; }"
            "QComboBox::drop-down { border: none; width: 20px; }"
            "QComboBox::down-arrow { image: none; border-left: 4px solid #f4f7ff; border-top: 4px solid transparent; "
            "border-bottom: 4px solid transparent; width: 0; height: 0; }"
        )

        self.group_combo = QComboBox()
        self._polish_combo(self.group_combo)
        self.group_combo.setStyleSheet(combo_style)
        self.group_combo.addItem("全部群", "")
        for gid in self._available_groups():
            self.group_combo.addItem(f"{self._group_name(gid)} · {gid}", gid)
        if self.filter_group:
            gi = self.group_combo.findData(self.filter_group)
            if gi >= 0:
                self.group_combo.setCurrentIndex(gi)
        layout.addWidget(self.group_combo)

        layout.addWidget(QL("排序方式"))
        self.sort_combo = QComboBox()
        self._polish_combo(self.sort_combo)
        self.sort_combo.setStyleSheet(combo_style)
        self.sort_combo.addItem("按首字母 A-Z", "letter")
        self.sort_combo.addItem("剩余次数升序", "asc")
        self.sort_combo.addItem("剩余次数降序", "desc")
        si = self.sort_combo.findData(self.filter_sort)
        if si >= 0:
            self.sort_combo.setCurrentIndex(si)
        layout.addWidget(self.sort_combo)

        layout.addSpacing(10)
        btn_row = QHL()
        btn_row.addStretch(1)
        cancel = QPB("取消")
        cancel.setStyleSheet("background: #1e2540; color: #e2e8f0; border: none; border-radius: 8px; padding: 8px 14px;")
        cancel.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel)
        ok = QPB("应用")
        ok.setStyleSheet("background: #ffd166; color: #201a10; border: none; border-radius: 8px; padding: 8px 18px; font-weight: bold;")
        ok.clicked.connect(lambda: self._apply_filter(dialog))
        btn_row.addWidget(ok)
        layout.addLayout(btn_row)
        self._show_dialog(dialog)

    def _apply_filter(self, dialog):
        self.filter_group = self.group_combo.currentData() or ""
        self.filter_sort = self.sort_combo.currentData() or "letter"
        self.refresh_stats()
        dialog.accept()

    def _letter_sort_key(self, nickname):
        first = (nickname or "").strip()[:1]
        if not first:
            return (1, "~", "", "")
        if first.isascii() and first.isalpha():
            return (0, first.upper(), (nickname or "").lower(), "")
        if ord(first) > 127:
            try:
                py = lazy_pinyin(first)
                return (0, py[0][0].upper() if py and py[0] else "Z", (nickname or "").lower(), "")
            except Exception:
                return (0, "Z", (nickname or "").lower(), "")
        return (1, "~", (nickname or "").lower(), "")

    def _remaining_num(self, row):
        _, _, _, remain, total = row
        if total == "∞":
            return 999999
        try:
            return int(remain)
        except Exception:
            return 0

    def refresh_stats(self):
        self.stat_list.clear()
        d_limits = self._load_json(self._qq_path("daily_limits.json"), {})
        if d_limits.get("date") != time.strftime("%Y-%m-%d"):
            chat = {}
        else:
            chat = d_limits.get("chat", {}) or {}
        members = self._load_json(self._qq_path("group_members.json"), {}).get("groups", {}) or {}
        feng = self._load_json(self._qq_path("fengshen_data.json"), {})

        # 以 NapCat 实时群列表（或本地缓存）作为权威群号，过滤掉已退出/迁移残留的群
        real_groups = set(str(g) for g in self._available_groups())
        if real_groups:
            members = {gid: users for gid, users in members.items() if str(gid) in real_groups}

        # group_members.json 还没生成时，用封神榜昵称数据兜底，保证列表不为空
        if not members:
            for group_id, gd in feng.items():
                if not isinstance(gd, dict):
                    continue
                if real_groups and str(group_id) not in real_groups:
                    continue
                stats = gd.get("stats", {})
                user_map = {}
                for uid, us in stats.items():
                    if not isinstance(us, dict):
                        continue
                    nick = us.get("nickname", "") or uid
                    user_map[uid] = nick
                if user_map:
                    members[group_id] = user_map

        rows = []
        if members:
            for group_id, user_map in members.items():
                if not isinstance(user_map, dict):
                    continue
                for user_id, nickname in user_map.items():
                    used = chat.get(f"{group_id}:{user_id}", 0)
                    rows.append(self._stat_entry(group_id, user_id, nickname or user_id, used))
        else:
            for key, used in chat.items():
                if ":" not in key:
                    continue
                group_id, user_id = key.split(":", 1)
                if real_groups and str(group_id) not in real_groups:
                    continue
                nickname = self._nickname_from_fengshen(feng, group_id, user_id) or user_id
                rows.append(self._stat_entry(group_id, user_id, nickname, used))

        query = self.search_input.text().strip().lower() if hasattr(self, "search_input") else ""
        if query:
            filtered = []
            for row in rows:
                group_id, user_id, nickname, remain, total = row
                if (query in nickname.lower() or query in user_id
                        or query in group_id or query in self._group_name(group_id).lower()):
                    filtered.append(row)
            rows = filtered

        if not rows:
            self.stat_list.addItem("没有匹配的群友")
            return

        if self.filter_group:
            rows = [r for r in rows if r[0] == self.filter_group]

        by_group = {}
        for row in rows:
            by_group.setdefault(row[0], []).append(row)

        group_names = [(gid, self._group_name(gid)) for gid in by_group]
        group_names.sort(key=lambda x: self._sort_key(x[1]))
        for gid, gname in group_names:
            header = QListWidgetItem(f"{gname}  ·  {gid}")
            header.setFlags(Qt.ItemIsEnabled)
            header.setForeground(QColor("#ffd166"))
            header.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
            self.stat_list.addItem(header)

            members = by_group[gid]
            if self.filter_sort == "letter":
                members.sort(key=lambda r: self._letter_sort_key(r[2]))
            elif self.filter_sort == "asc":
                members.sort(key=lambda r: (self._remaining_num(r), r[2].lower()))
            else:
                members.sort(key=lambda r: (-self._remaining_num(r), r[2].lower()))
            for group_id, user_id, nickname, remain, total in members:
                item = QListWidgetItem()
                item.setData(Qt.UserRole, {
                    "key": f"{group_id}:{user_id}",
                    "gid": str(group_id),
                    "nick": nickname,
                    "uid": user_id,
                    "remain": remain,
                    "total": total,
                })
                item.setText(self._member_text(item))
                self.stat_list.addItem(item)
