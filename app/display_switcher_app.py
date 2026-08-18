# -*- coding: utf-8 -*-
"""
DisplaySwitcher // CONTROL DECK
显示器分辨率 / 刷新率切换终端 —— 原生窗口 (wx.WebView) + 本地 REST API 桥接真实显示控制。

编译:
    pyinstaller --onefile --windowed --name DisplaySwitcher ^
        --add-data "webroot;webroot" display_switcher_app.py

命令行 (无界面直切):
    DisplaySwitcher.exe --switch 1920 1080 60
"""
import os, sys, json, uuid, threading, socket, time, ctypes, functools, http.server, urllib.request, urllib.error, ssl, webbrowser, subprocess
from urllib.parse import urlparse, parse_qs
import ctypes.wintypes  # 显式导入，确保冻结( PyInstaller )后 ctypes.wintypes 仍可用

# ----------------------------------------------------------------------------
# 1. Win32 显示控制 (复用原程序的 ctypes 实现)
# ----------------------------------------------------------------------------
try:
    user32 = ctypes.windll.user32
except Exception:
    user32 = None

try:
    dwmapi = ctypes.windll.dwmapi
except Exception:
    dwmapi = None

# ----------------------------------------------------------------------------
# 1.1 窗口原生外观（无边框 / 圆角 / 阴影 / 命中测试 / 贴边分屏）
# ----------------------------------------------------------------------------
WM_NCHITTEST      = 0x0084
WM_NCLBUTTONDOWN  = 0x00A1
WM_NCLBUTTONDBLCLK= 0x00A3
WM_MOUSEMOVE      = 0x0200
WM_LBUTTONUP      = 0x0202
WM_CAPTURECHANGED = 0x0215
WM_SIZE           = 0x0005
SIZE_RESTORED     = 0
SIZE_MAXIMIZED    = 2
HTCLIENT          = 1
HTCAPTION         = 2
HTLEFT, HTRIGHT, HTTOP, HTBOTTOM = 10, 11, 12, 15
HTTOPLEFT, HTTOPRIGHT, HTBOTTOMLEFT, HTBOTTOMRIGHT = 13, 14, 16, 17
GWL_STYLE          = -16
GWL_WNDPROC        = -4
GCL_STYLE          = -26
WS_CLIPCHILDREN    = 0x02000000
WS_CLIPSIBLINGS    = 0x04000000
CS_DROPSHADOW      = 0x00020000
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND       = 2
DWMWCP_DONOTROUND  = 1
SWP_FRAMECHANGED   = 0x0020
SWP_NOZORDER       = 0x0004
SWP_NOMOVE         = 0x0002
SWP_NOSIZE         = 0x0001
MONITOR_DEFAULTTONEAREST = 2
GUI_FRAME = None   # 由 start_gui 赋值，供 HTTP 桥接调用窗口操作

def _set_argtypes():
    """为 Win32 API 设置 64 位正确的参数/返回类型（仅一次）。"""
    try:
        u = user32
        u.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.GetWindowLongPtrW.restype = ctypes.c_void_p
        u.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        u.SetWindowLongPtrW.restype = ctypes.c_void_p
        u.CallWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                                     ctypes.c_void_p, ctypes.c_void_p]
        u.CallWindowProcW.restype = ctypes.c_long
        u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        u.GetWindowRect.restype = ctypes.c_int
        u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        u.SetWindowPos.restype = ctypes.c_int
        u.ReleaseCapture.argtypes = []
        u.ReleaseCapture.restype = ctypes.c_int
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        u.SendMessageW.restype = ctypes.c_long
        u.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        u.MonitorFromWindow.restype = ctypes.c_void_p
        u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        u.GetMonitorInfoW.restype = ctypes.c_int
        u.GetClassLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.GetClassLongW.restype = ctypes.c_ulong
        u.SetClassLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong]
        u.SetClassLongW.restype = ctypes.c_ulong
    except Exception:
        pass

class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong),
                ("rcMonitor", ctypes.wintypes.RECT),
                ("rcWork", ctypes.wintypes.RECT),
                ("dwFlags", ctypes.c_ulong)]

def _get_work_area(hwnd):
    """获取窗口所在显示器的「工作区」（去掉任务栏）。"""
    try:
        hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        w = mi.rcWork
        return (w.left, w.top, w.right - w.left, w.bottom - w.top)
    except Exception:
        return (0, 0, 800, 600)

def _win_op(action, path=""):
    """HTML 自定义标题栏调用：执行窗口操作，调度到 wx 主线程。
    拖动/缩放采用「前端指针捕获 + Python 增量 SetWindowPos」的确定性方案，
    不再依赖原生 SendMessage(WM_NCLBUTTONDOWN) 模态循环 —— 彻底规避 WebView2
    对自己子窗口 SetCapture 导致模态循环锚点错乱引发的窗口瞬移，以及模态循环
    吞掉 dblclick、preventDefault 抑制双击最大化等问题。
    坐标全部为屏幕坐标，由前端 e.screenX/Y 提供，偏移量在 mousedown 时记录，
    移动时按 delta 重算窗口位置，丝滑无跳变、无白边。
    """
    import wx
    from urllib.parse import urlparse, parse_qs
    f = GUI_FRAME
    if not f:
        return
    qs = parse_qs(urlparse(path).query)

    def _int(name, default):
        try:
            return int(qs.get(name, [default])[0])
        except Exception:
            return default

    def _rect():
        r = ctypes.wintypes.RECT()
        user32.GetWindowRect(f.GetHandle(), ctypes.byref(r))
        return r

    def _run():
        try:
            if action == "min":
                f.Iconize(True)
            elif action == "max":
                f._toggle_max()
            elif action == "close":
                f.Close()
            elif action == "dragstart":
                r = _rect()
                # 从最大化状态直接拖动标题栏：先还原再拖动（与系统标题栏一致）
                if f._maxed:
                    f._maxed = False
                    f._apply_round()
                    if f._normal_rect:
                        x, y, w, h = f._normal_rect
                        user32.SetWindowPos(f.GetHandle(), 0, x, y, w, h,
                                            SWP_FRAMECHANGED | SWP_NOZORDER)
                        r = _rect()
                x = _int("x", 0); y = _int("y", 0)
                f._drag_off = (x - r.left, y - r.top)
            elif action == "dragmove":
                off = getattr(f, "_drag_off", None)
                if off:
                    x = _int("x", 0); y = _int("y", 0)
                    user32.SetWindowPos(f.GetHandle(), 0, x - off[0], y - off[1],
                                        0, 0, SWP_NOSIZE | SWP_NOZORDER)
            elif action == "dragend":
                f._drag_off = None
            elif action == "resizestart":
                r = _rect()
                f._resize = {"sx": _int("x", 0), "sy": _int("y", 0),
                             "d": _int("d", 10),
                             "left": r.left, "top": r.top,
                             "w": r.right - r.left, "h": r.bottom - r.top}
            elif action == "resizemove":
                rs = getattr(f, "_resize", None)
                if rs:
                    nx = _int("x", 0); ny = _int("y", 0)
                    d = rs["d"]
                    left, top, w, h = rs["left"], rs["top"], rs["w"], rs["h"]
                    dx = nx - rs["sx"]; dy = ny - rs["sy"]
                    if d in (HTLEFT, HTTOPLEFT, HTBOTTOMLEFT):
                        left += dx; w -= dx
                    if d in (HTRIGHT, HTTOPRIGHT, HTBOTTOMRIGHT):
                        w += dx
                    if d in (HTTOP, HTTOPLEFT, HTTOPRIGHT):
                        top += dy; h -= dy
                    if d in (HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT):
                        h += dy
                    if w < 360: w = 360
                    if h < 260: h = 260
                    user32.SetWindowPos(f.GetHandle(), 0, left, top, w, h,
                                       SWP_NOZORDER)
            elif action == "resizeend":
                f._resize = None
        except Exception:
            pass
    wx.CallAfter(_run)

