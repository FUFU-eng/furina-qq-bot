"""浏览器实例管理：单例复用、健康检查、超时恢复、预加载。"""

import base64
import ctypes
import json
import os
import subprocess
import threading
import time as _t
from ctypes import wintypes
from urllib.parse import urlparse

from nonebot import logger
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from . import config, runtime
from .driver_resolver import resolve_edge_driver
from .runtime import SearchAbortedError, _interruptible_sleep, aborted
from .similarity import _looks_like_image

# ============ Windows API 底层窗口控制 ============
# 用 ShowWindow(SW_SHOWMINNOACTIVE) 最小化（不激活不跳前台）+ CDP 设窗口状态，
# 比 Selenium 的 minimize_window()（内部会先激活再最小化）更可靠。
# 导航时用 CDP Browser.setWindowBounds 设 minimized，绕过 Selenium 窗口激活。
if hasattr(ctypes, "windll"):
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
else:  # 非 Windows（仅用于测试环境导入）：窗口控制调用全部变成空操作
    class _NoWin:
        def __getattr__(self, name):
            return lambda *a, **k: 0

    _user32 = _kernel32 = _NoWin()
SW_SHOWMINNOACTIVE = 7  # 最小化但不激活
SW_RESTORE = 9
GW_OWNER = 4
LSFW_LOCK = 1
LSFW_UNLOCK = 2
SWP_NOACTIVATE = 0x0010
SWP_NOZORDER = 0x0004
SWP_NOSIZE = 0x0001
_NORMAL_X = 100  # 用户主动打开时恢复窗口到屏幕内正常位置
_NORMAL_Y = 100
_NORMAL_W = 1440
_NORMAL_H = 900


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]

_browser_hwnd = None  # 机器人 Edge 窗口的 HWND，启动时捕获
_browser_pid = None  # HWND 所属的 msedge 进程 PID，防止误操作普通 Edge 窗口
_user_open_until = 0.0  # 用户手动打开窗口后的宽限期截止时间
_USER_OPEN_GRACE_SECONDS = 300.0  # 宽限期内不再强制最小化，避免和用户抢窗口
_USER_INPUT_WINDOW_SECONDS = 1.5  # 前台窗口最近一次真实输入在此窗口内才算用户主动打开
_profile_pids = []  # 命令行里带机器人 profile 目录的 msedge 进程 PID 缓存
_profile_pids_time = 0.0
_PROFILE_PIDS_CACHE_SECONDS = 5.0
_suppression_thread = None
_suppression_stop = threading.Event()
_previous_fg_hwnd = None  # 浏览器抢焦点前的用户前台窗口 HWND


def _hwnd_pid(hwnd):
    """返回窗口所属的进程 PID。"""
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _get_profile_edge_pids():
    """获取命令行里带机器人用户目录的 msedge 进程 PID（短缓存，避免反复调 WMI）。"""
    global _profile_pids, _profile_pids_time
    now = _t.time()
    if now - _profile_pids_time < _PROFILE_PIDS_CACHE_SECONDS and _profile_pids:
        return _profile_pids
    try:
        profile = os.path.normpath(config.EDGE_USER_DATA_DIR)
        ps_script = (
            "$ErrorActionPreference = 'SilentlyContinue'; "
            "$dir = $env:QQBOT_EDGE_PROFILE; "
            "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
            "Where-Object { $_.CommandLine -and "
            "$_.CommandLine.IndexOf($dir, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 } | "
            "ForEach-Object { $_.ProcessId }"
        )
        env = os.environ.copy()
        env["QQBOT_EDGE_PROFILE"] = profile
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=15, env=env,
        )
        pids = []
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                pids.append(int(line))
        if pids:
            _profile_pids = pids
            _profile_pids_time = now
        return pids
    except Exception:
        return _profile_pids


def _is_edge_window(hwnd) -> bool:
    """检查窗口是否是可见的 Edge 顶层窗口。"""
    if not hwnd or not _user32.IsWindowVisible(hwnd):
        return False
    if _user32.GetWindow(hwnd, GW_OWNER):
        return False  # 有所有者的子窗口，跳过
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return False
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value or ""
    # Edge 窗口标题通常以 " - Microsoft Edge" 或页面标题结尾
    return "Edge" in title or "Microsoft Edge" in title


