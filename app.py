from flask import Flask, Response, render_template_string, request, redirect, url_for, jsonify, session, send_file
from functools import wraps
import requests
import json
import os
import logging
import time
import secrets
import threading
import hashlib
from datetime import datetime
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from PIL import Image, ImageChops
    import io as _io
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# User-Agent customizado para evitar bloqueio do ModSecurity/WAF
HTTP_HEADERS = {'User-Agent': 'Mozilla/5.0 (DVR-Camera-Viewer/1.0)'}

# Arquivo de configuração (caminho absoluto para funcionar com Passenger)
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_APP_DIR, 'cameras_config.json')
RECORDINGS_DIR = os.path.join(_APP_DIR, 'recordings')
os.makedirs(RECORDINGS_DIR, exist_ok=True)

# Estado da detecção de movimento (por câmera)
_motion_threads = {}    # cam_id -> Thread
_motion_stop   = {}    # cam_id -> threading.Event
_motion_status = {}    # cam_id -> {'active': bool, 'last_motion': str, 'count': int}

def _get_or_create_secret_key():
    """Carrega secret_key persistente (nunca cria arquivo para evitar race condition entre workers)"""
    import hashlib
    # Tenta LER arquivo existente — nunca cria (workers em paralelo criariam chaves diferentes)
    for base_dir in [_APP_DIR, os.path.expanduser('~'), '/tmp']:
        key_file = os.path.join(base_dir, '.dvr_secret')
        try:
            if os.path.exists(key_file):
                with open(key_file, 'r') as f:
                    key = f.read().strip()
                    if len(key) >= 32:
                        return key
        except Exception:
            continue
    # Fallback determinístico: derivado do diretório da app (estável e único por instalação)
    seed = f'dvr-secret-v3:{_APP_DIR}:dvr.regivan.tec.br'.encode()
    return hashlib.sha256(seed).hexdigest()

app.secret_key = os.environ.get('DVR_SECRET_KEY', _get_or_create_secret_key())

# Configurações de sessão para funcionar corretamente com HTTPS/Passenger
app.config['SESSION_COOKIE_SECURE'] = True       # site é HTTPS, cookie deve ter flag Secure
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_NAME'] = 'dvr_session'

# Modelos de câmeras suportados
CAMERA_MODELS = {
    'iscee': {
        'name': 'ISCEE / Genérico',
        'paths': ['/snapshot.cgi', '/tmpfs/auto.jpg', '/cgi-bin/snapshot.cgi', '/image.jpg']
    },
    'hikvision': {
        'name': 'Hikvision',
        'paths': ['/ISAPI/Streaming/channels/1/picture', '/Streaming/channels/1/picture']
    },
    'dahua': {
        'name': 'Dahua',
        'paths': ['/cgi-bin/snapshot.cgi', '/onvif/snapshot']
    },
    'intelbras': {
        'name': 'Intelbras',
        'paths': ['/cgi-bin/snapshot.cgi', '/snapshot.cgi']
    },
    'generic': {
        'name': 'Genérico (tentar todos)',
        'paths': ['/snapshot.cgi', '/image.jpg', '/snap.jpg', '/tmpfs/auto.jpg', '/cgi-bin/snapshot.cgi', '/jpg/image.jpg']
    }
}

def load_config():
    """Carrega configuração das câmeras"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {'cameras': {}}
    return {'cameras': {}}

def save_config(config):
    """Salva configuração das câmeras"""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def get_credentials():
    """Retorna (user, password) definidos na config ou variáveis de ambiente"""
    user = os.environ.get('DVR_USER')
    password = os.environ.get('DVR_PASSWORD')
    if user and password:
        return user, password
    config = load_config()
    auth = config.get('auth', {})
    return auth.get('user', 'admin'), auth.get('password', '')

def login_required(f):
    """Decorator que exige sessão autenticada"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login_page', next=request.path))
        return f(*args, **kwargs)
    return decorated

