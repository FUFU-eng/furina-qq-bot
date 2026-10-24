"""经典面板的自绘小控件：状态卡、背景层、启动按钮、仪表卡、动画条、右侧视觉区。"""
from .common import *  # noqa: F401,F403


class StatusCard(QWidget):
    """左侧角色卡：当前背景缩略图 + 暗色蒙层 + 角色名与运行状态。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(186)
        self.bg_index = 0
        self.pixmap = QPixmap()
        self.status_text = "机器人未运行"
        self.setCursor(Qt.PointingHandCursor)

    def set_bg(self, index):
        self.bg_index = index
        self.pixmap = QPixmap(BG_FILES[index])
        self.update()

    def set_status(self, running):
        self.status_text = "机器人运行中" if running else "机器人未运行"
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        w = self.width()
        h = self.height()

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        painter.setClipPath(path)

        if not self.pixmap.isNull():
            scaled = self.pixmap.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            painter.drawPixmap(0, 0, scaled)

        painter.fillRect(self.rect(), QColor(8, 12, 28, 120))

        bottom = QLinearGradient(0, h - 84, 0, h)
        bottom.setColorAt(0.0, QColor(8, 12, 28, 0))
        bottom.setColorAt(1.0, QColor(6, 9, 22, 235))
        painter.fillRect(QRect(0, h - 84, w, 84), bottom)

        painter.setPen(QColor(255, 255, 255))
        font = QFont("Microsoft YaHei", 21, QFont.Bold)
        painter.setFont(font)
        painter.drawText(QRect(16, h - 62, w - 32, 34), Qt.AlignLeft | Qt.AlignVCenter, "芙宁娜")

        painter.setPen(QColor(160, 200, 255))
        font2 = QFont("Microsoft YaHei", 11)
        painter.setFont(font2)
        painter.drawText(QRect(16, h - 28, w - 32, 22), Qt.AlignLeft | Qt.AlignVCenter, self.status_text)

        painter.setPen(QColor(255, 209, 102))
        font3 = QFont("Microsoft YaHei", 9)
        painter.setFont(font3)
        painter.drawText(QRect(w - 90, h - 30, 74, 20), Qt.AlignRight | Qt.AlignVCenter, "v2.0.0")

        painter.setClipping(False)


class BackgroundLayer(QWidget):
    """全局背景层：支持静态图、GIF/WebP 动态壁纸和视频壁纸。"""

    VIDEO_EXT = (".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v")
    ANIMATED_EXT = (".gif", ".webp")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.custom_pixmap = QPixmap()
        self.fallback_pixmap = QPixmap()
        self._scaled_cache = (None, QPixmap())  # (图片+尺寸, 缩放好的图)
        self.using_fallback = False
        self.is_active = False

        self.video_widget = QVideoWidget(self)
        self.video_widget.hide()
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video_widget)
        self.player.mediaStatusChanged.connect(self._video_status)

        self.movie_label = QLabel(self)
        self.movie_label.setScaledContents(True)
        self.movie_label.hide()
        self.movie = None

        # 壁纸层不再使用全窗口 QGraphicsOpacityEffect。该效果会触发离屏合成，
        # 在切页、滚动和动态壁纸同时存在时容易造成明显卡顿。功能页的透明度
        # 交给下方的 detailPanel / stack 遮罩处理，视觉效果一致但渲染成本更低。

        self.hide()

    def fade_switch(self, path):
        """切换壁纸。页面切换要求直接、利落，避免延迟淡入造成卡顿。"""
        return self.set_media(path)

    def _apply_faded_media(self, path):
        # 兼容旧调用方；现在统一采用即时切换。
        return self.set_media(path)

    def set_fallback_background(self, path):
        """为功能页提供内置壁纸兜底；不改变自定义壁纸的活动状态。"""
        pix = QPixmap(path)
        if pix.isNull():
            return False
        self.fallback_pixmap = pix
        self.using_fallback = True
        self.show()
        self.update()
        return True

    def clear_fallback_background(self):
        self.fallback_pixmap = QPixmap()
        self.using_fallback = False
        if not self.is_active:
            self.hide()
        self.update()

    def set_media(self, path, image=None):
        """image：已在后台按屏幕尺寸解码好的 QImage（轮播预取），没有时在这里解码。"""
        ext = os.path.splitext(path)[1].lower()
        self._stop_media()
        self.clear_fallback_background()
        if ext in self.VIDEO_EXT:
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.play()
            self.video_widget.show()
            self.show()
            self.is_active = True
            self._resize_children()
            return True
        if ext in self.ANIMATED_EXT:
            self.movie = QMovie(path)
            if not self.movie.isValid():
                return False
            self.movie_label.setMovie(self.movie)
            self.movie_label.show()
            self.movie.start()
            self.show()
            self.is_active = True
            self._resize_children()
            return True
        if image is None or image.isNull():
            # 只解码到能铺满屏幕的尺寸：超大壁纸不再整张解码（耗时、内存都降一个数量级），画质不变
            image = _read_image_fit(path, *_screen_cover_px(), expand=True)
        pix = QPixmap.fromImage(image)
        if pix.isNull():
            return False
        self.custom_pixmap = pix
        self.show()
        self.update()
        self.is_active = True
        self._resize_children()
        return True

    def clear_media(self):
        self._stop_media()
        self.is_active = False
        self.clear_fallback_background()
        self.hide()

    def _stop_media(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.video_widget.hide()
        if self.movie is not None:
            self.movie.stop()
            self.movie_label.setMovie(None)
            self.movie = None
        self.movie_label.hide()
        self.custom_pixmap = QPixmap()
        self._scaled_cache = (None, QPixmap())

    def _video_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.player.play()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_dwm_round()

    def _apply_dwm_round(self):
        try:
            hwnd = int(self.winId())
            attr = 33  # DWMWA_WINDOW_CORNER_PREFERENCE
            value = 2  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(ctypes.c_int(value)), ctypes.sizeof(ctypes.c_int)
            )
        except Exception:
            pass

    def resizeEvent(self, event):
        self._resize_children()
        super().resizeEvent(event)

    def _resize_children(self):
        rect = self.rect()
        self.video_widget.setGeometry(rect)
        self.movie_label.setGeometry(rect)

    def paintEvent(self, event):
        pixmap = self.custom_pixmap if not self.custom_pixmap.isNull() else self.fallback_pixmap
        if pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return
        clip = QPainterPath()
        clip.addRoundedRect(0, 0, w, h, 16, 16)
        painter.setClipPath(clip)
        # 高分屏按设备像素比渲染，避免壁纸被二次放大发虚
        dpr = self.devicePixelRatioF() or 1.0
        key = (pixmap.cacheKey(), w, h, dpr)
        if self._scaled_cache[0] != key:
            # 缩放结果按「图片 + 尺寸」缓存；以前每次重绘（按钮悬停、每秒刷新的计时）都把原图整张重新缩放
            scaled = pixmap.scaled(int(round(w * dpr)), int(round(h * dpr)),
                                   Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            self._scaled_cache = (key, scaled)
        scaled = self._scaled_cache[1]
        dw = scaled.width() / dpr
        dh = scaled.height() / dpr
        painter.drawPixmap(int(round((w - dw) / 2)), int(round((h - dh) / 2)), scaled)


class StartButton(QWidget):
    """启动按钮：背景、图标、文字全部自绘，悬停时颜色平滑过渡。

    旧实现在悬停动画的每一帧都对按钮和文字标签 setStyleSheet、并重新缩放图标——
    每帧触发一次样式重解析 + 阴影离屏重绘，鼠标移上去会明显闪烁/掉帧。
    现在动画帧只调用 update() 重画这一个控件；图标按 (图标, 设备像素比) 缓存。
    """

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("startBtn")
        self.setFixedSize(188, 56)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self._hover = False
        self._text = "启动机器人"
        self._normal_icon = QIcon()
        self._hover_icon = QIcon()
        self._colors = (QColor("#ffd700"), QColor("#212429"), QColor("#111111"), QColor("#ffd700"))
        self._pix_cache = {}
        self._hover_progress = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_hover_progress)
        self._font = QFont("Microsoft YaHei")
        self._font.setPixelSize(18)
        self._font.setBold(True)

        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setBlurRadius(12)
        self._glow.setOffset(0, 4)
        self._glow.setColor(QColor(0, 0, 0, 90))
        self.setGraphicsEffect(self._glow)

    @staticmethod
    def _mix(c1, c2, t):
        return QColor(
            round(c1.red() + (c2.red() - c1.red()) * t),
            round(c1.green() + (c2.green() - c1.green()) * t),
            round(c1.blue() + (c2.blue() - c1.blue()) * t),
        )

    def configure(self, normal_icon, hover_icon, normal_bg, hover_bg, normal_fg, hover_fg):
        colors = (QColor(normal_bg), QColor(hover_bg), QColor(normal_fg), QColor(hover_fg))
        same = (normal_icon is self._normal_icon and hover_icon is self._hover_icon
                and [c.rgb() for c in colors] == [c.rgb() for c in self._colors])
        if same:
            return  # 状态轮询每几秒会重复调用：没变化就什么都不做
        self._normal_icon, self._hover_icon, self._colors = normal_icon, hover_icon, colors
        self._pix_cache.clear()
        self.update()

    def _on_hover_progress(self, value):
        self._hover_progress = float(value)
        self.update()

    def _pixmap(self, icon):
        dpr = self.devicePixelRatioF() or 1.0
        key = (icon.cacheKey(), dpr)
        pix = self._pix_cache.get(key)
        if pix is None:
            pix = icon.pixmap(64, 64).scaled(int(34 * dpr), int(34 * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pix.setDevicePixelRatio(dpr)
            self._pix_cache[key] = pix
        return pix

    def paintEvent(self, event):
        t = self._hover_progress if self.isEnabled() else 0.0
        nb, hb, nf, hf = self._colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._mix(nb, hb, t))
        painter.drawRoundedRect(QRectF(self.rect()), 28, 28)
        icon = self._hover_icon if t >= 0.5 else self._normal_icon
        if not icon.isNull():
            painter.drawPixmap(10, (self.height() - 34) // 2, self._pixmap(icon))
        painter.setPen(self._mix(nf, hf, t))
        painter.setFont(self._font)
        painter.drawText(QRect(58, 0, self.width() - 58 - 16, self.height()), Qt.AlignLeft | Qt.AlignVCenter, self._text)
        painter.end()

    def setText(self, text):
        if text != self._text:
            self._text = text
            self.update()

    def text(self):
        return self._text

    def _animate_to(self, end):
        self._anim.stop()
        self._anim.setStartValue(self._hover_progress)
        self._anim.setEndValue(end)
        self._anim.start()

    def enterEvent(self, event):
        self._hover = True
        self._animate_to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._animate_to(0.0)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class StatusLabel(QLabel):
    """侧栏底部的状态文字：放不下时以省略号收尾（完整内容看悬停提示），并且可以点击。"""
    clicked = Signal()

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setMinimumWidth(1)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):  # noqa: N802 - 保持 Qt 的方法名，外部照常调用 setText
        self._full_text = text or ""
        self._apply_elide()

    def text(self):
        return self._full_text

    def _apply_elide(self):
        shown = self._full_text
        width = self.contentsRect().width()
        metrics = self.fontMetrics()
        if width > 8 and metrics.horizontalAdvance(shown) > width:
            shown = metrics.elidedText(shown, Qt.ElideRight, width)
        QLabel.setText(self, shown)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elide()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class HoverButton(QPushButton):
    """带正常/悬停两套图标与样式的按钮。"""

    def __init__(self, text, normal_icon, hover_icon, normal_ss, hover_ss, parent=None):
        super().__init__(text, parent)
        self._normal_icon = normal_icon
        self._hover_icon = hover_icon
        self._normal_ss = normal_ss
        self._hover_ss = hover_ss
        self.setIcon(normal_icon)
        self.setStyleSheet(normal_ss)

    def configure(self, normal_icon, hover_icon, normal_ss, hover_ss):
        self._normal_icon = normal_icon
        self._hover_icon = hover_icon
        self._normal_ss = normal_ss
        self._hover_ss = hover_ss
        if self.underMouse():
            self.setIcon(self._hover_icon)
            self.setStyleSheet(self._hover_ss)
        else:
            self.setIcon(self._normal_icon)
            self.setStyleSheet(self._normal_ss)

    def enterEvent(self, event):
        self.setIcon(self._hover_icon)
        self.setStyleSheet(self._hover_ss)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setIcon(self._normal_icon)
        self.setStyleSheet(self._normal_ss)
        super().leaveEvent(event)


class DashCard(QFrame):
    """主页功能卡：小标题 + 大数值 + 副标题，点击触发。"""

    clicked = Signal()

    def __init__(self, title, value, sub, callback=None, parent=None):
        super().__init__(parent)
        self.setObjectName("dashCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # 样式（含悬停）统一由主窗口全局样式表 #dashCard 提供；不再给每张卡单独 setStyleSheet
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("dashTitle")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("dashValue")
        self.sub_label = QLabel(sub)
        self.sub_label.setObjectName("dashSub")
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.sub_label)
        if callback is not None:
            self.clicked.connect(callback)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

class AnimatedBar(QWidget):
    """平滑动画水平进度条，支持缓动展开与满格抗锯齿圆角渲染。"""

    def __init__(self, color_str, ratio, parent=None):
        super().__init__(parent)
        self.setFixedHeight(13)
        self.color = QColor(color_str)
        self.bg_color = QColor(255, 255, 255, 18)  # rgba(255, 255, 255, 0.07)
        self.target_ratio = max(0.0, min(1.0, float(ratio)))
        self.current_ratio = 0.0
        self._anim = None

    def start_animation(self, duration=420, delay=0):
        if self.target_ratio <= 0:
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(self.target_ratio)
        anim.setDuration(duration)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._on_step)
        self._anim = anim
        if delay > 0:
            QTimer.singleShot(delay, anim.start)
        else:
            anim.start()

    def _on_step(self, val):
        self.current_ratio = float(val)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        radius = 6.0

        # 底色轨道
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.bg_color)
        painter.drawRoundedRect(r, radius, radius)

        # 动态填充条
        if self.current_ratio > 0.001:
            w = r.width() * self.current_ratio
            if w < radius * 2:
                w = radius * 2
            w = min(float(r.width()), w)
            fill_rect = QRectF(0.0, 0.0, w, float(r.height()))
            painter.setBrush(self.color)
            painter.drawRoundedRect(fill_rect, radius, radius)


class ArtPanel(QWidget):
    """右侧主视觉区：全屏氛围底图 + 右侧立绘 + 右下角按钮旁圆点。"""

    bgClicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("artPanel")
        self.bg_index = 0
        self.splash_pixmap = QPixmap()
        self.backdrop_pixmap = QPixmap()
        self.dot_rects = []
        self.start_button = None
        self.use_custom = False
        self.custom_pixmap = QPixmap()
        self.logo_furina_light = QSvgRenderer(LOGO_SVG, self)
        self.logo_furina_dark = QSvgRenderer(LOGO_SVG_DARK, self)
        self._bg_brightness = 0
        self.load_bg(0)

    def set_custom_background(self, path):
        # 自定义壁纸由底层 BackgroundLayer 绘制，这里只切换模式（paintEvent 在 use_custom 时直接返回）。
        # 以前会再整张解码一份从未用到的图（超大壁纸约 1 秒、上百 MB），现在只读文件头确认能解码。
        if not QImageReader(path).canRead():
            return False
        self.use_custom = True
        self.setStyleSheet("background: transparent;")
        self.update()
        return True

    def clear_custom_background(self):
        self.use_custom = False
        self.custom_pixmap = QPixmap()
        self.setStyleSheet("")
        self.update()

    def load_bg(self, index):
        self.bg_index = index
        self.splash_pixmap = QPixmap(BG_FILES[index])
        self.backdrop_pixmap = QPixmap(BACKDROP_FILES[index])
        # 预计算logo区域（左上角）的背景亮度
        self._bg_brightness = self._calc_logo_area_brightness()
        self.update()

    def _calc_logo_area_brightness(self):
        """检测背景图左上角logo区域的平均亮度（0-255）"""
        pm = self.backdrop_pixmap if not self.backdrop_pixmap.isNull() else self.splash_pixmap
        if pm.isNull():
            return 0
        img = pm.toImage()
        # 取左上角 300x150 区域
        sw, sh = min(300, img.width()), min(150, img.height())
        total, count = 0, 0
        for y in range(0, sh, 4):
            for x in range(0, sw, 4):
                c = img.pixelColor(x, y)
                total += c.lightness()
                count += 1
        return total / max(count, 1)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_dwm_round()

    def _apply_dwm_round(self):
        try:
            hwnd = int(self.winId())
            attr = 33  # DWMWA_WINDOW_CORNER_PREFERENCE
            value = 2  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(ctypes.c_int(value)), ctypes.sizeof(ctypes.c_int)
            )
        except Exception:
            pass

    def resizeEvent(self, event):
        if hasattr(self, "bg_layer"):
            self._layout_background()
        if hasattr(self, "window_body"):
            self.window_body.setGeometry(0, 0, self.width(), self.height())
        super().resizeEvent(event)

    def _layout_background(self):
        if not hasattr(self, "bg_layer") or not self.bg_layer.is_active:
            return
        self.bg_layer.setGeometry(0, 0, self.width(), self.height())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            for i, rect in enumerate(self.dot_rects):
                if rect.contains(pos):
                    self.bgClicked.emit(i)
                    return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return
        # 铺满壁纸模式：art 自身不绘制任何背景/立绘/圆点，只保留启动按钮子控件
        if getattr(self, "blank_mode", False):
            return
        # 圆角裁剪（右上角和右下角）
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(0, 0, w, h), 16, 16)
        painter.setClipPath(clip_path)
        if self.use_custom:
            return
        elif not self.backdrop_pixmap.isNull():
            scaled = self.backdrop_pixmap.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            painter.setOpacity(0.82)
            painter.drawPixmap(0, 0, scaled)
            painter.setOpacity(1.0)

        if not self.splash_pixmap.isNull():
            ratio = self.splash_pixmap.width() / self.splash_pixmap.height()
            target_h = h
            target_w = int(target_h * ratio)
            if target_w > w:
                target_w = w
                target_h = int(target_w / ratio)
            scaled = self.splash_pixmap.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            painter.drawPixmap(w - target_w, 0, scaled)

        # 右下角压暗，突出主按钮
        bottom = QLinearGradient(0, h - 150, 0, h)
        bottom.setColorAt(0.0, QColor(5, 8, 16, 0))
        bottom.setColorAt(1.0, QColor(5, 8, 16, 190))
        painter.fillRect(QRect(0, h - 150, w, 150), bottom)

        self._draw_dots(painter, w, h)

    def _draw_dots(self, painter, w, h):
        self.dot_rects = []
        if self.use_custom:
            return
        n = len(BG_FILES)
        gap = 15
        total = (n - 1) * gap
        if self.start_button is not None:
            btn = self.start_button.geometry()
            y = btn.center().y() - 4
            start_x = btn.left() - 20 - total
        else:
            start_x = w - 62 - total
            y = h - 120
        for i in range(n):
            rect = QRect(start_x + i * gap, y, 8, 8)
            if i == self.bg_index:
                painter.setBrush(QColor("#ffd166"))
                painter.setPen(QColor("#ffffff"))
            else:
                painter.setBrush(QColor("#718096"))
                painter.setPen(Qt.NoPen)
            painter.drawEllipse(rect)
            self.dot_rects.append(rect)
