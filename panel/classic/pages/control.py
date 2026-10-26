"""状态检测与机器人启动/停止/重启、NapCat 登录（PanelWindow 的 mixin，由 window.py 拆出）。"""
from ..common import *  # noqa: F401,F403
from ..widgets import *  # noqa: F401,F403


class BotControlMixin:
    # ===== 状态检测 =====

    def _powershell(self, script, timeout=30):
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, timeout=timeout, creationflags=_NO_WINDOW,
            )
            raw = result.stdout
            if isinstance(raw, bytes):
                return raw.decode("utf-8", errors="replace")
            return str(raw)
        except Exception:
            return ""

    def check_port(self, port):
        try:
            result = subprocess.run(["netstat", "-ano"], capture_output=True,
                                    timeout=5, creationflags=_NO_WINDOW)
            raw = result.stdout
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            return any(f":{port}" in line and "LISTENING" in line
                       for line in raw.splitlines())
        except Exception:
            return False

    def _find_bot_processes(self, include_cmd=True):
        """只认本项目的 bot.py（按绝对路径匹配命令行），不会误伤其他 Python 程序。"""
        return _v2.bot_pids()

    def _kill_process_ids(self, pids):
        for pid in pids:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=15, creationflags=_NO_WINDOW)
            except Exception:
                pass

    def _kill_bot_processes(self):
        proc = getattr(self, "bot_proc", None)
        if proc is not None:
            try:
                if proc.poll() is None:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   capture_output=True, timeout=15, creationflags=_NO_WINDOW)
            except Exception:
                pass
            self.bot_proc = None
        self._kill_process_ids(self._find_bot_processes(include_cmd=True))
        for _ in range(20):
            if not self._find_bot_processes(include_cmd=True):
                break
            time.sleep(0.3)

    def check_nonebot_running(self):
        return bool(self._find_bot_processes(include_cmd=False))

    def _kill_napcat_processes(self):
        """Explicit stop/account switch only: target this installation's boot tree."""
        _v2.stop_napcat()
        proc = getattr(self, "napcat_proc", None)
        if proc is not None and proc.poll() is None:
            self._kill_process_ids([proc.pid])
        self.napcat_proc = None

    def check_status(self):
        if self.busy or self._status_check_inflight:
            return
        self._status_check_inflight = True
        generation = self._login_generation
        qq = self._read_env_value("BOT_QQ").strip()

        def work():
            try:
                napcat_ok = self.check_port(6099)
                nonebot_ok = self.check_nonebot_running()
                health = query_health(qq)
                # A result from before a start/stop/account switch is stale.
                if generation == self._login_generation and not self.busy:
                    self.signals.running.emit(napcat_ok and nonebot_ok)
                    self.signals.online_state.emit(health)
            finally:
                self._status_check_inflight = False
        threading.Thread(target=work, daemon=True).start()

    def _query_qq_online(self):
        return query_health(self._read_env_value("BOT_QQ").strip())

    def _render_health(self):
        if not hasattr(self, "rail_status_text"):
            return
        health = self._health
        # 侧栏只有约 100px 给文字：用短词，完整说明放在悬停提示里（放不下时 StatusLabel 会以省略号收尾）。
        if not self.bot_running:
            text, color = "机器人未运行", "#ef4444"
            tip = "NapCat（6099）或 NoneBot（8080）没有在运行，点主页「启动机器人」。\n点击查看登录状态"
        elif health.state == "online":
            text, color = "QQ 在线", "#10b981"
            tip = health.detail
        else:
            text = {"offline": "⚠ QQ 离线", "logged_out": "⚠ QQ 未登录",
                    "mismatch": "⚠ 账号不一致"}.get(health.state, "⚠ 状态待确认")
            color = "#f59e0b"
            tip = health.detail + "\n点击查看扫码登录指引"
        self._set_text_color(self.rail_status_dot, color)
        self.rail_status_text.setText(text)
        self._set_text_color(self.rail_status_text, color)
        self.rail_status_text.setToolTip(tip)

    def _on_online_state(self, health):
        previous = self._health.state
        self._health = health
        self._qq_online = True if health.state == "online" else (
            False if health.state in ("offline", "logged_out") else None)
        self._render_health()
        # 机器人本来就停着时 3000 端口当然不通，不算掉线、不弹提示。
        if health.state in ("offline", "mismatch", "logged_out") and self.bot_running:
            self._offline_fail_count += 1
        else:
            self._offline_fail_count = 0
        # Unknown is visible immediately, but never becomes a false recovery.
        if previous != health.state:
            self.add_log("info" if health.state == "online" else "warn", "[连接状态] " + health.detail)
        now = time.monotonic()
        if (self._offline_fail_count >= 4 and
                now - getattr(self, "_last_offline_toast_time", -600.0) >= 600):
            self._last_offline_toast_time = now
            hint = {"offline": "⚠ QQ 已离线：点左下角状态查看处理办法",
                    "logged_out": "⚠ QQ 未登录：点左下角状态查看扫码指引"}.get(health.state, "⚠ " + health.detail)
            self._toast(hint, "#f59e0b")

    @staticmethod
    def _set_text_color(widget, color):
        """只在颜色真的变了才 setStyleSheet。

        状态每隔几秒轮询一次，以前每次都对侧栏标签重设样式表，Qt 会重新解析样式并重排侧栏，
        鼠标正停在侧栏按钮上时就会看到悬停效果闪一下。
        """
        if widget.property("furinaColor") == color:
            return
        widget.setProperty("furinaColor", color)
        widget.setStyleSheet(f"color: {color};")

    def set_running(self, running):
        self.bot_running = running
        if running:
            if hasattr(self, "rail_status_dot"):
                self._set_text_color(self.rail_status_dot, STATUS_OK)
                self.rail_status_text.setText("机器人运行中")
            self.start_btn.setText("停止机器人")
            self.start_btn.configure(
                self._make_icon("stop_btn", "#000000"),
                self._make_icon("stop_btn_hover", "#ffd700"),
                "#c62828", "#212429", "#ffffff", "#ffd700",
            )
            if self.start_time is None:
                self.start_time = time.time()
        else:
            if hasattr(self, "rail_status_dot"):
                self._set_text_color(self.rail_status_dot, STATUS_BAD)
                self.rail_status_text.setText("机器人未运行")
            self.start_btn.setText("启动机器人")
            self.start_btn.configure(
                self._make_icon("play_btn", "#000000"),
                self._make_icon("play_btn_hover", "#ffd700"),
                "#ffd700", "#212429", "#111111", "#ffd700",
            )
            self.start_time = None
        self._render_health()

    def set_busy(self, busy):
        self.busy = busy
        self.start_btn.setEnabled(not busy)

    def update_uptime(self):
        if self.start_time:
            diff = int(time.time() - self.start_time)
            h, rem = divmod(diff, 3600)
            m, s = divmod(rem, 60)
            self.stat_uptime.setText(f"运行时长 {h:02d}:{m:02d}:{s:02d}")
        QTimer.singleShot(1000, self.update_uptime)

    # ===== 机器人控制 =====

    def test_connection(self):
        """点击「芙芙状态·测试」卡：后台探测 NapCat(127.0.0.1:3000)，用浮层/弹窗反馈，不卡界面。"""
        card = getattr(self, "start_card", None)
        if card is not None:
            card.value_label.setText("检测中…")
            card.value_label.setStyleSheet("color: #91a2bc;")

        qq = self._read_env_value("BOT_QQ").strip()

        def work():
            health = query_health(qq)
            ready = self.check_nonebot_running()
            ok = health.state == "online" and ready
            detail = health.detail
            if not ready:
                detail += "\nNoneBot 服务未运行或尚未就绪。"
            self.signals.test_result.emit(ok, "状态检查" if ok else "连接未就绪", detail)
        threading.Thread(target=work, daemon=True).start()

    def _toast(self, text, color="#6ad4ff"):
        """在主界面居中偏上浮一行柔和提示，约 2 秒后淡出（替代系统黑色 tooltip）。"""
        try:
            from PySide6.QtWidgets import QGraphicsOpacityEffect
            old = getattr(self, "_toast_lbl", None)
            if old is not None:
                try:
                    old.deleteLater()
                except Exception:
                    pass
            lbl = QLabel(self)
            lbl.setText(text)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                "background: rgba(13,20,38,0.92); color: %s;"
                "border: 1px solid rgba(255,255,255,0.16); border-radius: 16px;"
                "padding: 11px 26px; font-size: 15px; font-weight: bold;" % color
            )
            lbl.adjustSize()
            x = (self.width() - lbl.width()) // 2
            y = int(self.height() * 0.16)
            lbl.move(max(20, x), y)
            lbl.raise_()
            lbl.show()
            eff = QGraphicsOpacityEffect(lbl)
            eff.setOpacity(0.0)
            lbl.setGraphicsEffect(eff)
            ain = QPropertyAnimation(eff, b"opacity", lbl)
            ain.setDuration(300); ain.setStartValue(0.0); ain.setEndValue(1.0); ain.start()
            self._toast_lbl = lbl
            self._toast_anim = ain

            def fade_out():
                fo = QPropertyAnimation(eff, b"opacity", lbl)
                fo.setDuration(400); fo.setStartValue(1.0); fo.setEndValue(0.0)
                fo.finished.connect(lbl.deleteLater)
                fo.start()
                self._toast_anim = fo
            QTimer.singleShot(1900, fade_out)
        except Exception:
            pass

    def _on_test_done(self, ok, headline, detail):
        card = getattr(self, "start_card", None)
        if card is not None:
            card.value_label.setText("测试")
            card.value_label.setStyleSheet("color: #ffd166;")
        if ok:
            self._toast(f"连接正常 · {detail}", "#3ecf8e")
        else:
            self._toast("连接失败", "#e5484d")
            from PySide6.QtWidgets import QMessageBox
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle(headline)
            box.setText(detail)
            guide = box.addButton("登录助手", QMessageBox.ActionRole)
            box.addButton("知道了", QMessageBox.AcceptRole)
            box.setStyleSheet(
                "QMessageBox{background:#142033;color:#f4f7ff;font-size:13px;}"
                "QMessageBox QLabel{color:#f4f7ff;}"
            )
            box.exec()
            if box.clickedButton() is guide:
                self.open_login_helper()

    def toggle_bot(self):
        if self.busy:
            self.add_log("warn", "[系统] 正在处理上一步操作，请稍候")
            return
        if self.bot_running:
            self.stop_bot()
        else:
            self.start_bot()

    def start_bot(self):
        if self.busy:
            self.add_log("warn", "[系统] 正在处理上一步操作，请稍候")
            return
        self.set_busy(True)
        self._login_generation += 1

        def run():
            try:
                self._start_bot_work()
            except Exception as e:
                self.signals.log.emit("error", f"[错误] 启动失败: {e}")
                self.signals.running.emit(False)
            finally:
                self.signals.busy.emit(False)
        threading.Thread(target=run, daemon=True).start()

    def _napcat_is_running(self):
        return _v2.napcat_running()

    def _start_bot_work(self):
        self.signals.log.emit("info", "[系统] 正在启动机器人（保留已有 QQ 登录会话）...")
        qq = self._read_env_value("BOT_QQ").strip()
        if not qq.isdigit():
            raise ValueError(".env 里的 BOT_QQ 为空或不是纯数字")
        if self._napcat_is_running():
            health = query_health(qq)
            if health.state == "mismatch":
                raise RuntimeError(health.detail + "；请在 QQ 管理中明确切换账号")
            self.signals.log.emit("info", "[系统] 复用已有 NapCat；未登录时请在 WebUI 人工确认，不自动重登")
        else:
            self.napcat_proc = subprocess.Popen(
                ["cmd.exe", "/k", f"launcher-user.bat {qq}"],
                cwd=NAPCAT_DIR, creationflags=_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(8)
        generation = self._login_generation
        if self.check_nonebot_running():
            self.signals.running.emit(self.check_port(6099))
            self.signals.log.emit("info", "[系统] 检测到机器人服务已运行，不重复启动")
            threading.Thread(target=self._monitor_napcat_login, args=(qq, generation), daemon=True).start()
            return
        self._kill_bot_processes()
        for log_path in (BOT_STDOUT_LOG, BOT_STDERR_LOG):
            _trim_log(log_path)
        os.makedirs(os.path.dirname(BOT_STDOUT_LOG), exist_ok=True)
        out_f = open(BOT_STDOUT_LOG, "ab")
        err_f = out_f
        try:
            # 显式把 BOT_QQ 注入机器人进程环境，确保 qq_data.active_qq() 取到当前切换的号；预置 UTF-8 确保解释器底层正确初始化
            run_env = dict(os.environ)
            run_env["BOT_QQ"] = qq
            run_env["PYTHONUTF8"] = "1"
            run_env["PYTHONIOENCODING"] = "utf-8"
            self.bot_proc = subprocess.Popen(
                [PYTHON_EXE, os.path.join(BOT_DIR, "bot.py")],
                cwd=BOT_DIR,
                env=run_env,
                creationflags=_NO_WINDOW,
                stdout=out_f,
                stderr=subprocess.STDOUT,
            )
        finally:
            out_f.close()
        bot_started = False
        for _ in range(30):
            if self.check_port(_v2.bot_port()):
                self.signals.log.emit("success", "[系统] NoneBot2 已启动并监听端口，日志见「详情日志」")
                self.signals.running.emit(True)
                bot_started = True
                break
            if self.bot_proc.poll() is not None:
                break
            time.sleep(1)
        if not bot_started:
            if self.bot_proc.poll() is not None:
                self.signals.log.emit("error", "[错误] NoneBot2 启动后退出，请看 logs\\console.log / 详情日志")
                self.signals.running.emit(False)
                return
            self.signals.log.emit("warn", "[系统] NoneBot2 进程已拉起，但端口尚未就绪，请稍候查看日志")
            self.signals.running.emit(True)
        # 后台监测 NapCat 是否真的登录了该 QQ；未登录/启动失败则弹窗提示扫码
        threading.Thread(target=self._monitor_napcat_login, args=(qq, generation), daemon=True).start()

    def _monitor_napcat_login(self, qq, generation, timeout=120):
        """Only online + matching account is login success; cached UIN is not proof."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if generation != self._login_generation:
                return
            health = query_health(qq)
            if generation != self._login_generation:
                return
            if health.state == "online":
                self.signals.log.emit("success", f"[系统] NapCat 已确认 QQ {qq} 在线")
                self.signals.online_state.emit(health)
                return
            if health.state == "mismatch":
                self.signals.log.emit("error", "[登录检查] " + health.detail)
                self.signals.online_state.emit(health)
                return
            time.sleep(3)
        if generation == self._login_generation:
            self.signals.log.emit("warn", f"[登录检查] 未能确认 QQ {qq} 在线；点左下角状态打开「登录助手」，用该账号扫码即可，无需反复重启")
            self.signals.online_state.emit(health)

    def stop_bot(self):
        if self.busy:
            self.add_log("warn", "[系统] 正在处理上一步操作，请稍候")
            return
        self.set_busy(True)
        self._login_generation += 1

        def run():
            try:
                self._stop_bot_work()
            except Exception as e:
                self.signals.log.emit("error", f"[错误] 停止失败: {e}")
            finally:
                self.signals.busy.emit(False)
        threading.Thread(target=run, daemon=True).start()

    def _stop_bot_work(self):
        self.signals.log.emit("info", "[系统] 正在停止机器人...")
        self._kill_bot_processes()
        self._kill_napcat_processes()
        for _ in range(20):
            if not self.check_nonebot_running() and not self.check_port(6099):
                break
            time.sleep(0.5)
        self.signals.log.emit("success", "[系统] 机器人已停止")
        self.signals.running.emit(False)

    def restart_bot(self, restart_napcat=False):
        if self.busy:
            self.add_log("warn", "[系统] 正在处理上一步操作，请稍候")
            return
        self.set_busy(True)
        self._login_generation += 1
        self.signals.log.emit("info", "[系统] 正在重启机器人...")

        def run():
            try:
                if restart_napcat:
                    self._stop_bot_work()
                else:
                    self._kill_bot_processes()
                    self.signals.log.emit("info", "[系统] 仅重启 NoneBot，保留 NapCat 登录会话")
                time.sleep(2)
                self._start_bot_work()
            except Exception as e:
                self.signals.log.emit("error", f"[错误] 重启失败: {e}")
                self.signals.running.emit(False)
            finally:
                self.signals.busy.emit(False)
        threading.Thread(target=run, daemon=True).start()

    def restart_napcat_for_login(self):
        """登录助手里的「重启 NapCat」，只在用户点击时执行。

        - 只重启机器人用的 NapCat / QQ 进程树：不碰用户自己打开的 QQ，也不停 NoneBot；
        - 先带 QQ 号快速登录（免扫码）：被踢下线后很多时候凭据还能用，60 秒内上线就不用扫码；
        - 快速登录不成功再不带参数重启，直接生成新二维码，用机器人账号扫码。
        逻辑在 furina/services/napcat_recover.py（掉线后的自动恢复也用它）。
        返回 False 表示有别的操作正在进行，本次没有执行。
        """
        if self.busy:
            self.add_log("warn", "[系统] 正在处理上一步操作，请稍候")
            return False
        self.set_busy(True)
        self._login_generation += 1
        generation = self._login_generation
        qq = self._read_env_value("BOT_QQ").strip()

        def run():
            from furina.services import napcat_recover

            result = ""
            try:
                result = napcat_recover.relogin(
                    qq, reason="登录助手", on_event=lambda level, msg: self.signals.log.emit(level, "[登录] " + msg))
            except Exception as e:
                self.signals.log.emit("error", f"[登录] 重启 NapCat 失败：{e}")
            finally:
                self.signals.busy.emit(False)
            if result in ("quick_ok", "qr", "qr_missing"):
                self._monitor_napcat_login(qq, generation, timeout=30 if result == "quick_ok" else 600)

        threading.Thread(target=run, daemon=True).start()
        return True

    def _wait_for_new_qrcode(self, since, timeout=60):
        """等 NapCat 写出比 since 更新的登录二维码（E:\\NapCat\\cache\\qrcode.png）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if os.path.getmtime(NAPCAT_QRCODE_PATH) >= since:
                    return True
            except OSError:
                pass
            time.sleep(1)
        return False
