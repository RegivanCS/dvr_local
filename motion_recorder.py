"""
Detecção de movimento com gravação local via RTSP.

Conecta diretamente nas câmeras via RTSP usando OpenCV,
detecta movimento por subtração de fundo e grava vídeo MP4
quando movimento é detectado.

Uso: python motion_recorder.py
Requisitos: pip install opencv-python
"""
import cv2
import os
import time
import threading
import subprocess
import shutil
import glob
from datetime import datetime
from urllib.parse import quote

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(errors='replace')


def _find_ffmpeg():
    ff = shutil.which('ffmpeg')
    if ff:
        return ff
    for pattern in ['C:/ffmpeg/*/bin/ffmpeg.exe', 'C:/ffmpeg/bin/ffmpeg.exe']:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None


FFMPEG = _find_ffmpeg()

# ─────────────────────────────────────────────
# CONFIGURAÇÕES — edite conforme necessário
# ─────────────────────────────────────────────
CAM_USER     = 'admin'
CAM_PASSWORD = '!Rede!123'

CAMERAS = [
    {'id': 'cam1', 'name': 'Câmera 1', 'ip': '192.168.1.5', 'port': 554,
     'path': '/cam/realmonitor?channel=1&subtype=1'},
    {'id': 'cam2', 'name': 'Câmera 2', 'ip': '192.168.1.6', 'port': 554,
     'path': '/cam/realmonitor?channel=1&subtype=1'},
]

# Pasta de gravações (relativa ao diretório deste script)
RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'recordings')

# Sensibilidade: pixels em movimento para disparar (menor = mais sensível)
MOTION_THRESHOLD = 3000

# Segundos para continuar gravando após o último movimento detectado
RECORD_COOLDOWN = 30

# Resolução de captura RTSP (None = padrão da câmera)
FRAME_WIDTH  = None
FRAME_HEIGHT = None

# FPS do vídeo gravado
RECORD_FPS = 10
# ─────────────────────────────────────────────


def build_rtsp_url(cam: dict) -> str:
    pwd = quote(CAM_PASSWORD, safe='')
    return f"rtsp://{CAM_USER}:{pwd}@{cam['ip']}:{cam['port']}{cam['path']}"


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def camera_worker(cam: dict):
    """Thread de detecção e gravação para uma câmera."""
    rtsp_url = build_rtsp_url(cam)
    cam_dir  = os.path.join(RECORDINGS_DIR, cam['id'])
    ensure_dir(cam_dir)

    print(f"[{cam['name']}] Conectando: rtsp://{cam['ip']}:{cam['port']}{cam['path']}")

    while True:
        cap = cv2.VideoCapture(rtsp_url)
        if not cap.isOpened():
            print(f"[{cam['name']}] ✗ Falha na conexão. Retry em 5s...")
            time.sleep(5)
            continue

        if FRAME_WIDTH:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
        if FRAME_HEIGHT:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

        print(f"[{cam['name']}] ✓ Conectado. Monitorando...")

        subtractor   = cv2.createBackgroundSubtractorMOG2(history=200, varThreshold=40, detectShadows=False)
        writer       = None
        last_motion  = 0
        motion_count = 0
        video_path   = None

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    print(f"[{cam['name']}] Sem frame — reconectando...")
                    break

                # Detectar movimento
                small   = cv2.resize(frame, (320, 240))
                fgmask  = subtractor.apply(small)
                _, mask = cv2.threshold(fgmask, 128, 255, cv2.THRESH_BINARY)
                pixels  = cv2.countNonZero(mask)

                now = time.time()
                if pixels > MOTION_THRESHOLD:
                    last_motion = now
                    motion_count += 1
                    ts = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
                    print(f"[{cam['name']}] 🎯 Movimento #{motion_count} — {pixels}px — {ts}")

                # Gerenciar gravação
                if last_motion and now - last_motion < RECORD_COOLDOWN:
                    if writer is None:
                        ts_str     = datetime.now().strftime('%Y%m%d_%H%M%S')
                        video_path = os.path.join(cam_dir, f'motion_{ts_str}.mp4')
                        h, w       = frame.shape[:2]
                        if FFMPEG:
                            # Grava H.264 via ffmpeg (compatível com browsers)
                            writer = subprocess.Popen(
                                [
                                    FFMPEG, '-y',
                                    '-f', 'rawvideo', '-vcodec', 'rawvideo',
                                    '-s', f'{w}x{h}', '-pix_fmt', 'bgr24',
                                    '-r', str(RECORD_FPS), '-i', 'pipe:0',
                                    '-vcodec', 'libx264', '-preset', 'fast',
                                    '-crf', '23', '-pix_fmt', 'yuv420p',
                                    '-movflags', '+faststart',
                                    video_path,
                                ],
                                stdin=subprocess.PIPE,
                                stderr=subprocess.DEVNULL,
                            )
                        else:
                            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                            writer = cv2.VideoWriter(video_path, fourcc, RECORD_FPS, (w, h))
                        print(f"[{cam['name']}] 📹 Gravando: {video_path}")
                    # Escreve frame
                    if FFMPEG and hasattr(writer, 'stdin') and writer.stdin:
                        writer.stdin.write(frame.tobytes())
                    elif hasattr(writer, 'write'):
                        writer.write(frame)
                else:
                    if writer is not None:
                        if FFMPEG and hasattr(writer, 'stdin') and writer.stdin:
                            writer.stdin.close()
                            writer.wait()
                        elif hasattr(writer, 'release'):
                            writer.release()
                        writer = None
                        print(f"[{cam['name']}] 💾 Gravação salva: {video_path}")
                        video_path = None

        except Exception as e:
            print(f"[{cam['name']}] Erro: {e}")
        finally:
            if writer is not None:
                if FFMPEG and hasattr(writer, 'stdin') and writer.stdin:
                    writer.stdin.close()
                    writer.wait()
                elif hasattr(writer, 'release'):
                    writer.release()
            cap.release()
            time.sleep(3)


# ── MAIN ─────────────────────────────────────
print('=' * 60)
print('🎬 Motion Recorder — Detecção de Movimento RTSP')
print('=' * 60)
print(f'Gravações em: {RECORDINGS_DIR}')
print(f'Câmeras: {len(CAMERAS)}')
print(f'Sensibilidade: {MOTION_THRESHOLD} pixels')
print(f'Cooldown após movimento: {RECORD_COOLDOWN}s')
print('Pressione Ctrl+C para parar.')
print('=' * 60)

ensure_dir(RECORDINGS_DIR)

threads = []
for cam in CAMERAS:
    t = threading.Thread(target=camera_worker, args=(cam,), daemon=True, name=cam['id'])
    t.start()
    threads.append(t)

try:
    while True:
        # Verifica threads vivas
        for i, (t, cam) in enumerate(zip(threads, CAMERAS)):
            if not t.is_alive():
                print(f"[{cam['name']}] Thread morta — reiniciando...")
                new_t = threading.Thread(target=camera_worker, args=(cam,), daemon=True, name=cam['id'])
                new_t.start()
                threads[i] = new_t
        time.sleep(5)
except KeyboardInterrupt:
    print('\n⏹️  Parando detecção de movimento...')