def _capture_browser_hwnd(driver=None):
    """捕获机器人 Edge 窗口的 HWND 并缓存。

    优先用前台窗口（浏览器刚启动/操作后通常在前台），否则枚举所有 Edge 窗口。
    无论哪种方式，只认命令行里带机器人 profile 目录的 msedge 窗口，
    避免把用户自己日常用的 Edge 当成机器人窗口（否则会把别人的浏览器最小化）。
    """
    global _browser_hwnd, _browser_pid
    profile_pids = _get_profile_edge_pids()
    # 先试前台窗口
    fg = _user32.GetForegroundWindow()
    if fg and _is_edge_window(fg) and _hwnd_pid(fg) in profile_pids:
        _browser_hwnd = fg
        _browser_pid = _hwnd_pid(fg)
        return
    # 枚举所有顶层窗口
    found = []
    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, lParam):
        if _is_edge_window(hwnd) and _hwnd_pid(hwnd) in profile_pids:
            found.append(hwnd)
        return True
    _user32.EnumWindows(_cb, 0)
    if found:
        _browser_hwnd = found[0]
        _browser_pid = _hwnd_pid(found[0])
    else:
        _browser_hwnd = None
        _browser_pid = None


def _valid_browser_hwnd():
    """返回当前有效的机器人 Edge 窗口 HWND；失效或抓错时重新捕获。"""
    global _browser_hwnd, _browser_pid
    if _browser_hwnd and _user32.IsWindow(_browser_hwnd):
        pid = _hwnd_pid(_browser_hwnd)
        if pid:
            if _browser_pid == pid:
                return _browser_hwnd
            if pid in _get_profile_edge_pids():
                _browser_pid = pid
                return _browser_hwnd
    _browser_hwnd = None
    _browser_pid = None
    _capture_browser_hwnd()
    return _browser_hwnd


def _mark_user_opened():
    """记录用户手动打开了浏览器窗口，宽限期内不再强制最小化。"""
    global _user_open_until
    _user_open_until = _t.time() + _USER_OPEN_GRACE_SECONDS
    logger.info("[搜图] 检测到浏览器窗口被手动打开，5 分钟内不再强制最小化")


def _last_input_age_seconds():
    """返回 Windows 最近一次真实用户输入距现在的秒数。"""
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        now = int(_kernel32.GetTickCount()) & 0xFFFFFFFF
        elapsed = (now - int(info.dwTime)) & 0xFFFFFFFF
        return elapsed / 1000.0
    except Exception:
        return None


def _is_user_open_request(hwnd):
    """只有任务栏点击把窗口带到前台时，才认定为用户主动打开。"""
    if not hwnd or _user32.GetForegroundWindow() != hwnd:
        return False
    age = _last_input_age_seconds()
    return (
        age is not None
        and 0.0 <= age <= _USER_INPUT_WINDOW_SECONDS
        and _is_cursor_on_taskbar()
    )


def _is_cursor_on_taskbar():
    """判断鼠标当前是否位于 Windows 任务栏，避免把前台打字误认为打开浏览器。"""
    try:
        point = wintypes.POINT()
        if not _user32.GetCursorPos(ctypes.byref(point)):
            return False
        hit = False

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lparam):
            nonlocal hit
            class_name = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(hwnd, class_name, 256)
            if class_name.value not in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                return True
            rect = wintypes.RECT()
            if _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                if rect.left <= point.x < rect.right and rect.top <= point.y < rect.bottom:
                    hit = True
                    return False
            return True

        _user32.EnumWindows(_cb, 0)
        return hit
    except Exception:
        return False


def _win32_minimize_no_activate():
    """用 Windows API 最小化浏览器窗口，不激活、不跳前台。"""
    if _user_open_until > _t.time():
        return
    hwnd = _valid_browser_hwnd()
    if not hwnd:
        return
    for _ in range(3):
        try:
            _user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)
        except Exception:
            return
        if _user32.IsIconic(hwnd):
            return
        _t.sleep(0.25)
def _restore_window(hwnd):
    """恢复窗口到屏幕内正常位置（用户主动打开时调用）。"""
    try:
        _user32.ShowWindow(hwnd, SW_RESTORE)
        _user32.SetWindowPos(hwnd, 0, _NORMAL_X, _NORMAL_Y,
                             _NORMAL_W, _NORMAL_H, SWP_NOACTIVATE | SWP_NOZORDER)
    except Exception:
        pass
def _cdp_minimize(driver):
    """用 CDP Browser.setWindowBounds 把窗口设为 minimized，绕过 Selenium 的窗口激活。

    Selenium 的 minimize_window() 内部会先激活窗口再最小化，导致闪烁。
    CDP 直接设窗口状态不会激活窗口，导航前后用它压制最可靠。"""
    try:
        info = driver.execute_cdp_cmd("Browser.getWindowForTarget")
        window_id = (info or {}).get("windowId")
        if not window_id:
            return
        driver.execute_cdp_cmd("Browser.setWindowBounds", {
            "windowId": window_id,
            "bounds": {"windowState": "minimized"}
        })
    except Exception:
        pass
