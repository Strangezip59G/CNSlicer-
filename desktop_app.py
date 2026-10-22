"""
CNSlicer — Subtractive Studio
Native Windows Desktop Application Entry Point
"""
from __future__ import annotations
import ctypes
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

# Enable High-DPI crisp rendering on Windows 10/11
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# Resolve base directory (handles PyInstaller bundle or standard execution)
if getattr(sys, "frozen", False):
    BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    BASE_DIR = Path(__file__).resolve().parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from server.app import app
import webview


def find_free_port() -> int:
    """Find an available loopback TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def is_port_in_use(port: int) -> bool:
    """Check if a specific loopback port is already bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def wait_for_server(port: int, timeout: float = 12.0) -> bool:
    """Poll until the local server starts accepting connections."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.1)
    return False


def run_flask(port: int):
    """Run Flask quietly without debug server banners."""
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


def main():
    # Pick port 5000 if free, otherwise find an open ephemeral port
    port = 5000 if not is_port_in_use(5000) else find_free_port()

    # Start Flask backend in background daemon thread
    server_thread = threading.Thread(target=run_flask, args=(port,), daemon=True)
    server_thread.start()

    # Wait for the backend to become responsive
    if not wait_for_server(port):
        print(f"Warning: Slicer backend on port {port} took longer than expected to start.")

    # Locate application icon
    icon_candidates = [
        BASE_DIR / "assets" / "icon.ico",
        Path(__file__).resolve().parent / "assets" / "icon.ico" if not getattr(sys, "frozen", False) else None,
        Path(sys.executable).parent / "assets" / "icon.ico" if getattr(sys, "frozen", False) else None,
        Path(sys.executable).parent / "_internal" / "assets" / "icon.ico" if getattr(sys, "frozen", False) else None,
        BASE_DIR / "ui" / "static" / "icon.png",
    ]
    icon_path = next((str(c) for c in icon_candidates if c and c.exists()), None)

    # Launch native desktop application window via Edge WebView2
    window = webview.create_window(
        title="CNSlicer — Subtractive Studio v1.0",
        url=f"http://127.0.0.1:{port}",
        width=1400,
        height=900,
        min_size=(1024, 650),
        background_color="#0f1115",
        text_select=True
    )

    # Start webview event loop with custom desktop icon
    if icon_path:
        webview.start(icon=icon_path, debug=False)
    else:
        webview.start(debug=False)

    # When the desktop window is closed by the user, terminate application
    sys.exit(0)


if __name__ == "__main__":
    main()
