"""角色（人格）管理页（PanelWindow 的 mixin，由 window.py 拆出）。"""
from ..common import *  # noqa: F401,F403
from ..widgets import *  # noqa: F401,F403


class RolePageMixin:
    def _build_role_page(self):
        page = QWidget()
        page.setObjectName("stackPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.addWidget(self._label("为每个 QQ 绑定一套性格；指令规则已自动携带，角色里只写性格即可。", "pageSubLabel", 11), 1)
        header.addWidget(self._tool_button("共享设定", self._open_shared_lore))
        layout.addLayout(header)

        split = QHBoxLayout()
        split.setSpacing(12)

        list_surface = QFrame()
        list_surface.setObjectName("contentSurface")
        list_surface.setMinimumWidth(270)
        list_layout = QVBoxLayout(list_surface)
        list_layout.setContentsMargins(14, 14, 14, 14)
        list_layout.setSpacing(8)
        list_head = QHBoxLayout()
        list_head.addWidget(self._label("角色列表", "sectionLabel", 12))
        list_head.addStretch(1)
        list_head.addWidget(self._label("可编辑", "surfaceHint", 10))
        list_layout.addLayout(list_head)
        self.role_list = QListWidget()
        self.role_list.setObjectName("roleList")
        self.role_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.role_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.role_list.currentRowChanged.connect(self._role_select)
        list_layout.addWidget(self.role_list, 1)
        list_actions = QHBoxLayout()
        list_actions.setSpacing(8)
        list_actions.addWidget(self._tool_button("新建角色", self._role_new))
        list_actions.addWidget(self._tool_button("删除角色", self._role_delete))
        list_layout.addLayout(list_actions)
        split.addWidget(list_surface, 1)

        editor_surface = QFrame()
        editor_surface.setObjectName("contentSurface")
        editor = QVBoxLayout(editor_surface)
        editor.setContentsMargins(18, 16, 18, 16)
        editor.setSpacing(9)
        editor.addWidget(self._label("角色名称", "fieldLabel", 11))
        self.role_name_edit = QLineEdit()
        self.role_name_edit.setObjectName("roleNameEdit")
        self.role_name_edit.setPlaceholderText("例如：芙宁娜 / 雷电将军")
        self.role_name_edit.textChanged.connect(self._role_on_edit)
        editor.addWidget(self.role_name_edit)
        editor.addWidget(self._label("称呼（群友怎么叫 TA，逗号分隔，第一个作为名字；留空取角色名「-」前面的部分）", "fieldLabel", 11))
        self.role_alias_edit = QLineEdit()
        self.role_alias_edit.setObjectName("roleNameEdit")
        self.role_alias_edit.setPlaceholderText("例如：胡桃, 胡堂主, 堂主")
        self.role_alias_edit.textChanged.connect(self._role_on_edit)
        editor.addWidget(self.role_alias_edit)
        editor.addWidget(self._label("性格与说话风格提示词", "fieldLabel", 11))
        self.role_prompt_edit = QPlainTextEdit()
        self.role_prompt_edit.setObjectName("rolePromptEdit")
        self.role_prompt_edit.setPlaceholderText("填写该角色的性格、说话风格与行为边界提示词。")
        self.role_prompt_edit.textChanged.connect(self._role_on_edit)
        editor.addWidget(self.role_prompt_edit, 1)
        editor_actions = QHBoxLayout()
        editor_actions.setSpacing(8)
        editor_actions.addStretch(1)
        editor_actions.addWidget(self._tool_button("打开提示词", self._role_open_prompt))
        editor_actions.addWidget(self._tool_button("复制提示词", self._role_copy_prompt))
        editor_actions.addWidget(self._tool_button("设为当前角色", self._role_apply_current))
        save_btn = self._tool_button("保存提示词", self._role_save)
        editor_actions.addWidget(save_btn)
        editor.addLayout(editor_actions)
        split.addWidget(editor_surface, 3)
        layout.addLayout(split, 1)

        self.roles_data = []
        self.role_current = -1
        self._roles_deleted = set()
        self._roles_dirty = set()
        self._role_refresh_list()
        return page

    def _role_refresh_list(self):
        self.roles_data = self._load_roles()
        self.role_list.blockSignals(True)
        self.role_list.clear()
        for r in self.roles_data:
            item = QListWidgetItem(r.get("name", r.get("id", "")))
            item.setData(256, r.get("id", ""))
            self.role_list.addItem(item)
        self.role_list.blockSignals(False)
        if self.roles_data:
            self.role_list.setCurrentRow(0)
            self._role_select(0)
        else:
            self.role_current = -1
            self.role_name_edit.clear()
            self.role_alias_edit.clear()
            self.role_prompt_edit.clear()

    def _role_select(self, idx):
        if idx < 0 or idx >= len(self.roles_data):
            return
        self.role_current = idx
        r = self.roles_data[idx]
        self.role_name_edit.blockSignals(True)
        self.role_alias_edit.blockSignals(True)
        self.role_prompt_edit.blockSignals(True)
        self.role_name_edit.setText(r.get("name", ""))
        _al = r.get("aliases") or ""
        self.role_alias_edit.setText("，".join(_al) if isinstance(_al, list) else str(_al))
        # 芙宁娜也是普通角色，提示词直接可编辑；选中即显示该角色完整提示词
        self.role_prompt_edit.setReadOnly(False)
        self.role_prompt_edit.setPlainText(r.get("prompt", "") or "")
        self.role_name_edit.blockSignals(False)
        self.role_alias_edit.blockSignals(False)
        self.role_prompt_edit.blockSignals(False)

    def _role_on_edit(self):
        if self.role_current < 0 or self.role_current >= len(self.roles_data):
            return
        r = self.roles_data[self.role_current]
        self._roles_dirty.add(str(r.get("id", "")))
        r["name"] = self.role_name_edit.text().strip()
        r["prompt"] = self.role_prompt_edit.toPlainText()
        _al = self.role_alias_edit.text().strip()
        if _al or "aliases" in r:
            r["aliases"] = _al
        item = self.role_list.item(self.role_current)
        if item:
            item.setText(r.get("name", r.get("id", "")))

    def _role_apply_current(self):
        """把当前选中的角色绑定为「当前机器人 QQ」实际使用的角色（写 bot_qq_profiles.json）。"""
        from PySide6.QtWidgets import QMessageBox
        if self.role_current < 0 or self.role_current >= len(self.roles_data):
            return
        r = self.roles_data[self.role_current]
        rid = r.get("id", "")
        name = r.get("name", rid) or rid
        qq = (self._read_env_value("BOT_QQ") or "").strip()
        if not qq:
            QMessageBox.information(self, "提示", "还没有当前机器人 QQ，请先在「设置-管理 QQ 号列表」里选择。")
            return
        # 先把编辑器里可能改过的角色内容保存下来，再绑定，避免用到旧提示词
        try:
            self._role_save()
        except Exception:
            pass
        nickname = name
        for p in self._load_qq_profiles():
            if str(p.get("qq")) == str(qq):
                nickname = p.get("nickname") or name
                break
        self._save_qq_profile(str(qq), nickname, rid)
        if hasattr(self, "log_text"):
            self.add_log("success", f"[角色] 当前机器人 QQ {qq} 已切换为「{name}」，下一条消息起生效")
        QMessageBox.information(
            self, "已设为当前角色",
            f"已把当前机器人（QQ {qq}）使用的角色设为「{name}」。\n机器人热读取，下一条消息起就用新角色说话，无需重启。",
        )

    def _role_new(self):
        """点「新建角色」弹出独立窗口，填写角色ID / 名称 / 提示词。"""
        from PySide6.QtWidgets import (
            QDialog, QFormLayout, QLabel as QL, QLineEdit as QLE,
            QPlainTextEdit as QPTE, QPushButton as QPB, QVBoxLayout as QVL,
        )
        dlg = QDialog(self)
        _dp = dlg.palette(); _dp.setColor(QPalette.ColorRole.Window, QColor("#0a101e"))
        dlg.setPalette(_dp); dlg.setAutoFillBackground(True)
        dlg.setWindowTitle("新建角色")
        dlg.setFixedSize(520, 460)
        dlg.setStyleSheet("background: #0a101e; color: #f4f7ff; font-family: 'Microsoft YaHei';")
        v = QVL(dlg)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)
        id_edit = QLE()
        id_edit.setPlaceholderText("留空则自动生成（如 role_xxx）")
        id_edit.setStyleSheet(self._line_edit_style())
        name_edit = QLE()
        name_edit.setPlaceholderText("角色名称，如：雷电将军")
        name_edit.setStyleSheet(self._line_edit_style())
        alias_edit = QLE()
        alias_edit.setPlaceholderText("群友怎么叫 TA，逗号分隔，如：胡桃, 胡堂主（可留空）")
        alias_edit.setStyleSheet(self._line_edit_style())
        form.addRow(QL("角色 ID："), id_edit)
        form.addRow(QL("角色名称："), name_edit)
        form.addRow(QL("称呼："), alias_edit)
        v.addLayout(form)

        v.addWidget(QL("性格提示词："))
        prompt_edit = QPTE()
        prompt_edit.setPlaceholderText("填写该角色的性格 / 说话风格 / 人设。可直接复制芙宁娜的提示词再改。")
        prompt_edit.setStyleSheet(
            f"background-color: {SURFACE_DEEP}; color: {TEXT_BODY}; border: 1px solid rgba(255,255,255,0.10);"
            "border-radius: 10px; padding: 10px; font-family: 'Microsoft YaHei'; font-size: 12px;"
        )
        v.addWidget(prompt_edit, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPB("取消"); cancel.setStyleSheet(self._mini_btn_style())
        cancel.clicked.connect(dlg.reject)
        btns.addWidget(cancel)
        ok = QPB("创建"); ok.setStyleSheet("background: #ec4899; color: #fff; border: none; border-radius: 8px; padding: 8px 18px; font-weight: bold;")
        ok.clicked.connect(dlg.accept)
        btns.addWidget(ok)
        v.addLayout(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = name_edit.text().strip()
        if not name:
            self.add_log("warn", "[角色管理] 角色名称不能为空")
            return
        rid = id_edit.text().strip() or ("role_" + str(int(time.time() * 1000)))
        if any(r.get("id") == rid for r in self.roles_data):
            self.add_log("warn", "[角色管理] 角色 ID 已存在")
            return
        new_role = {"id": rid, "name": name, "prompt": prompt_edit.toPlainText()}
        if alias_edit.text().strip():
            new_role["aliases"] = alias_edit.text().strip()
        self.roles_data.append(new_role)
        self._roles_dirty.add(rid)
        # _role_refresh_list 会从磁盘重新读取；先保存，避免新角色刚加入内存就被刷新丢掉。
        if not self._role_save():
            return
        self._role_refresh_list()
        for i, r in enumerate(self.roles_data):
            if r.get("id") == rid:
                self.role_list.setCurrentRow(i)
                break
        self.add_log("success", f"[角色管理] 已新建角色「{name}」并保存")

    def _role_delete(self):
        idx = self.role_list.currentRow()
        if idx < 0 or idx >= len(self.roles_data):
            self.add_log("warn", "[角色管理] 请先选择一个角色")
            return
        r = self.roles_data[idx]
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("删除角色")
        box.setText(f"确定删除角色「{r.get('name', '')}」吗？")
        box.setInformativeText("删除后无法恢复，需要重新添加。")
        yes = box.addButton("删除", QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.setStyleSheet(
            "QMessageBox{background:#1a1626;color:#f4f7ff;font-size:13px;}"
            "QMessageBox QLabel{color:#f4f7ff;}"
            "QPushButton{background:#1e2540;color:#e2e8f0;border:none;border-radius:8px;"
            "padding:7px 18px;font-size:13px;min-width:64px;}"
            "QPushButton:hover{background:#2b3354;}"
        )
        box.exec()
        if box.clickedButton() is not yes:
            return
        self.roles_data.pop(idx)
        self._roles_deleted.add(str(r.get("id", "")))
        # 删除与新建保持一致：立即落盘，避免刷新列表后又从旧文件恢复。
        if not self._role_save():
            return
        self._role_refresh_list()
        self.add_log("info", f"[角色管理] 已删除角色 {r.get('name', '')}")

    def _role_save(self):
        try:
            # 面板开着期间，别处（网页面板、脚本、机器人）可能改过角色；这份内存列表是打开面板时读的。
            # 保存时只写本窗口真正改过的角色，其余一律以库里最新的为准，免得整表覆盖把别人的修改冲掉。
            dirty = getattr(self, "_roles_dirty", set())
            deleted = getattr(self, "_roles_deleted", set())
            disk = {str(r.get("id", "")): r for r in self._load_roles() if r.get("id")}
            merged, known = [], set()
            for r in self.roles_data:
                rid = str(r.get("id", ""))
                if rid in deleted:
                    continue
                merged.append(r if (rid in dirty or rid not in disk) else disk[rid])
                known.add(rid)
            for rid, r in disk.items():
                if rid not in known and rid not in deleted:
                    merged.append(r)
                    _it = QListWidgetItem(r.get("name", rid))
                    _it.setData(256, rid)
                    self.role_list.addItem(_it)
            self.roles_data = merged
            _v2.write_doc(self._roles_path(), {"roles": self.roles_data})
            dirty.clear()
            # 当前选中的角色如果换成了库里的新版本，编辑框也跟着刷新
            cur = self.role_current
            if 0 <= cur < len(self.roles_data) and self.role_prompt_edit.toPlainText() != (self.roles_data[cur].get("prompt") or ""):
                self._role_select(cur)
            self.add_log("success", "[角色管理] 角色已保存，机器人热读取")
            return True
        except Exception as e:
            self.add_log("error", f"[角色管理] 保存失败: {e}")
            return False

    def _role_current_prompt(self):
        """返回当前选中角色的提示词文本；未选中或无提示词返回 (None, 原因)。"""
        idx = self.role_list.currentRow()
        if idx < 0 or idx >= len(self.roles_data):
            return None, "请先选择一个角色"
        prompt = self.role_prompt_edit.toPlainText()
        if not prompt.strip():
            return None, "当前角色提示词为空"
        return prompt, None

    def _role_open_prompt(self):
        """把当前角色提示词写入临时 txt 并用系统记事本打开查看。"""
        prompt, err = self._role_current_prompt()
        if prompt is None:
            self.add_log("warn", f"[角色管理] {err}")
            return
        name = (self.role_name_edit.text().strip() or "角色").replace("/", "_").replace("\\", "_")
        tmp = os.path.join(os.environ.get("TEMP", "."), f"{name}_提示词.txt")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(prompt)
            subprocess.Popen(["notepad", tmp])
            self.add_log("success", f"[角色管理] 已用记事本打开「{name}」的提示词")
        except Exception as e:
            self.add_log("error", f"[角色管理] 打开提示词失败: {e}")

    def _role_copy_prompt(self):
        """把当前角色提示词复制到系统剪贴板。"""
        prompt, err = self._role_current_prompt()
        if prompt is None:
            self._toast(err or "请先选择角色", "#ffd166")
            return
        QApplication.clipboard().setText(prompt)
        self._toast("复制成功", "#3ecf8e")
