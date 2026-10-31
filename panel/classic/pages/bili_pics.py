"""经典面板的全局画师订阅管理。网络核对在工作线程，绝不堵住界面。"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QVBoxLayout)

from ...services import data
from ..theme import (ACCENT, BORDER, BORDER_ACCENT, LINE_EDIT_QSS, MINI_BUTTON_QSS,
                     RADIUS_CONTROL, SURFACE, SURFACE_DEEP, SURFACE_SELECTED, TEXT_BODY, TEXT_HINT)

_DIALOG_QSS = LINE_EDIT_QSS + MINI_BUTTON_QSS + f"""
QDialog {{ background: {SURFACE}; color: {TEXT_BODY}; }}
QLabel, QCheckBox {{ background: transparent; color: {TEXT_BODY}; font-size: 12px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; background: {SURFACE_DEEP}; border: 1px solid {TEXT_HINT}; border-radius: 3px; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QListWidget, QSpinBox {{ background: {SURFACE_DEEP}; color: {TEXT_BODY}; border: 1px solid {BORDER};
    border-radius: {RADIUS_CONTROL}px; padding: 6px; font-size: 12px; }}
QListWidget::item {{ padding: 8px; }}
QListWidget::item:selected {{ background: {SURFACE_SELECTED}; }}
QPushButton:disabled {{ color: {TEXT_HINT}; background: {SURFACE_DEEP}; }}
QPushButton#biliPicsAdd {{ background: {ACCENT}; color: {SURFACE_DEEP}; font-weight: bold; }}
QPushButton#biliPicsAdd:disabled {{ color: {TEXT_HINT}; background: {SURFACE_DEEP}; }}
QLineEdit:focus, QSpinBox:focus {{ border-color: {BORDER_ACCENT}; }}
"""


def _status(row):
    return (f'所有群共用 {len(row.get("subscriptions") or [])} 个UP · 每{row.get("interval_minutes", 20)}分钟分批检查'
            f' · 待发送 {row.get("pending", 0)} 份 · 待确认 {row.get("uncertain", 0)} 份 · 失败 {row.get("failed", 0)} 份'
            + (" · 图片订阅暂停中" if not row.get("enabled", True) else "")
            + (" · B站请求已暂停" if row.get("halted") else ""))


class BiliPicsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("B站画师图片 · 全群共用名单")
        self.setStyleSheet(_DIALOG_QSS)
        self.resize(700, 500)
        self.setMinimumSize(500, 380)
        self._job = None
        self._dirty = False
        self._last_refresh = 0
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        note = QLabel("一份名单，所有群同步。首次订阅只追新图；推送只带UP名，不附动态链接，不查历史作品。")
        note.setWordWrap(True)
        lay.addWidget(note)
        row = QHBoxLayout()
        self.uid = QLineEdit()
        self.uid.setPlaceholderText("UP的UID或B站个人主页链接")
        self.uid.setAccessibleName("UP UID或个人主页链接")
        self.add = QPushButton("核对并加入")
        self.add.setObjectName("biliPicsAdd")
        self.add.clicked.connect(self._add)
        self.uid.returnPressed.connect(self._add)
        row.addWidget(self.uid, 1)
        row.addWidget(self.add)
        lay.addLayout(row)
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.list = QListWidget()
        self.list.setAccessibleName("所有群共用的UP名单")
        lay.addWidget(self.list, 1)
        controls = QHBoxLayout()
        self.enabled = QCheckBox("启用图片订阅")
        self.interval = QSpinBox()
        self.interval.setRange(5, 1440)
        self.interval.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.interval.setSuffix(" 分钟")
        self.interval.setAccessibleName("检查间隔分钟")
        self.interval.setValue(20)
        self.enabled.toggled.connect(self._mark_dirty)
        self.interval.valueChanged.connect(self._mark_dirty)
        self.save = QPushButton("保存设置")
        self.save.clicked.connect(self._save)
        controls.addWidget(self.enabled)
        controls.addWidget(QLabel("检查间隔"))
        controls.addWidget(self.interval)
        controls.addWidget(self.save)
        controls.addStretch(1)
        lay.addLayout(controls)
        actions = QHBoxLayout()
        self.check = QPushButton("检查新图")
        self.check.clicked.connect(lambda: self._run(data.check_bili_pics, "已安排检查，机器人下一轮分批执行"))
        self.retry = QPushButton("重试未确认或失败的发送")
        self.retry.clicked.connect(self._retry)
        self.remove = QPushButton("取消选中UP的全局订阅")
        self.remove.clicked.connect(self._remove)
        actions.addWidget(self.check)
        actions.addWidget(self.retry)
        actions.addStretch(1)
        actions.addWidget(self.remove)
        lay.addLayout(actions)
        self.result = QLabel()
        self.result.setTextFormat(Qt.PlainText)
        self.result.setWordWrap(True)
        lay.addWidget(self.result)
        self.buttons = [self.add, self.save, self.check, self.retry, self.remove]
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self.refresh()

    def _mark_dirty(self, *_):
        self._dirty = True

    def refresh(self):
        row = data.bili_pics_data()
        self._last_refresh = time.monotonic()
        self.status.setText(_status(row))
        selected = self.list.currentItem().data(Qt.UserRole) if self.list.currentItem() else None
        self.list.clear()
        for up in row["subscriptions"]:
            item = QListWidgetItem(f'{up["name"]} · UID {up["uid"]}' + (" · " + up["last_error"] if up.get("last_error") else ""))
            item.setData(Qt.UserRole, up["uid"])
            item.setData(Qt.UserRole + 1, up["name"])
            self.list.addItem(item)
            if up["uid"] == selected:
                self.list.setCurrentItem(item)
        if not self._dirty:
            for control in (self.enabled, self.interval):
                control.blockSignals(True)
            self.enabled.setChecked(row["enabled"])
            self.interval.setValue(row["interval_minutes"])
            for control in (self.enabled, self.interval):
                control.blockSignals(False)
        if self._job is None:
            self.retry.setEnabled(bool(row["uncertain"] + row["failed"]))

    def _run(self, fn, success):
        if self._job is not None:
            return
        job = {"done": False, "success": success}
        self._job = job
        self.result.setText("正在核对并处理…")
        for button in self.buttons:
            button.setEnabled(False)
        def work():
            try:
                job["value"] = fn()
            except Exception as exc:
                job["error"] = str(exc)[:200]
            finally:
                job["done"] = True
        threading.Thread(target=work, daemon=True, name="bili-pics-panel").start()

    def _poll(self):
        job = self._job
        if job is not None and job["done"]:
            self._job = None
            for button in self.buttons:
                button.setEnabled(True)
            if job.get("error"):
                self.result.setText(job["error"])
            else:
                message = job["success"](job.get("value")) if callable(job["success"]) else job["success"]
                self.result.setText(message)
            self.refresh()
        elif job is None and self.isVisible() and time.monotonic() - self._last_refresh > 5:
            self.refresh()

    def _add(self):
        target = self.uid.text().strip()
        if not target:
            self.result.setText("先填UP的UID或主页链接")
            return
        def done(row):
            if self.uid.text().strip() == target:
                self.uid.clear()
            return ("已订阅：" if row["created"] else "已经在名单里：") + row["name"] + "，所有群同步"
        self._run(lambda: data.add_bili_pic(target), done)

    def _save(self):
        values = {"enabled": self.enabled.isChecked(), "interval_minutes": self.interval.value()}
        def done(_):
            self._dirty = False
            return "图片订阅设置已保存，机器人会自动读到"
        self._run(lambda: data.save_bili_pics(values), done)

    def _remove(self):
        item = self.list.currentItem()
        if item is None:
            self.result.setText("先选中一个UP")
            return
        uid, name = item.data(Qt.UserRole), item.data(Qt.UserRole + 1)
        if QMessageBox.question(self, "取消全局订阅", f"取消「{name}」后，所有群都不再接收这个UP的新图，确定吗？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._run(lambda: data.remove_bili_pic(uid), "已取消，所有群同步")

    def _retry(self):
        if QMessageBox.question(self, "重试未确认的发送", "上次有些发送没收到回执，可能其实已经送达。重试可能重复发图，确定继续吗？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self._run(lambda: data.retry_bili_pics(True), "已安排重试，机器人会分批执行")


class BiliPicsMixin:
    def _build_bili_pics(self):
        frame = QFrame()
        frame.setObjectName("contentSurface")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 10)
        row = QHBoxLayout()
        row.addWidget(self._label("B站画师图片 · 全群共用名单", "sectionLabel", 12), 1)
        row.addWidget(self._tool_button("管理UP名单", self._bili_pics_open))
        row.addWidget(self._tool_button("检查新图", self._bili_pics_check))
        lay.addLayout(row)
        self._bili_pics_status = QLabel("读取全局名单中…")
        self._bili_pics_status.setTextFormat(Qt.PlainText)
        self._bili_pics_status.setWordWrap(True)
        lay.addWidget(self._bili_pics_status)
        return frame

    def _refresh_bili_pics(self, row):
        self._bili_pics_status.setText(_status(row))

    def _bili_pics_open(self):
        BiliPicsDialog(self).exec()
        self.refresh_bili()

    def _bili_pics_check(self):
        n = data.check_bili_pics()
        self._bili_pics_status.setText(f"已安排检查 {n} 个UP，机器人下一轮分批执行")
