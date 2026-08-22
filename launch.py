import subprocess
import time
import sys
import os
import signal

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
    print(BANNER)

    if getattr(sys, "frozen", False):
        work_dir = os.path.dirname(sys.executable)
    else:
        work_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(work_dir)

    python_path = find_python()
    ollama_proc = None

    def cleanup(sig=None, frame=None):
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
                    else:
                        ollama_proc = None
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
