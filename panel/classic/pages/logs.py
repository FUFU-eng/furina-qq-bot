"""日志读取与显示（PanelWindow 的 mixin，由 window.py 拆出）。"""
from ..common import *  # noqa: F401,F403
from ..widgets import *  # noqa: F401,F403


class LogsMixin:
    # ===== 日志 =====

    def _append_log_batch(self, box, lines, slack):
        """一次性追加多行：暂停重绘、最后只滚动一次，避免逐行排版。"""
        if not lines:
            return
        sb = box.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - slack
        box.setUpdatesEnabled(False)
        try:
            for line, color in lines:
                box.appendHtml(f'<span style="color:{color};">{escape(line)}</span>')
        finally:
            box.setUpdatesEnabled(True)
        if at_bottom:
            sb.setValue(sb.maximum())

    def _append_log(self, line, color):
        sb = self.log_text.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        self.log_text.appendHtml(f'<span style="color:{color};">{escape(line)}</span>')
        if at_bottom:
            sb.setValue(sb.maximum())

    def refresh_logs(self):
        if not hasattr(self, "log_text"):
            return
        # 日志页不在前台时不读文件、不往文本框里塞内容（切回来时再一次性补上最近一段），
        # 否则机器人日志多的时候，每 2 秒一次的追加排版会让整个面板周期性卡一下。
        if not self.log_text.isVisible():
            return
        pos = getattr(self, "_log_file_pos", 0)
        try:
            size = os.path.getsize(BOT_STDOUT_LOG)
        except Exception:
            return
        if size < pos:
            pos = 0
        if size - pos > 240000:
            pos = size - 48000
        try:
            with open(BOT_STDOUT_LOG, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(pos)
                chunk = f.read()
                # 注意：tell() 必须在 with 块内、文件关闭前调用，否则 closed file 报错被吞，日志永远读不出来
                self._log_file_pos = f.tell()
        except Exception:
            return
        lines = []
        for raw in chunk.splitlines():
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            if "ERROR" in line or "CRITICAL" in line:
                color = "#e5484d"
            elif "WARNING" in line or "WARN" in line:
                color = "#ffd166"
            elif "SUCCESS" in line:
                color = "#3ecf8e"
            else:
                color = "#cbd5e1"
            lines.append((line, color))
        self._append_log_batch(self.log_text, lines[-1500:], 4)
        self._refresh_napcat_log()

    def _find_napcat_log(self):
        """NapCat 文件日志目录下最新的 .log（文件名可能按日期 / QQ 变化，自动跟随）。"""
        try:
            files = [
                os.path.join(NAPCAT_LOG_DIR, x)
                for x in os.listdir(NAPCAT_LOG_DIR)
                if x.lower().endswith(".log")
            ]
            if not files:
                return None
            return max(files, key=lambda p: os.path.getmtime(p))
        except Exception:
            return None

    def _refresh_napcat_log(self):
        if not hasattr(self, "napcat_text"):
            return
        now = time.monotonic()
        if now - getattr(self, "_napcat_find_ts", 0.0) > 30 or not getattr(self, "_napcat_log_path", None):
            self._napcat_find_ts = now
            self._napcat_found = self._find_napcat_log()
        path = getattr(self, "_napcat_found", None)
        if not path:
            return  # NapCat 还没开文件日志 / 未重启，保持提示文案
        if path != getattr(self, "_napcat_log_path", None):
            self._napcat_log_path = path
            try:  # 跨天 / 重启换文件：只先读尾部一小段
                self._napcat_log_pos = max(0, os.path.getsize(path) - 12000)
            except Exception:
                self._napcat_log_pos = 0
            if hasattr(self, "napcat_status"):
                self.napcat_status.setText(os.path.basename(path))
        pos = getattr(self, "_napcat_log_pos", 0)
        try:
            size = os.path.getsize(path)
        except Exception:
            return
        if size < pos:
            pos = 0
        if size - pos > 120000:
            pos = size - 24000
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(pos)
                chunk = f.read()
                self._napcat_log_pos = f.tell()
        except Exception:
            return
        import re
        ansi = re.compile(r"\x1b\[[0-9;]*m")
        batch = []
        for raw in chunk.splitlines():
            line = ansi.sub("", raw).rstrip()
            if not line.strip():
                continue
            low = line.lower()
            if "error" in low or "错误" in line or "失败" in line:
                color = "#e5484d"
            elif "warn" in low or "offline" in low or "掉线" in line or "失效" in line or "下线" in line:
                color = "#ffd166"
            elif "online" in low or "上线" in low or "success" in low:
                color = "#3ecf8e"
            else:
                color = "#a9b7cc"
            batch.append((line, color))
        if batch and hasattr(self, "napcat_text"):
            self._append_log_batch(self.napcat_text, batch[-1000:], 40)

    def _append_napcat_line(self, line, color):
        if not hasattr(self, "napcat_text"):
            return
        sb = self.napcat_text.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 40
        self.napcat_text.appendHtml(f'<span style="color:{color};">{escape(line)}</span>')
        if at_bottom:
            sb.setValue(sb.maximum())

    def add_log(self, log_type, message):
        # 「详情日志」只展示机器人运行日志（refresh_logs 从机器人日志文件增量读取）。
        # 面板自身的动作日志（启动/停止/重启/连接测试等）不再混入这个框，避免喧宾夺主。
        return

    def clear_log(self):
        if not hasattr(self, "log_text"):
            return
        self.log_text.clear()
        self._log_file_pos = self._bot_log_size()
        if hasattr(self, "napcat_text"):
            self.napcat_text.clear()
        self.add_log("info", "[系统] 日志已清空")
