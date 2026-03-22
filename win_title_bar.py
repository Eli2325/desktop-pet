"""Windows：浅色应用内与系统标题栏协调（关闭沉浸式深色标题栏）。"""

import sys
import ctypes
from ctypes import wintypes


def force_light_title_bar(win_id: int) -> None:
    """对 HWND 关闭 DWM 沉浸式深色标题栏，使标题栏与浅色 Fusion UI 一致。"""
    if sys.platform != "win32" or not win_id:
        return
    try:
        hwnd = wintypes.HWND(int(win_id))
    except Exception:
        return
    false = ctypes.c_int(0)
    dwm = ctypes.windll.dwmapi
    # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE（Win10 1903+ / Win11）；0 = 浅色标题栏
    for attr in (20, 19):
        try:
            dwm.DwmSetWindowAttribute(
                hwnd,
                ctypes.c_uint(attr),
                ctypes.byref(false),
                ctypes.sizeof(false),
            )
        except Exception:
            pass


def force_light_title_bar_widget(widget) -> None:
    """从 QWidget 取 winId 并应用浅色标题栏（需在窗口已有有效 HWND 后调用，如 showEvent）。"""
    if sys.platform != "win32" or widget is None:
        return
    try:
        wid = int(widget.winId())
    except Exception:
        return
    force_light_title_bar(wid)
