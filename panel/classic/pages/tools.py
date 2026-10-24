"""其他功能：每日次数、.env 读写、共享设定、设置中心入口（PanelWindow 的 mixin，由 window.py 拆出）。"""
from ..common import *  # noqa: F401,F403
from ..widgets import *  # noqa: F401,F403


class ToolsMixin:
    # ===== 其他功能 =====

    def open_webui(self):
        """QQ 在线时直接打开 WebUI；未登录 / 掉线 / 状态不明时先打开登录助手，
        说明该用哪个号扫码，避免用自己的 QQ 去扫、再被 WebUI 提示「已登录,无法重复登录」。"""
        if self._health.state == "online":
            self._open_webui_page()
        else:
            self.open_login_helper()

    def _open_webui_page(self):
        webbrowser.open(_v2.webui_url())
        self.add_log("info", "[系统] 正在打开 NapCat WebUI...")

    def open_login_helper(self, *_):
        from ..login import open_login_helper
        open_login_helper(self, NAPCAT_QRCODE_PATH, _v2.webui_port())

    def open_folder(self):
        subprocess.Popen(f'explorer "{BOT_DIR}"', creationflags=_NO_WINDOW)

    def _read_daily_limit(self):
        try:
            with open(os.path.join(BOT_DIR, ".env"), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("AI_CHAT_DAILY_CHAT_LIMIT="):
                        return int(line.split("=", 1)[1].strip() or 30)
        except Exception:
            pass
        return 30

    def _env_snapshot(self):
        """按 mtime 缓存 .env 全量键值，列表逐行计算有效上限时不反复读盘。"""
        try:
            path = os.path.join(BOT_DIR, ".env")
            mtime = os.path.getmtime(path)
        except Exception:
            return {}
        cache = getattr(self, "_env_snapshot_cache", None)
        if cache and cache[0] == mtime:
            return cache[1]
        values = {}
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        values[k.strip()] = v.strip()
        except Exception:
            pass
        self._env_snapshot_cache = (mtime, values)
        return values

    def _unlimited_qq_set(self):
        values = self._env_snapshot()
        owner = (values.get("AI_CHAT_GROUP_OWNER_QQ") or "").strip()
        special = (values.get("AI_CHAT_SPECIAL_QQ") or "").strip()
        return {q for q in (owner, special) if q}

    @staticmethod
    def _parse_limit_overrides(raw):
        """与机器人 daily_limits.parse_limit_overrides 同格式：uid:limit 或 gid:uid:limit。"""
        out = {}
        for pair in (raw or "").replace(" ", "").split(","):
            if not pair:
                continue
            parts = pair.split(":")
            try:
                if len(parts) == 2 and parts[0]:
                    out[str(parts[0])] = int(parts[1])
                elif len(parts) == 3 and parts[0] and parts[1]:
                    out[f"{parts[0]}:{parts[1]}"] = int(parts[2])
            except (ValueError, TypeError):
                continue
        return out

    def _limit_overrides(self):
        return self._parse_limit_overrides(self._env_snapshot().get("AI_CHAT_DAILY_LIMIT_OVERRIDES", ""))

    def _effective_user_limit(self, group_id, user_id):
        """返回 (生效上限, 来源说明），与机器人侧优先级一致：本群专属 > 所有群 > 统一。0=无限次。"""
        gid, uid = str(group_id), str(user_id)
        ov = self._limit_overrides()
        if f"{gid}:{uid}" in ov:
            return ov[f"{gid}:{uid}"], "本群专属"
        if uid in ov:
            return ov[uid], "所有群"
        try:
            unified = int(self._env_snapshot().get("AI_CHAT_DAILY_CHAT_LIMIT", 30) or 30)
        except (TypeError, ValueError):
            unified = 30
        return unified, "统一"

    def _set_env_value(self, key, value):
        """写入 BOT_DIR/.env 的一行（存在则替换，不存在则追加）。"""
        env_path = os.path.join(BOT_DIR, ".env")
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        found = False
        for i, line in enumerate(lines):
            if line.strip().startswith(key + "="):
                lines[i] = f"{key}={value}\n"
                found = True
                break
        if not found:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] = lines[-1] + "\n"
            lines.append(f"{key}={value}\n")
        _atomic_write_text(env_path, "".join(lines), newline="")
        self._env_snapshot_cache = None

    def _save_limit_overrides(self, mapping):
        raw = ",".join(f"{k}:{v}" for k, v in sorted(mapping.items()))
        self._set_env_value("AI_CHAT_DAILY_LIMIT_OVERRIDES", raw)

    def _save_daily_limit(self, dialog=None):
        if getattr(self, "unlimited_check", None) is not None and self.unlimited_check.isChecked():
            value = 0
        else:
            value = self.limit_spin.value()
        try:
            self._set_env_value("AI_CHAT_DAILY_CHAT_LIMIT", str(value))
            self.add_log("success", f"[设置] 每日自动回复上限已改为 {'无限次' if value <= 0 else f'{value} 次'}（机器人实时生效，无需重启）")
            if dialog is not None:
                dialog.accept()
        except Exception as e:
            self.add_log("error", f"[错误] 保存上限失败: {e}")

    def _favor_line(self, uid):
        """返回“等级（分数 · 认识N天）”，读不到返回空串。"""
        return _v2.favor_line(uid)

    def _edit_user_limit(self, item):
        from PySide6.QtWidgets import (QButtonGroup, QDialog, QHBoxLayout as QHL, QLabel as QL,
                                       QPushButton as QPB, QRadioButton, QSpinBox as QSB, QVBoxLayout as QVL)
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict):
            return
        gid = str(data.get("gid", "") or "")
        uid = str(data.get("uid", "") or "")
        if not gid or not uid:
            try:
                gid, uid = str(data.get("key", "")).split(":", 1)
            except ValueError:
                return
        nick = data.get("nick", "") or uid
        if uid in self._unlimited_qq_set():
            dialog = QDialog(self)
            _dp = dialog.palette(); _dp.setColor(QPalette.ColorRole.Window, QColor("#0a101e")); dialog.setPalette(_dp); dialog.setAutoFillBackground(True)
            dialog.setWindowTitle("个人上限")
            dialog.setFixedSize(360, 225)
            dialog.setStyleSheet("background: #0a101e; color: #f4f7ff; font-family: 'Microsoft YaHei';")
            layout = QVL(dialog)
            layout.setContentsMargins(22, 22, 22, 22)
            info = QL(f"{nick}（{uid}）\n是固定无限用户（群主/特殊群友），不消耗每日次数，无需设置。")
            info.setWordWrap(True)
            layout.addWidget(info)
            _favor_txt = self._favor_line(uid)
            if _favor_txt:
                _favor_lbl = QL(f"芙芙对TA的好感：{_favor_txt}")
                _favor_lbl.setStyleSheet("color:#9fb0cc; font-size:12px;")
                _favor_lbl.setWordWrap(True)
                layout.addWidget(_favor_lbl)
            close_btn = QPB("关闭")
            close_btn.setStyleSheet("background: #1e2540; color: #e2e8f0; border: none; border-radius: 8px; padding: 8px 14px;")
            close_btn.clicked.connect(dialog.accept)
            layout.addWidget(close_btn, 0, Qt.AlignRight)
            self._show_dialog(dialog)
            return
        limit, src = self._effective_user_limit(gid, uid)
        dialog = QDialog(self)
        _dp = dialog.palette(); _dp.setColor(QPalette.ColorRole.Window, QColor("#0a101e")); dialog.setPalette(_dp); dialog.setAutoFillBackground(True)
        dialog.setWindowTitle(f"个人上限 · {nick}")
        dialog.setFixedSize(380, 372)
        dialog.setStyleSheet("background: #0a101e; color: #f4f7ff; font-family: 'Microsoft YaHei';")
        layout = QVL(dialog)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(8)
        current = "无限次" if limit <= 0 else f"{limit} 次/天"
        layout.addWidget(QL(f"{self._group_name(gid)}（{gid}）\n{nick}（{uid}）\n当前生效：{current}（{src}）"))
        _favor_txt = self._favor_line(uid)
        if _favor_txt:
            _favor_lbl = QL(f"芙芙对TA的好感：{_favor_txt}")
            _favor_lbl.setStyleSheet("color:#9fb0cc; font-size:12px;")
            _favor_lbl.setWordWrap(True)
            layout.addWidget(_favor_lbl)
        radio_follow = QRadioButton("跟随统一上限（清除该用户所有专属设置）")
        radio_custom = QRadioButton("自定义每天")
        radio_unlimited = QRadioButton("无限次")
        mode_group = QButtonGroup(dialog)
        for rb in (radio_follow, radio_custom, radio_unlimited):
            rb.setStyleSheet("color:#e2e8f0; font-size:13px;")
            mode_group.addButton(rb)
        custom_row = QHL()
        custom_row.addWidget(radio_custom)
        custom_spin = QSB()
        custom_spin.setRange(1, 999)
        custom_spin.setValue(limit if limit > 0 else 30)
        custom_spin.setFixedWidth(80)
        custom_spin.setStyleSheet("QSpinBox{background:#0d1726;color:#f4f7ff;border:1px solid rgba(255,255,255,0.14);border-radius:8px;padding:4px 8px;}"
                                  "QSpinBox:disabled{background:#0a1220;color:#5b6678;}")
        custom_row.addWidget(custom_spin)
        custom_row.addWidget(QL("次"))
        custom_row.addStretch(1)
        scope_group = QButtonGroup(dialog)
        scope_row = QHL()
        scope_row.addWidget(QL("生效范围："))
        scope_group_only = QRadioButton("仅本群")
        scope_all = QRadioButton("所有群")
        for rb in (scope_group_only, scope_all):
            rb.setStyleSheet("color:#e2e8f0; font-size:13px;")
            scope_group.addButton(rb)
            scope_row.addWidget(rb)
        scope_row.addStretch(1)
        scope_group_only.setChecked(True)
        ov = self._limit_overrides()
        if f"{gid}:{uid}" in ov:
            scope_group_only.setChecked(True)
            if ov[f"{gid}:{uid}"] <= 0:
                radio_unlimited.setChecked(True)
            else:
                radio_custom.setChecked(True)
                custom_spin.setValue(ov[f"{gid}:{uid}"])
        elif uid in ov:
            scope_all.setChecked(True)
            if ov[uid] <= 0:
                radio_unlimited.setChecked(True)
            else:
                radio_custom.setChecked(True)
                custom_spin.setValue(ov[uid])
        else:
            radio_follow.setChecked(True)
        custom_spin.setEnabled(radio_custom.isChecked())
        radio_custom.toggled.connect(custom_spin.setEnabled)

        def _refresh_scope_enabled():
            on = not radio_follow.isChecked()
            scope_group_only.setEnabled(on)
            scope_all.setEnabled(on)

        for rb in (radio_follow, radio_custom, radio_unlimited):
            rb.toggled.connect(lambda _on: _refresh_scope_enabled())
        _refresh_scope_enabled()
        layout.addWidget(radio_follow)
        layout.addLayout(custom_row)
        layout.addWidget(radio_unlimited)
        layout.addLayout(scope_row)
        btn_row = QHL()
        btn_row.addStretch(1)
        save_btn = QPB("保存")
        save_btn.setObjectName("primaryBtn")
        save_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn = QPB("取消")
        cancel_btn.setStyleSheet("background: #1e2540; color: #e2e8f0; border: none; border-radius: 8px; padding: 8px 14px;")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        def _on_save_personal():
            mapping = self._limit_overrides()
            mapping.pop(f"{gid}:{uid}", None)
            mapping.pop(uid, None)
            if radio_custom.isChecked():
                key = f"{gid}:{uid}" if scope_group_only.isChecked() else uid
                mapping[key] = custom_spin.value()
                desc = f"{custom_spin.value()} 次/天（{'仅本群' if scope_group_only.isChecked() else '所有群'}）"
            elif radio_unlimited.isChecked():
                key = f"{gid}:{uid}" if scope_group_only.isChecked() else uid
                mapping[key] = 0
                desc = f"无限次（{'仅本群' if scope_group_only.isChecked() else '所有群'}）"
            else:
                desc = "跟随统一上限"
            try:
                self._save_limit_overrides(mapping)
            except Exception as e:
                self.add_log("error", f"[错误] 保存个人上限失败: {e}")
                return
            self.add_log("success", f"[设置] {nick}（{uid}）个人上限已改为{desc}（机器人实时生效，无需重启）")
            dialog.accept()
            self.refresh_stats()

        save_btn.clicked.connect(_on_save_personal)
        cancel_btn.clicked.connect(dialog.reject)
        self._show_dialog(dialog)

    def _read_env_value(self, key, default=""):
        try:
            with open(os.path.join(BOT_DIR, ".env"), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(key + "="):
                        return line.split("=", 1)[1].strip() or default
        except Exception:
            pass
        return default

    def _qq_profiles_path(self):
        return V2DOC + "bot_qq_profiles.json"

    def _load_qq_profiles(self):
        data = _v2.read_doc(self._qq_profiles_path())
        return data if isinstance(data, list) else []

    def _roles_path(self):
        return V2DOC + "roles.json"

    def _load_roles(self):
        data = _v2.read_doc(self._roles_path())
        if isinstance(data, dict) and isinstance(data.get("roles"), list):
            return data["roles"]
        return []

    def _shared_lore_path(self):
        return V2DOC + "shared_lore.json"

    def _load_shared_lore(self):
        data = _v2.read_doc(self._shared_lore_path())
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
        return []

    def _save_shared_lore(self, items):
        try:
            _atomic_write_json(self._shared_lore_path(), {"items": items})
            return True
        except Exception:
            return False

    def _open_shared_lore(self):
        from PySide6.QtWidgets import (
            QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout as QHL,
            QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
            QVBoxLayout as QVL,
        )
        import uuid

        roles = self._load_roles()
        items = self._load_shared_lore()
        editing = {"id": None}

        dlg = QDialog(self)
        dlg.setWindowTitle("共享设定")
        dlg.resize(920, 620)
        root = QVL(dlg)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        root.addWidget(self._label(
            "故事 / 背景类设定集中管理，可关联任意角色；对方消息命中触发词时才调取，平时不占额度。",
            "pageSubLabel", 11))

        body = QHL()
        body.setSpacing(12)

        left = QFrame()
        left.setObjectName("contentSurface")
        left.setMinimumWidth(240)
        lv = QVL(left)
        lv.setContentsMargins(14, 14, 14, 14)
        lv.setSpacing(8)
        lv.addWidget(self._label("设定列表", "sectionLabel", 12))
        lore_list = QListWidget()
        lore_list.setObjectName("roleList")
        lv.addWidget(lore_list, 1)
        list_row = QHL()
        list_row.setSpacing(8)
        lv.addLayout(list_row)
        body.addWidget(left, 1)

        right = QFrame()
        right.setObjectName("contentSurface")
        rv = QVL(right)
        rv.setContentsMargins(18, 16, 18, 16)
        rv.setSpacing(9)
        rv.addWidget(self._label("设定名称", "fieldLabel", 11))
        name_edit = QLineEdit()
        name_edit.setObjectName("roleNameEdit")
        name_edit.setPlaceholderText("例如：芙宁娜·原神完整背景")
        rv.addWidget(name_edit)
        rv.addWidget(self._label("触发词（逗号分隔，命中任一才调取；留空则永不自动注入）", "fieldLabel", 11))
        kw_edit = QLineEdit()
        kw_edit.setObjectName("roleNameEdit")
        kw_edit.setPlaceholderText("例如：你是谁, 原神, 枫丹, 背景, 过去")
        rv.addWidget(kw_edit)
        rv.addWidget(self._label("设定内容", "fieldLabel", 11))
        content_edit = QPlainTextEdit()
        content_edit.setObjectName("rolePromptEdit")
        rv.addWidget(content_edit, 1)
        btn_link = self._tool_button("关联角色：全部角色", lambda: open_picker())
        rv.addWidget(btn_link, 0, Qt.AlignLeft)
        # 关联状态：None = 对全部角色生效；set = 仅这些角色 id
        link_state = {"ids": None}
        act_row = QHL()
        act_row.addStretch(1)
        rv.addLayout(act_row)
        body.addWidget(right, 3)
        root.addLayout(body, 1)

        def fill_form(item):
            name_edit.setText(item.get("name", ""))
            kw_edit.setText(item.get("keywords", ""))
            content_edit.setPlainText(item.get("content", "") or "")
            ids = [str(x) for x in (item.get("role_ids") or [])]
            link_state["ids"] = None if not ids else set(ids)
            refresh_link_summary()

        def clear_form():
            editing["id"] = None
            name_edit.clear()
            kw_edit.clear()
            content_edit.clear()
            link_state["ids"] = None
            refresh_link_summary()

        def on_select():
            row = lore_list.currentRow()
            if row < 0 or row >= len(items):
                return
            item = items[row]
            editing["id"] = item.get("id")
            fill_form(item)

        def on_new():
            lore_list.clearSelection()
            clear_form()
            name_edit.setFocus()

        def on_delete():
            row = lore_list.currentRow()
            if row < 0 or row >= len(items):
                return
            items.pop(row)
            self._save_shared_lore(items)
            reload_list()
            clear_form()

        def on_save():
            name = name_edit.text().strip() or "未命名设定"
            if link_state["ids"] is None or len(link_state["ids"]) >= len(roles):
                role_ids = []
            else:
                role_ids = sorted(link_state["ids"])
            payload = {
                "name": name,
                "keywords": kw_edit.text().strip(),
                "content": content_edit.toPlainText().strip(),
                "role_ids": role_ids,
            }
            target = None
            if editing["id"]:
                for it in items:
                    if it.get("id") == editing["id"]:
                        target = it
                        break
            if target is not None:
                target.update(payload)
            else:
                payload["id"] = "lore_" + uuid.uuid4().hex[:8]
                items.append(payload)
                editing["id"] = payload["id"]
            ok = self._save_shared_lore(items)
            reload_list(editing["id"])
            if not ok:
                name_edit.setText(name + "（保存失败）")

        def reload_list(select_id=None):
            lore_list.blockSignals(True)
            lore_list.clear()
            sel = 0
            for i, it in enumerate(items):
                li = QListWidgetItem(it.get("name", it.get("id", "")))
                li.setData(256, it.get("id", ""))
                lore_list.addItem(li)
                if select_id and it.get("id") == select_id:
                    sel = i
            lore_list.blockSignals(False)
            if items:
                lore_list.setCurrentRow(sel)

        def refresh_link_summary():
            ids = link_state["ids"]
            if ids is None or not roles or len(ids) >= len(roles):
                btn_link.setText("关联角色：全部角色")
            else:
                name_map = {str(r.get("id", "")): r.get("name", r.get("id", "")) for r in roles}
                names = "、".join(name_map.get(i, i) for i in sorted(ids))
                btn_link.setText(f"关联角色：{names}")

        def open_picker():
            from PySide6.QtWidgets import QDialogButtonBox
            sub = QDialog(dlg)
            sub.setWindowTitle("关联角色")
            sub.setMinimumWidth(320)
            sv = QVL(sub)
            sv.setContentsMargins(18, 16, 18, 16)
            sv.setSpacing(8)
            sv.addWidget(self._label("勾选要关联的角色；勾「全选」即对全部角色生效", "pageSubLabel", 11))
            cur = link_state["ids"]
            is_all = cur is None or not roles or len(cur) >= len(roles)
            all_cb = QCheckBox("全选")
            sv.addWidget(all_cb)
            checks = []
            for r in roles:
                rid = str(r.get("id", ""))
                cb = QCheckBox(r.get("name", rid))
                cb.setProperty("rid", rid)
                cb.setChecked(is_all or rid in (cur or set()))
                checks.append(cb)
                sv.addWidget(cb)
            all_cb.setChecked(is_all)

            def on_all(checked):
                for cb in checks:
                    cb.setChecked(checked)

            def on_one():
                all_cb.blockSignals(True)
                all_cb.setChecked(all(cb.isChecked() for cb in checks))
                all_cb.blockSignals(False)

            all_cb.toggled.connect(on_all)
            for cb in checks:
                cb.toggled.connect(lambda _v: on_one())

            btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            btns.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
            btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
            btns.accepted.connect(sub.accept)
            btns.rejected.connect(sub.reject)
            sv.addWidget(btns)
            if sub.exec() == QDialog.DialogCode.Accepted:
                picked = [cb for cb in checks if cb.isChecked()]
                # 全选 / 全勾 / 全不勾，都按“对全部角色生效”处理
                if all_cb.isChecked() or not picked or len(picked) == len(checks):
                    link_state["ids"] = None
                else:
                    link_state["ids"] = {cb.property("rid") for cb in picked}
                refresh_link_summary()

        refresh_link_summary()
        lore_list.currentRowChanged.connect(lambda _r: on_select())
        list_row.addWidget(self._tool_button("新建", on_new))
        list_row.addWidget(self._tool_button("删除", on_delete))
        act_row.addWidget(self._tool_button("保存设定", on_save))

        reload_list()
        self._show_dialog(dlg)

    def _save_qq_profile(self, qq, nickname, role_id=""):
        profiles = self._load_qq_profiles()
        for p in profiles:
            if p["qq"] == qq:
                p["nickname"] = nickname
                p["role_id"] = role_id
                break
        else:
            profiles.append({"qq": qq, "nickname": nickname, "role_id": role_id})
        # 机器人每次回复都会读这个文件来决定人格，原子写避免读到半截文件
        _atomic_write_json(self._qq_profiles_path(), profiles)

    def _delete_qq_profile(self, qq):
        profiles = self._load_qq_profiles()
        new_profiles = [p for p in profiles if p["qq"] != qq]
        if len(new_profiles) == len(profiles):
            return False
        _atomic_write_json(self._qq_profiles_path(), new_profiles)
        return True

    def _ensure_napcat_config(self, qq):
        """为指定QQ号生成/更新NapCat OneBot配置（WebSocket连接+HTTP服务），登录后自动连接。"""
        return _v2.ensure_napcat_config(qq)

    def _write_env_values(self, values):
        env_path = os.path.join(BOT_DIR, ".env")
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            remaining = dict(values)
            for i, line in enumerate(lines):
                key = line.strip().split("=", 1)[0]
                if key in remaining:
                    lines[i] = f"{key}={remaining.pop(key)}\n"
            for key, value in remaining.items():
                lines.append(f"\n{key}={value}\n")
            _atomic_write_text(env_path, "".join(lines), newline="")
        except Exception as e:
            self.add_log("error", f"[错误] 保存模型设置失败: {e}")

    # ---- 设置中心（panel_settings.py）：原来的设置卡片页和各个子窗口已合并为一个窗口 ----
    def _open_settings(self, page=None):
        from ..settings import open_settings
        open_settings(self, page)

    def toggle_settings(self, *_):
        self._open_settings()

    def open_model_settings(self, *_):
        self._open_settings("model")

    def open_qq_manager(self, *_):
        self._open_settings("account")

    def open_pixiv_settings(self, *_):
        self._open_settings("pixiv")

    def open_reminder_settings(self, *_):
        self._open_settings("reminder")

    def show_help(self):
        self.add_log("info", "[帮助] 左侧图标可切换功能清单、详情日志、角色管理、回复次数，右下角按钮启动机器人")
