"""
RTSP Proxy — captura frames das câmeras via RTSP e serve como JPEG via HTTP.

Cada câmera fica disponível em:
  http://localhost:{PORTA_BASE + N}/snapshot.jpg

O tunnel_relay.py aponta para este proxy, que por sua vez expõe via Cloudflare.

Uso: python rtsp_proxy.py  (manter rodando junto com tunnel_relay.py)
"""

import threading
import time
import io
from http.server import HTTPServer, BaseHTTPRequestHandler
import subprocess
import os
import tempfile
from urllib.parse import quote

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(errors='replace')

# ─────────────────────────────────────────────
CAMERAS = [
    {'ip': '192.168.1.5', 'rtsp_port': 554, 'user': 'admin', 'password': '!Rede!123', 'http_port': 8191},
    {'ip': '192.168.1.6', 'rtsp_port': 554, 'user': 'admin', 'password': '!Rede!123', 'http_port': 8192},
]
# ─────────────────────────────────────────────

# Frame buffer compartilhado por câmera
_frames = {}
_frame_ts = {}
_lock = threading.Lock()


# Paths RTSP para câmeras XM/Positivo (DIPC firmware)
# subtype=0 = stream principal 1080p, subtype=1 = sub-stream 576p (mais leve)
RTSP_PATH = '/cam/realmonitor?channel=1&subtype=1'

def rtsp_url(cam):
    if cam['user']:
        u = quote(cam['user'], safe='')
        p = quote(cam['password'], safe='')
        creds = f'{u}:{p}@'
    else:
        creds = ''
    return f'rtsp://{creds}{cam["ip"]}:{cam["rtsp_port"]}{RTSP_PATH}'


def capture_loop(cam):
    """Captura frames em loop contínuo via ffmpeg streaming (processo único, sem reiniciar a cada frame)."""
    url = rtsp_url(cam)
    key = f'{cam["ip"]}:{cam["rtsp_port"]}'
    print(f'  📷 Capturando: {url}')

    while True:
        proc = None
        try:
            # Processo ffmpeg persistente: lê o stream RTSP e gera ~5 JPEGs/s continuamente
            proc = subprocess.Popen(
                [
                    FFMPEG,
                    '-rtsp_transport', 'tcp',
                    '-i', url,
                    '-vf', 'fps=5',          # 5 fps de saída
                    '-f', 'image2pipe',
                    '-vcodec', 'mjpeg',
                    '-q:v', '5',
                    '-loglevel', 'error',
                    'pipe:1',
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )

            buf = b''
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break  # ffmpeg encerrou — reconecta
                buf += chunk

                # Extrai todos os frames JPEG completos do buffer
                while True:
                    start = buf.find(b'\xff\xd8')
                    if start == -1:
                        buf = b''
                        break
                    end = buf.find(b'\xff\xd9', start + 2)
                    if end == -1:
                        # Frame incompleto — aguarda mais dados
                        buf = buf[start:]
                        break
                    frame = buf[start:end + 2]
                    buf = buf[end + 2:]
                    with _lock:
                        _frames[key] = frame
                        _frame_ts[key] = time.time()

        except Exception as e:
            print(f'  ⚠️  Erro captura {key}: {e}')
        finally:
            if proc:
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except Exception:
                    pass

        print(f'  🔄 Reconectando câmera {key}...')
        time.sleep(2)  # Aguarda antes de reconectar


def make_handler(cam):
    key = f'{cam["ip"]}:{cam["rtsp_port"]}'

    class SnapshotHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            with _lock:
                frame = _frames.get(key)
                ts = _frame_ts.get(key, 0)
            if frame:
                age = max(0, int(time.time() - ts))
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(frame)))
                self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
                self.send_header('Pragma', 'no-cache')
                self.send_header('Expires', '0')
                self.send_header('X-Frame-Age', str(age))
                self.end_headers()
                self.wfile.write(frame)
            else:
                self.send_response(503)
                self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
                self.end_headers()
                self.wfile.write(b'Aguardando primeiro frame...')

        def log_message(self, fmt, *args):
            pass  # silencia logs HTTP

    return SnapshotHandler


def start_server(cam):
    handler = make_handler(cam)
    server = HTTPServer(('0.0.0.0', cam['http_port']), handler)
    print(f'  🌐 Servidor HTTP na porta {cam["http_port"]} → {cam["ip"]}:{cam["rtsp_port"]}')
    server.serve_forever()


# ── MAIN ─────────────────────────────────────
print('=' * 60)
print('📡 RTSP → HTTP Proxy')
print('=' * 60)

# Verifica ffmpeg
import shutil, glob

def find_ffmpeg():
    # 1. PATH normal
    ff = shutil.which('ffmpeg')
    if ff:
        return ff
    # 2. Instalação local em C:\ffmpeg
    candidates = glob.glob(r'C:\ffmpeg\*\bin\ffmpeg.exe')
    if candidates:
        return candidates[0]
    return None

FFMPEG = find_ffmpeg()
if not FFMPEG:
    print('✗ ffmpeg não encontrado. Instale: https://ffmpeg.org/download.html')
    import sys; sys.exit(1)
print(f'✓ ffmpeg: {FFMPEG}')

threads = []
for cam in CAMERAS:
    t = threading.Thread(target=capture_loop, args=(cam,), daemon=True)
    t.start()
    threads.append(t)

    t2 = threading.Thread(target=start_server, args=(cam,), daemon=True)
    t2.start()
    threads.append(t2)

print(f'\n✅ Proxy ativo em:')
for cam in CAMERAS:
    print(f'  http://localhost:{cam["http_port"]}/snapshot.jpg  ←  {cam["ip"]}')
print('\nMantenha este script rodando. Pressione Ctrl+C para parar.\n')

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print('\nProxy encerrado.')
