"""
DVR Local - Captura via interface web (alternativa ao RTSP)
"""

from flask import Flask, Response, render_template_string
import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
import cv2
import numpy as np
import time
import logging
import json
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# IPs e credenciais são carregados de cameras_config.json (configure pela tela de configurações)
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'cameras_config.json')

# As variáveis abaixo foram removidas do código; use a tela de configurações para definir IPs
# CAMERAS = {...}  # não use IPs fixos aqui
# AUTH = HTTPBasicAuth(...)  # não use credenciais fixas aqui

def load_cameras():
    """Carrega câmeras do arquivo de configuração"""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f).get('cameras', {})
    except:
        return {}


def generate_from_web_interface(camera_id):
    """Captura da interface web da câmera usando config carregado de cameras_config.json"""
    cameras = load_cameras()
    cam = cameras.get(camera_id)
    if not cam:
        return

    ip = cam.get('ip', '')
    port = cam.get('port', 80)
    user = cam.get('user', '')
    password = cam.get('password', '')
    auth = HTTPBasicAuth(user, password) if user else None

    base_url = f"http://{ip}:{port}"
    
    logger.info(f"{camera_id}: Conectando a {base_url}")
    
    # URLs comuns de snapshot em câmeras IP
    snapshot_urls = [
        f'{base_url}/cgi-bin/snapshot.cgi',
        f'{base_url}/tmpfs/auto.jpg',
        f'{base_url}/snapshot.cgi',
        f'{base_url}/image/jpeg.cgi',
        f'{base_url}/jpg/image.jpg',
    ]
    
    working_url = None
    
    # Encontrar URL que funciona
    for url in snapshot_urls:
        try:
            r = requests.get(url, auth=auth, timeout=2)
            if r.status_code == 200 and len(r.content) > 1000:
                if r.content[:2] == b'\xff\xd8':  # JPEG header
                    working_url = url
                    logger.info(f"{camera_id}: ✓ Snapshot encontrado em {url}")
                    break
        except:
            pass
    
    if not working_url:
        logger.error(f"{camera_id}: Nenhuma URL de snapshot funcional")
        yield create_error_frame(f"{camera_id}: Sem snapshot")
        return
    
    # Stream de frames
    frame_count = 0
    error_count = 0
    
    while True:
        try:
            r = requests.get(working_url, auth=auth, timeout=5)
            
            if r.status_code == 200 and len(r.content) > 1000:
                frame_count += 1
                error_count = 0
                
                if frame_count % 50 == 0:
                    logger.info(f"{camera_id}: {frame_count} frames")
                
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + r.content + b'\r\n')
                
                time.sleep(0.2)  # 5 FPS
            else:
                error_count += 1
                if error_count > 10:
                    break
                time.sleep(1)
                
        except Exception as e:
            error_count += 1
            logger.error(f"{camera_id}: {e}")
            if error_count > 10:
                break
            time.sleep(1)


def create_error_frame(text):
    """Cria frame de erro"""
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(frame, text, (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    ret, jpeg = cv2.imencode('.jpg', frame)
    return b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n'


@app.route('/')
def index():
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>DVR Local - Web Interface</title>
    <meta charset="utf-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: Arial;
            background: #2c3e50;
            color: white;
        }
        .header {
            background: #34495e;
            padding: 20px;
            text-align: center;
        }
        .cameras {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(600px, 1fr));
            gap: 20px;
            padding: 20px;
        }
        .camera {
            background: #34495e;
            padding: 15px;
            border-radius: 10px;
        }
        .camera img {
            width: 100%;
            border-radius: 5px;
            background: #000;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🎥 DVR Local - Captura WebInterface</h1>
        <p>Alternativa ao RTSP</p>
    </div>
    <div class="cameras">
        {% for cam_id, cam in cameras.items() %}
        <div class="camera">
            <h2>{{ cam.name }}</h2>
            <img src="/video/{{ cam_id }}">
        </div>
        {% endfor %}
    </div>
</body>
</html>
    """, cameras=CAMERAS)


@app.route('/video/<camera_id>')
def video(camera_id):
    return Response(generate_from_web_interface(camera_id),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


if __name__ == '__main__':
    print("=" * 70)
    print("DVR Local - Via Web Interface (sem RTSP)")
    print("=" * 70)
    print("\nAcesse: http://localhost:8001/")
    print("=" * 70)
    app.run(host='0.0.0.0', port=8001, threaded=True)
