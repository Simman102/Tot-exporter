#   Windows-specific imports
import sys
if sys.platform == 'win32':
    import win32api
    import win32gui
    import win32clipboard
    import win32con
    import ctypes
    import threading
    import time
    import re

    class ClipboardListener:
        def __init__(self):
            self.SERVER_IP = None
            self.IP_ADDRESS_REGEX = re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\:7777$')

        def _read_clipboard(self):
            try:
                win32clipboard.OpenClipboard()
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                    return win32clipboard.GetClipboardData(win32con.CF_TEXT).decode()
                return None
            finally:
                win32clipboard.CloseClipboard()

        def _process_message(self, hwnd, msg, wparam, lparam):
            WM_CLIPBOARDUPDATE = 0x031D
            if msg == WM_CLIPBOARDUPDATE:
                time.sleep(0.1)
                clipboard_content = self._read_clipboard()
                if clipboard_content is not None and self.IP_ADDRESS_REGEX.match(clipboard_content):
                    self.SERVER_IP = clipboard_content[:-5]


        def _create_window(self):
            window_class = win32gui.WNDCLASS()
            window_class.lpfnWndProc = self._process_message
            window_class.lpszClassName = self.__class__.__name__
            window_class.hInstance = win32api.GetModuleHandle(None)
            class_atom = win32gui.RegisterClass(window_class)
            return win32gui.CreateWindow(class_atom, self.__class__.__name__, 0, 0, 0, 0, 0, 0, 0, window_class.hInstance, None)
        
        def listen(self):
            def runner():
                hwnd = self._create_window()
                ctypes.windll.user32.AddClipboardFormatListener(hwnd)
                win32gui.PumpMessages()
            th = threading.Thread(target=runner, daemon=True)
            th.start()