DM_PELSWIDTH          = 0x80000
DM_PELSHEIGHT         = 0x100000
DM_DISPLAYFREQUENCY   = 0x400000
CDS_UPDATEREGISTRY    = 0x01
ENUM_CURRENT_SETTINGS = -1
DISP_CHANGE_SUCCESSFUL = 0

ERROR_MAP = {
    0:   "设置成功",
    1:   "不支持的显示模式",
    -1:  "无效参数",
    -2:  "不支持该操作",
    -3:  "无法写入注册表",
    -4:  "需要重启才能生效",
    -5:  "其他显示设备冲突",
    -6:  "设置更改失败",
}

class DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName",     ctypes.c_char * 32),
        ("dmSpecVersion",    ctypes.c_ushort),
        ("dmDriverVersion",  ctypes.c_ushort),
        ("dmSize",           ctypes.c_ushort),
        ("dmDriverExtra",    ctypes.c_ushort),
        ("dmFields",         ctypes.c_ulong),
        ("dmOrientation",    ctypes.c_short),
        ("dmPaperSize",      ctypes.c_short),
        ("dmPaperLength",    ctypes.c_short),
        ("dmPaperWidth",     ctypes.c_short),
        ("dmScale",          ctypes.c_short),
        ("dmCopies",         ctypes.c_short),
        ("dmDefaultSource",  ctypes.c_short),
        ("dmPrintQuality",   ctypes.c_short),
        ("dmColor",          ctypes.c_short),
        ("dmDuplex",         ctypes.c_short),
        ("dmYResolution",    ctypes.c_short),
        ("dmTTOption",       ctypes.c_short),
        ("dmCollate",        ctypes.c_short),
        ("dmFormName",       ctypes.c_char * 32),
        ("dmLogPixels",      ctypes.c_ushort),
        ("dmBitsPerPel",     ctypes.c_ulong),
        ("dmPelsWidth",      ctypes.c_ulong),
        ("dmPelsHeight",     ctypes.c_ulong),
        ("dmDisplayFlags",   ctypes.c_ulong),
        ("dmDisplayFrequency",ctypes.c_ulong),
        ("dmICMMethod",      ctypes.c_ulong),
        ("dmICMIntent",      ctypes.c_ulong),
        ("dmMediaType",      ctypes.c_ulong),
        ("dmDitherType",     ctypes.c_ulong),
        ("dmReserved1",      ctypes.c_ulong),
        ("dmReserved2",      ctypes.c_ulong),
        ("dmPanningWidth",   ctypes.c_ulong),
        ("dmPanningHeight",  ctypes.c_ulong),
    ]

class DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [
        ("cb",           ctypes.c_ulong),
        ("DeviceName",   ctypes.c_char * 32),
        ("DeviceString", ctypes.c_char * 128),
        ("StateFlags",   ctypes.c_ulong),
        ("DeviceID",     ctypes.c_char * 128),
        ("DeviceKey",    ctypes.c_char * 128),
    ]

def get_current_settings():
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    user32.EnumDisplaySettingsA(None, ENUM_CURRENT_SETTINGS, ctypes.byref(dm))
    return {
        "width":      int(dm.dmPelsWidth),
        "height":     int(dm.dmPelsHeight),
        "frequency":  int(dm.dmDisplayFrequency),
        "bitsPerPel": int(dm.dmBitsPerPel),
    }

def get_available_modes():
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    modes = set()
    i = 0
    while user32.EnumDisplaySettingsA(None, i, ctypes.byref(dm)):
        modes.add((int(dm.dmPelsWidth), int(dm.dmPelsHeight), int(dm.dmDisplayFrequency)))
        i += 1
    return [{"width": w, "height": h, "frequency": f} for w, h, f in sorted(modes)]

def switch_mode(width, height, frequency):
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    user32.EnumDisplaySettingsA(None, ENUM_CURRENT_SETTINGS, ctypes.byref(dm))
    dm.dmPelsWidth = int(width)
    dm.dmPelsHeight = int(height)
    dm.dmDisplayFrequency = int(frequency)
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
    result = user32.ChangeDisplaySettingsA(ctypes.byref(dm), CDS_UPDATEREGISTRY)
    return result, ERROR_MAP.get(result, "未知错误 (代码: %s)" % result)

def safe(fn, default=None):
    try:
        return fn()
    except Exception as e:
        return default

# ----------------------------------------------------------------------------
# 1.5 硬件识别 (CPU / GPU / 显示器型号) —— 无第三方依赖，纯 Win32 + 注册表
# ----------------------------------------------------------------------------
def get_cpu_info():
    """CPU 型号，来自注册表 ProcessorNameString。"""
    import winreg
    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                         r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
    val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
    val = (val or "").strip()
    # 折叠多余空格： "Intel(R) Core(TM) i7-13700K  @ 3.60GHz" -> 单行
    return " ".join(val.split())

