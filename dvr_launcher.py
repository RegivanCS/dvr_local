"""
DVR Launcher — Interface gráfica com bandeja do sistema e janela embutida.

Gerencia todos os serviços do DVR:
  - rtsp_proxy.py     (captura câmeras via RTSP)
  - tunnel_relay.py   (tunnels Cloudflare)
  - motion_recorder.py
  - recordings_relay.py
  - app.py            (servidor web Flask, porta 8000)

Uso:
  python dvr_launcher.py
  ou duplo clique em dvr_launcher.exe (após build com PyInstaller)
"""

import sys
import os
import threading
import subprocess
import time
import webbrowser
import signal

# ── garante que o cwd seja sempre a pasta do launcher ─────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
os.chdir(BASE_DIR)

APP_PORT  = 8000
APP_URL   = f"http://127.0.0.1:{APP_PORT}"

# Serviços que rodam como subprocessos
SERVICES = [
    {"name": "rtsp_proxy",       "script": "rtsp_proxy.py"},
    {"name": "tunnel_relay",     "script": "tunnel_relay.py"},
    {"name": "motion_recorder",  "script": "motion_recorder.py"},
    {"name": "recordings_relay", "script": "recordings_relay.py"},
    {"name": "app",              "script": "app.py"},
]

_procs: dict[str, subprocess.Popen] = {}
_running = False
_webview_window = None

# ── detecção do executável Python ─────────────────────────────────────────────
def _python_exe():
    """Retorna o executável python correto (venv ou sistema)."""
    venv_win  = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")
    venv_unix = os.path.join(BASE_DIR, ".venv", "bin", "python")
    if os.path.exists(venv_win):
        return venv_win
    if os.path.exists(venv_unix):
        return venv_unix
    return sys.executable


# ── gerenciamento de serviços ──────────────────────────────────────────────────
def _log_path(name: str) -> str:
    logs = os.path.join(BASE_DIR, "logs")
    os.makedirs(logs, exist_ok=True)
    return os.path.join(logs, f"{name}.log")


def start_services():
    global _running
    if _running:
        return
    _running = True
    py = _python_exe()
    for svc in SERVICES:
        name   = svc["name"]
        script = os.path.join(BASE_DIR, svc["script"])
        if not os.path.exists(script):
            continue
        
        # Tenta abrir o arquivo de log, se estiver locked pula
        log_f = None
        try:
            log_f = open(_log_path(name), "a", encoding="utf-8", errors="replace")
        except (IOError, PermissionError):
            print(f"[DVR] ! Nao conseguiu abrir log de {name}, usando DEVNULL")
            log_f = subprocess.DEVNULL
        
        proc = subprocess.Popen(
            [py, script],
            cwd=BASE_DIR,
            stdout=log_f if log_f != subprocess.DEVNULL else subprocess.DEVNULL,
            stderr=log_f if log_f != subprocess.DEVNULL else subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        _procs[name] = proc
        print(f"[DVR] > {name} iniciado (PID {proc.pid})")


def stop_services():
    global _running
    _running = False
    for name, proc in list(_procs.items()):
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        print(f"[DVR] X {name} encerrado")
    _procs.clear()


def _wait_app_ready(timeout=30) -> bool:
    """Aguarda o Flask responder na porta 8000."""
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(APP_URL + "/login", timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


# ── ícone da bandeja (tray) ────────────────────────────────────────────────────
def _make_icon_image():
    """Cria ícone DVR programaticamente (sem arquivo externo)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img  = Image.new("RGBA", (64, 64), (30, 30, 30, 255))
        draw = ImageDraw.Draw(img)
        # Câmera estilizada
        draw.rectangle([8, 16, 46, 48], fill=(80, 160, 80), outline=(200, 255, 200), width=2)
        draw.polygon([(46, 22), (58, 16), (58, 48), (46, 42)], fill=(60, 140, 60))
        draw.ellipse([16, 24, 36, 40], fill=(20, 20, 20))
        draw.ellipse([20, 28, 32, 36], fill=(100, 200, 100))
        return img
    except ImportError:
        return None


def _open_in_browser(_icon=None, _item=None):
    webbrowser.open(APP_URL)


def _open_embedded(_icon=None, _item=None):
    _launch_webview()


def _exit_app(icon, _item=None):
    stop_services()
    icon.stop()
    # encerra o processo principal
    os.kill(os.getpid(), signal.SIGTERM if sys.platform != "win32" else signal.SIGBREAK)


def _run_tray():
    try:
        import pystray
        from pystray import MenuItem as Item, Menu
    except ImportError:
        print("[DVR] pystray nao instalado - abrindo browser diretamente.")
        _wait_app_ready()
        webbrowser.open(APP_URL)
        return

    try:
        icon_image = _make_icon_image()
    except Exception:
        icon_image = None
    
    if icon_image is None:
        print("[DVR] Nao conseguiu gerar icone - abrindo browser.")
        _wait_app_ready()
        webbrowser.open(APP_URL)
        return

    try:
        icon = pystray.Icon(
            "dvr_local",
            icon_image,
            "DVR Local",
            menu=Menu(
                Item("[ ] Abrir interface", _open_embedded, default=True),
                Item("[W] Abrir no browser", _open_in_browser),
                Menu.SEPARATOR,
                Item("[X] Encerrar DVR", _exit_app),
            ),
        )
        # Abre a janela embutida automaticamente após os serviços subirem
        threading.Thread(target=_auto_open, daemon=True).start()
        icon.run()
    except Exception as e:
        print(f"[DVR] Erro na bandeja: {e} - abrindo browser.")
        _wait_app_ready()
        webbrowser.open(APP_URL)


def _auto_open():
    """Aguarda Flask e abre a janela embutida."""
    if _wait_app_ready(timeout=40):
        _launch_webview()
    else:
        webbrowser.open(APP_URL)


# ── janela embutida (pywebview) ────────────────────────────────────────────────
def _launch_webview():
    global _webview_window
    try:
        import webview
    except ImportError:
        webbrowser.open(APP_URL)
        return

    try:
        if _webview_window is not None:
            # já aberta — só traz para frente
            try:
                _webview_window.show()
            except Exception:
                pass
            return

        def _create():
            global _webview_window
            try:
                _webview_window = webview.create_window(
                    "DVR Local",
                    APP_URL,
                    width=1280,
                    height=780,
                    resizable=True,
                    min_size=(800, 500),
                )
                webview.start()
            except Exception as e:
                print(f"[DVR] Erro pywebview: {e}")
            finally:
                _webview_window = None

        # pywebview precisa rodar em thread dedicada
        t = threading.Thread(target=_create, daemon=True)
        t.start()
    except Exception as e:
        print(f"[DVR] Erro ao abrir janela: {e}")
        webbrowser.open(APP_URL)


# ── entrypoint ─────────────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("  DVR Local - iniciando...")
    print(f"  Pasta base: {BASE_DIR}")
    print(f"  Python:     {_python_exe()}")
    print("=" * 50)

    # Inicia todos os serviços em background
    threading.Thread(target=start_services, daemon=True).start()

    # Roda a bandeja do sistema (bloqueia até sair)
    _run_tray()


if __name__ == "__main__":
    main()