def _try_restore_foreground(hwnd):
    """尽力恢复前台窗口焦点，减少浏览器抢焦点对用户打字的影响。"""
    try:
        if not hwnd or not _user32.IsWindow(hwnd):
            return
        fg = _user32.GetForegroundWindow()
        if fg == hwnd:
            return
        fg_tid = _user32.GetWindowThreadProcessId(fg, None)
        cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        if fg_tid and fg_tid != cur_tid:
            _user32.AttachThreadInput(fg_tid, cur_tid, True)
        try:
            _user32.SetForegroundWindow(hwnd)
        finally:
            if fg_tid and fg_tid != cur_tid:
                _user32.AttachThreadInput(fg_tid, cur_tid, False)
    except Exception:
        pass
def _start_suppression(epoch: int):
    """启动持续后台压制线程：搜图期间每 200ms 检查并最小化浏览器窗口。

    线程持续运行直到 _stop_suppression 或搜图中止。
    自动导航造成的窗口恢复会立即被压回后台；只有前台窗口刚收到真实用户输入
    时才认定为用户主动点击任务栏打开。用户最小化后立即恢复后台压制。
    """
    global _suppression_thread, _previous_fg_hwnd, _user_open_until
    _suppression_stop.clear()
    _previous_fg_hwnd = None
    try:
        # 阻止自动化调用抢前台；用户点击任务栏仍可正常打开窗口。
        _user32.LockSetForegroundWindow(LSFW_LOCK)
    except Exception:
        pass
    def _run():
        global _previous_fg_hwnd, _user_open_until
        while not _suppression_stop.is_set():
            if aborted(epoch):
                break
            try:
                hwnd = _valid_browser_hwnd()
                if not hwnd:
                    _suppression_stop.wait(0.5)
                    continue
                if _user_open_until > _t.time():
                    # 宽限期：用户正在看浏览器，不压制；
                    # 用户最小化后退出宽限期，恢复压制
                    if _user32.IsIconic(hwnd):
                        _user_open_until = 0.0
                        logger.info("[搜图] 用户已最小化浏览器窗口，恢复后台压制")
                    _suppression_stop.wait(0.3)
                    continue
                if _user32.IsIconic(hwnd):
                    _previous_fg_hwnd = None
                else:
                    fg = _user32.GetForegroundWindow()
                    # 仅用真实输入确认用户点击任务栏，避免 Edge 自动恢复时误放行。
                    if _is_user_open_request(hwnd):
                        _mark_user_opened()
                        _suppression_stop.wait(0.3)
                        continue
                    # 记住浏览器抢焦点前的用户窗口，稍后尽力恢复焦点。
                    if fg and fg != hwnd:
                        _previous_fg_hwnd = fg
                    # 立即最小化，减少可见时间
                    _user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)
                    # 浏览器抢了前台焦点时尝试恢复
                    if _previous_fg_hwnd and fg == hwnd:
                        _try_restore_foreground(_previous_fg_hwnd)
            except Exception:
                pass
            _suppression_stop.wait(0.2)
    _suppression_thread = threading.Thread(target=_run, daemon=True)
    _suppression_thread.start()
    logger.info("[搜图] 已启动浏览器后台压制线程")
def _stop_suppression():
    """停止持续后台压制线程。"""
    _suppression_stop.set()
    t = _suppression_thread
    if t:
        t.join(timeout=2)
    try:
        _user32.LockSetForegroundWindow(LSFW_UNLOCK)
    except Exception:
        pass
    logger.info("[搜图] 已停止浏览器后台压制线程")


def _win32_is_minimized() -> bool:
    """检查浏览器窗口是否已最小化。"""
    hwnd = _valid_browser_hwnd()
    if not hwnd:
        return False
    return bool(_user32.IsIconic(hwnd))


def _quit_driver_quietly(driver):
    """在后台线程退出旧浏览器，避免卡死的 quit 阻塞新搜图。"""
    try:
        if driver is not None:
            driver.quit()
    except Exception:
        pass


def _force_reset_driver():
    """超时/卡死恢复：重建线程池与浏览器锁，并后台退出旧浏览器实例。"""
    logger.warning("[搜图] 强制重置搜图运行时（浏览器/线程池/锁）...")
    old_driver = runtime.reset_search_runtime()
    if old_driver is not None:
        threading.Thread(target=_quit_driver_quietly, args=(old_driver,), daemon=True).start()