def _is_integrated_gpu(name):
    """判断是否为核显（仅保留独显）。"""
    n = name.lower()
    if "intel" in n and "arc" not in n:        # Intel UHD / Iris 核显（Arc 为独显）
        return True
    if "radeon" in n and "graphics" in n:      # AMD APU 内置显卡 (Radeon(TM) Graphics)
        return True
    if "uhd graphics" in n or "iris" in n or "vega" in n:
        return True
    return False

def get_gpu_info():
    """GPU 型号列表（仅独显），使用 EnumDisplayDevices 枚举显示适配器。"""
    gpus = []
    if not user32:
        return gpus
    dd = DISPLAY_DEVICE()
    dd.cb = ctypes.sizeof(DISPLAY_DEVICE)
    i = 0
    while user32.EnumDisplayDevicesA(None, i, ctypes.byref(dd), 0):
        name = dd.DeviceString.decode("ascii", "ignore").strip()
        # 过滤掉 RDP / 基础显示适配器等伪设备
        low = name.lower()
        if name and "rdp" not in low and "basic" not in low and "microsoft" not in low:
            if name not in gpus:
                gpus.append(name)
        i += 1
        dd = DISPLAY_DEVICE()
        dd.cb = ctypes.sizeof(DISPLAY_DEVICE)
    # 仅保留独显；若过滤后为空（无独显）则回退保留全部，避免无显示
    discrete = [g for g in gpus if not _is_integrated_gpu(g)]
    return discrete or gpus

def _parse_edid_name(edid):
    """从 EDID 二进制中解析显示器型号字符串 (descriptor type 0xFC)。"""
    if not edid or len(edid) < 128:
        return None
    for off in (54, 72, 90, 108):  # 四个 18 字节描述符块
        try:
            if edid[off + 3] != 0xFC:   # 0xFC = Monitor Name
                continue
            raw = edid[off + 5:off + 18]
            name = raw.split(b"\x0a")[0]          # 截断换行
            name = name.decode("ascii", "ignore")
            name = name.replace("\x00", "").strip()
            if name:
                return name
        except Exception:
            continue
    return None

def _parse_edid_manufacturer(edid):
    """从 EDID 头两个字节解析 3 字母厂商代码（如 AOC/LG/SDC）。"""
    if not edid or len(edid) < 10:
        return None
    try:
        b8, b9 = edid[8], edid[9]
        c1 = (b8 >> 2) & 0x1F
        c2 = ((b8 & 0x03) << 3) | ((b9 >> 5) & 0x07)
        c3 = b9 & 0x1F
        mfr = "".join(chr(0x40 + c) for c in (c1, c2, c3))
        return mfr if mfr.isalpha() else None
    except Exception:
        return None

def _display_name_from_edid(edid):
    """组合厂商 + 型号，与 NVIDIA 控制面板显示格式一致。

    若 0xFC 描述符本身已包含厂商前缀（如 'LG UltraGear'），则不再重复拼接。
    """
    name = _parse_edid_name(edid)
    if not name:
        return None
    mfr = _parse_edid_manufacturer(edid)
    if not mfr:
        return name
    # 已以厂商名（或厂商名加空格）开头 → 不再拼
    head = name[:len(mfr)].upper()
    if head == mfr.upper() or head == (mfr.upper() + " ")[:len(mfr)]:
        return name
    if name.upper().startswith(mfr.upper() + " "):
        return name
    return f"{mfr} {name}"

def get_monitor_models():
    """已连接显示器真实型号（数据源与 NVIDIA 控制面板一致）。

    关键修复：NVIDIA 控制面板读取的是各显示器实例自己的 EDID
    （HKLM\\SYSTEM\\CurrentControlSet\\Enum\\DISPLAY\\*\\*\\Device Parameters\\EDID），
    并解析其中的 0xFC 型号描述符 + EDID 厂商代码，组合为与面板一致的
    完整显示名称（如 AOC 25G4S）。原先代码读的是 Monitor 类
    （{4d36e96e...}）路径，该路径在本机 EDID 为空，只能回退到
    "Generic PnP Monitor"，故与面板不一致。现改为优先读 Enum\\DISPLAY
    的真实 EDID。
    """
    import winreg
    models, seen = [], set()

    def add(name):
        if name and name not in seen:
            seen.add(name)
            models.append(name)

    def _enum_display_edid():
        base = r"SYSTEM\CurrentControlSet\Enum\DISPLAY"
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
        except OSError:
            return
        i = 0
        while True:
            try:
                pnp = winreg.EnumKey(k, i)
            except OSError:
                break
            i += 1
            try:
                k2 = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + "\\" + pnp)
            except OSError:
                continue
            j = 0
            while True:
                try:
                    inst = winreg.EnumKey(k2, j)
                except OSError:
                    break
                j += 1
                try:
                    edid_k = winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE,
                        base + "\\" + pnp + "\\" + inst + "\\Device Parameters")
                    edid, _ = winreg.QueryValueEx(edid_k, "EDID")
                    name = _display_name_from_edid(bytes(edid))
                    if name:
                        add(name)
                except OSError:
                    pass

    # 1) 首选：每个显示器实例的真实 EDID（与 NVIDIA 面板同源）
    _enum_display_edid()
    # 2) 回退：Monitor 类 DriverDesc（如 Generic PnP Monitor）
    if not models:
        base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e96e-e325-11ce-bfc1-08002be10318}"
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
        except OSError:
            return models
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(k, i)
            except OSError:
                break
            i += 1
            try:
                sk = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + "\\" + sub)
                desc, _ = winreg.QueryValueEx(sk, "DriverDesc")
                if desc and desc.strip():
                    add(desc.strip())
            except OSError:
                pass
    return models

def get_system_info():
    return {
        "cpu":     safe(get_cpu_info) or "未识别",
        "gpu":     safe(get_gpu_info, []) or ["未识别"],
        "monitor": safe(get_monitor_models, []) or ["未识别显示器"],
    }

# ----------------------------------------------------------------------------
# 2. 配置持久化 (JSON) + 注册表自启
# ----------------------------------------------------------------------------
CONFIG_DIR  = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "DisplaySwitcher")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
REVERT_SECONDS = 15

DEFAULT_CFG = {
    "profiles": [],
    "settings": {
        "autostart": False,
        "confirm":   True,
        "particle":  True,
        "glow":      0.7,
        "accent":    [55, 224, 212],
    },
    "active_profile": None,
}

cfg = json.loads(json.dumps(DEFAULT_CFG))  # deep copy

