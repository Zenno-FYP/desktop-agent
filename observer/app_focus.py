"""Active app tracker for Windows."""
import ctypes
from ctypes import wintypes


def get_active_window():
    """Get the currently active window on Windows.
    
    Returns:
        tuple: (app_name, window_title) or (None, None) if error
    """
    try:
        # Get foreground window handle
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        
        if not hwnd:
            return None, None

        # Get window title length
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            window_title = ""
        else:
            # Get window title
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            window_title = buf.value

        # Get process name from window handle
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        
        # Get process name
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_process = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        
        if h_process:
            MAX_PATH = 260
            buf_path = ctypes.create_unicode_buffer(MAX_PATH)
            if ctypes.windll.psapi.GetModuleFileNameExW(
                h_process, None, buf_path, MAX_PATH
            ):
                app_path = buf_path.value
                app_name = app_path.split("\\")[-1]  # Extract exe name
            else:
                app_name = "Unknown"
            
            ctypes.windll.kernel32.CloseHandle(h_process)
        else:
            app_name = "Unknown"

        return app_name, window_title

    except Exception as e:
        print(f"Error getting active window: {e}")
        return None, None
