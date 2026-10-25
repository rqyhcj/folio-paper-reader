"""Double-click launcher: isolated environment, local server, default browser."""
from __future__ import annotations
import http.client
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import venv
import webbrowser

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("FOLIO_PORT", "8765"))
URL = f"http://127.0.0.1:{PORT}"


def is_running():
    try:
        connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=1)
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        return response.status == 200 and b'"folio"' in response.read()
    except OSError:
        return False
    finally:
        if "connection" in locals():
            connection.close()


def open_when_ready():
    for _ in range(100):
        if is_running():
            webbrowser.open(URL)
            return
        time.sleep(0.3)


def main():
    os.chdir(ROOT)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if is_running():
        webbrowser.open(URL)
        return
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if Path(sys.executable).resolve() != python.resolve():
        if not python.exists():
            print("正在准备纸间的独立运行环境，首次启动需要下载依赖…", flush=True)
            venv.create(ROOT / ".venv", with_pip=True)
        result = subprocess.run([str(python), "-c", "import fastapi, uvicorn, fitz, httpx, multipart, ziamath"], capture_output=True)
        if result.returncode:
            subprocess.check_call([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
        raise SystemExit(subprocess.call([str(python), str(ROOT / "launcher.py")]))
    print(f"\n纸间 · Folio 已启动：{URL}\n保留此窗口即可持续阅读，关闭窗口可停止程序。\n", flush=True)
    threading.Thread(target=open_when_ready, daemon=True).start()
    import uvicorn
    uvicorn.run("backend.app:app", host="127.0.0.1", port=PORT)


if __name__ == "__main__":
    main()