def load_cfg():
    global cfg
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = json.loads(json.dumps(DEFAULT_CFG))
        merged.update({k: v for k, v in data.items() if k in DEFAULT_CFG})
        for k in DEFAULT_CFG["settings"]:
            if k in (data.get("settings") or {}):
                merged["settings"][k] = data["settings"][k]
        cfg = merged
    except Exception:
        cfg = json.loads(json.dumps(DEFAULT_CFG))
    # 自启状态以注册表为准
    cfg["settings"]["autostart"] = get_autostart()

def save_cfg():
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("save_cfg error:", e)

def get_exe_path():
    return sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)

def get_autostart():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run")
        winreg.QueryValueEx(key, "DisplaySwitcher")
        return True
    except Exception:
        return False

def set_autostart(enabled):
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        if enabled:
            winreg.SetValueEx(key, "DisplaySwitcher", 0, winreg.REG_SZ, get_exe_path())
        else:
            try:
                winreg.DeleteValue(key, "DisplaySwitcher")
            except FileNotFoundError:
                pass
        cfg["settings"]["autostart"] = bool(enabled)
        return bool(enabled)
    except Exception as e:
        return {"ok": False, "message": str(e)}

# ----------------------------------------------------------------------------
# 3. 应用 / 回退 (切换时确认 + 自动回退)
# ----------------------------------------------------------------------------
_lock = threading.Lock()
prev_settings = None
revert_timer = None

def _do_revert():
    global prev_settings, revert_timer
    with _lock:
        p = prev_settings
        prev_settings = None
        revert_timer = None
    if p:
        switch_mode(p["width"], p["height"], p["frequency"])

def apply_mode(width, height, frequency, confirm=None):
    global prev_settings, revert_timer
    if confirm is None:
        confirm = cfg["settings"]["confirm"]
    cur = safe(get_current_settings)
    rc, msg = switch_mode(width, height, frequency)
    if rc == DISP_CHANGE_SUCCESSFUL:
        if confirm:
            with _lock:
                prev_settings = cur
                if revert_timer:
                    revert_timer.cancel()
                revert_timer = threading.Timer(REVERT_SECONDS, _do_revert)
                revert_timer.start()
        return {"ok": True, "message": "已应用显示设置", "current": safe(get_current_settings)}
    return {"ok": False, "message": msg, "code": rc}

def confirm_keep():
    global prev_settings, revert_timer
    with _lock:
        if revert_timer:
            revert_timer.cancel()
        revert_timer = None
        prev_settings = None
    return {"ok": True, "message": "已保持当前设置"}

def revert_now():
    _do_revert()
    return {"ok": True, "message": "已回退到切换前设置"}

# ----------------------------------------------------------------------------
# 4. 状态聚合
# ----------------------------------------------------------------------------
def get_state():
    return {
        "current":  safe(get_current_settings),
        "modes":    safe(get_available_modes, []),
        "profiles": cfg["profiles"],
        "settings": cfg["settings"],
        "active_profile": cfg.get("active_profile"),
    }

# ----------------------------------------------------------------------------
# 5. HTTP REST API + 静态资源
# ----------------------------------------------------------------------------
WEBROOT = None

def _webroot():
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "webroot")

# ----------------------------------------------------------------------------
# 5.5 检查更新 (GitHub Release)
# ----------------------------------------------------------------------------
APP_VERSION = "4.2"
GITHUB_REPO = "fourth-generation-winter/DisplaySwitcher"

def _parse_ver(s):
    """'v3.0' / '3.0' -> (3, 0)。仅取前两段数字用于比较。"""
    s = (s or "").lstrip("vV").strip()
    parts = []
    for seg in s.split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while len(parts) < 2:
        parts.append(0)
    return tuple(parts[:2])