def _normalize_window_bounds(driver):
    """浏览器刚创建时先放屏幕内正常位置（用于捕获 HWND），随后由压制线程移到屏幕外。"""
    try:
        driver.set_window_position(100, 100)
        driver.set_window_size(1440, 900)
    except Exception:
        pass


def keep_window_minimized(driver, force=False):
    """即时最小化浏览器窗口（持续压制线程负责长期兜底，这里只做即时补充）。
    用户宽限期内不压制。force=True 用于浏览器刚创建等场景，效果相同。"""
    if driver is None:
        return
    if _user_open_until > _t.time():
        return
    hwnd = _valid_browser_hwnd()
    if not hwnd:
        return
    if not _user32.IsIconic(hwnd):
        _win32_minimize_no_activate()


def _window_state_minimized(driver):
    """通过 CDP 判断 Edge 窗口是否最小化；返回 True/False，检测不到时返回 None。"""
    try:
        info = driver.execute_cdp_cmd("Browser.getWindowForTarget")
        window_id = (info or {}).get("windowId")
        if not window_id:
            return None
        bounds = driver.execute_cdp_cmd("Browser.getWindowBounds", {"windowId": window_id})
        return (bounds or {}).get("windowState") == "minimized"
    except Exception:
        return None


def minimized_navigate(driver, action):
    """执行会触发 Edge 恢复/激活的导航动作，前后都用 CDP 设窗口为 minimized。

    关键：用 CDP Browser.setWindowBounds 设 minimized 状态，绕过 Selenium 的窗口激活，
    这样导航时不会把最小化的窗口"恢复"到前台，不会闪烁。
    持续压制线程负责长期兜底，这里做即时压制。用户宽限期内不压制。"""
    if driver is None:
        return action()
    if not _browser_hwnd or not _user32.IsWindow(_browser_hwnd):
        _capture_browser_hwnd(driver)
    in_grace = _user_open_until > _t.time()
    if not in_grace:
        _cdp_minimize(driver)
        _win32_minimize_no_activate()
    try:
        return action()
    finally:
        if not in_grace:
            _cdp_minimize(driver)
            _win32_minimize_no_activate()


def _is_profile_lock_error(err_text: str) -> bool:
    """判断浏览器启动失败是否疑似 Edge 配置目录被占用（残留进程/手动打开的登录窗口）。"""
    err_text = err_text or ""
    return (
        "user data directory is already in use" in err_text
        or "DevToolsActivePort" in err_text
        or "instance exited" in err_text
    )


def _cleanup_stale_edge():
    """清理占用机器人 Edge 配置目录的残留进程。

    场景：上次搜图时机器人被强杀/重启，遗留的 msedge.exe 还占着
    edge_profile_v2 目录，导致下次启动报 "Chrome instance exited"。
    只杀命令行里带本机器人 profile 目录的 msedge 及其子进程，
    不影响用户自己日常用的 Edge（不同 profile 目录）。
    """
    profile = os.path.normpath(config.EDGE_USER_DATA_DIR)
    ps_script = (
        "$ErrorActionPreference = 'SilentlyContinue'; "
        "$dir = '" + profile + "'; "
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
        "Where-Object { $_.CommandLine -and $_.CommandLine -like ('*' + $dir + '*') } | "
        "ForEach-Object { & taskkill.exe /PID $_.ProcessId /T /F 2>$null | Out-Null }; "
        "exit 0"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            timeout=25,
        )
    except Exception:
        pass


def _wait_and_acquire_lock(timeout: float, role: str, epoch: int) -> bool:
    """轮询等待浏览器锁释放（被卡死线程强制重置后，等它退出并还锁）"""
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        wait = min(2.0, max(0.2, deadline - _t.time()))
        if runtime._driver_lock.acquire(timeout=wait, role=role, epoch=epoch):
            return True
    return False



_STEALTH_INIT_JS = """
(() => {
    try {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });
        if (!window.chrome) window.chrome = { runtime: {}, loadTimes: () => ({}), csi: () => ({}), app: {} };
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'], configurable: true });
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8, configurable: true });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8, configurable: true });
        // WebRTC local IP leak guard (Brana AI stealth pattern)
        const OrigPeerConnection = window.RTCPeerConnection || window.webkitRTCPeerConnection;
        if (OrigPeerConnection) {
            const WrappedPC = function(config, constraints) {
                const cfg = Object.assign({}, config || {});
                if (cfg.iceTransportPolicy !== 'relay') {
                    cfg.iceCandidatePoolSize = 0;
                }
                return new OrigPeerConnection(cfg, constraints);
            };
            WrappedPC.prototype = OrigPeerConnection.prototype;
            window.RTCPeerConnection = WrappedPC;
            if (window.webkitRTCPeerConnection) window.webkitRTCPeerConnection = WrappedPC;
        }
    } catch (e) {}
})();
"""


