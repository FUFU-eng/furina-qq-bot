"""壁纸：自定义背景、壁纸库与轮播、Logo 取色（PanelWindow 的 mixin，由 window.py 拆出）。"""
from ..common import *  # noqa: F401,F403
from ..widgets import *  # noqa: F401,F403


class WallpaperMixin:
    def _load_custom_background(self):
        self.wallpaper_library, self.wallpaper_list, self.wallpaper_interval = self._load_wallpaper_library()
        if self.wallpaper_list:
            self.wallpaper_index = 0
            self._apply_custom_background(self.wallpaper_list[0])
            self._start_wallpaper_timer()
            # 启动几秒后在后台预先生成壁纸库缩略图，第一次打开设置也不用等
            QTimer.singleShot(4000, lambda: self._load_wallpaper_art(list(self.wallpaper_library)))
            return
        try:
            data = _v2.read_doc(CUSTOM_BG_CONFIG) or {}
            path = data.get("path", "")
            if path and os.path.exists(path):
                self.wallpaper_library = [path]
                self.wallpaper_list = [path]
                self._apply_custom_background(path)
        except Exception:
            pass

    def _load_wallpaper_library(self):
        """返回 (壁纸库, 正在轮播的列表, 间隔秒数)。旧文件没有 playlist 字段时轮播整个壁纸库。"""
        data = self._load_json(WALLPAPER_LIBRARY_PATH, {})
        if not isinstance(data, dict):
            data = {}
        raw = data.get("wallpapers")
        wallpapers = [p for p in (raw if isinstance(raw, list) else []) if isinstance(p, str) and os.path.exists(p)]
        raw = data.get("playlist")
        playlist = [p for p in (raw if isinstance(raw, list) else []) if isinstance(p, str) and p in wallpapers]
        try:
            interval = int(data.get("interval_seconds", 30) or 30)
        except Exception:
            interval = 30
        if interval < 1:
            interval = 1
        return wallpapers, playlist or list(wallpapers), interval

    def _save_wallpaper_library(self):
        try:
            _atomic_write_json(WALLPAPER_LIBRARY_PATH, {
                "interval_seconds": self.wallpaper_interval,
                "wallpapers": self.wallpaper_library,
                "playlist": self.wallpaper_list,
            })
        except Exception as e:
            self.add_log("error", f"[错误] 保存壁纸库失败: {e}")

    def _start_wallpaper_timer(self):
        if not hasattr(self, "wallpaper_timer"):
            return
        self.wallpaper_timer.stop()
        if len(self.wallpaper_list) > 1:
            self.wallpaper_timer.start(max(1, self.wallpaper_interval) * 1000)

    def _stop_wallpaper_timer(self):
        if hasattr(self, "wallpaper_timer"):
            self.wallpaper_timer.stop()

    def _next_wallpaper(self):
        if not self.wallpaper_list:
            return
        if self._games_page_active():
            # 游戏管理页不显示壁纸：暂停轮播，避免在内嵌控制台下方重绘。
            self._start_wallpaper_timer()
            return
        self.wallpaper_index = (self.wallpaper_index + 1) % len(self.wallpaper_list)
        self._apply_custom_background(self.wallpaper_list[self.wallpaper_index])
        self._start_wallpaper_timer()

    def _apply_custom_background(self, path):
        # 直接替换壁纸，避免切换壁纸时创建全窗口离屏动画导致页面卡顿。
        self.bg_layer.set_media(path, self._take_prefetched_background(path))
        self.current_wallpaper_path = path
        self.art.set_custom_background(path)
        self._layout_background()
        self._update_logo_from_background()
        # 首页继续使用原始主视觉；功能页在同一张壁纸上叠加高可读性的半透明内容层。
        self._apply_page_mode(getattr(self, "_active_tab", 0))
        self._prefetch_next_background()

    def _take_prefetched_background(self, path):
        cached, self._bg_prefetch = self._bg_prefetch, None
        if cached and cached[0] == path and cached[1] == _file_stamp(path):
            return cached[2]
        return None

    def _prefetch_next_background(self):
        """轮播时提前在后台解码下一张壁纸，到点直接换上，切换时不再卡住界面。"""
        if len(self.wallpaper_list) < 2 or not hasattr(self, "signals"):
            return
        path = self.wallpaper_list[(self.wallpaper_index + 1) % len(self.wallpaper_list)]
        if os.path.splitext(path)[1].lower() in VIDEO_EXTS + BackgroundLayer.ANIMATED_EXT:
            return
        size = _screen_cover_px()

        def work():
            try:
                img = _read_image_fit(path, *size, expand=True)
            except Exception:
                return
            self.signals.bg_prefetched.emit(path, img)
        threading.Thread(target=work, daemon=True).start()

    def _on_bg_prefetched(self, path, img):
        if img is not None and not img.isNull():
            self._bg_prefetch = (path, _file_stamp(path), img)

    def _save_custom_background(self, path):
        try:
            os.makedirs(CUSTOM_BG_DIR, exist_ok=True)
            ext = os.path.splitext(path)[1].lower() or ".png"
            dest = os.path.join(CUSTOM_BG_DIR, f"wallpaper_{int(time.time() * 1000)}{ext}")
            shutil.copy2(path, dest)
            return dest
        except Exception:
            return path

    def reset_background(self):
        self._stop_wallpaper_timer()
        self.wallpaper_library = []
        self.wallpaper_list = []
        self.wallpaper_index = 0
        self.current_wallpaper_path = ""
        self.bg_layer.clear_media()
        self.art.clear_custom_background()
        self.detail_panel.setStyleSheet("")
        if hasattr(self, "window_body"):
            self.window_body.setStyleSheet("")
        # reset 会先清掉页面内联样式；强制重套当前模式，避免首页被默认深色背景遮住。
        self._page_mode_home = None
        self.load_bg(0)
        self._apply_page_mode(getattr(self, "_active_tab", 0), force=True)
        try:
            for p in (CUSTOM_BG_CONFIG, WALLPAPER_LIBRARY_PATH):
                _v2.delete_doc(p)
        except Exception:
            pass
        self.add_log("info", "[系统] 已恢复默认轮播背景")

    def open_wallpaper_library(self):
        self._open_settings("appearance")

    def _refresh_wallpaper_list(self):
        widget = getattr(self, "wallpaper_list_widget", None)
        if widget is None:
            return
        playing = set(self.wallpaper_list)
        # 只轮播壁纸库的一部分时，用 ▶ 标出正在播放的壁纸
        subset = 0 < len(playing) < len(self.wallpaper_library)
        library = set(self.wallpaper_library)
        for stale in [p for p in self._wallpaper_art_cache if p not in library]:
            del self._wallpaper_art_cache[stale]
        widget.clear()
        missing = []
        for p in self.wallpaper_library:
            name = os.path.basename(p)
            on = p in playing
            item = QListWidgetItem(f"▶ {name}" if subset and on else name)
            icon = self._wallpaper_thumb(p)
            if icon is None:
                # 缩略图在后台生成，先放占位图、生成后自动换上；打开设置不再等着解码大图
                icon = self._wallpaper_placeholder()
                missing.append(p)
            item.setIcon(icon)
            item.setData(Qt.UserRole, p)
            item.setToolTip(f"{p}\n{'正在显示 / 轮播' if on else '未在轮播'}")
            widget.addItem(item)
        if missing:
            self._load_wallpaper_art(missing)

    def _wallpaper_thumb(self, path):
        """视频画一张占位图；图片返回缓存的缩略图，还没生成时返回 None。"""
        if os.path.splitext(path)[1].lower() in VIDEO_EXTS:
            pix = QPixmap(168, 104)
            pix.fill(QColor("#1a2740"))
            painter = QPainter(pix)
            painter.setPen(QColor("#8b9bb6"))
            painter.drawText(pix.rect(), Qt.AlignCenter, "视频")
            painter.end()
            return QIcon(pix)
        art = self._cached_wallpaper_art(path)
        return art[0] if art else None

    def _cached_wallpaper_art(self, path):
        entry = self._wallpaper_art_cache.get(path)
        if entry and entry[0] == _file_stamp(path):
            return entry[1], entry[2]
        return None

    def _wallpaper_placeholder(self):
        icon = getattr(self, "_wallpaper_placeholder_icon", None)
        if icon is None:
            pix = QPixmap(168, 104)
            pix.fill(QColor("#16223a"))
            painter = QPainter(pix)
            painter.setPen(QColor("#6b7c99"))
            painter.drawText(pix.rect(), Qt.AlignCenter, "加载中…")
            painter.end()
            icon = self._wallpaper_placeholder_icon = QIcon(pix)
        return icon

    def _load_wallpaper_art(self, paths):
        """在后台线程为壁纸生成缩略图和预览：每张只解码一次，而且只解码到显示需要的尺寸。"""
        if not hasattr(self, "signals"):
            return
        todo = [p for p in dict.fromkeys(paths)
                if p not in self._wallpaper_art_inflight
                and os.path.splitext(p)[1].lower() not in VIDEO_EXTS
                and self._cached_wallpaper_art(p) is None]
        if not todo:
            return
        self._wallpaper_art_inflight.update(todo)
        dpr = self.devicePixelRatioF() or 1.0
        width = max(WALLPAPER_THUMB_PX[0], int(WALLPAPER_PREVIEW_SIZE[0] * dpr))
        height = max(WALLPAPER_THUMB_PX[1], int(WALLPAPER_PREVIEW_SIZE[1] * dpr))

        def work():
            for p in todo:
                try:
                    img = _read_image_fit(p, width, height)
                except Exception:
                    img = QImage()
                self.signals.wallpaper_art.emit(p, img)
        threading.Thread(target=work, daemon=True).start()

    def _on_wallpaper_art(self, path, img):
        self._wallpaper_art_inflight.discard(path)
        if img is None or img.isNull():
            icon, preview = QIcon(), QPixmap()
        else:
            tw, th = WALLPAPER_THUMB_PX
            thumb = img.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation) \
                if img.width() > tw or img.height() > th else img
            icon = QIcon(QPixmap.fromImage(thumb))
            preview = QPixmap.fromImage(img)
        self._wallpaper_art_cache[path] = (_file_stamp(path), icon, preview)
        widget = getattr(self, "wallpaper_list_widget", None)
        if widget is not None:
            try:
                for i in range(widget.count()):
                    item = widget.item(i)
                    if item.data(Qt.UserRole) == path:
                        item.setIcon(icon)
            except RuntimeError:  # 设置窗口已关闭，控件已销毁
                pass
        if path == self._wallpaper_preview_wanted:
            self._wallpaper_preview_wanted = ""
            label = getattr(self, "wallpaper_preview_label", None)
            if label is not None:
                try:
                    self._set_wallpaper_preview(label, preview)
                except RuntimeError:
                    pass

    def _show_wallpaper_preview(self, row):
        label = getattr(self, "wallpaper_preview_label", None)
        if label is None or row < 0 or row >= len(self.wallpaper_library):
            return
        path = self.wallpaper_library[row]
        if os.path.splitext(path)[1].lower() in VIDEO_EXTS:
            self._wallpaper_preview_wanted = ""
            label.setPixmap(QPixmap())
            label.setText("视频壁纸")
            return
        art = self._cached_wallpaper_art(path)
        if art is None:
            self._wallpaper_preview_wanted = path
            label.setPixmap(QPixmap())
            label.setText("正在生成预览…")
            self._load_wallpaper_art([path])
            return
        self._wallpaper_preview_wanted = ""
        self._set_wallpaper_preview(label, art[1])

    def _set_wallpaper_preview(self, label, pix):
        if pix.isNull():
            label.setPixmap(QPixmap())
            label.setText("无法预览")
            return
        dpr = label.devicePixelRatioF() or 1.0
        scaled = pix.scaled(int((label.width() - 2) * dpr), int((label.height() - 2) * dpr),
                            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        label.setPixmap(scaled)

    def _selected_wallpaper_rows(self):
        widget = getattr(self, "wallpaper_list_widget", None)
        if widget is None:
            return []
        rows = sorted({widget.row(it) for it in widget.selectedItems()})
        return [r for r in rows if 0 <= r < len(self.wallpaper_library)]

    def _add_wallpaper_to_library(self, parent=None):
        from PySide6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(
            parent or self, "添加壁纸（可多选）", "",
            "壁纸文件 (*.jpg *.jpeg *.png *.bmp *.gif *.webp *.mp4 *.mkv *.webm *.avi *.mov *.m4v)"
        )
        if not paths:
            return
        was_empty = not self.wallpaper_list
        added = [self._save_custom_background(path) for path in paths]
        self.wallpaper_library.extend(added)
        self.wallpaper_list.extend(added)
        self._save_wallpaper_library()
        self._refresh_wallpaper_list()
        if was_empty:
            self.wallpaper_index = 0
            self._apply_custom_background(self.wallpaper_list[0])
        self._start_wallpaper_timer()
        self.add_log("success", f"[壁纸库] 已添加 {len(added)} 张壁纸")

    def _remove_selected_wallpaper(self):
        rows = self._selected_wallpaper_rows()
        if not rows:
            self.add_log("warn", "[壁纸库] 请先选择要删除的壁纸")
            return
        removed = {self.wallpaper_library[r] for r in rows}
        showing = self.current_wallpaper_path
        self.wallpaper_library = [p for p in self.wallpaper_library if p not in removed]
        self.wallpaper_list = [p for p in self.wallpaper_list if p not in removed]
        if not self.wallpaper_library:
            self.reset_background()
            self._refresh_wallpaper_list()
            self.add_log("info", f"[壁纸库] 已删除 {len(removed)} 张壁纸，恢复默认背景")
            return
        if not self.wallpaper_list:
            self.wallpaper_list = list(self.wallpaper_library)
        self._save_wallpaper_library()
        self._refresh_wallpaper_list()
        if showing not in self.wallpaper_list:
            self.wallpaper_index = 0
            self._apply_custom_background(self.wallpaper_list[0])
        self._start_wallpaper_timer()
        self.add_log("info", f"[壁纸库] 已删除 {len(removed)} 张壁纸")

    def _apply_selected_wallpaper(self):
        rows = self._selected_wallpaper_rows()
        if not rows:
            self.add_log("warn", "[壁纸库] 请先选择壁纸")
            return
        # 按壁纸库顺序组成轮播列表（单选则为单张壁纸）；壁纸库本身保持不变
        ordered = [self.wallpaper_library[r] for r in rows]
        self.wallpaper_list = ordered
        self.wallpaper_index = 0
        self._save_wallpaper_library()
        self._refresh_wallpaper_list()
        self._apply_custom_background(ordered[0])
        self._start_wallpaper_timer()
        if len(ordered) > 1:
            self.add_log("success", f"[壁纸库] 已应用 {len(ordered)} 张壁纸循环播放")
        else:
            self.add_log("success", f"[壁纸库] 已应用单张壁纸：{os.path.basename(ordered[0])}")

    def _clear_wallpaper_library(self):
        self.reset_background()
        self._refresh_wallpaper_list()
        self.add_log("info", "[壁纸库] 已清空")

    # ===== 壁纸 =====

    def load_bg(self, index):
        self.bg_index = index
        self.art.load_bg(index)
        self._update_logo_color()

    def _update_logo_from_background(self):
        if not hasattr(self, "furina_logo_label") or not hasattr(self, "bg_layer"):
            return
        try:
            pt = self.furina_logo_label.mapTo(self, QPoint(0, 0))
            rect = QRect(pt, self.furina_logo_label.size()).intersected(self.bg_layer.rect())
            if rect.width() <= 0 or rect.height() <= 0:
                return
            pix = self.bg_layer.grab(rect)
            img = pix.toImage().scaled(8, 8, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            total = 0
            count = 0
            for yy in range(img.height()):
                for xx in range(img.width()):
                    total += img.pixelColor(xx, yy).lightness()
                    count += 1
            self.art._bg_brightness = total / count if count else 0
            self._update_logo_color()
        except Exception:
            pass

    def _render_logo_path(self, svg_path):
        renderer = QSvgRenderer(svg_path)
        if not renderer.isValid() or not hasattr(self, 'furina_logo_label'):
            return
        target_w = 220
        target_h = int(target_w * 1006 / 2000)
        pm = QPixmap(target_w * 2, target_h * 2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        renderer.render(p)
        p.end()
        pm.setDevicePixelRatio(2)
        self.furina_logo_label.setPixmap(pm)
        self._current_logo_path = svg_path

    def _fade_logo(self, start, end):
        self._logo_anim.stop()
        self._logo_anim.setDuration(180)
        self._logo_anim.setStartValue(start)
        self._logo_anim.setEndValue(end)
        self._logo_anim.start()

    def _update_logo_color(self):
        """根据背景亮度切换logo颜色，带淡出/淡入过渡。"""
        brightness = getattr(self.art, '_bg_brightness', 0)
        use_dark = brightness > 130
        svg_path = LOGO_SVG_DARK if use_dark else LOGO_SVG
        if not hasattr(self, 'furina_logo_label') or not hasattr(self, '_current_logo_path'):
            return
        if svg_path == self._current_logo_path:
            return

        def apply_new():
            self._render_logo_path(svg_path)
            self._fade_logo(0.0, 1.0)

        if self._current_logo_path is None:
            self._render_logo_path(svg_path)
            self._logo_effect.setOpacity(1.0)
        else:
            self._fade_logo(1.0, 0.0)
            QTimer.singleShot(180, apply_new)
