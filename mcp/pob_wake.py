"""Best-effort Windows keepalive/wake for the PoB window.

PoB uses GLFW and can stop pumping frames when its window is occluded or when
the OS decides it is idle.  The bridge lives in PoB's frame loop, so a paused
window means tools time out.  Posting WM_NULL to the top-level window is a
harmless way to poke the message loop; a background thread calls this while the
MCP server is running.

No-op on non-Windows platforms.
"""

from __future__ import annotations

import ctypes
import sys
import time

WM_NULL = 0x0000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010


def _windows_impl():
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p), ctypes.c_void_p]
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    return user32


_USER32 = _windows_impl()


def find_pob_windows() -> list[int]:
    if not _USER32:
        return []
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lparam):
        length = _USER32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _USER32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        # PoB's window title always ends with " - Path of Building".
        if "Path of Building" in title and "DeepSeek" not in title:
            found.append(int(hwnd))
        return True

    _USER32.EnumWindows(callback, 0)
    return found


def _window_rect(hwnd: int):
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    rect = RECT()
    if _USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return rect
    return None


def _nudge_cursor(hwnd: int) -> bool:
    """Move the cursor into the PoB window and put it back.

    GLFW's wait loop wakes on a real mouse-move message, while WM_NULL posted
    from another process does not.  The movement is 1 frame and restored, so it
    is only done immediately before a tool call.
    """
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    rect = _window_rect(hwnd)
    if not rect:
        return False
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return False
    original = POINT()
    if not _USER32.GetCursorPos(ctypes.byref(original)):
        return False
    target_x = rect.left + max(1, width // 2)
    target_y = rect.top + max(1, min(24, height // 2))
    if target_x < 0 or target_y < 0:
        return False
    _USER32.SetCursorPos(target_x, target_y)
    # GLFW needs a frame or two to process the WM_MOUSEMOVE and wake its wait.
    time.sleep(0.30)
    _USER32.SetCursorPos(original.x, original.y)
    return True


def wake_pob(force: bool = False) -> int:
    """Poke PoB windows. force=True also nudges the cursor (tool calls only)."""
    if not _USER32:
        return 0
    windows = find_pob_windows()
    count = 0
    for hwnd in windows:
        _USER32.PostMessageW(hwnd, WM_NULL, 0, 0)
        # A no-op window-position change can break some wait loops without
        # moving, resizing, activating, or changing z-order.
        _USER32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
        count += 1
    if force and windows:
        # Use the largest window (the real PoB UI; there is usually a tiny helper window too).
        main = max(windows, key=lambda h: (_window_rect(h).right - _window_rect(h).left) * (_window_rect(h).bottom - _window_rect(h).top) if _window_rect(h) else 0)
        _nudge_cursor(main)
    return count


def start_keepalive(interval: float = 1.0):
    """Start a daemon thread that pokes PoB. Returns (thread, stop_event)."""
    import threading

    stop = threading.Event()

    def loop():
        while not stop.wait(interval):
            try:
                wake_pob()
            except Exception:  # noqa: BLE001 - keepalive must never crash
                pass

    thread = threading.Thread(target=loop, name="pob-keepalive", daemon=True)
    thread.start()
    return thread, stop


if __name__ == "__main__":
    print("PoB windows:", find_pob_windows())
    while True:
        print("woke", wake_pob())
        time.sleep(1)