def _get_driver(force_reset_on_stuck: bool = True, lock_timeout: float = 30.0):
    """获取或创建浏览器实例（带健康检查）。

    lock_timeout：等待浏览器锁的最长时间；预加载等后台任务传小值，
    抢不到就放弃本次预加载，避免和正式搜图/下载原图抢浏览器。
    """
    # 获取锁；如果被上一个卡死的搜图占着，区分情况处理：
    # - 持有者是"卡死的搜图线程"（epoch 已过期）：强制重置运行时后直接换新锁，不等旧线程
    # - 持有者是"预加载线程"等有界任务：耐心等待其完成，不做破坏性重置
    for attempt in range(4):
        epoch = runtime._search_epoch
        if runtime._driver_lock.acquire(timeout=lock_timeout, role="search", epoch=epoch):
            break
        if not force_reset_on_stuck:
            logger.warning(f"[搜图] 浏览器锁被占用超过{lock_timeout}秒，放弃本次操作")
            return None
        owner_role = runtime._driver_lock.owner_role
        owner_epoch = runtime._driver_lock.owner_epoch
        if owner_role == "search" and owner_epoch != epoch:
            logger.warning(f"[搜图] 浏览器锁被卡死的搜图线程占用，强制重置搜图运行时（第{attempt + 1}次）")
            _force_reset_driver()
            lock_timeout = min(10.0, lock_timeout)
            continue
        # 被预加载等有界任务占用：等待其完成（预加载自身有超时上限）
        logger.info(f"[搜图] 浏览器锁被其他任务占用（{owner_role or '未知'}），等待其完成...")
        if _wait_and_acquire_lock(timeout=max(lock_timeout * 2, 30), role="search", epoch=epoch):
            break
        logger.warning("[搜图] 浏览器锁等待超时，强制重置搜图运行时兜底")
        _force_reset_driver()
        lock_timeout = min(10.0, lock_timeout)
    else:
        raise RuntimeError("浏览器锁反复被占用，无法获取（请稍后再试）")

    try:
        # 拿到锁后若搜图已被中止（超时重置），立即放弃，绝不操作新浏览器
        if runtime._search_epoch != epoch:
            runtime._driver_lock.release()
            raise SearchAbortedError("搜图已中止")
        # 检查浏览器是否还活着（执行个简单JS，确保真的响应）
        if runtime._driver is not None:
            try:
                # 执行个简单的 JS，看看浏览器是不是真的响应
                runtime._driver.execute_script("return 1;")

                # 清理多余的标签页，只保留第一个，防止内存泄漏
                handles = runtime._driver.window_handles
                if len(handles) > 1:
                    logger.info(f"[搜图] 清理多余标签页: {len(handles)} -> 1")
                    for handle in handles[1:]:
                        try:
                            minimized_navigate(
                                runtime._driver,
                                lambda h=handle: runtime._driver.switch_to.window(h),
                            )
                            runtime._driver.close()
                        except Exception:
                            pass
                    minimized_navigate(
                        runtime._driver,
                        lambda h=handles[0]: runtime._driver.switch_to.window(h),
                    )

                logger.info("[搜图] 浏览器实例正常，复用")
                keep_window_minimized(runtime._driver)
            except Exception:
                # 浏览器挂了，重新创建
                logger.warning("[搜图] 浏览器实例已失效，重新创建...")
                try:
                    runtime._driver.quit()
                except Exception:
                    pass
                runtime._driver = None

        if runtime._driver is None:
            logger.info("[搜图] 创建新的浏览器实例...")
            # ========== 启动 Edge 浏览器 ==========
            from selenium import webdriver
            from selenium.webdriver.edge.options import Options
            from selenium.webdriver.edge.service import Service

            options = Options()
            # none：导航立即返回，不等待页面加载（所有关键内容都有显式 WebDriverWait 兜底，
            # 这样 Google Lens 上传页能在后台加载，上传框一出现就立刻上传，省掉整页加载等待）
            options.page_load_strategy = "none"

            # 独立的用户数据目录，保存登录状态和cookie
            options.add_argument(f"--user-data-dir={config.EDGE_USER_DATA_DIR}")

            # 代理设置
            if config.PROXY_URL:
                options.add_argument(f"--proxy-server={config.PROXY_URL}")

            # 窗口先按屏幕内正常尺寸启动，创建后立即最小化；不依赖 --start-minimized，
            # 否则 Edge 恢复时可能白屏或任务栏点击无效
            options.add_argument("--window-position=100,100")
            options.add_argument("--window-size=1440,900")

            # 隐藏自动化特征
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--webrtc-ip-handling-policy=disable_non_proxied_udp")
            options.add_argument("--enforce-webrtc-ip-permission-check")

            # ========== 稳定性增强参数（减少崩溃概率） ==========
            # 注意：不能禁用 GPU / 软件光栅化，否则最小化恢复后页面会白屏
            options.add_argument("--headless=new")  # 无头模式，不在桌面弹出浏览器窗口
            options.add_argument("--no-sandbox")  # 禁用沙箱，避免权限问题导致崩溃
            options.add_argument("--disable-dev-shm-usage")  # 解决资源不足问题
            options.add_argument("--disable-extensions")  # 禁用扩展，避免冲突
            options.add_argument("--disable-background-networking")
            options.add_argument("--disable-default-apps")
            options.add_argument("--disable-sync")  # 禁用同步，减少后台活动
            # 防白屏：最小化后不暂停渲染进程，避免恢复时页面全白
            options.add_argument("--disable-renderer-backgrounding")
            options.add_argument("--disable-background-timer-throttling")
            options.add_argument("--disable-backgrounding-occluded-windows")
            # ====================================================

            # 排除 enable-automation，进一步隐藏自动化特征
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)

            # 创建失败时自动重试（Edge 刚开机/Profile 被占用时经常第一次启动失败）
            # Resolve once, outside browser retries; a cached compatible driver needs no network.
            driver_path = resolve_edge_driver()
            last_error = None
            cleaned = False
            for attempt in range(3):
                try:
                    service = Service(driver_path)
                    runtime._driver = webdriver.Edge(service=service, options=options)
                    break
                except Exception as e:
                    last_error = e
                    logger.warning(f"[搜图] 浏览器启动失败（第{attempt + 1}次）: {e}")
                    runtime._driver = None
                    if not cleaned and _is_profile_lock_error(str(e)):
                        # 疑似上次强杀/崩溃遗留的 Edge 还占着配置目录：清掉残留进程再重试
                        logger.info("[搜图] Edge 配置目录疑似被残留进程占用，清理后重试...")
                        _cleanup_stale_edge()
                        cleaned = True
                    _interruptible_sleep(2, runtime._search_epoch)
            if runtime._driver is None:
                hint = ""
                err_text = str(last_error)
                if _is_profile_lock_error(err_text):
                    hint = "。提示：Edge 用户目录被占用，已尝试自动清理残留进程，若仍失败请手动关闭使用该配置目录的 Edge 窗口"
                raise RuntimeError(f"Edge 浏览器启动失败（已重试3次）: {last_error}{hint}")

            # 移除 webdriver 标志并注入 Brana AI 级反指纹/防 WebRTC 泄露脚本
            try:
                runtime._driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": _STEALTH_INIT_JS})
            except Exception:
                pass
            runtime._driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            # 设置超时
            runtime._driver.set_page_load_timeout(config.PAGE_LOAD_TIMEOUT)
            runtime._driver.set_script_timeout(config.PAGE_LOAD_TIMEOUT)
            _normalize_window_bounds(runtime._driver)
            # 浏览器刚启动时在前台，此时捕获 HWND 最准确
            _capture_browser_hwnd(runtime._driver)
            keep_window_minimized(runtime._driver, force=True)

            logger.info("[搜图] 已启动 Edge 浏览器")

        return runtime._driver
    except Exception as e:
        # 创建失败，释放锁
        logger.error(f"[搜图] 创建浏览器失败: {e}")
        _release_driver()
        raise
