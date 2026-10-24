"""群功能权限页（PanelWindow 的 mixin，由 window.py 拆出）。"""
from ..common import *  # noqa: F401,F403
from ..widgets import *  # noqa: F401,F403


class PermissionsPageMixin:
    def _build_permissions_page(self):
        from PySide6.QtWidgets import QCheckBox

        page = QWidget()
        page.setObjectName("stackPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)

        layout.addWidget(self._label("按群聊独立控制机器人能力，选中群聊后可直接编辑并保存", "pageSubLabel", 11))

        split = QHBoxLayout()
        split.setSpacing(12)

        list_surface = QFrame()
        list_surface.setObjectName("contentSurface")
        list_surface.setMinimumWidth(300)
        list_layout = QVBoxLayout(list_surface)
        list_layout.setContentsMargins(14, 14, 14, 14)
        list_layout.setSpacing(8)
        list_layout.addWidget(self._label("群聊列表", "sectionLabel", 12))
        self.perm_list = QListWidget()
        self.perm_list.setObjectName("permList")
        self.perm_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.perm_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.perm_list.currentRowChanged.connect(lambda _row: self.open_permission_editor())
        self.perm_list.itemDoubleClicked.connect(lambda _item: self.open_permission_editor())
        list_layout.addWidget(self.perm_list, 1)

        list_actions = QHBoxLayout()
        list_actions.setContentsMargins(0, 0, 0, 0)
        list_actions.setSpacing(8)
        list_actions.addStretch(1)
        self.perm_refresh_btn = self._tool_button("刷新群列表", self._refresh_permission_groups)
        self.perm_refresh_btn.setMinimumHeight(32)
        self.perm_refresh_btn.setMaximumWidth(118)
        list_actions.addWidget(self.perm_refresh_btn)
        list_layout.addLayout(list_actions)
        split.addWidget(list_surface, 1)

        editor_surface = QFrame()
        editor_surface.setObjectName("contentSurface")
        editor_layout = QVBoxLayout(editor_surface)
        editor_layout.setContentsMargins(18, 16, 18, 16)
        editor_layout.setSpacing(10)
        editor_layout.addWidget(self._label("权限列表", "sectionLabel", 12))

        from furina.core.permissions import DEFAULT_ON_FEATURES, FEATURES
        features = dict(FEATURES)  # 与机器人同一份功能清单与默认值
        self._default_on_features = set(DEFAULT_ON_FEATURES)
        self.perm_checkboxes = {}
        for key, label in features.items():
            cb = QCheckBox(label)
            cb.setObjectName("permissionCheck")
            cb.setMinimumHeight(36)
            cb.setEnabled(False)
            self.perm_checkboxes[key] = cb
            editor_layout.addWidget(cb)
        editor_layout.addStretch(1)
        editor_actions = QHBoxLayout()
        editor_actions.setSpacing(8)
        editor_actions.addStretch(1)
        self.perm_select_all_btn = self._tool_button("全选", self._select_all_permissions)
        self.perm_select_all_btn.setEnabled(False)
        editor_actions.addWidget(self.perm_select_all_btn)
        self.perm_save_btn = self._tool_button("保存权限", self._save_current_group_permissions)
        self.perm_save_btn.setEnabled(False)
        editor_actions.addWidget(self.perm_save_btn)
        editor_layout.addLayout(editor_actions)
        split.addWidget(editor_surface, 2)
        layout.addLayout(split, 1)

        self.perm_current_gid = None
        self._refresh_permission_groups()
        return page

    def _read_feature_permissions(self):
        return self._load_json(self._qq_path("feature_permissions.json"), {"groups": {}})

    def _save_feature_permissions(self, data):
        try:
            _atomic_write_json(self._qq_path("feature_permissions.json"), data)
        except Exception as e:
            self.add_log("error", f"[错误] 保存功能权限失败: {e}")

    def _refresh_permission_groups(self):
        if not hasattr(self, "perm_list"):
            return
        gids = self._available_groups()
        current_gid = getattr(self, "perm_current_gid", None)
        self.perm_list.blockSignals(True)
        self.perm_list.clear()
        for gid in gids:
            item = QListWidgetItem(f"{self._group_name(gid)}  ·  {gid}")
            item.setData(Qt.UserRole, gid)
            self.perm_list.addItem(item)
        self.perm_list.blockSignals(False)
        if gids:
            row = next((i for i, gid in enumerate(gids) if str(gid) == str(current_gid)), 0)
            self.perm_list.setCurrentRow(row)
            self.open_permission_editor()
        else:
            self.perm_current_gid = None
            self._clear_permission_editor()

    def _clear_permission_editor(self):
        if not hasattr(self, "perm_checkboxes"):
            return
        for cb in self.perm_checkboxes.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.setEnabled(False)
            cb.blockSignals(False)
        self.perm_save_btn.setEnabled(False)
        if hasattr(self, "perm_select_all_btn"):
            self.perm_select_all_btn.setEnabled(False)

    def _select_all_permissions(self):
        if getattr(self, "perm_current_gid", None) is None:
            return
        for cb in self.perm_checkboxes.values():
            if cb.isEnabled():
                cb.setChecked(True)

    def _save_current_group_permissions(self):
        gid = getattr(self, "perm_current_gid", None)
        if gid is None:
            self.add_log("warn", "[权限] 请先选择一个群")
            return
        self._save_group_permissions(None, gid, self.perm_checkboxes)

    def open_permission_editor(self):
        if not hasattr(self, "perm_list"):
            return
        row = self.perm_list.currentRow()
        item = self.perm_list.currentItem()
        gids = self._available_groups()
        if row < 0 or row >= len(gids):
            self._clear_permission_editor()
            return
        gid = item.data(Qt.UserRole) if item is not None else gids[row]
        gid = str(gid)
        data = self._read_feature_permissions()
        group_cfg = data.get("groups", {}).get(gid, {"enabled": True, "features": {}})
        self.perm_current_gid = gid
        default_on = getattr(self, "_default_on_features", set())
        for key, cb in self.perm_checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(bool(group_cfg.get("features", {}).get(key, key in default_on)))
            cb.setEnabled(True)
            cb.blockSignals(False)
        self.perm_save_btn.setEnabled(True)
        if hasattr(self, "perm_select_all_btn"):
            self.perm_select_all_btn.setEnabled(True)

    def _save_group_permissions(self, dialog, gid, checkboxes):
        data = self._read_feature_permissions()
        groups = data.setdefault("groups", {})
        old_features = (groups.get(gid) or {}).get("features") or {}
        new_features = {k: cb.isChecked() for k, cb in checkboxes.items()}
        groups[gid] = {
            "enabled": True,
            "features": new_features,
        }
        self._save_feature_permissions(data)
        self.add_log("success", f"[权限] 已保存 {self._group_name(gid)} 的功能权限")

        # 米游社推送从关闭变为开启时，建立基线（不补推历史资讯）
        if new_features.get("miyoushe") and not old_features.get("miyoushe"):
            self._baseline_miyoushe_for_group(gid)

        if dialog is not None:
            dialog.accept()

    def _baseline_miyoushe_for_group(self, gid):
        """给群建立米游社推送基线：把所有历史帖标记为已推送，只推之后的新帖。"""
        try:
            count = _v2.baseline_miyoushe(gid)
            self.add_log("info", f"[米游社] 群 {self._group_name(gid)} 已建立基线，跳过 {count} 条历史资讯")
        except Exception as e:
            self.add_log("error", f"[米游社] 建立基线失败: {e}")
