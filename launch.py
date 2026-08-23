import subprocess
import time
import sys
import os
import signal
import threading
import traceback
from datetime import datetime

# ---------------------------------------------------------------------------
# Inline debugging: prints to stderr and appends to agent_debug.log
# (set AGENT_DEBUG=0 to disable)
# ---------------------------------------------------------------------------
_DEBUG_ON = os.environ.get("AGENT_DEBUG", "1") != "0"
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_debug.log")


def _dbg(msg: str):
    if not _DEBUG_ON:
        return
    try:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        thread = threading.current_thread().name
        line = f"[{ts}] [launch] [{thread}] {msg}"
        print(line[:4000], file=sys.stderr, flush=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


BANNER = """
  +======================================+
  |        Coding Agent Launcher         |
  +======================================+
"""

def find_python():
    import shutil
    p = shutil.which("python")
    if p:
        return p
    return sys.executable

def find_ollama():
    paths = [
        r"C:\Users\Admin\AppData\Local\Programs\Ollama\ollama.exe",
        "ollama",
    ]
    for p in paths:
        try:
            subprocess.run([p, "--version"], capture_output=True, timeout=5)
            return p
        except Exception:
            continue
    return None

def is_port_in_use(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0

def main():
    _dbg(f"launcher starting cwd={os.getcwd()} python={sys.executable}")
    print(BANNER)

    if getattr(sys, "frozen", False):
        work_dir = os.path.dirname(sys.executable)
    else:
        work_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(work_dir)
    _dbg(f"work_dir={work_dir}")

    python_path = find_python()
    ollama_proc = None

    def cleanup(sig=None, frame=None):
        _dbg(f"cleanup called sig={sig}")
        print("\n  Shutting down...")
        if ollama_proc:
            ollama_proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    print("  [1/2] Starting Ollama...")
    if is_port_in_use(11434):
        print("        Ollama already running on port 11434")
    else:
        ollama_path = find_ollama()
        if not ollama_path:
            print("        ERROR: Ollama not found. Install from https://ollama.com")
            input("        Press Enter to exit...")
            return
        ollama_proc = subprocess.Popen(
            [ollama_path, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        print(f"        Started Ollama (PID: {ollama_proc.pid})")

    print("  [2/2] Starting Agent GUI...")
    time.sleep(1)
    gui_proc = subprocess.Popen(
        [python_path, "gui.py"],
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    _dbg(f"gui.py launched pid={gui_proc.pid} via {python_path}")
    print(f"        GUI launched (PID: {gui_proc.pid})")

    print("")
    print("  +======================================+")
    print("  |  Agent is running!                   |")
    print("  |  Close the GUI window to stop        |")
    print("  +======================================+")
    print("")

    try:
        last_ollama_restart = 0
        while True:
            time.sleep(2)
            now = time.time()

            if gui_proc.poll() is not None:
                _dbg(f"gui.py exited with code {gui_proc.returncode} - shutting down launcher")
                cleanup()

            if ollama_proc and ollama_proc.poll() is not None:
                if is_port_in_use(11434):
                    # Another instance is already serving Ollama (usually the
                    # tray app that auto-starts on login) - our spawned copy
                    # lost the port race and exited, so stop restarting it.
                    print("  Ollama already running elsewhere - auto-restart disabled.")
                    ollama_proc = None
                elif now - last_ollama_restart > 10:
                    print("  Ollama stopped. Restarting...")
                    ollama_path = find_ollama()
                    if ollama_path:
                        ollama_proc = subprocess.Popen(
                            [ollama_path, "serve"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                        )
                        last_ollama_restart = now
                        _dbg(f"ollama restarted pid={ollama_proc.pid}")
                    else:
                        ollama_proc = None
                        _dbg("ollama restart skipped: ollama.exe not found")
    except KeyboardInterrupt:
        cleanup()
    except Exception as e:
        _dbg(f"FATAL EXCEPTION in launcher loop: {e}\n{traceback.format_exc()}")
        raise


if __name__ == "__main__":
    main()
