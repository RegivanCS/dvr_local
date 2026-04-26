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
import numpy as np

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
SNAPSHOTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'snapshots')

# Sensibilidade: pixels em movimento para disparar (menor = mais sensível)
MOTION_THRESHOLD = 3000

# Segundos para continuar gravando após o último movimento detectado
RECORD_COOLDOWN = 30

# Resolução de captura RTSP (None = padrão da câmera)
FRAME_WIDTH  = None
FRAME_HEIGHT = None

# FPS do vídeo gravado
RECORD_FPS = 10

# Detecção humana (snapshot local)
HUMAN_DETECT_ENABLED = True
HUMAN_DETECT_INTERVAL = 2.0
HUMAN_SNAPSHOT_COOLDOWN = 20
# ─────────────────────────────────────────────


def build_rtsp_url(cam: dict) -> str:
    pwd = quote(CAM_PASSWORD, safe='')
    return f"rtsp://{CAM_USER}:{pwd}@{cam['ip']}:{cam['port']}{cam['path']}"


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def detect_person_hog(hog, frame):
    # Reduz a imagem para acelerar inferencia sem perder contexto
    h, w = frame.shape[:2]
    scale = 1.0
    if w > 960:
        scale = 960.0 / float(w)
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)))

    rects, _weights = hog.detectMultiScale(
        frame,
        winStride=(8, 8),
        padding=(8, 8),
        scale=1.05,
    )
    return len(rects) > 0


def camera_worker(cam: dict):
    """Thread de detecção e gravação para uma câmera."""
    rtsp_url = build_rtsp_url(cam)
    cam_dir  = os.path.join(RECORDINGS_DIR, cam['id'])
    snap_dir = os.path.join(SNAPSHOTS_DIR, cam['id'])
    ensure_dir(cam_dir)
    ensure_dir(snap_dir)

    print(f"[{cam['name']}] Conectando: rtsp://{cam['ip']}:{cam['port']}{cam['path']}")

    while True:
        proc = None
        try:
            # Usa ffmpeg para capturar frames (mais confiável que OpenCV direto)
            if FFMPEG:
                proc = subprocess.Popen(
                    [
                        FFMPEG,
                        '-rtsp_transport', 'tcp',
                        '-i', rtsp_url,
                        '-vf', f'fps={RECORD_FPS}',
                        '-f', 'image2pipe',
                        '-vcodec', 'mjpeg',
                        '-q:v', '5',
                        '-loglevel', 'error',
                        'pipe:1',
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )

                print(f"[{cam['name']}] ✓ Conectado via ffmpeg. Monitorando...")

                subtractor   = cv2.createBackgroundSubtractorMOG2(history=200, varThreshold=40, detectShadows=False)
                hog = cv2.HOGDescriptor()
                hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
                writer       = None
                last_motion  = 0
                motion_count = 0
                last_human_check = 0
                last_human_snapshot = 0
                video_path   = None
                buf = b''

                while True:
                    # Lê dados do ffmpeg
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        print(f"[{cam['name']}] Sem dados do ffmpeg — reconectando...")
                        break

                    buf += chunk

                    # Processa frames JPEG do stream
                    while True:
                        start = buf.find(b'\xff\xd8')
                        if start == -1:
                            buf = b''
                            break
                        end = buf.find(b'\xff\xd9', start + 2)
                        if end == -1:
                            buf = buf[start:]
                            break

                        # Decodifica frame JPEG
                        frame_data = buf[start:end + 2]
                        buf = buf[end + 2:]

                        # Converte para numpy array
                        frame = cv2.imdecode(np.frombuffer(frame_data, np.uint8), cv2.IMREAD_COLOR)
                        if frame is None:
                            continue

                        now = time.time()

                        # Detectar movimento
                        small   = cv2.resize(frame, (320, 240))
                        fgmask  = subtractor.apply(small)
                        _, mask = cv2.threshold(fgmask, 128, 255, cv2.THRESH_BINARY)
                        pixels  = cv2.countNonZero(mask)

                        if pixels > MOTION_THRESHOLD:
                            last_motion = now
                            motion_count += 1
                            ts = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
                            print(f"[{cam['name']}] 🎯 Movimento #{motion_count} — {pixels}px — {ts}")

                        # Captura imagem quando detectar pessoa durante movimento
                        if HUMAN_DETECT_ENABLED and last_motion and (now - last_human_check) >= HUMAN_DETECT_INTERVAL:
                            last_human_check = now
                            try:
                                if detect_person_hog(hog, frame):
                                    if now - last_human_snapshot >= HUMAN_SNAPSHOT_COOLDOWN:
                                        ts_img = datetime.now().strftime('%Y%m%d_%H%M%S')
                                        img_path = os.path.join(snap_dir, f'human_{ts_img}.jpg')
                                        cv2.imwrite(img_path, frame)
                                        last_human_snapshot = now
                                        print(f"[{cam['name']}] 🧍 Pessoa detectada — snapshot: {img_path}")
                            except Exception as e:
                                print(f"[{cam['name']}] Aviso detecção humana: {e}")

                        # Gerenciar gravação
                        if last_motion and now - last_motion < RECORD_COOLDOWN:
                            if writer is None:
                                ts_str     = datetime.now().strftime('%Y%m%d_%H%M%S')
                                video_path = os.path.join(cam_dir, f'motion_{ts_str}.mp4')
                                h, w       = frame.shape[:2]
                                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                                writer = cv2.VideoWriter(video_path, fourcc, RECORD_FPS, (w, h))
                                print(f"[{cam['name']}] 📹 Gravando: {video_path}")
                            # Escreve frame
                            if writer:
                                writer.write(frame)
                        else:
                            if writer is not None:
                                writer.release()
                                writer = None
                                print(f"[{cam['name']}] 💾 Gravação salva: {video_path}")
                                video_path = None
            else:
                print(f"[{cam['name']}] ✗ FFMPEG não encontrado. Abortando.")
                break

        except Exception as e:
            print(f"[{cam['name']}] Erro: {e}")
        finally:
            if proc:
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except Exception:
                    pass
            if writer is not None:
                writer.release()
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
ensure_dir(SNAPSHOTS_DIR)

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