def _github_open(url, timeout=8):
    req = urllib.request.Request(
        url, headers={"User-Agent": "DisplaySwitcher/%s" % APP_VERSION,
                      "Accept": "application/vnd.github+json"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e:
        # Windows 上个别环境 OpenSSL 默认证书链缺失，回退为不校验（仅用于公开 Release 信息）
        if isinstance(getattr(e, "reason", None), ssl.SSLError):
            return urllib.request.urlopen(req, timeout=timeout,
                                          context=ssl._create_unverified_context())
        raise

def check_for_update():
    try:
        with _github_open("https://api.github.com/repos/%s/releases/latest" % GITHUB_REPO) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name") or ""
        latest = _parse_ver(tag)
        current = _parse_ver(APP_VERSION)
        assets = data.get("assets") or []
        dl = (assets[0].get("browser_download_url") if assets else None) or data.get("html_url")
        return {
            "ok": True,
            "current": "v" + APP_VERSION,
            "latest": tag or ("v" + APP_VERSION),
            "update_available": latest > current,
            "download_url": dl,
            "release_url": data.get("html_url"),
            "name": data.get("name"),
            "published_at": data.get("published_at"),
            "notes": (data.get("body") or "")[:2000],
        }
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": True, "current": "v" + APP_VERSION, "latest": None,
                    "update_available": False, "message": "暂无已发布的 Release"}
        if e.code == 403:
            return {"ok": False, "message": "GitHub API 速率限制，请稍后再试"}
        return {"ok": False, "message": "GitHub 返回错误 %s" % e.code}
    except Exception as e:
        return {"ok": False, "message": "无法连接 GitHub：%s" % str(e)[:120]}


# ===== 自动更新下载管理器（直连 GitHub → 低速/停滞自动切换镜像代理）=====
_DL = {
    "running": False, "downloaded": 0, "total": 0,
    "url": "", "source": "github", "mirror": False,
    "done": False, "error": None, "path": "", "aborted": False,
}
_DL_LOCK = threading.Lock()
_GITHUB_MIRROR = "https://mirror.ghproxy.com/"


class _SwitchToMirror(Exception):
    """直连 GitHub 下载过慢/停滞，需切到镜像代理。"""
    pass


class _Aborted(Exception):
    pass


class _SockBuf:
    """对 socket 的带缓冲读取，支持按行读取（解析分块编码 / 响应头）。"""
    def __init__(self, sock):
        self.s = sock
        self.buf = b""

    def _fill(self):
        c = self.s.recv(65536)
        if not c:
            raise IOError("连接已关闭")
        self.buf += c

    def read_line(self):
        while b"\r\n" not in self.buf:
            self._fill()
        line, self.buf = self.buf.split(b"\r\n", 1)
        return line

    def take(self, n):
        while len(self.buf) < n:
            self._fill()
        out = self.buf[:n]
        self.buf = self.buf[n:]
        return out


def _raw_download(src, dest, name, use_mirror, switched, depth=0):
    """原生 socket 下载（HTTPS 经 ssl 包装）。

    关键可靠性设计：每个 recv 都显式 settimeout(8)。Windows 上 urllib 的缓冲
    读会忽略 Python 级超时、直到系统 TCP 超时(约 21s)才报错，导致无法及时切镜像；
    而直接对 socket 设置 recv 超时，停滞连接会在 8s 内抛 socket.timeout，从而可靠
    触发切镜像。直连阶段若平均速率 <80KB/s 持续 >6s 也主动切镜像。
    返回最终字节数；遇到 3xx 重定向递归跟随（最多 6 跳）。
    """
    if depth > 6:
        raise IOError("重定向次数过多")
    parsed = urlparse(src)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    ctx = ssl.create_default_context()
    try:
        s = socket.create_connection((host, port), timeout=10)
        if parsed.scheme == "https":
            s = ctx.wrap_socket(s, server_hostname=host)
    except ssl.SSLError:
        # 个别 Windows 环境缺 OpenSSL 证书链，回退为不校验（仅用于公开 Release 文件）
        ctx = ssl._create_unverified_context()
        s = socket.create_connection((host, port), timeout=10)
        if parsed.scheme == "https":
            s = ctx.wrap_socket(s, server_hostname=host)
    s.settimeout(8)  # 每个 recv 的硬超时 → 停滞连接 8s 内必抛 socket.timeout
    req = ("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: DisplaySwitcher/%s\r\n"
           "Accept: */*\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n"
           ) % (path, host, APP_VERSION)
    s.sendall(req.encode("utf-8"))

    # 读响应头（以 \r\n\r\n 为界）
    hdr = b""
    while b"\r\n\r\n" not in hdr:
        try:
            c = s.recv(4096)
        except socket.timeout:
            raise
        if not c:
            raise IOError("连接提前关闭（响应头）")
        hdr += c
        if len(hdr) > 65536:
            raise IOError("响应头异常")
    hb, _, body0 = hdr.partition(b"\r\n\r\n")
    lines = hb.split(b"\r\n")
    try:
        code = int(lines[0].decode("utf-8", "ignore").split(" ")[1])
    except Exception:
        raise IOError("无法解析响应状态行")
    hmap = {}
    for ln in lines[1:]:
        if b":" in ln:
            k, _, v = ln.partition(b":")
            hmap[k.decode("utf-8", "ignore").strip().lower()] = v.decode("utf-8", "ignore").strip()
    if code in (301, 302, 303, 307, 308):
        loc = hmap.get("location", "")
        if not loc:
            raise IOError("重定向但缺失 Location")
        if loc.startswith("/"):
            loc = "%s://%s%s" % (parsed.scheme, host, loc)
        try:
            s.close()
        except Exception:
            pass
        return _raw_download(loc, dest, name, use_mirror, switched, depth + 1)  # 跟随重定向
    if code != 200:
        try:
            s.close()
        except Exception:
            pass
        raise IOError("HTTP %d" % code)

    total = int(hmap.get("content-length", "0") or 0)
    chunked = hmap.get("transfer-encoding", "").lower() == "chunked"
    with _DL_LOCK:
        _DL["total"] = total
        _DL["url"] = src
        _DL["source"] = name
        _DL["mirror"] = (name == "mirror")

    dl = 0
    t0 = time.time()

    def _progress(n):
        nonlocal dl
        dl += n
        with _DL_LOCK:
            _DL["downloaded"] = dl
        now = time.time()
        # 直连阶段持续低速 → 主动切镜像
        if (name == "github" and use_mirror and not switched["v"]
                and now - t0 > 6 and (dl / (now - t0)) < 80 * 1024):
            raise _SwitchToMirror()

    f = open(dest, "wb")
    try:
        if chunked:
            br = _SockBuf(s)
            br.buf = body0
            while True:
                if _DL.get("aborted"):
                    raise _Aborted()
                size_line = br.read_line().strip()
                if not size_line:
                    continue
                try:
                    size = int(size_line.split(b";")[0], 16)
                except ValueError:
                    raise IOError("分块长度解析失败")
                if size == 0:
                    break
                data = br.take(size)
                f.write(data)
                br.take(2)  # 块尾 \r\n
                _progress(len(data))
        else:
            if body0:
                f.write(body0)
                _progress(len(body0))
            while True:
                if _DL.get("aborted"):
                    raise _Aborted()
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    raise  # 停滞超时 → 由 worker 切镜像
                if not chunk:
                    break
                f.write(chunk)
                _progress(len(chunk))
    finally:
        try:
            f.close()
        except Exception:
            pass
        try:
            s.close()
        except Exception:
            pass
    return dl


def _dl_worker(url, dest):
    """后台线程：先直连 GitHub，若连接超时/停滞/速率过低则切换镜像代理重试。"""
    global _DL
    with _DL_LOCK:
        _DL.update({"running": True, "downloaded": 0, "total": 0, "done": False,
                    "error": None, "path": dest, "aborted": False, "mirror": False,
                    "source": "github"})
    use_mirror = url.startswith("https://github.com/")
    switched = {"v": False}
    sources = [("github", url)]
    if use_mirror:
        sources.append(("mirror", _GITHUB_MIRROR + url))
    for name, src in sources:
        if _DL.get("aborted"):
            break
        try:
            final = _raw_download(src, dest, name, use_mirror, switched)
        except _SwitchToMirror:
            if name == "github" and use_mirror:
                switched["v"] = True
                with _DL_LOCK:
                    _DL["source"] = "mirror"
                    _DL["mirror"] = True
                    _DL["url"] = _GITHUB_MIRROR + url
                continue
            with _DL_LOCK:
                _DL["error"] = "直连与镜像代理均下载过慢，请改为手动下载"
                _DL["running"] = False
            return
        except socket.timeout:
            if name == "github" and use_mirror:
                switched["v"] = True
                with _DL_LOCK:
                    _DL["source"] = "mirror"
                    _DL["mirror"] = True
                    _DL["url"] = _GITHUB_MIRROR + url
                continue
            with _DL_LOCK:
                _DL["error"] = "下载连接超时（停滞），请检查网络或手动下载"
                _DL["running"] = False
            return
        except _Aborted:
            with _DL_LOCK:
                _DL["error"] = "已取消"
                _DL["running"] = False
            return
        except Exception as e:
            if name == "github" and use_mirror and not switched["v"]:
                switched["v"] = True
                with _DL_LOCK:
                    _DL["source"] = "mirror"
                    _DL["mirror"] = True
                    _DL["url"] = _GITHUB_MIRROR + url
                continue
            with _DL_LOCK:
                _DL["error"] = str(e)[:220]
                _DL["running"] = False
            return
        # 成功完成
        with _DL_LOCK:
            _DL["done"] = True
            _DL["running"] = False
            _DL["downloaded"] = final
            if _DL.get("total", 0) == 0:
                _DL["total"] = final
        return
    with _DL_LOCK:
        _DL["running"] = False


def start_download(url):
    with _DL_LOCK:
        if _DL.get("running"):
            return False
    name = os.path.basename(url.split("?")[0]) or "DisplaySwitcher_update.exe"
    dest = os.path.join(os.path.expanduser("~"), "Downloads", name)
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
    except Exception:
        dest = os.path.join(os.environ.get("TEMP", "/tmp"), name)
    threading.Thread(target=_dl_worker, args=(url, dest), daemon=True).start()
    return True


def open_url_in_browser(url):
    try:
        webbrowser.open(url)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


def open_folder(path):
    d = os.path.dirname(path) if os.path.isfile(path) else path
    try:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b""
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/state":
            return self._json(200, get_state())
        if p == "/api/system":
            return self._json(200, get_system_info())
        if p == "/api/update/check":
            return self._json(200, check_for_update())
        if p == "/api/update/progress":
            with _DL_LOCK:
                return self._json(200, dict(_DL))
        if p.startswith("/api/open_url"):
            url = parse_qs(urlparse(self.path).query).get("url", [""])[0]
            return self._json(200, open_url_in_browser(url) if url else {"ok": False, "error": "missing url"})
        if p.startswith("/api/open_folder"):
            path = parse_qs(urlparse(self.path).query).get("path", [""])[0]
            return self._json(200, open_folder(path) if path else {"ok": False, "error": "missing path"})
        if p == "/api/settings":
            return self._json(200, cfg["settings"])
        if p.startswith("/api/window/"):
            act = p.rsplit("/", 1)[-1]
            _win_op(act, self.path)
            return self._json(200, {"ok": True, "action": act})
        if p.startswith("/api/"):
            return self._json(404, {"ok": False, "message": "not found"})
        self._serve_static(p)

    def do_POST(self):
        p = self.path.split("?")[0]
        d = self._read_body()
        if p == "/api/apply":
            r = apply_mode(d.get("width"), d.get("height"), d.get("frequency"),
                           d.get("confirm"))
            return self._json(200 if r.get("ok") else 400, r)
        if p == "/api/profile":
            prof = {
                "id": uuid.uuid4().hex[:8],
                "name": str(d.get("name", "自定义方案")).strip() or "自定义方案",
                "width": int(d.get("width")),
                "height": int(d.get("height")),
                "frequency": int(d.get("frequency")),
            }
            cfg["profiles"].append(prof)
            save_cfg()
            return self._json(200, {"ok": True, "id": prof["id"], "profile": prof})
        if p == "/api/profile/apply":
            prof = next((x for x in cfg["profiles"] if x["id"] == d.get("id")), None)
            if not prof:
                return self._json(404, {"ok": False, "message": "方案不存在"})
            r = apply_mode(prof["width"], prof["height"], prof["frequency"])
            if r.get("ok"):
                cfg["active_profile"] = prof["id"]
                save_cfg()
            return self._json(200 if r.get("ok") else 400, r)
        if p == "/api/confirm":
            return self._json(200, confirm_keep())
        if p == "/api/revert":
            return self._json(200, revert_now())
        if p == "/api/update/download":
            url = d.get("url") or ""
            if not url:
                return self._json(400, {"ok": False, "message": "缺少下载地址"})
            ok = start_download(url)
            return self._json(200, {"ok": ok, "message": "已启动下载" if ok else "已有下载任务进行中"})
        if p == "/api/update/cancel":
            with _DL_LOCK:
                _DL["aborted"] = True
            return self._json(200, {"ok": True})
        if p == "/api/autostart":
            res = set_autostart(bool(d.get("enabled")))
            save_cfg()
            return self._json(200, {"ok": True, "enabled": get_autostart()})
        if p == "/api/settings":
            for k in DEFAULT_CFG["settings"]:
                if k in d:
                    cfg["settings"][k] = d[k]
            if "autostart" in d:
                set_autostart(bool(d["autostart"]))
            save_cfg()
            return self._json(200, cfg["settings"])
        if p.startswith("/api/window/"):
            act = p.rsplit("/", 1)[-1]
            _win_op(act, self.path)
            return self._json(200, {"ok": True, "action": act})
        return self._json(404, {"ok": False, "message": "not found"})

    def do_DELETE(self):
        p = self.path.split("?")[0]
        if p.startswith("/api/profile/"):
            pid = p.rsplit("/", 1)[-1]
            before = len(cfg["profiles"])
            cfg["profiles"] = [x for x in cfg["profiles"] if x["id"] != pid]
            if cfg.get("active_profile") == pid:
                cfg["active_profile"] = None
            save_cfg()
            return self._json(200, {"ok": before != len(cfg["profiles"])})
        return self._json(404, {"ok": False, "message": "not found"})

    def _serve_static(self, p):
        root = WEBROOT or _webroot()
        rel = p.lstrip("/")
        if rel == "" or rel == "index.html":
            rel = "index.html"
        path = os.path.normpath(os.path.join(root, rel))
        if not path.startswith(os.path.normpath(root)):
            return self._json(403, {"ok": False, "message": "forbidden"})
        if not os.path.isfile(path):
            return self._json(404, {"ok": False, "message": "not found"})
        ctype = "text/html; charset=utf-8" if path.endswith(".html") else \
                "application/javascript" if path.endswith(".js") else \
                "text/css" if path.endswith(".css") else "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

def find_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port

def start_server():
    global WEBROOT
    WEBROOT = _webroot()
    env_port = int(os.environ.get("DS_PORT") or 0)
    port = env_port if env_port else find_free_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return port

# ----------------------------------------------------------------------------
# 6. 原生窗口 (wx.WebView 渲染前端 UI)
# ----------------------------------------------------------------------------
def wait_server(port, tries=80):
    import socket as _s
    for _ in range(tries):
        try:
            c = _s.create_connection(("127.0.0.1", port), timeout=0.2)
            c.close()
            return True
        except Exception:
            time.sleep(0.05)
    return False

def _show_error_box(msg, title="DisplaySwitcher"):
    """尽量用原生 MessageBox 弹出错误（即便 wx 尚未就绪也能用），并落日志。"""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(os.path.join(CONFIG_DIR, "error.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(0, str(msg), str(title), 0x10 | 0x0)
    except Exception:
        pass

def start_gui(url):
    import wx
    import wx.html2
    log = os.path.join(CONFIG_DIR, "error.log")
    def fatal(msg):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        except Exception:
            pass
    def _resource_path(relative_path):
        """Get absolute path to resource, works for dev and for PyInstaller."""
        if hasattr(sys, '_MEIPASS'):
            return os.path.join(sys._MEIPASS, relative_path)
        base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, relative_path)

    class MainFrame(wx.Frame):
        def __init__(self):
            _set_argtypes()
            super().__init__(None, title="DisplaySwitcher // CONTROL DECK",
                             style=wx.BORDER_NONE, size=(1180, 760))
            self.SetBackgroundColour(wx.Colour(8, 12, 18))
            self.hwnd = self.GetHandle()
            self._normal_rect = None
            self._maxed = False
            # 无边框下必须补回 WS_CLIPCHILDREN/WS_CLIPSIBLINGS，否则 WebView 子窗口不会被绘制（黑屏）
            try:
                style = user32.GetWindowLongPtrW(self.hwnd, GWL_STYLE)
                user32.SetWindowLongPtrW(self.hwnd, GWL_STYLE,
                                         style | WS_CLIPCHILDREN | WS_CLIPSIBLINGS)
                user32.SetWindowPos(self.hwnd, 0, 0, 0, 0, 0,
                                    SWP_FRAMECHANGED | SWP_NOZORDER | SWP_NOMOVE | SWP_NOSIZE)
            except Exception as e:
                fatal("fix frame styles failed: %r" % e)
            # 设置窗口/任务栏图标
            try:
                icon_path = _resource_path("webroot/assets/DisplaySwitcher.ico")
                if os.path.exists(icon_path):
                    icon = wx.Icon(icon_path, wx.BITMAP_TYPE_ICO)
                    self.SetIcon(icon)
            except Exception as e:
                fatal("SetIcon failed: %r" % e)
            # 原生外观：圆角 + 阴影 + 自定义边缘缩放 + 双击最大化（不依赖 WS_THICKFRAME，无白边）
            self._apply_round()
            self._apply_shadow()
            self._install_wndproc()
            self._panel = wx.Panel(self, style=wx.WANTS_CHARS | wx.FULL_REPAINT_ON_RESIZE)
            sizer = wx.BoxSizer(wx.VERTICAL)
            try:
                self.web = wx.html2.WebView.New(self._panel)
                self.web.LoadURL(url)
                self.web.Bind(wx.html2.EVT_WEBVIEW_NEWWINDOW, self._on_web_new_window)
                sizer.Add(self.web, 1, wx.EXPAND)
            except Exception as e:
                fatal("WebView.New failed: %r" % e)
                import webbrowser
                webbrowser.open(url)
                wx.MessageBox("无法加载内嵌浏览器 (WebView2)：%s\n\n已改在默认浏览器中打开控制台。\n"
                              "若要原生窗口，请安装 Microsoft Edge WebView2 运行时。" % e,
                              "DisplaySwitcher", wx.OK | wx.ICON_WARNING)
                self.web = None
            self._panel.SetSizer(sizer)
            self.SetSizer(wx.BoxSizer(wx.VERTICAL))
            self.GetSizer().Add(self._panel, 1, wx.EXPAND)
            self.Bind(wx.EVT_SIZE, self._on_size)
            self.Centre()

        def _on_size(self, event):
            try:
                self.Layout()
                if self.web and self._panel:
                    self.web.SetSize(self._panel.GetClientSize())
            except Exception:
                pass
            if event:
                event.Skip()

        def _on_web_new_window(self, event):
            # 外链（仓库、下载页等）用系统默认浏览器打开，而非在 WebView 内导航
            url = event.GetURL()
            if url:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
            event.Veto()

        def _refresh_layout(self):
            self.Layout()
            self._on_size(None)
            self.Refresh()

        # ---- 原生外观：圆角 / 阴影 / 边缘命中 / 最大化 ----
        def _apply_round(self):
            if not dwmapi:
                return
            try:
                v = ctypes.c_int(DWMWCP_ROUND)
                dwmapi.DwmSetWindowAttribute(self.hwnd,
                                            DWMWA_WINDOW_CORNER_PREFERENCE,
                                            ctypes.byref(v), 4)
            except Exception:
                pass

        def _remove_round(self):
            if not dwmapi:
                return
            try:
                v = ctypes.c_int(DWMWCP_DONOTROUND)
                dwmapi.DwmSetWindowAttribute(self.hwnd,
                                            DWMWA_WINDOW_CORNER_PREFERENCE,
                                            ctypes.byref(v), 4)
            except Exception:
                pass

        def _apply_shadow(self):
            try:
                cur = user32.GetClassLongW(self.hwnd, GCL_STYLE)
                user32.SetClassLongW(self.hwnd, GCL_STYLE, cur | CS_DROPSHADOW)
            except Exception:
                pass

        def _toggle_max(self):
            if self._maxed:
                # 还原：必须先置标志，再 SetWindowPos，否则 WM_SIZE 回调仍以
                # _maxed=True 读到旧值而错误地保留「去圆角」，导致还原后缺圆角。
                self._maxed = False
                self._apply_round()
                if self._normal_rect:
                    x, y, w, h = self._normal_rect
                    user32.SetWindowPos(self.hwnd, 0, x, y, w, h,
                                       SWP_FRAMECHANGED | SWP_NOZORDER)
            else:
                r = ctypes.wintypes.RECT()
                user32.GetWindowRect(self.hwnd, ctypes.byref(r))
                self._normal_rect = (r.left, r.top,
                                     r.right - r.left, r.bottom - r.top)
                wa = _get_work_area(self.hwnd)
                # 关键：先置 _maxed=True 并主动去掉圆角，再 SetWindowPos。
                # 否则 SetWindowPos 同步触发的 WM_SIZE 会以旧值(_maxed=False)
                # 误调 _apply_round()，圆角内缩在最大化后留出白边空隙。
                self._maxed = True
                self._remove_round()
                user32.SetWindowPos(self.hwnd, 0, wa[0], wa[1], wa[2], wa[3],
                                   SWP_FRAMECHANGED | SWP_NOZORDER)
            wx.CallAfter(self._refresh_layout)

        def _install_wndproc(self):
            """子类化 WndProc（无边框窗口外观与最大化圆角控制，不依赖 WS_THICKFRAME）：
            - 仅接管 WM_SIZE：依据 self._maxed 在尺寸真正变更后切换圆角——
              最大化去圆角（消除圆角内缩留出的白边空隙），还原恢复圆角；
            - 窗口拖动 / 边缘缩放 / 双击最大化 全部由前端 JS（Pointer Events +
              setPointerCapture）驱动，经 /api/window/* 让 Python 以确定性增量
              SetWindowPos 完成，不再使用 SendMessage(WM_NCLBUTTONDOWN) 模态循环，
              从而绕开 WebView2 对自己子窗口 SetCapture 导致的瞬移、以及模态循环
              吞掉 dblclick 等问题，同时保留「无白边、无 Aero 贴边」的纯净外观。
            关键：回调 cb 内部必须 100% 不向外抛异常，否则会冲进 Windows 消息泵导致崩溃。
            """
            if not user32:
                return
            try:
                # 确保指针返回类型正确（64 位下不截断），否则 old 指针损坏 → 崩溃
                user32.GetWindowLongPtrW.restype = ctypes.c_void_p
                user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
                user32.SetWindowLongPtrW.restype = ctypes.c_void_p
                user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
                # sizing 相关 API 的 argtypes（64 位下必须显式，否则指针/坐标截断 → 崩溃）
                user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                user32.GetWindowRect.restype = ctypes.c_int
                user32.SetWindowPos.restype = ctypes.c_int
                user32.SetCapture.argtypes = [ctypes.c_void_p]
                user32.SetCapture.restype = ctypes.c_void_p
                user32.ReleaseCapture.argtypes = []
                user32.ReleaseCapture.restype = ctypes.c_int
                user32.GetCursorPos.argtypes = [ctypes.c_void_p]
                user32.GetCursorPos.restype = ctypes.c_int

                old = user32.GetWindowLongPtrW(self.hwnd, GWL_WNDPROC)
                if not old:
                    return
                self._old_wndproc = old

                def cb(hwnd, msg, wparam, lparam):
                    try:
                        # 仅接管 WM_SIZE：依据 self._maxed 切换圆角。注意 _toggle_max
                        # 已保证在 SetWindowPos 之前正确置位 _maxed，故此处读到的值
                        # 与实际状态一致（最大化去圆角、还原恢复圆角）。
                        if msg == WM_SIZE:
                            if self._maxed:
                                self._remove_round()
                            else:
                                self._apply_round()
                    except Exception:
                        pass
                    # 其余消息（含异常兜底）一律转发给原 WndProc
                    try:
                        return user32.CallWindowProcW(old, hwnd, msg, wparam, lparam)
                    except Exception:
                        return 0

                proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                          ctypes.c_ulong, ctypes.c_void_p,
                                          ctypes.c_void_p)
                self._new_wndproc = proto(cb)
                user32.SetWindowLongPtrW(self.hwnd, GWL_WNDPROC, self._new_wndproc)
            except Exception:
                # 子类化失败绝不影响窗口打开：保持原生行为即可
                pass
    try:
        app = wx.App(False)
    except Exception as e:
        _show_error_box("wx.App 初始化失败：%r\n\n请确认已安装 Microsoft Edge WebView2 运行时。" % e)
        raise
    try:
        frame = MainFrame()
    except Exception as e:
        _show_error_box("主窗口初始化失败：%r" % e)
        raise
    global GUI_FRAME
    GUI_FRAME = frame
    frame.Show()
    wx.CallAfter(frame._refresh_layout)
    try:
        app.MainLoop()
    except Exception as e:
        _show_error_box("运行时异常：%r" % e)

# ----------------------------------------------------------------------------
# 7. 入口
# ----------------------------------------------------------------------------
def run_cli_switch(args):
    if "--switch" in args:
        i = args.index("--switch")
        try:
            w, h, f = int(args[i + 1]), int(args[i + 2]), int(args[i + 3])
        except Exception:
            print("用法: DisplaySwitcher --switch <宽> <高> <刷新率>")
            return 1
        rc, msg = switch_mode(w, h, f)
        print(msg)
        return 0 if rc == DISP_CHANGE_SUCCESSFUL else 1
    return 1

# ----------------------------------------------------------------------------
# 1.9 单实例保护 (互斥锁；重复打开时聚焦已有窗口)
# ----------------------------------------------------------------------------
SINGLE_INSTANCE_MUTEX = None

def enforce_single_instance():
    """确保同一时刻只有一个 DisplaySwitcher 实例运行。

    返回 True 表示本进程是首个实例（继续启动）；返回 False 表示已有实例在运行，
    此时会将其窗口恢复到前台并应退出本进程。任何异常都放行，避免单实例逻辑故障
    导致程序无法启动。
    """
    global SINGLE_INSTANCE_MUTEX
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        SINGLE_INSTANCE_MUTEX = kernel32.CreateMutexW(None, False, "DisplaySwitcher_SingleInstance_v1")
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            if user32:
                user32.FindWindowW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
                user32.FindWindowW.restype = ctypes.c_void_p
                user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
                user32.ShowWindow.restype = ctypes.c_int
                user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
                user32.SetForegroundWindow.restype = ctypes.c_int
                hwnd = user32.FindWindowW(None, "DisplaySwitcher // CONTROL DECK")
                if hwnd:
                    user32.ShowWindow(hwnd, 9)        # SW_RESTORE
                    user32.SetForegroundWindow(hwnd)
            return False
        return True
    except Exception:
        return True


def main():
    load_cfg()
    args = sys.argv[1:]
    if "--switch" in args:
        sys.exit(run_cli_switch(args))

    # 单实例：已有实例运行时聚焦其窗口并退出
    if not enforce_single_instance():
        sys.exit(0)

    port = start_server()
    url = "http://127.0.0.1:%d/" % port
    if "--server-only" in args:
        print("DisplaySwitcher 服务已启动:", url, flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return
    wait_server(port)
    try:
        start_gui(url)
    except Exception as e:
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(os.path.join(CONFIG_DIR, "error.log"), "a", encoding="utf-8") as f:
                f.write("[%s] fatal: %r\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), e))
        except Exception:
            pass
        raise

if __name__ == "__main__":
    main()