def _release_driver():
    """释放浏览器锁（不关闭浏览器）"""
    if not runtime._driver_lock.locked:
        return
    # DriverLock.release 内部会校验持有线程，防止误放他人锁
    runtime._driver_lock.release()


def _fetch_image_bytes_via_browser(driver, url: str, epoch: int, site: str = "", quick: bool = False, deadline=None) -> bytes:
    """新开标签页访问图片URL，优先 fetch 拿原始字节，失败用 canvas 无损提取。

    quick=True 用于直连图片 CDN（如 cdn.donmai.us）：遇到 Cloudflare 验证页时
    缩短等待时间快速放弃，避免单个候选卡住 40 秒拖垮整次搜图。
    """
    image_data = None
    access_blocked = False
    def remaining(seconds):
        # 本模块把 time 导入为 _t；这里曾误写成 time.time()，传入 deadline 时必抛 NameError，
        # 导致所有带预算的浏览器取原图（Danbooru/直链兜底）直接失败。
        if deadline is not None and _t.time() >= deadline:
            raise TimeoutError('Source budget exhausted')
        return min(seconds, max(.1, deadline-_t.time())) if deadline else seconds
    try:
        # 新标签页直接打开目标网址，避免先出现 about:blank 空白页
        minimized_navigate(driver, lambda: driver.execute_script(f"window.open({json.dumps(url)});"))
        minimized_navigate(driver, lambda: driver.switch_to.window(driver.window_handles[-1]))
        keep_window_minimized(driver)
        # none 加载策略下，新标签页可能还停在 about:blank（readyState 已经是 complete），
        # 必须等到地址变成目标主机再继续，否则后面的检查会落在空页面上
        try:
            target_host = urlparse(url).netloc
            if target_host:
                WebDriverWait(driver, remaining(10)).until(lambda d: target_host in (d.current_url or ""))
        except Exception:
            pass
        try:
            WebDriverWait(driver, remaining(10)).until(lambda d: d.execute_script("return document.readyState") == "complete")
        except Exception:
            pass

        _challenge_titles = (
            "just a moment", "attention required", "enable javascript",
            "verify you are human", "checking your browser",
        )

        def _is_challenge_page():
            try:
                t = (driver.title or "").lower()
                return any(k in t for k in _challenge_titles)
            except Exception:
                return False

        def _wait_img(seconds):
            """等待真正的图片加载出来：部分 CDN（如 Cloudflare）会先显示验证页，
            几秒后才跳转到真实图片，直接 fetch 会拿到验证页的 HTML。"""
            img = None
            deadline = _t.time() + seconds
            while _t.time() < deadline:
                if aborted(epoch):
                    raise SearchAbortedError("搜图已中止")
                try:
                    img = driver.find_element(By.TAG_NAME, "img")
                    if img and driver.execute_script("return arguments[0].naturalWidth", img) > 0:
                        return img
                except Exception:
                    pass
                # 尝试点击 Cloudflare Turnstile 验证框（部分验证需要点一下才通过）
                try:
                    iframe = driver.find_element(By.CSS_SELECTOR, "iframe[src*='challenges.cloudflare.com']")
                    if iframe.is_displayed():
                        driver.execute_script("arguments[0].scrollIntoView();", iframe)
                        try:
                            iframe.click()
                        except Exception:
                            pass
                except Exception:
                    pass
                # 页面里根本没有图片（如 404 普通页）且不是验证页时，不再空等
                try:
                    if driver.execute_script("return document.images.length") == 0 and not _is_challenge_page():
                        return img
                except Exception:
                    pass
                _interruptible_sleep(0.4, epoch)
            return img

        def _grab_bytes():
            """优先 fetch 原始字节（100%原图，不做重编码/压缩），失败用 canvas 无损提取。"""
            nonlocal access_blocked
            data = None
            try:
                remaining(1)
                driver.set_script_timeout(remaining(10))
                base64_data = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    fetch(window.location.href, {signal: AbortSignal.timeout(8000)})
                        .then(r => {
                            if (!r.ok) throw new Error('HTTP:' + r.status);
                            return r.arrayBuffer();
                        })
                        .then(buf => {
                            let bin = '';
                            const bytes = new Uint8Array(buf);
                            for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
                            done(btoa(bin));
                        })
                        .catch(e => done('ERR:' + (e.message || e.name || 'unknown')));
                """)
                if base64_data:
                    if isinstance(base64_data, str) and base64_data.startswith("ERR:"):
                        logger.info(f"[搜图] 浏览器 fetch 未拿到图片[{site}]: {base64_data}")
                        access_blocked = any(x in base64_data for x in ("HTTP:401", "HTTP:403", "HTTP:429"))
                        if access_blocked: return None
                        base64_data = ""
                    else:
                        candidate = base64.b64decode(base64_data)
                        if _looks_like_image(candidate):
                            data = candidate
            except Exception:
                pass
            if not data:
                try:
                    img = driver.find_element(By.TAG_NAME, "img")
                    base64_data = driver.execute_script("""
                        const img = arguments[0];
                        const canvas = document.createElement('canvas');
                        canvas.width = img.naturalWidth;
                        canvas.height = img.naturalHeight;
                        const ctx = canvas.getContext('2d');
                        ctx.drawImage(img, 0, 0);
                        return canvas.toDataURL('image/png').split(',')[1];
                    """, img)
                    if base64_data:
                        candidate = base64.b64decode(base64_data)
                        if _looks_like_image(candidate):
                            data = candidate
                except Exception:
                    pass
            return data

        img = _wait_img(remaining(5 if quick else 20))
        image_data = _grab_bytes()
        if not image_data and not access_blocked:
            # Cloudflare 验证页兜底：第一次 fetch 拿到的是验证页 HTML；刷新一次，
            # 浏览器拿到 cf_clearance cookie 后通常会直接出图
            if _is_challenge_page():
                logger.info(f"[搜图] 检测到 Cloudflare 验证，刷新重试[{site}]...")
                minimized_navigate(driver, lambda: driver.refresh())
                keep_window_minimized(driver)
                img = _wait_img(remaining(5 if quick else 15))
                image_data = _grab_bytes()
        if not image_data and not access_blocked:
            # 通用兜底：瞬时网络错误/白屏/标题不含 "Just a moment" 的验证页都会走这里，
            # 再刷新一次，避免一次失败就把 Danbooru 原图候选整个放弃。
            try:
                logger.info(f"[搜图] 浏览器首次取图未成功[{site}]，刷新重试: {url[:70]}")
                minimized_navigate(driver, lambda: driver.refresh())
                keep_window_minimized(driver)
                img = _wait_img(remaining(5 if quick else 20))
                image_data = _grab_bytes()
            except Exception:
                pass
        if image_data:
            logger.info(f"[搜图] ✅ 浏览器获取原图成功[{site}] 大小: {len(image_data)} 字节")
        return image_data
    except Exception as e:
        logger.warning(f"[搜图] 浏览器获取原图失败[{site}]: {e}")
        return None
    finally:
        try:
            handles = driver.window_handles
            try:
                cur = driver.current_window_handle
            except Exception:
                cur = None  # 新标签页自己关掉了（例如 pixiv 的跳转页）
            if cur in handles and len(handles) > 1:
                driver.close()
            ensure_live_window(driver)
            keep_window_minimized(driver)
        except Exception:
            pass


def ensure_live_window(driver) -> None:
    """当前标签页已经被关掉（no such window）时切回还活着的第一个标签页。
    否则后面每个站点都会报「target window already closed」，整次搜图全军覆没。"""
    if driver is None:
        return
    try:
        handles = driver.window_handles
    except Exception:
        return
    if not handles:
        return
    try:
        cur = driver.current_window_handle
    except Exception:
        cur = None
    if cur not in handles:
        logger.info("[搜图] 当前标签页已被关闭，切回仍在的标签页")
        minimized_navigate(driver, lambda: driver.switch_to.window(handles[0]))


def _browser_fetch_google_image(url: str) -> bytes:
    """通过浏览器抓取 Google 图库图片（httpx 对 lens.usercontent 只能拿到空响应）"""
    driver = None
    try:
        driver = _get_driver()
        if driver is None:
            return None
        return _fetch_image_bytes_via_browser(driver, url, runtime._search_epoch)
    except Exception as e:
        logger.warning(f"[搜图] 浏览器抓取 Google 图失败: {e}")
        return None
    finally:
        if driver is not None:
            _release_driver()


def preload_upload_page_in_background():
    """搜图结束后，后台把浏览器切回 Google Lens 上传页并等它就绪，
    下一次搜图就能直接复用上传页（省掉打开谷歌搜图页的 7~10 秒）。
    使用短锁超时：抢不到就跳过本次预加载，绝不和正式搜图/下载原图抢浏览器。"""

    def _run():
        try:
            driver = _get_driver(force_reset_on_stuck=False, lock_timeout=2.0)
            if driver is None:
                return
            try:
                minimized_navigate(driver, lambda: driver.switch_to.window(driver.window_handles[0]))
                from . import lens_ui
                lens_ui.open_upload(driver, lambda fn: minimized_navigate(driver, fn))
                keep_window_minimized(driver)
                # 等上传框出现，确保下一次搜图真正就绪
                try:
                    WebDriverWait(driver, 20).until(
                        lambda d: d.find_elements(By.CSS_SELECTOR, 'input[name="encoded_image"]')
                    )
                    logger.info("[搜图] 已后台预加载 Google Lens 上传页，下次搜图可秒开")
                except Exception:
                    logger.warning("[搜图] 后台预加载上传页超时（不影响下次搜图，会自动重载）")
            finally:
                _release_driver()
        except Exception as e:
            logger.warning(f"[搜图] 后台预加载上传页失败: {e}")

    threading.Thread(target=_run, daemon=True).start()
