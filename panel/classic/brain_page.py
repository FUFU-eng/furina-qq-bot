"""设置中心「模式与内核」页：聊天模式、二代仿真的醒来条件、思考强度、表情包本子、DSH 内核与 Agent。

上半部分是普通设置项（写 .env，保存后实时生效，标「需重启」的除外）；
另外几张卡片是实时状态和操作：内核运行状态 / 安装、今天的花费（含思考）、她自己调过的醒来方式、表情包本子管理。
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QInputDialog, QListWidget, QListWidgetItem

from .settings import _ask, _Bridge, _button, _chip, _label, set_prop
from .settings_env import EnvPage

from ..services.brain_settings import BRAIN  # noqa: F401  设置项清单（和设置中心其它页共用）


def _money(v: float) -> str:
    return f"¥{v:.4f}" if v < 0.1 else f"¥{v:.2f}"


class BrainPage(EnvPage):
    """普通设置项 + 状态 / 操作卡片。"""

    def build(self):
        self._bridge = _Bridge()
        self._bridge.done.connect(self._show_status)
        self._build_status()
        super().build()
        self._build_spend()
        self._build_wake()
        self._build_stickers()
        self._timer = QTimer(self)
        self._timer.setInterval(8000)
        self._timer.timeout.connect(self._refresh_status_if_visible)
        self._timer.start()

    # ---------------- 内核状态 ----------------
    def _build_status(self):
        lay = self.card("内核状态", "二代仿真和 Agent 用的 DSH 内核。没装好时二代仿真会自动按一代回复。")
        row = QHBoxLayout()
        row.setSpacing(10)
        self.st_install = _chip("…", "off")
        self.st_run = _chip("…", "off")
        row.addWidget(_label("运行时", "fieldLabel"))
        row.addWidget(self.st_install)
        row.addSpacing(16)
        row.addWidget(_label("内核", "fieldLabel"))
        row.addWidget(self.st_run)
        row.addStretch(1)
        lay.addLayout(row)
        self.st_detail = _label("", "help", wrap=True)
        lay.addWidget(self.st_detail)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.btn_install = _button("安装 / 修复运行时", slot=self._install, tip="后台下载官方 deepseek-harness-sdk 到运行时目录（不弹窗口）")
        btns.addWidget(self.btn_install)
        btns.addWidget(_button("停止内核（释放内存）", slot=self._stop_worker, tip="下次有人叫她时会自动重新启动"))
        btns.addWidget(_button("清空二代会话", slot=self._reset_sessions, tip="所有群下一次醒来都从新会话开始（不删聊天记录）"))
        btns.addWidget(_button("刷新", slot=self._refresh_status))
        btns.addStretch(1)
        lay.addLayout(btns)

    def _refresh_status_if_visible(self):
        if self.isVisible():
            self._refresh_status()

    def _refresh_status(self):
        def work():
            try:
                from furina.core import agent_engine as eng

                st = eng.status()
                err = ""
            except Exception as e:  # noqa: BLE001
                st, err = {}, str(e)[:120]
            self._bridge.done.emit(st, err)
        threading.Thread(target=work, daemon=True).start()

    def _show_status(self, st: dict, err: str):
        if err:
            self.st_install.setText("读取失败")
            set_prop(self.st_install, "tone", "warn")
            self.st_detail.setText(err)
            return
        inst = st.get("install") or ("已安装" if st.get("installed") else "未安装")
        self.st_install.setText(inst)
        set_prop(self.st_install, "tone", "on" if inst == "已安装" else ("info" if inst == "安装中" else "warn"))
        self.btn_install.setEnabled(inst != "安装中")
        if st.get("running"):
            txt = "运行中" + (f"（正在处理 {st['busy']} 个）" if st.get("busy") else "")
            if st.get("stale"):
                txt += "，设置改了，空闲时自动换新"
            tone = "on"
        else:
            txt, tone = "未运行（用到时自动启动）", "off"
        self.st_run.setText(txt)
        set_prop(self.st_run, "tone", tone)
        bits = [f"运行时目录 {st.get('runtime_dir')}", f"端口 {st.get('port')}", f"SDK {st.get('sdk')}"]
        if st.get("agent_enabled"):
            bits.append("Agent 已允许")
        if st.get("error"):
            bits.append("最近错误：" + str(st["error"])[:120])
        self.st_detail.setText("；".join(bits))

    def _install(self):
        from furina.core import agent_engine as eng

        if not _ask(self, "安装 DSH 运行时", f"会在后台（不弹窗口）把官方 deepseek-harness-sdk 装到\n{eng.runtime_dir()}\n"
                                            "大约 80MB，用清华镜像下载，不改机器人自己的 Python 环境。继续吗？"):
            return
        try:
            msg = eng.install()
        except Exception as e:  # noqa: BLE001
            msg = f"没能开始安装：{e}"
        self.set_status(msg, "info")
        self.log("info", f"[模式与内核] {msg}")
        QTimer.singleShot(1500, self._refresh_status)

    def _stop_worker(self):
        from furina.core import agent_engine as eng

        threading.Thread(target=eng.stop, daemon=True).start()
        self.set_status("已请求内核退出", "info")
        QTimer.singleShot(2000, self._refresh_status)

    def _reset_sessions(self):
        if not _ask(self, "清空二代会话", "所有群下一次醒来都会从新会话开始（她会重新看最近的聊天记录）。继续吗？"):
            return
        from furina.core import agent_engine as eng

        eng.reset_all_sessions()
        self.set_status("已清空，下一次醒来生效", "ok")

    # ---------------- 花费 ----------------
    def _build_spend(self):
        lay = self.card("今天的花费", "按本机记账估算（价格在「模型花费」页设置）。思考的字数按输出价计费，已经算在各功能的花费里。")
        self.spend_label = _label("", "help", wrap=True)
        lay.addWidget(self.spend_label)

    def _refresh_spend(self):
        try:
            from furina.services import llm_spend

            s = llm_spend.thinking_summary()
        except Exception as e:  # noqa: BLE001
            self.spend_label.setText(f"读不到：{e}")
            return
        lines = [f"今天模型总花费约 {_money(s['total_cost'])}；二代仿真内核 {_money(s['sim2_cost'])}；Agent {_money(s['agent_cost'])}。"]
        if s["calls"]:
            per = "，".join(f"{k} {v['calls']} 次 {_money(v['cost'])}" for k, v in s["by_level"].items())
            think = f"，其中单列出的思考 {s['think']:,} tokens" if s["think"] else ""
            lines.append(f"开了思考的调用 {s['calls']} 次{think}，这些调用共 {_money(s['cost'])}（{per}）。"
                         "二代内核的思考字数含在输出里，不单列。")
        else:
            lines.append("今天还没有开思考的调用。")
        self.spend_label.setText("\n".join(lines))

    # ---------------- 她自己调过的醒来方式 ----------------
    def _build_wake(self):
        lay = self.card("她自己调过的醒来方式", "打开「允许她自己调整醒来方式」后，她改过的会记在这里（按群）。恢复 = 回到上面的设置。")
        self.wake_label = _label("", "help", wrap=True)
        lay.addWidget(self.wake_label)
        row = QHBoxLayout()
        row.addWidget(_button("全部恢复成面板设置", slot=self._clear_wake))
        row.addStretch(1)
        lay.addLayout(row)

    def _refresh_wake(self):
        try:
            from furina.core import brain

            over = brain.all_wake_overrides()
        except Exception as e:  # noqa: BLE001
            self.wake_label.setText(f"读不到：{e}")
            return
        if not over:
            self.wake_label.setText("没有，都按上面的设置。")
            return
        lines = []
        for gid, v in over.items():
            bits = []
            if "random" in v:
                bits.append(f"随机醒来 {float(v['random']) * 100:.0f}%")
            if "idle_minutes" in v:
                bits.append("不按时间醒" if not v["idle_minutes"] else f"潜水 {v['idle_minutes']} 分钟后醒")
            why = f"（她说：{v['reason']}）" if v.get("reason") else ""
            lines.append(f"群 {gid}：{'，'.join(bits)}{why}")
        self.wake_label.setText("\n".join(lines))

    def _clear_wake(self):
        from furina.core import brain

        n = brain.clear_wake_overrides()
        self._refresh_wake()
        self.set_status(f"已恢复 {n} 个群", "ok")

    # ---------------- 表情包本子 ----------------
    def _build_stickers(self):
        lay = self.card("表情包本子管理", "双击改备注。备注写清它表达什么（如「无语翻白眼」「晚安」），她按备注挑。")
        self.stk_stats = _label("", "help")
        lay.addWidget(self.stk_stats)
        self.stk_list = QListWidget()
        self.stk_list.setViewMode(QListWidget.IconMode)
        self.stk_list.setIconSize(QSize(64, 64))
        self.stk_list.setGridSize(QSize(104, 104))
        self.stk_list.setResizeMode(QListWidget.Adjust)
        self.stk_list.setMovement(QListWidget.Static)
        self.stk_list.setWordWrap(True)
        self.stk_list.setMinimumHeight(260)
        self.stk_list.setStyleSheet("QListWidget { background: #0b1526; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; color: #c9d4e6; }")
        self.stk_list.itemDoubleClicked.connect(lambda _it: self._edit_note())
        lay.addWidget(self.stk_list)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(_button("改备注", slot=self._edit_note))
        row.addWidget(_button("删除", slot=self._delete_sticker))
        row.addWidget(_button("只看没备注的", slot=lambda: self._refresh_stickers(only_unnoted=True)))
        row.addWidget(_button("全部", slot=self._refresh_stickers))
        row.addWidget(_button("打开文件夹", slot=self._open_folder))
        row.addStretch(1)
        lay.addLayout(row)

    def _refresh_stickers(self, only_unnoted: bool = False):
        try:
            from furina.services import stickerbook

            rows = stickerbook.items()
            st = stickerbook.stats()
        except Exception as e:  # noqa: BLE001
            self.stk_stats.setText(f"读不到：{e}")
            return
        self.stk_stats.setText(f"共 {st['total']} 张，有备注 {st['noted']} 张；QQ 收藏导入 {st['fav']} 张，她自己收藏 {st['collected']} 张。")
        self.stk_list.clear()
        if only_unnoted:
            rows = [r for r in rows if not r.get("note")]
        for r in reversed(rows[-400:]):
            note = r.get("note") or "（没备注）"
            it = QListWidgetItem(note)
            path = stickerbook.path_of(r)
            if path.is_file():
                pix = QPixmap(str(path))
                if not pix.isNull():
                    it.setIcon(QIcon(pix.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            src = {"fav": "QQ 收藏", "collected": "她收藏的"}.get(r.get("source"), r.get("source") or "")
            it.setToolTip(f"{note}\n来源：{src}{('（' + r['who'] + '）') if r.get('who') else ''}\n用过 {r.get('uses', 0)} 次")
            it.setData(Qt.UserRole, r["id"])
            self.stk_list.addItem(it)

    def _current_sticker(self):
        it = self.stk_list.currentItem()
        return (it, it.data(Qt.UserRole)) if it else (None, None)

    def _edit_note(self):
        from furina.services import stickerbook

        it, sid = self._current_sticker()
        if not sid:
            self.set_status("先选一张表情", "warn")
            return
        item = stickerbook.get(sid) or {}
        text, ok = QInputDialog.getText(self, "改备注", "这张表情表达什么（不超过 24 个字）：", text=item.get("note") or "")
        if ok:
            stickerbook.set_note(sid, text)
            it.setText(stickerbook.clean_note(text) or "（没备注）")
            self.set_status("备注已保存", "ok")

    def _delete_sticker(self):
        from furina.services import stickerbook

        it, sid = self._current_sticker()
        if not sid:
            self.set_status("先选一张表情", "warn")
            return
        if not _ask(self, "删除表情", f"从她的表情包本子里删掉「{it.text()}」？（图片文件也会删除）"):
            return
        stickerbook.remove(sid)
        self.stk_list.takeItem(self.stk_list.row(it))
        self.set_status("已删除", "ok")

    def _open_folder(self):
        from furina.services import stickerbook

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(stickerbook.folder())))

    # ---------------- 读取 ----------------
    def load(self):
        super().load()
        self._refresh_status()
        self._refresh_spend()
        self._refresh_wake()
        self._refresh_stickers()