# ---------- Rotas de autenticação ----------

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>DVR Local - Login</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Arial;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .login-box {
            background: rgba(255,255,255,0.15);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255,255,255,0.3);
            border-radius: 16px;
            padding: 50px 40px;
            width: 100%;
            max-width: 380px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
            color: white;
        }
        h1 { text-align: center; font-size: 1.8em; margin-bottom: 8px; }
        p.subtitle { text-align: center; opacity: 0.8; margin-bottom: 30px; font-size: 0.95em; }
        label { display: block; margin-bottom: 6px; font-size: 0.9em; opacity: 0.9; }
        input {
            width: 100%; padding: 12px 16px;
            border: 1px solid rgba(255,255,255,0.3);
            border-radius: 8px;
            background: rgba(255,255,255,0.1);
            color: white;
            font-size: 1em;
            margin-bottom: 20px;
            outline: none;
        }
        input::placeholder { color: rgba(255,255,255,0.5); }
        input:focus { border-color: rgba(255,255,255,0.7); background: rgba(255,255,255,0.2); }
        button {
            width: 100%; padding: 13px;
            background: rgba(255,255,255,0.25);
            color: white;
            border: 1px solid rgba(255,255,255,0.4);
            border-radius: 8px;
            font-size: 1.05em;
            cursor: pointer;
            transition: background 0.2s;
            font-weight: 600;
        }
        button:hover { background: rgba(255,255,255,0.35); }
        .error {
            background: rgba(231,76,60,0.4);
            border: 1px solid rgba(231,76,60,0.6);
            border-radius: 8px;
            padding: 10px 14px;
            margin-bottom: 20px;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <div class="login-box">
        <h1>📡 DVR Local</h1>
        <p class="subtitle">dvr.regivan.tec.br</p>
        {% if error %}<div class="error">⚠️ {{ error }}</div>{% endif %}
        <form method="POST" action="/login">
            <input type="hidden" name="next" value="{{ next }}">
            <label>Usuário</label>
            <input type="text" name="user" autocomplete="username" required autofocus>
            <label>Senha</label>
            <input type="password" name="password" autocomplete="current-password" required>
            <button type="submit">Entrar →</button>
        </form>
    </div>
</body>
</html>
"""

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if session.get('logged_in'):
        return redirect(url_for('index'))
    if request.method == 'POST':
        user_input = request.form.get('user', '').strip()
        pass_input = request.form.get('password', '')
        next_url = request.form.get('next', '/')
        expected_user, expected_pass = get_credentials()
        # Comparação em tempo constante para evitar timing attacks
        user_ok = secrets.compare_digest(user_input, expected_user)
        pass_ok = secrets.compare_digest(pass_input, expected_pass)
        if user_ok and pass_ok:
            session.permanent = False
            session['logged_in'] = True
            session['user'] = user_input
            logger.info(f"Login: {user_input} de {request.remote_addr}")
            # Validar next_url para evitar open redirect
            if not next_url.startswith('/'):
                next_url = '/'
            return redirect(next_url)
        logger.warning(f"Login falhou para '{user_input}' de {request.remote_addr}")
        return render_template_string(LOGIN_TEMPLATE, error='Usuário ou senha incorretos.', next=next_url)
    return render_template_string(LOGIN_TEMPLATE, error=None, next=request.args.get('next', '/'))

@app.route('/dvr-debug-session')
def debug_session():
    """Diagnóstico temporário — remover depois de resolver o problema"""
    from flask import jsonify
    cookie_name = app.config.get('SESSION_COOKIE_NAME', 'session')
    return jsonify({
        'session_logged_in': session.get('logged_in'),
        'session_user': session.get('user'),
        'session_keys': list(session.keys()),
        'cookie_present': cookie_name in request.cookies,
        'cookie_name': cookie_name,
        'secret_key_prefix': app.secret_key[:12] if app.secret_key else None,
        'all_cookies': list(request.cookies.keys()),
    })

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/api/auth/set', methods=['POST'])
@login_required
def set_auth():
    """Define usuário e senha do DVR (salva no cameras_config.json)"""
    data = request.get_json()
    if not data or not data.get('user') or not data.get('password'):
        return jsonify({'success': False, 'error': 'Usuário e senha obrigatórios'}), 400
    config = load_config()
    config['auth'] = {'user': data['user'], 'password': data['password']}
    save_config(config)
    return jsonify({'success': True})

# ---------- Fim das rotas de autenticação ----------

def test_camera_connection(ip, port, user, password, model):
    """Testa conexão com câmera e retorna path funcional"""
    paths = CAMERA_MODELS.get(model, {}).get('paths', [])
    
    for path in paths:
        url = f"http://{ip}:{port}{path}"
        try:
            response = requests.get(url, auth=(user, password), timeout=3, headers=HTTP_HEADERS)
            if response.status_code == 200 and len(response.content) > 100:
                return {'success': True, 'path': path, 'url': url}
        except:
            continue
    
    return {'success': False, 'error': 'Nenhum path funcional encontrado'}

def gen_frames_from_camera(cam_id):
    """Captura frames da câmera usando IP/porta/path configurados na tela de configurações"""
    config = load_config()
    cam = config.get('cameras', {}).get(cam_id)

    if not cam:
        logger.error(f"Câmera {cam_id} não encontrada na configuração")
        return

    ip = cam.get('ip', '')
    port = cam.get('port', 80)
    path = cam.get('path', '/snapshot.cgi')
    user = cam.get('user', '')
    password = cam.get('password', '')

    snapshot_url = f"http://{ip}:{port}{path}"
    auth = (user, password) if user else None

    logger.info(f"Conectando câmera {cam_id}: {snapshot_url}")

    frame_count = 0
    error_count = 0

    while True:
        try:
            response = requests.get(snapshot_url, auth=auth, timeout=5, headers=HTTP_HEADERS)
            
            if response.status_code == 200 and len(response.content) > 1000:
                frame_count += 1
                error_count = 0
                
                if frame_count % 100 == 0:
                    logger.info(f"Câmera {cam_id}: {frame_count} frames")
                
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + response.content + b'\r\n')
                
                time.sleep(0.1)  # ~10 FPS
            else:
                error_count += 1
                if error_count > 10:
                    logger.error(f"Câmera {cam_id}: muitos erros, parando")
                    break
                time.sleep(0.5)
                
        except Exception as e:
            error_count += 1
            logger.error(f"Câmera {cam_id}: {str(e)}")
            
            if error_count > 10:
                logger.error(f"Câmera {cam_id}: Agent DVR não responde")
                break
            
            time.sleep(1)

@app.route('/')
@login_required
def index():
    """Página principal com visualização das câmeras"""
    config = load_config()
    cameras = config.get('cameras', {})
    
    if not cameras:
        return redirect(url_for('config_page'))  # redireciona para /cameras
    
    camera_boxes = ""
    for cam_id, cam_info in cameras.items():
        if cam_info.get('enabled', True):
            camera_boxes += f"""
            <div class="camera-box" onclick="openFullscreen('{cam_id}')">
                <h3>📹 {cam_info['name']}</h3>
                <img src="/camera/{cam_id}" alt="{cam_info['name']}" id="cam-{cam_id}">
                <div class="status">
                    {cam_info['ip']}:{cam_info['port']} • {CAMERA_MODELS.get(cam_info.get('model', 'generic'), {}).get('name', 'Genérico')}
                </div>
            </div>
            """
    
    if not camera_boxes:
        camera_boxes = '<div style="grid-column: 1/-1; text-align: center; color: #fff;"><h2>⚠️ Nenhuma câmera ativa</h2><p>Configure câmeras na <a href="/cameras" style="color: #4CAF50;">página de configuração</a></p></div>'
    
    return render_template_string(INDEX_TEMPLATE, camera_boxes=camera_boxes)

@app.route('/cameras')
@login_required
def config_page():
    """Página de configuração"""
    config = load_config()
    cameras = config.get('cameras', {})
    
    return render_template_string(CONFIG_TEMPLATE, 
                                   cameras=cameras, 
                                   models=CAMERA_MODELS)

@app.route('/api/camera/add', methods=['POST'])
@login_required
def add_camera():
    """Adiciona nova câmera"""
    data = request.form
    
    config = load_config()
    cam_id = str(int(time.time() * 1000))  # ID único baseado em timestamp
    
    # Testar conexão
    result = test_camera_connection(
        data['ip'], data['port'], 
        data['user'], data['password'], 
        data['model']
    )
    
    if not result['success']:
        return jsonify({'success': False, 'error': result.get('error')}), 400
    
    config['cameras'][cam_id] = {
        'name': data['name'],
        'ip': data['ip'],
        'port': data['port'],
        'user': data['user'],
        'password': data['password'],
        'model': data['model'],
        'path': result['path'],
        'enabled': True,
        'created_at': datetime.now().isoformat()
    }
    
    save_config(config)
    logger.info(f"Câmera adicionada: {data['name']} ({data['ip']})")
    
    return jsonify({'success': True, 'cam_id': cam_id})

@app.route('/api/camera/edit/<cam_id>', methods=['POST'])
@login_required
def edit_camera(cam_id):
    """Edita câmera existente"""
    data = request.form
    config = load_config()
    
    if cam_id not in config['cameras']:
        return jsonify({'success': False, 'error': 'Câmera não encontrada'}), 404
    
    # Testar conexão com novos dados
    result = test_camera_connection(
        data['ip'], data['port'], 
        data['user'], data['password'], 
        data['model']
    )
    
    if not result['success']:
        return jsonify({'success': False, 'error': result.get('error')}), 400
    
    # Atualizar câmera
    config['cameras'][cam_id].update({
        'name': data['name'],
        'ip': data['ip'],
        'port': data['port'],
        'user': data['user'],
        'password': data['password'],
        'model': data['model'],
        'path': result['path'],
        'updated_at': datetime.now().isoformat()
    })
    
    save_config(config)
    logger.info(f"Câmera editada: {data['name']} ({data['ip']})")
    
    return jsonify({'success': True})

@app.route('/api/camera/get/<cam_id>', methods=['GET'])
@login_required
def get_camera(cam_id):
    """Retorna dados da câmera para edição"""
    config = load_config()
    
    if cam_id in config['cameras']:
        return jsonify({'success': True, 'camera': config['cameras'][cam_id]})
    
    return jsonify({'success': False}), 404

@app.route('/api/camera/delete/<cam_id>', methods=['POST'])
@login_required
def delete_camera(cam_id):
    """Remove câmera"""
    config = load_config()
    
    if cam_id in config['cameras']:
        cam_name = config['cameras'][cam_id]['name']
        del config['cameras'][cam_id]
        save_config(config)
        logger.info(f"Câmera removida: {cam_name}")
        return jsonify({'success': True})
    
    return jsonify({'success': False, 'error': 'Câmera não encontrada'}), 404

@app.route('/api/camera/toggle/<cam_id>', methods=['POST'])
@login_required
def toggle_camera(cam_id):
    """Ativa/desativa câmera"""
    config = load_config()
    
    if cam_id in config['cameras']:
        config['cameras'][cam_id]['enabled'] = not config['cameras'][cam_id].get('enabled', True)
        save_config(config)
        return jsonify({'success': True, 'enabled': config['cameras'][cam_id]['enabled']})
    
    return jsonify({'success': False}), 404

@app.route('/camera/<cam_id>')
@login_required
def camera_stream(cam_id):
    """Stream MJPEG da câmera"""
    return Response(
        gen_frames_from_camera(cam_id),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/scan')
@login_required
def scan_page():
    """Página de scanner de rede"""
    return render_template_string(SCAN_TEMPLATE)

@app.route('/api/scan', methods=['POST'])
@login_required
def scan_network():
    """Scanner de rede para encontrar câmeras (dois estágios: TCP rápido + HTTP nas respostas)"""
    # Detectar subnet local de forma confiável
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = socket.gethostbyname(socket.gethostname())
    network = '.'.join(local_ip.split('.')[:-1])

    common_ports = [80, 8080, 8899, 554]

    # --- Estágio 1: TCP socket check (rápido, sem trafegar dados HTTP) ---
    def tcp_open(ip, port):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.3)
            if sock.connect_ex((ip, port)) == 0:
                sock.close()
                return (ip, port)
            sock.close()
        except Exception:
            pass
        return None

    open_endpoints = []
    with ThreadPoolExecutor(max_workers=200) as executor:
        futures = {
            executor.submit(tcp_open, f"{network}.{i}", port): (f"{network}.{i}", port)
            for i in range(1, 255)
            for port in common_ports
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                open_endpoints.append(result)

    # --- Estágio 2: HTTP GET apenas nos endpoints que responderam ao TCP ---
    cameras_found = []
    camera_keywords = ['camera', 'video', 'stream', 'ipcam', 'webcam', 'snapshot', 'cgi-bin']

    def check_http(ip, port):
        try:
            url = f"http://{ip}:{port}"
            response = requests.get(url, timeout=2, allow_redirects=True, headers=HTTP_HEADERS)
            content = response.text.lower()
            if any(kw in content for kw in camera_keywords):
                return {'ip': ip, 'port': port, 'url': url}
            # Porta 554 é RTSP — se respondeu ao TCP já é candidata
            if port == 554:
                return {'ip': ip, 'port': port, 'url': f"rtsp://{ip}:{port}"}
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(check_http, ip, port) for ip, port in open_endpoints]
        for future in as_completed(futures):
            result = future.result()
            if result:
                cameras_found.append(result)

    return jsonify({'cameras': cameras_found, 'network': f"{network}.0/24"})

# ──────────────────────────────────────────────────────────────
# DETECÇÃO DE MOVIMENTO + GRAVAÇÃO
# ──────────────────────────────────────────────────────────────

def _frame_hash(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()

def _frames_differ(prev: bytes, curr: bytes, threshold: float = 0.02) -> bool:
    """Retorna True se os frames diferirem acima do threshold (0-1)."""
    if not PIL_AVAILABLE:
        # Fallback: comparação simples por hash MD5
        return _frame_hash(prev) != _frame_hash(curr)
    try:
        img1 = Image.open(_io.BytesIO(prev)).convert('L').resize((160, 120))
        img2 = Image.open(_io.BytesIO(curr)).convert('L').resize((160, 120))
        diff = ImageChops.difference(img1, img2)
        pixels = list(diff.getdata())
        changed = sum(1 for p in pixels if p > 25)
        return (changed / len(pixels)) > threshold
    except Exception:
        return _frame_hash(prev) != _frame_hash(curr)

def _motion_worker(cam_id: str, stop_event: threading.Event):
    """Thread de detecção de movimento para uma câmera."""
    config = load_config()
    cam = config.get('cameras', {}).get(cam_id)
    if not cam:
        return

    ip       = cam.get('ip', '')
    port     = cam.get('port', 80)
    path     = cam.get('path', '/snapshot.cgi')
    user     = cam.get('user', '')
    password = cam.get('password', '')
    url      = f"http://{ip}:{port}{path}"
    auth     = (user, password) if user else None

    cam_dir = os.path.join(RECORDINGS_DIR, cam_id)
    os.makedirs(cam_dir, exist_ok=True)

    _motion_status[cam_id] = {'active': True, 'last_motion': None, 'count': 0}
    logger.info(f"Detecção de movimento iniciada: câmera {cam_id}")

    prev_frame = None
    burst_frames = []
    burst_active = False
    burst_end_time = 0

    while not stop_event.is_set():
        try:
            r = requests.get(url, auth=auth, timeout=5, headers=HTTP_HEADERS)
            if r.status_code != 200 or len(r.content) < 1000:
                time.sleep(1)
                continue

            curr_frame = r.content

            if prev_frame is not None and _frames_differ(prev_frame, curr_frame):
                ts = datetime.now()
                ts_str = ts.strftime('%Y%m%d_%H%M%S')

                # Salvar snapshot do momento da detecção
                snap_path = os.path.join(cam_dir, f'motion_{ts_str}.jpg')
                with open(snap_path, 'wb') as f:
                    f.write(curr_frame)

                count = _motion_status[cam_id]['count'] + 1
                _motion_status[cam_id].update({
                    'last_motion': ts.strftime('%d/%m/%Y %H:%M:%S'),
                    'count': count
                })
                logger.info(f"Movimento detectado [{cam_id}] #{count} → {snap_path}")

                # Iniciar/estender burst de vídeo (30s após último movimento)
                burst_active = True
                burst_end_time = time.time() + 30
                burst_frames = [curr_frame]
            elif burst_active:
                burst_frames.append(curr_frame)

            # Finalizar burst e salvar vídeo quando o tempo acabar
            if burst_active and time.time() >= burst_end_time:
                _save_video_burst(cam_id, burst_frames)
                burst_frames = []
                burst_active = False

            prev_frame = curr_frame
            time.sleep(0.5)  # ~2 fps para detecção

        except Exception as e:
            logger.error(f"Motion worker [{cam_id}]: {e}")
            time.sleep(2)

    _motion_status[cam_id]['active'] = False
    logger.info(f"Detecção de movimento parada: câmera {cam_id}")

def _save_video_burst(cam_id: str, frames: list):
    """Salva burst de frames como vídeo MJPEG (.avi) se cv2 disponível, senão como ZIP de JPEGs."""
    if not frames:
        return
    ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    cam_dir = os.path.join(RECORDINGS_DIR, cam_id)

    try:
        import cv2
        import numpy as np
        first = cv2.imdecode(np.frombuffer(frames[0], np.uint8), cv2.IMREAD_COLOR)
        h, w = first.shape[:2]
        video_path = os.path.join(cam_dir, f'video_{ts_str}.avi')
        out = cv2.VideoWriter(video_path, cv2.VideoWriter_fourcc(*'MJPG'), 2, (w, h))
        for frame_data in frames:
            img = cv2.imdecode(np.frombuffer(frame_data, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                out.write(img)
        out.release()
        logger.info(f"Vídeo salvo: {video_path}")
    except ImportError:
        # cv2 não disponível: salvar frames individuais
        for i, frame_data in enumerate(frames):
            frame_path = os.path.join(cam_dir, f'video_{ts_str}_f{i:04d}.jpg')
            with open(frame_path, 'wb') as f:
                f.write(frame_data)
        logger.info(f"Frames de vídeo salvos em {cam_dir} (cv2 indisponível)")

# ── Rotas de gravação/detecção ─────────────────────────────────

@app.route('/api/camera/<cam_id>/motion/start', methods=['POST'])
@login_required
def start_motion(cam_id):
    """Inicia detecção de movimento para a câmera"""
    config = load_config()
    if cam_id not in config.get('cameras', {}):
        return jsonify({'success': False, 'error': 'Câmera não encontrada'}), 404
    if cam_id in _motion_threads and _motion_threads[cam_id].is_alive():
        return jsonify({'success': False, 'error': 'Já ativa'})
    stop_ev = threading.Event()
    _motion_stop[cam_id] = stop_ev
    t = threading.Thread(target=_motion_worker, args=(cam_id, stop_ev), daemon=True)
    _motion_threads[cam_id] = t
    t.start()
    return jsonify({'success': True})

@app.route('/api/camera/<cam_id>/motion/stop', methods=['POST'])
@login_required
def stop_motion(cam_id):
    """Para detecção de movimento"""
    if cam_id in _motion_stop:
        _motion_stop[cam_id].set()
    return jsonify({'success': True})

@app.route('/api/camera/<cam_id>/motion/status')
@login_required
def motion_status(cam_id):
    status = _motion_status.get(cam_id, {'active': False, 'last_motion': None, 'count': 0})
    if cam_id in _motion_threads:
        status['active'] = _motion_threads[cam_id].is_alive()
    return jsonify(status)

@app.route('/api/camera/<cam_id>/snapshot', methods=['POST'])
@login_required
def take_snapshot(cam_id):
    """Captura e salva um snapshot manual da câmera"""
    config = load_config()
    cam = config.get('cameras', {}).get(cam_id)
    if not cam:
        return jsonify({'success': False, 'error': 'Câmera não encontrada'}), 404
    url  = f"http://{cam['ip']}:{cam['port']}{cam['path']}"
    auth = (cam['user'], cam['password']) if cam.get('user') else None
    try:
        r = requests.get(url, auth=auth, timeout=5, headers=HTTP_HEADERS)
        if r.status_code == 200 and len(r.content) > 1000:
            cam_dir = os.path.join(RECORDINGS_DIR, cam_id)
            os.makedirs(cam_dir, exist_ok=True)
            ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')
            path = os.path.join(cam_dir, f'snap_{ts_str}.jpg')
            with open(path, 'wb') as f:
                f.write(r.content)
            return jsonify({'success': True, 'file': f'snap_{ts_str}.jpg'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': False, 'error': 'Sem resposta da câmera'}), 502

@app.route('/recordings')
@login_required
def recordings_page():
    """Página de gravações e snapshots"""
    config = load_config()
    cameras = config.get('cameras', {})
    files_by_cam = {}
    for cam_id, cam_info in cameras.items():
        cam_dir = os.path.join(RECORDINGS_DIR, cam_id)
        if os.path.isdir(cam_dir):
            files = sorted(os.listdir(cam_dir), reverse=True)[:50]
            files_by_cam[cam_id] = {'name': cam_info['name'], 'files': files}
    return render_template_string(RECORDINGS_TEMPLATE, files_by_cam=files_by_cam)

@app.route('/recordings/<cam_id>/<filename>')
@login_required
def serve_recording(cam_id, filename):
    """Serve arquivo de gravação"""
    # Sanitizar: apenas nome simples, sem path traversal
    filename = os.path.basename(filename)
    file_path = os.path.join(RECORDINGS_DIR, cam_id, filename)
    if not os.path.isfile(file_path):
        return 'Arquivo não encontrado', 404
    return send_file(file_path)


RECORDINGS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Gravações - DVR Local</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Arial; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; color: white; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { background: rgba(0,0,0,0.3); padding: 20px; border-radius: 12px; margin-bottom: 30px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
        .nav { display: flex; gap: 10px; flex-wrap: wrap; }
        .btn { padding: 10px 18px; border-radius: 6px; text-decoration: none; font-weight: bold; border: none; cursor: pointer; transition: all 0.2s; display: inline-block; font-size: 0.9em; }
        .btn-secondary { background: #2196F3; color: white; }
        .btn-danger { background: #f44336; color: white; }
        .btn:hover { transform: translateY(-2px); }
        .cam-section { background: rgba(255,255,255,0.15); border-radius: 12px; padding: 20px; margin-bottom: 20px; }
        .cam-section h2 { margin-bottom: 15px; }
        .files-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 15px; }
        .file-card { background: rgba(0,0,0,0.3); border-radius: 8px; overflow: hidden; text-align: center; }
        .file-card img { width: 100%; height: 130px; object-fit: cover; display: block; cursor: pointer; }
        .file-card .file-name { padding: 8px; font-size: 0.75em; opacity: 0.8; word-break: break-all; }
        .file-card .file-actions { padding: 0 8px 10px; display: flex; gap: 6px; justify-content: center; }
        .file-card .file-actions a { font-size: 0.8em; padding: 4px 10px; }
        .file-card.video-card img { position: relative; }
        .empty { text-align: center; opacity: 0.6; padding: 30px; }
        .lightbox { display: none; position: fixed; top:0; left:0; width:100vw; height:100vh; background:rgba(0,0,0,0.9); z-index:9999; align-items:center; justify-content:center; }
        .lightbox.active { display: flex; }
        .lightbox img { max-width: 95vw; max-height: 95vh; border-radius: 8px; }
        .lightbox-close { position: fixed; top: 20px; right: 30px; color: white; font-size: 2em; cursor: pointer; }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>🎞️ Gravações e Snapshots</h1>
        <div class="nav">
            <a href="/" class="btn btn-secondary">🏠 Câmeras</a>
            <a href="/cameras" class="btn btn-secondary">⚙️ Config</a>
            <a href="/logout" class="btn btn-danger">🚪 Sair</a>
        </div>
    </div>

    {% if files_by_cam %}
        {% for cam_id, data in files_by_cam.items() %}
        <div class="cam-section">
            <h2>📹 {{ data.name }}</h2>
            {% if data.files %}
            <div class="files-grid">
                {% for fname in data.files %}
                {% set is_video = fname.endswith('.avi') or fname.endswith('.mp4') %}
                <div class="file-card {{ 'video-card' if is_video else '' }}">
                    {% if is_video %}
                        <div style="height:130px; display:flex; align-items:center; justify-content:center; font-size:3em; background:rgba(0,0,0,0.5);">🎬</div>
                    {% else %}
                        <img src="/recordings/{{ cam_id }}/{{ fname }}" loading="lazy"
                             onclick="showLightbox('/recordings/{{ cam_id }}/{{ fname }}')" alt="{{ fname }}">
                    {% endif %}
                    <div class="file-name">{{ fname }}</div>
                    <div class="file-actions">
                        <a href="/recordings/{{ cam_id }}/{{ fname }}" download class="btn btn-secondary">⬇️</a>
                    </div>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <p class="empty">Nenhuma gravação ainda.</p>
            {% endif %}
        </div>
        {% endfor %}
    {% else %}
        <div class="cam-section"><p class="empty">Nenhuma gravação encontrada. Ative a detecção de movimento ou tire snapshots na tela de configuração.</p></div>
    {% endif %}
</div>

<div class="lightbox" id="lightbox" onclick="closeLightbox()">
    <span class="lightbox-close">✕</span>
    <img id="lightbox-img" src="" alt="">
</div>
<script>
function showLightbox(src) {
    document.getElementById('lightbox-img').src = src;
    document.getElementById('lightbox').classList.add('active');
}
function closeLightbox() { document.getElementById('lightbox').classList.remove('active'); }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLightbox(); });
</script>
</body>
</html>
"""

INDEX_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>DVR Local</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', Arial; 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            color: white;
        }
        .header {
            background: rgba(0, 0, 0, 0.3);
            padding: 20px;
            backdrop-filter: blur(10px);
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
        }
        h1 { font-size: 2em; }
        .header-buttons { display: flex; gap: 10px; }
        .btn {
            padding: 10px 20px;
            border-radius: 6px;
            text-decoration: none;
            font-weight: bold;
            transition: all 0.3s;
            display: inline-block;
        }
        .btn-config { background: #4CAF50; color: white; }
        .btn-scan { background: #2196F3; color: white; }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 4px 8px rgba(0,0,0,0.3); }
        .cameras {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 25px;
            max-width: 1800px;
            margin: 30px auto;
            padding: 0 20px;
        }
        .camera-box {
            background: rgba(255, 255, 255, 0.15);
            padding: 20px;
            border-radius: 12px;
            backdrop-filter: blur(10px);
            cursor: pointer;
            transition: all 0.3s;
            border: 2px solid rgba(255, 255, 255, 0.2);
        }
        .camera-box:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 30px rgba(0,0,0,0.3);
        }
        .camera-box h3 { margin-bottom: 10px; font-size: 1.3em; }
        .camera-box img {
            width: 100%;
            min-height: 300px;
            border-radius: 8px;
            background: #000;
            border: 2px solid rgba(255, 255, 255, 0.1);
        }
        .status {
            margin-top: 10px;
            font-size: 0.85em;
            opacity: 0.8;
            text-align: center;
        }
        .fullscreen {
            display: none;
            position: fixed;
            top: 0; left: 0;
            width: 100vw; height: 100vh;
            background: #000;
            z-index: 9999;
            flex-direction: column;
        }
        .fullscreen.active { display: flex; }
        .fullscreen-header {
            background: linear-gradient(135deg, rgba(102, 126, 234, 0.9), rgba(118, 75, 162, 0.9));
            padding: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .fullscreen-title { font-size: 1.5em; }
        .fullscreen-close {
            background: #e74c3c;
            color: white;
            border: none;
            padding: 10px 25px;
            border-radius: 6px;
            font-size: 1em;
            cursor: pointer;
        }
        .fullscreen-content {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .fullscreen-content img {
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
        }
        @media (max-width: 768px) {
            .cameras { grid-template-columns: 1fr; }
            .header { flex-direction: column; gap: 15px; text-align: center; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🎥 DVR Local</h1>
        <div class="header-buttons">
            <a href="/cameras" class="btn btn-config">⚙️ Configurar</a>
            <a href="/scan" class="btn btn-scan">🔍 Buscar</a>
            <a href="/recordings" class="btn" style="background:#e67e22;">🎞️ Gravações</a>
            <a href="/logout" class="btn" style="background:#e74c3c;">🚪 Sair</a>
        </div>
    </div>
    <div class="cameras">{{ camera_boxes|safe }}</div>
    <div id="fullscreen" class="fullscreen">
        <div class="fullscreen-header">
            <div class="fullscreen-title" id="fullscreen-title">Câmera</div>
            <button class="fullscreen-close" onclick="closeFullscreen()">✕ Fechar</button>
        </div>
        <div class="fullscreen-content">
            <img id="fullscreen-img" src="" alt="Camera">
        </div>
    </div>
    <script>
        let currentCam = null;
        function openFullscreen(camId) {
            currentCam = camId;
            document.getElementById('fullscreen-img').src = '/camera/' + camId;
            document.getElementById('fullscreen').classList.add('active');
        }
        function closeFullscreen() {
            document.getElementById('fullscreen').classList.remove('active');
            currentCam = null;
        }
        document.addEventListener('keydown', e => { if (e.key === 'Escape') closeFullscreen(); });
        setInterval(() => {
            document.querySelectorAll('.camera-box img').forEach(img => {
                img.src = img.src.split('?')[0] + '?t=' + Date.now();
            });
        }, 30000);
    </script>
</body>
</html>
"""

CONFIG_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Configurações - DVR Local</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Arial;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            color: white;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        .header {
            background: rgba(0, 0, 0, 0.3);
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .btn {
            padding: 10px 20px;
            border-radius: 6px;
            text-decoration: none;
            font-weight: bold;
            border: none;
            cursor: pointer;
            transition: all 0.3s;
            display: inline-block;
        }
        .btn-primary { background: #4CAF50; color: white; }
        .btn-secondary { background: #2196F3; color: white; }
        .btn-danger { background: #f44336; color: white; }
        .btn:hover { transform: translateY(-2px); }
        .form-container {
            background: rgba(255, 255, 255, 0.15);
            padding: 30px;
            border-radius: 12px;
            backdrop-filter: blur(10px);
            margin-bottom: 30px;
        }
        .form-group { margin-bottom: 20px; }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
        }
        input, select {
            width: 100%;
            padding: 10px;
            border-radius: 6px;
            border: 2px solid rgba(255, 255, 255, 0.3);
            background: rgba(255, 255, 255, 0.9);
            font-size: 1em;
            color: #333;
        }
        .cameras-list {
            background: rgba(255, 255, 255, 0.15);
            padding: 20px;
            border-radius: 12px;
        }
        .camera-item {
            background: rgba(0, 0, 0, 0.3);
            padding: 15px;
            margin-bottom: 15px;
            border-radius: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
        }
        .camera-info h3 { margin-bottom: 5px; }
        .camera-info p { font-size: 0.9em; opacity: 0.8; }
        .camera-actions {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>⚙️ Configurações</h1>
            <div style="display:flex; gap:8px; flex-wrap:wrap;">
                <a href="/" class="btn btn-secondary">🏠 Câmeras</a>
                <a href="/scan" class="btn btn-secondary">🔍 Buscar</a>
                <a href="/recordings" class="btn" style="background:#e67e22;">🎞️ Gravações</a>
                <a href="/logout" class="btn" style="background:#e74c3c;">🚪 Sair</a>
            </div>
        </div>
        
        <div class="form-container">
            <h2 id="formTitle">➕ Adicionar Nova Câmera</h2>
            <form id="cameraForm">
                <input type="hidden" id="editCamId" value="">
                <div class="form-group">
                    <label>Nome da Câmera</label>
                    <input type="text" id="camName" name="name" required placeholder="Ex: Câmera Entrada">
                </div>
                <div class="form-group">
                    <label>Endereço IP</label>
                    <input type="text" id="camIp" name="ip" required placeholder="Ex: 192.168.1.100">
                </div>
                <div class="form-group">
                    <label>Porta</label>
                    <input type="number" id="camPort" name="port" value="80" required>
                </div>
                <div class="form-group">
                    <label>Usuário</label>
                    <input type="text" id="camUser" name="user" value="admin" required>
                </div>
                <div class="form-group">
                    <label>Senha</label>
                    <input type="password" id="camPassword" name="password" required>
                </div>
                <div class="form-group">
                    <label>Modelo da Câmera</label>
                    <select id="camModel" name="model" required>
                        {% for key, model in models.items() %}
                        <option value="{{ key }}">{{ model.name }}</option>
                        {% endfor %}
                    </select>
                </div>
                <div style="display: flex; gap: 10px;">
                    <button type="submit" class="btn btn-primary" id="submitBtn">✓ Adicionar Câmera</button>
                    <button type="button" class="btn btn-secondary" id="cancelBtn" onclick="cancelEdit()" style="display: none;">✕ Cancelar</button>
                </div>
            </form>
        </div>
        
        <div class="cameras-list">
            <h2>📹 Câmeras Configuradas</h2>
            {% if cameras %}
                {% for cam_id, cam in cameras.items() %}
                <div class="camera-item">
                    <div class="camera-info">
                        <h3>{{ cam.name }}</h3>
                        <p>{{ cam.ip }}:{{ cam.port }} • {{ cam.user }} • {{ models[cam.model].name if cam.model in models else 'Genérico' }}</p>
                        <p style="font-size: 0.8em;">Path: {{ cam.path }}</p>
                    </div>
                    <div class="camera-actions">
                        <button onclick="editCamera('{{ cam_id }}')" class="btn btn-primary">✏️ Editar</button>
                        <button onclick="toggleCamera('{{ cam_id }}')" class="btn btn-secondary">
                            {{ '✓ Ativa' if cam.enabled else '✗ Inativa' }}
                        </button>
                        <button onclick="takeSnapshot('{{ cam_id }}')" class="btn" style="background:#27ae60;">📸 Snapshot</button>
                        <button id="motion-btn-{{ cam_id }}" onclick="toggleMotion('{{ cam_id }}')" class="btn" style="background:#8e44ad;">🎬 Detecção</button>
                        <button onclick="deleteCamera('{{ cam_id }}')" class="btn btn-danger">🗑️ Remover</button>
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <p style="text-align: center; opacity: 0.7;">Nenhuma câmera configurada ainda.</p>
            {% endif %}
        </div>

        <div class="form-container" style="margin-top: 30px;">
            <h2>🔐 Alterar Credenciais de Acesso</h2>
            <div class="form-group">
                <label>Novo Usuário</label>
                <input type="text" id="authUser" autocomplete="username" placeholder="Ex: admin">
            </div>
            <div class="form-group">
                <label>Nova Senha</label>
                <input type="password" id="authPass" autocomplete="new-password" placeholder="Mínimo 6 caracteres">
            </div>
            <div class="form-group">
                <label>Confirmar Senha</label>
                <input type="password" id="authPass2" autocomplete="new-password" placeholder="Repita a senha">
            </div>
            <button onclick="changePassword()" class="btn btn-primary">💾 Salvar Credenciais</button>
        </div>
    </div>
    
    <script>
        let editingCamId = null;
        
        document.getElementById('cameraForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData(e.target);
            const camId = document.getElementById('editCamId').value;
            
            const btn = document.getElementById('submitBtn');
            btn.disabled = true;
            btn.textContent = '⏳ Testando conexão...';
            
            try {
                const url = camId ? `/api/camera/edit/${camId}` : '/api/camera/add';
                const response = await fetch(url, {
                    method: 'POST',
                    body: formData
                });
                
                const result = await response.json();
                
                if (result.success) {
                    alert(camId ? '✓ Câmera editada com sucesso!' : '✓ Câmera adicionada com sucesso!');
                    location.reload();
                } else {
                    alert('✗ Erro: ' + (result.error || 'Falha ao salvar câmera'));
                }
            } catch (error) {
                alert('✗ Erro ao salvar câmera: ' + error);
            } finally {
                btn.disabled = false;
                btn.textContent = camId ? '✓ Salvar Alterações' : '✓ Adicionar Câmera';
            }
        });
        
        async function editCamera(camId) {
            try {
                const response = await fetch(`/api/camera/get/${camId}`);
                const result = await response.json();
                
                if (result.success) {
                    const cam = result.camera;
                    document.getElementById('editCamId').value = camId;
                    document.getElementById('camName').value = cam.name;
                    document.getElementById('camIp').value = cam.ip;
                    document.getElementById('camPort').value = cam.port;
                    document.getElementById('camUser').value = cam.user;
                    document.getElementById('camPassword').value = cam.password;
                    document.getElementById('camModel').value = cam.model;
                    
                    document.getElementById('formTitle').textContent = '✏️ Editar Câmera';
                    document.getElementById('submitBtn').textContent = '✓ Salvar Alterações';
                    document.getElementById('cancelBtn').style.display = 'inline-block';
                    
                    // Scroll para o formulário
                    document.querySelector('.form-container').scrollIntoView({ behavior: 'smooth' });
                }
            } catch (error) {
                alert('Erro ao carregar câmera: ' + error);
            }
        }
        
        function cancelEdit() {
            document.getElementById('cameraForm').reset();
            document.getElementById('editCamId').value = '';
            document.getElementById('formTitle').textContent = '➕ Adicionar Nova Câmera';
            document.getElementById('submitBtn').textContent = '✓ Adicionar Câmera';
            document.getElementById('cancelBtn').style.display = 'none';
        }
        
        async function deleteCamera(camId) {
            if (!confirm('Tem certeza que deseja remover esta câmera?')) return;
            
            try {
                const response = await fetch(`/api/camera/delete/${camId}`, { method: 'POST' });
                if (response.ok) {
                    location.reload();
                }
            } catch (error) {
                alert('Erro ao remover câmera: ' + error);
            }
        }
        
        async function toggleCamera(camId) {
            try {
                const response = await fetch(`/api/camera/toggle/${camId}`, { method: 'POST' });
                if (response.ok) {
                    location.reload();
                }
            } catch (error) {
                alert('Erro ao alternar câmera: ' + error);
            }
        }

        async function takeSnapshot(camId) {
            try {
                const r = await fetch(`/api/camera/${camId}/snapshot`, { method: 'POST' });
                const d = await r.json();
                if (d.success) alert(`✓ Snapshot salvo: ${d.file}`);
                else alert('✗ Erro: ' + (d.error || 'falha'));
            } catch(e) { alert('Erro: ' + e); }
        }

        const _motionActive = {};
        async function toggleMotion(camId) {
            const btn = document.getElementById('motion-btn-' + camId);
            const active = _motionActive[camId];
            const url = `/api/camera/${camId}/motion/${active ? 'stop' : 'start'}`;
            try {
                const r = await fetch(url, { method: 'POST' });
                const d = await r.json();
                if (d.success) {
                    _motionActive[camId] = !active;
                    btn.textContent = _motionActive[camId] ? '⏹️ Parar Detecção' : '🎬 Detecção';
                    btn.style.background = _motionActive[camId] ? '#c0392b' : '#8e44ad';
                }
            } catch(e) { alert('Erro: ' + e); }
        }

        // Atualizar status de detecção ao carregar a página
        document.querySelectorAll('[id^="motion-btn-"]').forEach(btn => {
            const camId = btn.id.replace('motion-btn-', '');
            fetch(`/api/camera/${camId}/motion/status`)
                .then(r => r.json())
                .then(d => {
                    _motionActive[camId] = d.active;
                    if (d.active) {
                        btn.textContent = `⏹️ Parar (${d.count} eventos)`;
                        btn.style.background = '#c0392b';
                    }
                }).catch(()=>{});
        });

        async function changePassword() {
            const user = document.getElementById('authUser').value.trim();
            const pass = document.getElementById('authPass').value;
            const pass2 = document.getElementById('authPass2').value;
            if (!user || !pass) return alert('Preencha usuário e senha.');
            if (pass !== pass2) return alert('As senhas não coincidem.');
            if (pass.length < 6) return alert('Senha deve ter pelo menos 6 caracteres.');
            try {
                const res = await fetch('/api/auth/set', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({user, password: pass})
                });
                const data = await res.json();
                if (data.success) {
                    alert('Credenciais atualizadas! Faça login novamente.');
                    window.location.href = '/logout';
                } else {
                    alert('Erro: ' + (data.error || 'desconhecido'));
                }
            } catch(e) {
                alert('Erro ao salvar credenciais.');
            }
        }
    </script>
</body>
</html>
"""

SCAN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Scanner - DVR Local</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Arial;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            color: white;
            padding: 20px;
        }
        .container { max-width: 1000px; margin: 0 auto; }
        .header {
            background: rgba(0, 0, 0, 0.3);
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .btn {
            padding: 10px 20px;
            border-radius: 6px;
            text-decoration: none;
            font-weight: bold;
            border: none;
            cursor: pointer;
            transition: all 0.3s;
            display: inline-block;
        }
        .btn-primary { background: #4CAF50; color: white; }
        .btn-secondary { background: #2196F3; color: white; }
        .btn:hover { transform: translateY(-2px); }
        .scan-container {
            background: rgba(255, 255, 255, 0.15);
            padding: 30px;
            border-radius: 12px;
            text-align: center;
        }
        #results { margin-top: 20px; text-align: left; }
        .result-item {
            background: rgba(0, 0, 0, 0.3);
            padding: 15px;
            margin-bottom: 10px;
            border-radius: 8px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔍 Scanner de Rede</h1>
            <a href="/cameras" class="btn btn-secondary">← Voltar</a>
        </div>
        
        <div class="scan-container">
            <h2>Buscar Câmeras na Rede</h2>
            <p style="margin: 20px 0;">Este scanner procura por câmeras IP na sua rede local.</p>
            <button onclick="startScan()" class="btn btn-primary" id="scanBtn">🔍 Iniciar Escaneamento</button>
            <div id="results"></div>
        </div>
    </div>
    
    <script>
        async function startScan() {
            const btn = document.getElementById('scanBtn');
            const results = document.getElementById('results');
            
            btn.disabled = true;
            btn.textContent = '⏳ Escaneando... (aguarde alguns minutos)';
            results.innerHTML = '<p style="text-align: center;">Buscando câmeras na rede...</p>';
            
            try {
                const response = await fetch('/api/scan', { method: 'POST' });
                const data = await response.json();
                
                results.innerHTML = `<h3>✓ Encontradas ${data.cameras.length} câmera(s) na rede ${data.network}:</h3>`;
                
                if (data.cameras.length > 0) {
                    data.cameras.forEach(cam => {
                        results.innerHTML += `
                            <div class="result-item">
                                <strong>IP:</strong> ${cam.ip}<br>
                                <strong>Porta:</strong> ${cam.port}<br>
                                <strong>URL:</strong> ${cam.url}
                            </div>
                        `;
                    });
                    results.innerHTML += '<p style="margin-top: 15px;">Adicione estas câmeras manualmente na página de configuração.</p>';
                } else {
                    results.innerHTML += '<p>⚠️ Nenhuma câmera encontrada. Tente adicionar manualmente.</p>';
                }
            } catch (error) {
                results.innerHTML = `<p style="color: #f44336;">✗ Erro: ${error}</p>`;
            } finally {
                btn.disabled = false;
                btn.textContent = '🔍 Iniciar Escaneamento';
            }
        }
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🚀 DVR Local - Sistema de Câmeras Iniciando...")
    logger.info("=" * 60)
    
    # Criar arquivo de config se não existir
    if not os.path.exists(CONFIG_FILE):
        save_config({'cameras': {}})
        logger.info("✓ Arquivo de configuração criado")
    
    config = load_config()
    logger.info(f"📹 {len(config.get('cameras', {}))} câmera(s) configurada(s)")
    logger.info("")
    logger.info("🌐 Acesse: http://localhost:8000/")
    logger.info("=" * 60)
    
    app.run(host='0.0.0.0', port=8000, threaded=True, debug=False)
