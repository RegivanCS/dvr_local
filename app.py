from flask import Flask, Response, render_template_string, request, redirect, url_for, jsonify, session, send_file
from functools import wraps
import requests
from requests.auth import HTTPDigestAuth
import json
import os
import shutil
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
    },
    'longse': {
        'name': 'Longse / LAPI (LongVision)',
        'paths': [
            '/LAPI/V1.0/Channel/0/Media/Video/ShotFrame',
            '/LAPI/V1.0/Channel/0/Media/JPEG/ShotFrame',
            '/LAPI/V1.0/Channel/0/Media/MainStream/ShotFrame',
        ]
    },
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

def get_storage_settings(config=None):
    """Retorna configuração de retenção cíclica das gravações."""
    if config is None:
        config = load_config()
    s = config.get('storage', {})
    try:
        max_gb = float(s.get('max_gb', 20))
    except Exception:
        max_gb = 20.0
    try:
        reserve_free_gb = float(s.get('reserve_free_gb', 2))
    except Exception:
        reserve_free_gb = 2.0
    return {
        'cyclic_enabled': bool(s.get('cyclic_enabled', True)),
        'max_gb': max(0.5, max_gb),
        'reserve_free_gb': max(0.0, reserve_free_gb),
    }

def _iter_recording_files():
    for root, _, files in os.walk(RECORDINGS_DIR):
        for name in files:
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            yield {
                'path': path,
                'size': st.st_size,
                'mtime': st.st_mtime,
            }

def enforce_recordings_limits(config=None, reason=''):
    """Apaga os arquivos mais antigos quando ultrapassa limites de retenção."""
    settings = get_storage_settings(config)
    if not settings['cyclic_enabled']:
        return {'deleted': 0, 'freed_bytes': 0, 'total_bytes': 0}

    files = sorted(_iter_recording_files(), key=lambda x: x['mtime'])
    total_bytes = sum(f['size'] for f in files)

    max_bytes = max(0, int(settings['max_gb'] * 1024 * 1024 * 1024))
    need_reduce_by_max = max(0, total_bytes - max_bytes) if max_bytes > 0 else 0

    usage = shutil.disk_usage(RECORDINGS_DIR)
    reserve_bytes = max(0, int(settings['reserve_free_gb'] * 1024 * 1024 * 1024))
    need_reduce_by_free = max(0, reserve_bytes - usage.free)

    need_remove = max(need_reduce_by_max, need_reduce_by_free)
    if need_remove <= 0:
        return {'deleted': 0, 'freed_bytes': 0, 'total_bytes': total_bytes}

    deleted, freed = 0, 0
    for item in files:
        try:
            os.remove(item['path'])
            deleted += 1
            freed += item['size']
            if freed >= need_remove:
                break
        except OSError:
            continue

    if deleted:
        logger.warning(
            f"Retenção cíclica: removidos {deleted} arquivo(s), liberado {freed/1024/1024:.1f} MB"
            + (f" [{reason}]" if reason else "")
        )

    return {'deleted': deleted, 'freed_bytes': freed, 'total_bytes': total_bytes}

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

@app.route('/api/set-recordings-url', methods=['POST'])
def set_recordings_url():
    """Salva URL pública do servidor local de gravações (chamado pelo agent.py).
    Autenticado por token derivado da senha, sem dependência de sessão."""
    import hashlib
    config = load_config()
    password = config.get('auth', {}).get('password', '')
    expected = hashlib.sha256(f'dvr-clear:{password}'.encode()).hexdigest()
    token = request.form.get('token')
    if not token or token != expected:
        return jsonify({'success': False, 'error': 'Token inválido'}), 403
    url = request.form.get('url', '').strip()
    if url:
        config['recordings_tunnel_url'] = url
    else:
        config.pop('recordings_tunnel_url', None)
    save_config(config)
    logger.info(f'recordings_tunnel_url atualizado: {url!r}')
    return jsonify({'success': True, 'url': url})


@app.route('/api/cameras/clear', methods=['POST'])
def clear_all_cameras_api():
    """Limpa TODAS as câmeras — autenticado por token derivado da senha (não depende de sessão).
    Usado pelo agent.py para evitar duplicatas em ambientes multi-worker (Phusion Passenger)."""
    import hashlib
    config = load_config()
    password = config.get('auth', {}).get('password', '')
    expected = hashlib.sha256(f'dvr-clear:{password}'.encode()).hexdigest()
    token = request.form.get('token') or request.json.get('token') if request.is_json else request.form.get('token')
    if not token or token != expected:
        return jsonify({'success': False, 'error': 'Token inválido'}), 403
    count = len(config.get('cameras', {}))
    config['cameras'] = {}
    save_config(config)
    logger.info(f'Todas as câmeras removidas via clear API ({count} câmeras)')
    return jsonify({'success': True, 'removed': count})


@app.route('/dvr-reset-senha-emergencia')
def emergency_reset():
    """Rota temporária de emergência — redefine senha para admin/!Rede!123.
    REMOVER após uso."""
    config = load_config()
    config['auth'] = {'user': 'admin', 'password': '!Rede!123'}
    save_config(config)
    return '<h2>✅ Senha redefinida!</h2><p>Usuário: <b>admin</b> / Senha: <b>!Rede!123</b></p><a href="/login">→ Fazer login</a>'

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

@app.route('/api/storage/settings', methods=['GET', 'POST'])
@login_required
def storage_settings_api():
    config = load_config()
    if request.method == 'GET':
        return jsonify({'success': True, 'storage': get_storage_settings(config)})

    data = request.get_json(silent=True) or request.form
    try:
        cyclic_enabled = str(data.get('cyclic_enabled', 'true')).lower() in ('1', 'true', 'yes', 'on')
        max_gb = float(data.get('max_gb', 20))
        reserve_free_gb = float(data.get('reserve_free_gb', 2))
    except Exception:
        return jsonify({'success': False, 'error': 'Parâmetros inválidos'}), 400

    if max_gb < 0.5 or max_gb > 5000:
        return jsonify({'success': False, 'error': 'Limite (GB) fora da faixa permitida (0.5 - 5000)'}), 400
    if reserve_free_gb < 0 or reserve_free_gb > 1000:
        return jsonify({'success': False, 'error': 'Reserva livre (GB) fora da faixa permitida (0 - 1000)'}), 400

    config['storage'] = {
        'cyclic_enabled': cyclic_enabled,
        'max_gb': max_gb,
        'reserve_free_gb': reserve_free_gb,
    }
    save_config(config)
    sweep = enforce_recordings_limits(config=config, reason='settings_update')
    return jsonify({'success': True, 'storage': config['storage'], 'sweep': sweep})

# ---------- Fim das rotas de autenticação ----------

def _rtsp_snapshot(ip: str, port: int, user: str, password: str):
    """Captura um frame JPEG via RTSP usando OpenCV.
    Retorna bytes JPEG ou None se não disponível."""
    try:
        import cv2, io
        import numpy as np
        creds = f'{user}:{password}@' if user else ''
        rtsp_url = f'rtsp://{creds}{ip}:{port}/stream'
        # Tentar varião de paths RTSP
        for rtsp_path in ['/stream', '/h264/ch1/main/av_stream', '/live/ch0/main', '/channel=1', '']:
            url = f'rtsp://{creds}{ip}:{port}{rtsp_path}'
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 4000)
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 4000)
            ret, frame = cap.read()
            cap.release()
            if ret and frame is not None:
                ok, buf = cv2.imencode('.jpg', frame)
                if ok:
                    return bytes(buf)
    except Exception:
        pass
    return None


def _camera_fetch(url: str, user: str, password: str, timeout: int = 5):
    """Faz GET na câmera tentando Basic Auth e depois Digest Auth automaticamente."""
    auth_basic  = (user, password) if user else None
    auth_digest = HTTPDigestAuth(user, password) if user else None
    for auth in [auth_basic, auth_digest]:
        try:
            r = requests.get(url, auth=auth, timeout=timeout, headers=HTTP_HEADERS)
            if r.status_code == 200:
                return r
        except Exception:
            pass
    return None


def _camera_auth(url: str, user: str, password: str):
    """Retorna o objeto auth que funciona (Basic ou Digest), ou None."""
    if not user:
        return None
    for auth in [(user, password), HTTPDigestAuth(user, password)]:
        try:
            r = requests.get(url, auth=auth, timeout=3, headers=HTTP_HEADERS)
            if r.status_code == 200:
                return auth
        except Exception:
            pass
    return None


def test_camera_connection(ip, port, user, password, model):
    """Testa conexão com câmera e retorna path funcional.
    Suporta Basic Auth e Digest Auth automaticamente."""
    all_paths = CAMERA_MODELS.get(model, {}).get('paths', [])
    # Para genérico, também tentar LAPI
    if model in ('generic', 'iscee'):
        all_paths = all_paths + CAMERA_MODELS['longse']['paths']

    for path in all_paths:
        url = f'http://{ip}:{port}{path}'
        r = _camera_fetch(url, user, password, timeout=3)
        if r is not None and len(r.content) > 100 and r.content[:2] == b'\xff\xd8':
            return {'success': True, 'path': path, 'url': url}

    # Fallback: tentar RTSP (câmeras que só entregam via RTSP)
    rtsp_port = 554
    snap = _rtsp_snapshot(ip, rtsp_port, user, password)
    if snap:
        return {'success': True, 'path': 'rtsp://', 'url': f'rtsp://{ip}:{rtsp_port}'}

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

    # Câmera RTSP-only (path salvo como 'rtsp://')
    if path == 'rtsp://':
        logger.info(f'Câmera {cam_id}: modo RTSP')
        try:
            import cv2
            creds = f'{user}:{password}@' if user else ''
            rtsp_url = f'rtsp://{creds}{ip}:554'
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            frame_count = 0
            error_count = 0
            while True:
                ret, frame = cap.read()
                if ret and frame is not None:
                    ok, buf = cv2.imencode('.jpg', frame)
                    if ok:
                        frame_count += 1
                        error_count = 0
                        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
                               + bytes(buf) + b'\r\n')
                        time.sleep(0.1)
                        continue
                error_count += 1
                if error_count > 20:
                    logger.error(f'Câmera {cam_id}: RTSP sem resposta')
                    cap.release()
                    break
                time.sleep(0.5)
        except ImportError:
            logger.error(f'Câmera {cam_id}: OpenCV não instalado — necessário para câmeras RTSP-only')
        return

    scheme = 'https' if str(port) == '443' else 'http'
    snapshot_url = f'{scheme}://{ip}:{port}{path}'
    # Detecta o tipo de auth correto (Basic ou Digest) na primeira conexão
    auth = _camera_auth(snapshot_url, user, password)

    logger.info(f'Conectando câmera {cam_id}: {snapshot_url}')

    frame_count = 0
    error_count = 0

    while True:
        try:
            # Evita cache intermediário (ex.: túnel/CDN) para não exibir frame antigo.
            sep = '&' if '?' in snapshot_url else '?'
            req_url = f"{snapshot_url}{sep}_ts={int(time.time() * 1000)}"
            response = requests.get(req_url, auth=auth, timeout=5, headers=HTTP_HEADERS)

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
            <div class="camera-box" data-cam-id="{cam_id}" onclick="openFullscreen('{cam_id}')">
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
    storage = get_storage_settings(config)
    
    return render_template_string(CONFIG_TEMPLATE, 
                                   cameras=cameras, 
                                   models=CAMERA_MODELS,
                                   storage=storage)

@app.route('/api/camera/add', methods=['POST'])
@login_required
def add_camera():
    """Adiciona nova câmera"""
    data = request.form
    
    config = load_config()
    cam_id = str(int(time.time() * 1000))  # ID único baseado em timestamp
    
    skip_test = request.form.get('skip_test', 'false').lower() == 'true'

    if skip_test:
        path = data.get('path', '/snapshot.cgi')
    else:
        # Testar conexão
        result = test_camera_connection(
            data['ip'], data['port'],
            data['user'], data['password'],
            data['model']
        )
        if not result['success']:
            return jsonify({'success': False, 'error': result.get('error')}), 400
        path = result['path']

    _scheme = 'https' if str(data['port']) == '443' else 'http'
    _snap_url = f"{_scheme}://{data['ip']}:{data['port']}{path}"
    config['cameras'][cam_id] = {
        'name': data['name'],
        'ip': data['ip'],
        'port': data['port'],
        'user': data['user'],
        'password': data['password'],
        'model': data['model'],
        'path': path,
        'snapshot_url': _snap_url,
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
    
    skip_test = request.form.get('skip_test', 'false').lower() == 'true'

    if skip_test:
        path = data.get('path', '/snapshot.jpg')
    else:
        # Testar conexão com novos dados
        result = test_camera_connection(
            data['ip'], data['port'],
            data['user'], data['password'],
            data['model']
        )
        if not result['success']:
            return jsonify({'success': False, 'error': result.get('error')}), 400
        path = result['path']

    _scheme = 'https' if str(data['port']) == '443' else 'http'
    _snap_url = f"{_scheme}://{data['ip']}:{data['port']}{path}"

    # Atualizar câmera
    config['cameras'][cam_id].update({
        'name': data['name'],
        'ip': data['ip'],
        'port': data['port'],
        'user': data['user'],
        'password': data['password'],
        'model': data['model'],
        'path': path,
        'snapshot_url': _snap_url,
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
# AGENT — endpoints para o agente local (agent.py)
# ──────────────────────────────────────────────────────────────
_agent_state = {}       # agent_name -> {'last_seen': float, 'command': str|None}
_snapshot_cache = {}    # cam_id -> bytes (último snapshot recebido do agente)

@app.route('/api/agent/heartbeat', methods=['POST'])
@login_required
def agent_heartbeat():
    data = request.get_json() or {}
    name = data.get('agent', 'unknown')
    if name not in _agent_state:
        _agent_state[name] = {'command': None}
    _agent_state[name]['last_seen'] = time.time()
    return jsonify({'ok': True})

@app.route('/api/agent/command')
@login_required
def agent_command():
    name = request.args.get('agent', 'unknown')
    if name not in _agent_state:
        _agent_state[name] = {'last_seen': time.time(), 'command': None}
    _agent_state[name]['last_seen'] = time.time()
    cmd = _agent_state[name].get('command')
    # Consumir o comando (one-shot)
    _agent_state[name]['command'] = None
    return jsonify({'command': cmd})

@app.route('/api/agent/trigger', methods=['POST'])
@login_required
def agent_trigger():
    """Browser envia este endpoint para pedir scan ao agente"""
    data = request.get_json() or {}
    name = data.get('agent')
    if not name or name not in _agent_state:
        return jsonify({'success': False, 'error': 'Agente não encontrado'}), 404
    _agent_state[name]['command'] = 'scan'
    return jsonify({'success': True})

@app.route('/api/agent/list')
@login_required
def agent_list():
    """Lista agentes conectados (heartbeat nos últimos 15s)"""
    now = time.time()
    active = [
        {'name': n, 'since': int(now - s.get('last_seen', now))}
        for n, s in _agent_state.items()
        if now - s.get('last_seen', 0) <= 15
    ]
    return jsonify({'agents': active})

@app.route('/api/agent/results', methods=['POST'])
@login_required
def agent_results():
    """Recebe resultados do scan do agente e cadastra câmeras"""
    data = request.get_json() or {}
    cameras   = data.get('cameras', [])
    cam_user  = data.get('cam_user', 'admin')
    cam_pass  = data.get('cam_password', '')
    cam_model = data.get('cam_model', 'generic')
    # O agente já verificou as câmeras localmente — não testar novamente do servidor
    skip_test = data.get('skip_test', False)

    config = load_config()
    registered = 0
    cam_ids = []
    for cam in cameras:
        if skip_test:
            path = cam.get('path', '/snapshot.cgi')
            success = True
        else:
            result  = test_camera_connection(cam['ip'], cam['port'], cam_user, cam_pass, cam_model)
            success = result['success']
            path    = result.get('path', '/snapshot.cgi') if success else '/snapshot.cgi'

        if success:
            cam_id = str(int(time.time() * 1000) + registered)
            config['cameras'][cam_id] = {
                'name': f'Câmera ({cam["ip"]})',
                'ip': cam['ip'],
                'port': cam['port'],
                'user': cam_user,
                'password': cam_pass,
                'model': cam_model,
                'path': path,
                'enabled': True,
                'created_at': datetime.now().isoformat(),
            }
            cam_ids.append(cam_id)
            registered += 1
            time.sleep(0.001)  # garante IDs únicos

    if registered:
        save_config(config)
        logger.info(f"Agente cadastrou {registered} câmera(s)")

    return jsonify({'registered': registered, 'found': len(cameras), 'cam_ids': cam_ids})


SNAPSHOTS_DIR = os.path.join(_APP_DIR, 'snapshots')
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

@app.route('/api/agent/push_snapshot/<cam_id>', methods=['POST'])
def agent_push_snapshot(cam_id):
    """Recebe snapshot (JPEG bytes) enviado pelo agente local e salva em disco."""
    # Aceita token (multi-worker safe) ou sessão normal
    token = request.args.get('token') or request.headers.get('X-Agent-Token', '')
    config = load_config()
    expected = hashlib.sha256(f"dvr-clear:{config.get('password','')}".encode()).hexdigest()
    if token != expected and not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    data = request.get_data()
    if not data:
        return jsonify({'success': False, 'error': 'Sem dados'}), 400
    _snapshot_cache[cam_id] = data  # cache em memória (mesmo worker)
    # Salva em disco para compartilhar entre workers Passenger
    snap_file = os.path.join(SNAPSHOTS_DIR, f'{cam_id}.jpg')
    try:
        with open(snap_file, 'wb') as f:
            f.write(data)
    except Exception:
        pass
    return jsonify({'success': True})


@app.route('/api/camera/<cam_id>/snapshot_img')
@login_required
def snapshot_img(cam_id):
    """Serve o snapshot mais recente (do disco ou cache em memória)."""
    # 1º tenta arquivo em disco (compartilhado entre workers)
    snap_file = os.path.join(SNAPSHOTS_DIR, f'{cam_id}.jpg')
    if os.path.exists(snap_file):
        return send_file(snap_file, mimetype='image/jpeg',
                         max_age=0, conditional=False)
    # 2º tenta cache em memória (mesmo worker)
    cached = _snapshot_cache.get(cam_id)
    if cached:
        return Response(cached, mimetype='image/jpeg',
                        headers={'Cache-Control': 'no-store'})
    # Fallback: tenta buscar diretamente na câmera
    config = load_config()
    cam = config.get('cameras', {}).get(cam_id)
    if not cam:
        return ('Câmera não encontrada', 404)
    _s = 'https' if str(cam.get('port', 80)) == '443' else 'http'
    url = cam.get('snapshot_url') or f"{_s}://{cam['ip']}:{cam['port']}{cam.get('path','/snapshot.cgi')}"
    try:
        r = _camera_fetch(url, cam.get('user',''), cam.get('password',''), timeout=5)
        if r is not None and len(r.content) > 500:
            return Response(r.content, mimetype='image/jpeg',
                            headers={'Cache-Control': 'no-store'})
    except Exception:
        pass
    return ('Snapshot indisponível', 503)


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

    # Se a câmera está acessível via proxy RTSP local (porta 8191/8192),
    # usa o snapshot do proxy em vez da URL remota (que seria o tunnel Cloudflare)
    _proxy_ports = {8191: 8191, 8192: 8192}
    if port in _proxy_ports:
        url  = f"http://127.0.0.1:{port}/snapshot.jpg"
        auth = None
    else:
        scheme = 'https' if port == 443 else 'http'
        url    = f"{scheme}://{ip}:{port}{path}"
        auth   = (user, password) if user else None

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
                enforce_recordings_limits(reason='motion_snapshot')

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
            time.sleep(0.15)  # ~6-7 fps para detecção

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
        out = cv2.VideoWriter(video_path, cv2.VideoWriter_fourcc(*'MJPG'), 6, (w, h))
        for frame_data in frames:
            img = cv2.imdecode(np.frombuffer(frame_data, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                out.write(img)
        out.release()
        logger.info(f"Vídeo salvo: {video_path}")
        enforce_recordings_limits(reason='video_burst')
    except ImportError:
        # cv2 não disponível: salvar frames individuais
        for i, frame_data in enumerate(frames):
            frame_path = os.path.join(cam_dir, f'video_{ts_str}_f{i:04d}.jpg')
            with open(frame_path, 'wb') as f:
                f.write(frame_data)
        logger.info(f"Frames de vídeo salvos em {cam_dir} (cv2 indisponível)")
        enforce_recordings_limits(reason='video_frames_fallback')

def _is_motion_active(cam_id: str) -> bool:
    t = _motion_threads.get(cam_id)
    return bool(t and t.is_alive())

def _start_motion_for_cam(cam_id: str, config: dict | None = None):
    if config is None:
        config = load_config()
    if cam_id not in config.get('cameras', {}):
        return False, 'Câmera não encontrada'
    if _is_motion_active(cam_id):
        return False, 'Já ativa'
    stop_ev = threading.Event()
    _motion_stop[cam_id] = stop_ev
    t = threading.Thread(target=_motion_worker, args=(cam_id, stop_ev), daemon=True)
    _motion_threads[cam_id] = t
    t.start()
    return True, 'Iniciada'

def _stop_motion_for_cam(cam_id: str):
    if cam_id in _motion_stop:
        _motion_stop[cam_id].set()
    return True

# ── Rotas de gravação/detecção ─────────────────────────────────

@app.route('/api/camera/<cam_id>/motion/start', methods=['POST'])
@login_required
def start_motion(cam_id):
    """Inicia detecção de movimento para a câmera"""
    ok, msg = _start_motion_for_cam(cam_id)
    if not ok and msg == 'Câmera não encontrada':
        return jsonify({'success': False, 'error': msg}), 404
    if not ok:
        return jsonify({'success': False, 'error': msg})
    return jsonify({'success': True})

@app.route('/api/camera/<cam_id>/motion/stop', methods=['POST'])
@login_required
def stop_motion(cam_id):
    """Para detecção de movimento"""
    _stop_motion_for_cam(cam_id)
    return jsonify({'success': True})

@app.route('/api/camera/<cam_id>/motion/status')
@login_required
def motion_status(cam_id):
    status = _motion_status.get(cam_id, {'active': False, 'last_motion': None, 'count': 0})
    if cam_id in _motion_threads:
        status['active'] = _motion_threads[cam_id].is_alive()
    return jsonify(status)

@app.route('/api/motion/start-all', methods=['POST'])
@login_required
def start_motion_all():
    """Inicia detecção para todas as câmeras ativas (enabled=true)."""
    config = load_config()
    cameras = config.get('cameras', {})
    active_cam_ids = [cid for cid, cam in cameras.items() if cam.get('enabled', True)]

    started, already, failed = [], [], []
    for cam_id in active_cam_ids:
        ok, msg = _start_motion_for_cam(cam_id, config=config)
        if ok:
            started.append(cam_id)
        elif msg == 'Já ativa':
            already.append(cam_id)
        else:
            failed.append({'cam_id': cam_id, 'error': msg})

    return jsonify({
        'success': True,
        'enabled': len(active_cam_ids),
        'started': started,
        'already': already,
        'failed': failed,
    })

@app.route('/api/motion/stop-all', methods=['POST'])
@login_required
def stop_motion_all():
    """Para detecção para todas as câmeras com thread ativa."""
    active_ids = [cid for cid in _motion_threads.keys() if _is_motion_active(cid)]
    for cam_id in active_ids:
        _stop_motion_for_cam(cam_id)
    return jsonify({'success': True, 'stopped': active_ids})

@app.route('/api/motion/summary')
@login_required
def motion_summary():
    """Resumo global de motion para o botão do dashboard."""
    config = load_config()
    cameras = config.get('cameras', {})
    enabled_ids = [cid for cid, cam in cameras.items() if cam.get('enabled', True)]
    active_ids = [cid for cid in enabled_ids if _is_motion_active(cid)]
    return jsonify({
        'enabled_count': len(enabled_ids),
        'active_count': len(active_ids),
        'all_active': (len(enabled_ids) > 0 and len(active_ids) == len(enabled_ids)),
        'active_ids': active_ids,
    })

@app.route('/api/camera/<cam_id>/snapshot', methods=['POST'])
@login_required
def take_snapshot(cam_id):
    """Captura e salva um snapshot manual da câmera"""
    config = load_config()
    cam = config.get('cameras', {}).get(cam_id)
    if not cam:
        return jsonify({'success': False, 'error': 'Câmera não encontrada'}), 404
    url  = f"http://{cam['ip']}:{cam['port']}{cam['path']}"
    try:
        r = _camera_fetch(url, cam.get('user',''), cam.get('password',''), timeout=5)
        if r is not None and len(r.content) > 1000:
            cam_dir = os.path.join(RECORDINGS_DIR, cam_id)
            os.makedirs(cam_dir, exist_ok=True)
            ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')
            path = os.path.join(cam_dir, f'snap_{ts_str}.jpg')
            with open(path, 'wb') as f:
                f.write(r.content)
            enforce_recordings_limits(reason='manual_snapshot')
            return jsonify({'success': True, 'file': f'snap_{ts_str}.jpg'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': False, 'error': 'Sem resposta da câmera'}), 502

def _parse_recording(fname):
    """Extrai datetime de nomes como motion_20260420_142055.mp4 ou snap_20260420_142055.jpg"""
    import re as _re
    m = _re.search(r'(\d{8})_(\d{6})', fname)
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S')
        except Exception:
            pass
    return None


@app.route('/recordings')
@login_required
def recordings_page():
    """Página de gravações e snapshots — linha do tempo"""
    config = load_config()
    tunnel_url = config.get('recordings_tunnel_url', '').strip()
    if tunnel_url:
        return redirect(tunnel_url)
    cameras = config.get('cameras', {})
    files_by_cam = {}
    for cam_id, cam_info in cameras.items():
        cam_dir = os.path.join(RECORDINGS_DIR, cam_id)
        if not os.path.isdir(cam_dir):
            continue
        raw = sorted(os.listdir(cam_dir), reverse=True)[:200]
        by_date = {}
        for fname in raw:
            fp = os.path.join(cam_dir, fname)
            dt = _parse_recording(fname)
            date_str = dt.strftime('%d/%m/%Y') if dt else 'Data desconhecida'
            hour     = f'{dt.hour:02d}' if dt else '??'
            label    = dt.strftime('%H:%M:%S') if dt else fname
            size_b   = os.path.getsize(fp) if os.path.isfile(fp) else 0
            size_s   = f'{size_b/1024/1024:.1f} MB' if size_b > 1024*1024 else f'{size_b//1024} KB'
            is_video = fname.lower().endswith(('.mp4', '.avi'))
            item = {'fname': fname, 'label': label, 'size': size_s, 'is_video': is_video}
            by_date.setdefault(date_str, {}).setdefault(hour, []).append(item)
        # ordenar horas desc dentro de cada data
        for d in by_date:
            by_date[d] = dict(sorted(by_date[d].items(), reverse=True))
        files_by_cam[cam_id] = {
            'name': cam_info['name'],
            'files': raw,
            'by_date': dict(sorted(by_date.items(),
                                   key=lambda x: x[0][::-1], reverse=True)),
        }
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
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Linha do Tempo – DVR</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',Arial,sans-serif;background:#0f1923;color:#e0e0e0;min-height:100vh}
a{color:inherit;text-decoration:none}

/* HEADER */
.topbar{background:#1a2533;padding:14px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;border-bottom:2px solid #263548}
.topbar h1{font-size:1.1em;color:#fff}
.topbar-nav{display:flex;gap:8px}
.btn{padding:7px 16px;border-radius:6px;font-size:.85em;font-weight:600;cursor:pointer;border:none;display:inline-block;transition:opacity .2s}
.btn:hover{opacity:.85}
.btn-live{background:#2196F3;color:#fff}
.btn-logout{background:#f44336;color:#fff}

/* LAYOUT */
.wrap{max-width:1280px;margin:0 auto;padding:24px 16px}

/* CAMERA TABS */
.cam-tabs{display:flex;gap:8px;margin-bottom:24px;flex-wrap:wrap}
.cam-tab{padding:8px 20px;border-radius:20px;background:#1a2533;border:2px solid #263548;cursor:pointer;font-size:.9em;transition:all .2s}
.cam-tab.active{background:#2196F3;border-color:#2196F3;color:#fff}

/* DATE GROUP */
.date-group{margin-bottom:32px}
.date-label{font-size:.8em;font-weight:700;color:#90a4ae;text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px;padding-left:4px}

/* TIMELINE */
.timeline{position:relative;padding-left:56px}
.timeline::before{content:'';position:absolute;left:20px;top:0;bottom:0;width:2px;background:#263548}

/* HOUR BLOCK */
.hour-block{margin-bottom:18px}
.hour-pin{position:absolute;left:8px;width:26px;height:26px;border-radius:50%;background:#263548;border:2px solid #37474f;display:flex;align-items:center;justify-content:center;font-size:.65em;color:#90a4ae;font-weight:700;margin-top:3px}
.hour-label{font-size:.75em;color:#546e7a;margin-bottom:8px;padding-left:4px}
.clips-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(165px,1fr));gap:10px}

/* CLIP CARD */
.clip{width:100%;border-radius:8px;overflow:hidden;background:#1a2533;border:1px solid #263548;cursor:pointer;transition:transform .15s,border-color .15s}
.clip:hover{transform:translateY(-3px);border-color:#2196F3}
.clip-thumb{width:100%;aspect-ratio:16/9;background:#0d1520;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden}
.clip-thumb img{width:100%;height:100%;object-fit:cover}
.clip-play{position:absolute;width:44px;height:44px;border-radius:50%;background:rgba(33,150,243,.85);display:flex;align-items:center;justify-content:center;font-size:1.2em}
.clip-info{padding:8px 10px}
.clip-time{font-size:.8em;font-weight:600;color:#e0e0e0}
.clip-size{font-size:.7em;color:#546e7a;margin-top:2px}
.clip-dl{float:right;font-size:.7em;padding:3px 8px;border-radius:4px;background:#263548;color:#90a4ae;margin-top:-2px}
.clip-dl:hover{background:#2196F3;color:#fff}

/* SNAP CARD */
.snap{width:100%;border-radius:8px;overflow:hidden;background:#1a2533;border:1px solid #263548;cursor:pointer;transition:transform .15s,border-color .15s}
.snap:hover{transform:translateY(-3px);border-color:#4caf50}
.snap-thumb{width:100%;aspect-ratio:5/3;object-fit:cover;display:block}
.snap-info{padding:6px 8px}
.snap-time{font-size:.75em;color:#90a4ae}

/* MODAL */
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:9999;align-items:center;justify-content:center;flex-direction:column;gap:12px}
.modal.open{display:flex}
.modal video,.modal img{max-width:94vw;max-height:82vh;border-radius:8px;outline:none}
.modal-close{position:fixed;top:16px;right:24px;font-size:2em;color:#fff;cursor:pointer;line-height:1}
.modal-title{color:#ccc;font-size:.85em;max-width:94vw;text-align:center}
.modal-dl{margin-top:4px;padding:7px 18px;border-radius:6px;background:#2196F3;color:#fff;font-size:.85em;font-weight:600}

.empty{text-align:center;color:#546e7a;padding:48px;font-size:.95em}
.cam-panel{display:none}.cam-panel.active{display:block}

@media (max-width: 900px){
    .timeline{padding-left:30px}
    .timeline::before{left:10px}
    .hour-pin{left:-2px;width:22px;height:22px;font-size:.6em}
    .clips-row{grid-template-columns:repeat(auto-fill,minmax(145px,1fr));gap:8px}
}
@media (max-width: 600px){
    .topbar{padding:10px 12px}
    .wrap{padding:12px 8px}
    .topbar-nav{width:100%;display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px}
    .btn{width:100%;text-align:center;padding:8px 6px;font-size:.78em}
    .clips-row{grid-template-columns:1fr 1fr}
    .modal video,.modal img{max-width:98vw;max-height:88vh}
}

/* scrollbar */
::-webkit-scrollbar{width:6px}::-webkit-scrollbar-track{background:#0f1923}::-webkit-scrollbar-thumb{background:#263548;border-radius:3px}
</style>
</head>
<body>

<div class="topbar">
  <h1>&#x23F1;&#xFE0F; Linha do Tempo de Gravações</h1>
  <div class="topbar-nav">
    <a href="/" class="btn btn-live">&#x1F4F9; Ao Vivo</a>
    <a href="/cameras" class="btn btn-live" style="background:#455a64">&#x2699;&#xFE0F; Config</a>
    <a href="/logout" class="btn btn-logout">Sair</a>
  </div>
</div>

<div class="wrap">

{% if files_by_cam %}
  <!-- TABS -->
  <div class="cam-tabs">
    {% for cam_id, data in files_by_cam.items() %}
    <div class="cam-tab {% if loop.first %}active{% endif %}"
         onclick="switchCam('{{ cam_id }}')">
      &#x1F4F7; {{ data.name }}
      <span style="opacity:.6;font-size:.8em">({{ data.files|length }})</span>
    </div>
    {% endfor %}
  </div>

  <!-- PANELS -->
  {% for cam_id, data in files_by_cam.items() %}
  <div class="cam-panel {% if loop.first %}active{% endif %}" id="panel-{{ cam_id }}">
    {% if data.by_date %}
      {% for date_str, hours in data.by_date.items() %}
      <div class="date-group">
        <div class="date-label">&#x1F4C5; {{ date_str }}</div>
        <div class="timeline">
          {% for hour, items in hours.items() %}
          <div class="hour-block" style="position:relative">
            <div class="hour-pin">{{ hour }}h</div>
            <div style="padding-left:28px">
              <div class="clips-row">
                {% for item in items %}
                {% if item.is_video %}
                <div class="clip"
                     onclick="playVideo('/recordings/{{ cam_id }}/{{ item.fname }}','{{ item.label }}','/recordings/{{ cam_id }}/{{ item.fname }}')">
                  <div class="clip-thumb">
                    <div style="color:#546e7a;font-size:2em">&#x1F3AC;</div>
                    <div class="clip-play">&#x25B6;</div>
                  </div>
                  <div class="clip-info">
                    <div class="clip-time">{{ item.label }}</div>
                    <div class="clip-size">{{ item.size }}</div>
                    <a href="/recordings/{{ cam_id }}/{{ item.fname }}" download
                       onclick="event.stopPropagation()" class="clip-dl">&#x2B07;</a>
                  </div>
                </div>
                {% else %}
                <div class="snap"
                     onclick="showImg('/recordings/{{ cam_id }}/{{ item.fname }}','{{ item.label }}','/recordings/{{ cam_id }}/{{ item.fname }}')">
                  <img class="snap-thumb"
                       src="/recordings/{{ cam_id }}/{{ item.fname }}" loading="lazy"
                       alt="{{ item.fname }}">
                  <div class="snap-info">
                    <div class="snap-time">{{ item.label }}</div>
                  </div>
                </div>
                {% endif %}
                {% endfor %}
              </div>
            </div>
          </div>
          {% endfor %}
        </div>
      </div>
      {% endfor %}
    {% else %}
      <p class="empty">Nenhuma gravação ainda para esta câmera.</p>
    {% endif %}
  </div>
  {% endfor %}

{% else %}
  <p class="empty">Nenhuma gravação encontrada. Ative a detecção de movimento na tela de configuração.</p>
{% endif %}

</div><!-- /wrap -->

<!-- MODAL VIDEO/IMAGEM -->
<div class="modal" id="modal" onclick="closeModal(event)">
  <span class="modal-close" onclick="closeModal()">&#x2715;</span>
  <video id="modal-video" controls style="display:none"></video>
  <img  id="modal-img"   style="display:none" alt="">
  <div class="modal-title" id="modal-title"></div>
  <a   id="modal-dl" href="#" download class="modal-dl">&#x2B07; Baixar</a>
</div>

<script>
function switchCam(id){
  document.querySelectorAll('.cam-tab').forEach((t,i)=>{
    const panels=document.querySelectorAll('.cam-panel');
    if(t.getAttribute('onclick').includes(id)){t.classList.add('active');panels[i].classList.add('active');}
    else{t.classList.remove('active');panels[i].classList.remove('active');}
  });
}

function openModal(title,dlUrl){
  document.getElementById('modal').classList.add('open');
  document.getElementById('modal-title').textContent=title;
  document.getElementById('modal-dl').href=dlUrl;
}
function closeModal(e){
  if(e&&e.target!==document.getElementById('modal')&&!e.target.classList.contains('modal-close'))return;
  const m=document.getElementById('modal');
  m.classList.remove('open');
  const v=document.getElementById('modal-video');
  v.pause();v.src='';v.style.display='none';
  document.getElementById('modal-img').style.display='none';
}
function playVideo(src,title,dl){
  const v=document.getElementById('modal-video');
  document.getElementById('modal-img').style.display='none';
  v.style.display='block';v.src=src;
  openModal(title,dl);
  v.play();
}
function showImg(src,title,dl){
  const i=document.getElementById('modal-img');
  document.getElementById('modal-video').style.display='none';
  i.style.display='block';i.src=src;
  openModal(title,dl);
}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal({target:document.getElementById('modal')});});
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
        .btn-record { background: #c0392b; color: white; }
        .btn-record.active { background: #27ae60; }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 4px 8px rgba(0,0,0,0.3); }
        .cameras {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
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
            height: auto;
            display: block;
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
            padding: 10px;
            overflow: hidden;
        }
        .fullscreen-content img {
            width: 100%;
            height: 100%;
            object-fit: contain;
            transition: transform 0.15s ease;
            user-select: none;
            -webkit-user-drag: none;
            cursor: zoom-in;
        }
        .zoom-controls { display: flex; align-items: center; gap: 6px; }
        .zoom-btn {
            background: rgba(255,255,255,0.2);
            border: none;
            color: white;
            width: 34px;
            height: 34px;
            border-radius: 6px;
            font-size: 1.1em;
            cursor: pointer;
            font-weight: bold;
            transition: background 0.2s;
        }
        .zoom-btn:hover { background: rgba(255,255,255,0.4); }
        .zoom-btn-reset { width: auto; padding: 0 10px; font-size: 0.85em; }
        .zoom-label { font-size: 0.85em; min-width: 44px; text-align: center; opacity: 0.9; }
        @media (max-width: 768px) {
            .cameras { grid-template-columns: 1fr; gap: 14px; margin: 16px auto; padding: 0 10px; }
            .header { flex-direction: column; gap: 15px; text-align: center; }
            .header-buttons { justify-content: center; flex-wrap: wrap; }
            .btn { width: 100%; max-width: 320px; text-align: center; }
            .camera-box { padding: 12px; }
            .fullscreen-header { padding: 10px 12px; }
            .fullscreen-title { font-size: 1.05em; }
            .fullscreen-close { padding: 8px 14px; font-size: .9em; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🎥 DVR Local</h1>
        <div class="header-buttons">
            <button id="btn-motion-all" class="btn btn-record" onclick="toggleMotionAll()">⏺️ Gravar todas</button>
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
            <div style="display:flex;align-items:center;gap:10px;">
                <div class="zoom-controls">
                    <button class="zoom-btn" onclick="zoomOut()" title="Diminuir zoom">−</button>
                    <span class="zoom-label" id="zoom-label">100%</span>
                    <button class="zoom-btn" onclick="zoomIn()" title="Aumentar zoom">+</button>
                    <button class="zoom-btn zoom-btn-reset" onclick="resetZoom()" title="Resetar zoom">↺ Reset</button>
                </div>
                <button class="fullscreen-close" onclick="closeFullscreen()">✕ Fechar</button>
            </div>
        </div>
        <div class="fullscreen-content">
            <img id="fullscreen-img" src="" alt="Camera">
        </div>
    </div>
    <script>
        let currentCam = null;
        let motionAllActive = false;

        function getEnabledCamIds() {
            return Array.from(document.querySelectorAll('.camera-box[data-cam-id]'))
                .map(el => el.getAttribute('data-cam-id'))
                .filter(Boolean);
        }

        async function parseJsonSafe(response) {
            const txt = await response.text();
            try {
                return { ok: true, data: JSON.parse(txt), raw: txt };
            } catch (_) {
                return { ok: false, data: null, raw: txt };
            }
        }

        async function startOrStopEachCam(start) {
            const camIds = getEnabledCamIds();
            if (!camIds.length) return { success: false, error: 'Nenhuma câmera ativa na tela' };
            let okCount = 0;
            for (const camId of camIds) {
                const endpoint = start
                    ? `/api/camera/${camId}/motion/start`
                    : `/api/camera/${camId}/motion/stop`;
                try {
                    const r = await fetch(endpoint, { method: 'POST' });
                    if (!r.ok) continue;
                    okCount += 1;
                } catch (_) {}
            }
            return { success: okCount > 0, count: okCount, total: camIds.length };
        }

        function updateMotionButton(summary) {
            const btn = document.getElementById('btn-motion-all');
            if (!btn) return;
            if (summary.enabled_count === 0) {
                btn.disabled = true;
                btn.classList.remove('active');
                btn.textContent = '⏺️ Sem câmeras ativas';
                return;
            }
            btn.disabled = false;
            motionAllActive = !!summary.all_active;
            if (motionAllActive) {
                btn.classList.add('active');
                btn.textContent = '⏹️ Parar gravação (' + summary.active_count + '/' + summary.enabled_count + ')';
            } else {
                btn.classList.remove('active');
                btn.textContent = '⏺️ Gravar todas (' + summary.enabled_count + ')';
            }
        }

        async function refreshMotionAllState() {
            try {
                const r = await fetch('/api/motion/summary');
                const parsed = await parseJsonSafe(r);
                const d = parsed.data;
                if (parsed.ok && d && typeof d.enabled_count !== 'undefined') {
                    updateMotionButton(d);
                    return;
                }
                // Fallback quando rota global ainda não existe no servidor remoto
                const enabled = getEnabledCamIds().length;
                updateMotionButton({ enabled_count: enabled, active_count: 0, all_active: false });
            } catch (e) {
                console.warn('Falha ao atualizar estado de gravação global', e);
            }
        }

        async function toggleMotionAll() {
            const btn = document.getElementById('btn-motion-all');
            btn.disabled = true;
            try {
                const endpoint = motionAllActive ? '/api/motion/stop-all' : '/api/motion/start-all';
                const r = await fetch(endpoint, { method: 'POST' });
                const parsed = await parseJsonSafe(r);
                const d = parsed.data;

                if (parsed.ok && d && d.success) {
                    await refreshMotionAllState();
                    return;
                }

                // Fallback: usa APIs por câmera quando /api/motion/start-all não estiver disponível
                const fallback = await startOrStopEachCam(!motionAllActive);
                if (!fallback.success) {
                    if (!parsed.ok && parsed.raw && parsed.raw.includes('/login')) {
                        throw new Error('Sessão expirada. Faça login novamente.');
                    }
                    throw new Error((d && d.error) || fallback.error || 'Falha ao alterar gravação');
                }
                await refreshMotionAllState();
            } catch (e) {
                alert('Erro: ' + e.message);
            } finally {
                btn.disabled = false;
            }
        }

        // === ZOOM ===
        let _zoomLevel = 1, _panX = 0, _panY = 0;
        let _isPanning = false, _panStartX = 0, _panStartY = 0;
        let _lastTouchDist = null;

        function _applyZoom() {
            const img = document.getElementById('fullscreen-img');
            const lbl = document.getElementById('zoom-label');
            img.style.transform = `translate(${_panX}px, ${_panY}px) scale(${_zoomLevel})`;
            img.style.transformOrigin = '50% 50%';
            img.style.cursor = _zoomLevel > 1 ? 'grab' : 'zoom-in';
            if (lbl) lbl.textContent = Math.round(_zoomLevel * 100) + '%';
        }
        function zoomIn()  { _zoomLevel = Math.min(_zoomLevel + 0.25, 5); _applyZoom(); }
        function zoomOut() {
            _zoomLevel = Math.max(_zoomLevel - 0.25, 1);
            if (_zoomLevel === 1) { _panX = 0; _panY = 0; }
            _applyZoom();
        }
        function resetZoom() { _zoomLevel = 1; _panX = 0; _panY = 0; _applyZoom(); }

        // Zoom pela roda do mouse (centrado no cursor)
        document.getElementById('fullscreen').addEventListener('wheel', function(e) {
            if (!this.classList.contains('active')) return;
            e.preventDefault();
            const delta = e.deltaY < 0 ? 0.15 : -0.15;
            const oldZoom = _zoomLevel;
            _zoomLevel = Math.max(1, Math.min(5, _zoomLevel + delta));
            if (_zoomLevel === 1) { _panX = 0; _panY = 0; _applyZoom(); return; }
            // Ajusta o pan para manter o ponto sob o cursor
            const content = document.querySelector('.fullscreen-content');
            const rect = content.getBoundingClientRect();
            const mx = e.clientX - rect.left - rect.width / 2;
            const my = e.clientY - rect.top - rect.height / 2;
            const ratio = _zoomLevel / oldZoom;
            _panX = mx * (1 - ratio) + _panX * ratio;
            _panY = my * (1 - ratio) + _panY * ratio;
            _applyZoom();
        }, { passive: false });

        // Drag para navegar quando com zoom
        const _fsImg = document.getElementById('fullscreen-img');
        _fsImg.addEventListener('mousedown', function(e) {
            if (_zoomLevel <= 1) return;
            _isPanning = true;
            _panStartX = e.clientX - _panX;
            _panStartY = e.clientY - _panY;
            this.style.cursor = 'grabbing';
            e.preventDefault();
        });
        document.addEventListener('mousemove', function(e) {
            if (!_isPanning) return;
            _panX = e.clientX - _panStartX;
            _panY = e.clientY - _panStartY;
            _applyZoom();
        });
        document.addEventListener('mouseup', function() {
            if (_isPanning) {
                _isPanning = false;
                const img = document.getElementById('fullscreen-img');
                if (img) img.style.cursor = _zoomLevel > 1 ? 'grab' : 'zoom-in';
            }
        });

        // Pinch-to-zoom (touch)
        const _fsEl = document.getElementById('fullscreen');
        _fsEl.addEventListener('touchstart', function(e) {
            if (e.touches.length === 2) {
                const dx = e.touches[0].clientX - e.touches[1].clientX;
                const dy = e.touches[0].clientY - e.touches[1].clientY;
                _lastTouchDist = Math.sqrt(dx*dx + dy*dy);
            }
        }, { passive: true });
        _fsEl.addEventListener('touchmove', function(e) {
            if (e.touches.length === 2 && _lastTouchDist) {
                e.preventDefault();
                const dx = e.touches[0].clientX - e.touches[1].clientX;
                const dy = e.touches[0].clientY - e.touches[1].clientY;
                const dist = Math.sqrt(dx*dx + dy*dy);
                const ratio = dist / _lastTouchDist;
                _lastTouchDist = dist;
                _zoomLevel = Math.max(1, Math.min(5, _zoomLevel * ratio));
                if (_zoomLevel === 1) { _panX = 0; _panY = 0; }
                _applyZoom();
            }
        }, { passive: false });
        _fsEl.addEventListener('touchend', function() { _lastTouchDist = null; }, { passive: true });
        // === FIM ZOOM ===

        function openFullscreen(camId) {
            currentCam = camId;
            resetZoom();
            document.getElementById('fullscreen-img').src = '/camera/' + camId;
            document.getElementById('fullscreen').classList.add('active');
        }
        function closeFullscreen() {
            document.getElementById('fullscreen').classList.remove('active');
            resetZoom();
            currentCam = null;
        }
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape') closeFullscreen();
            if (e.key === '+' || e.key === '=') zoomIn();
            if (e.key === '-') zoomOut();
            if (e.key === '0') resetZoom();
        });
        setInterval(() => {
            document.querySelectorAll('.camera-box img').forEach(img => {
                img.src = img.src.split('?')[0] + '?t=' + Date.now();
            });
        }, 30000);
        refreshMotionAllState();
        setInterval(refreshMotionAllState, 12000);
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
        @media (max-width: 768px) {
            body { padding: 10px; }
            .header { flex-direction: column; gap: 12px; text-align: center; }
            .camera-item { flex-direction: column; align-items: flex-start; gap: 10px; }
            .camera-actions { width: 100%; display: grid; grid-template-columns: 1fr 1fr; }
            .camera-actions .btn { width: 100%; text-align: center; }
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
            <h2>💾 Armazenamento e Gravação Cíclica</h2>
            <p style="opacity:.85; margin:10px 0 18px 0;">
                Quando ativado, o sistema remove automaticamente os arquivos mais antigos
                ao atingir o limite definido ou quando o espaço livre ficar abaixo da reserva.
            </p>
            <div class="form-group" style="display:flex; align-items:center; gap:10px;">
                <input type="checkbox" id="stCyclic" style="width:auto; transform:scale(1.25);"
                       {% if storage.cyclic_enabled %}checked{% endif %}>
                <label for="stCyclic" style="margin:0;">Ativar gravação cíclica automática</label>
            </div>
            <div class="form-group">
                <label>Limite total para pasta de gravações (GB)</label>
                <input type="number" id="stMaxGb" min="0.5" max="5000" step="0.5" value="{{ storage.max_gb }}">
            </div>
            <div class="form-group">
                <label>Reserva mínima de espaço livre no disco (GB)</label>
                <input type="number" id="stReserveGb" min="0" max="1000" step="0.5" value="{{ storage.reserve_free_gb }}">
            </div>
            <button onclick="saveStorageSettings()" class="btn btn-primary">💾 Salvar Política de Armazenamento</button>
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

        async function parseJsonSafe(response) {
            const txt = await response.text();
            try {
                return { ok: true, data: JSON.parse(txt), raw: txt };
            } catch (_) {
                return { ok: false, data: null, raw: txt };
            }
        }

        function ensureJsonResponse(parsed) {
            if (parsed.ok) return parsed.data;
            if (parsed.raw && parsed.raw.includes('/login')) {
                throw new Error('Sessão expirada. Faça login novamente.');
            }
            throw new Error('Resposta inválida do servidor. Atualize a página (Ctrl+F5).');
        }
        
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
                const parsed = await parseJsonSafe(response);
                const result = ensureJsonResponse(parsed);
                
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
                const parsed = await parseJsonSafe(response);
                const result = ensureJsonResponse(parsed);
                
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
                const parsed = await parseJsonSafe(r);
                const d = ensureJsonResponse(parsed);
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
                const parsed = await parseJsonSafe(r);
                const d = ensureJsonResponse(parsed);
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
                .then(parseJsonSafe)
                .then(ensureJsonResponse)
                .then(d => {
                    _motionActive[camId] = d.active;
                    if (d.active) {
                        btn.textContent = `⏹️ Parar (${d.count} eventos)`;
                        btn.style.background = '#c0392b';
                    }
                }).catch(()=>{});
        });

        async function saveStorageSettings() {
            const cyclic_enabled = document.getElementById('stCyclic').checked;
            const max_gb = parseFloat(document.getElementById('stMaxGb').value || '0');
            const reserve_free_gb = parseFloat(document.getElementById('stReserveGb').value || '0');

            if (isNaN(max_gb) || max_gb < 0.5) {
                return alert('Defina um limite válido (mínimo 0.5 GB).');
            }
            if (isNaN(reserve_free_gb) || reserve_free_gb < 0) {
                return alert('Defina uma reserva livre válida (mínimo 0 GB).');
            }

            try {
                const res = await fetch('/api/storage/settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ cyclic_enabled, max_gb, reserve_free_gb })
                });
                const parsed = await parseJsonSafe(res);
                const data = ensureJsonResponse(parsed);
                if (!data.success) {
                    return alert('Erro: ' + (data.error || 'falha ao salvar'));
                }
                const mb = ((data.sweep && data.sweep.freed_bytes) ? data.sweep.freed_bytes : 0) / 1024 / 1024;
                alert('✓ Política de armazenamento salva.' + (mb > 0 ? `\nLimpeza aplicada: ${mb.toFixed(1)} MB liberados.` : ''));
            } catch (e) {
                alert('Erro ao salvar política de armazenamento: ' + e.message);
            }
        }

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
                const parsed = await parseJsonSafe(res);
                const data = ensureJsonResponse(parsed);
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
        body { font-family: 'Segoe UI', Arial; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; color: white; padding: 20px; }
        .container { max-width: 1000px; margin: 0 auto; }
        .header { background: rgba(0,0,0,0.3); padding: 20px; border-radius: 12px; margin-bottom: 30px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
        .nav { display: flex; gap: 8px; flex-wrap: wrap; }
        .btn { padding: 10px 18px; border-radius: 6px; text-decoration: none; font-weight: bold; border: none; cursor: pointer; transition: all 0.2s; display: inline-block; font-size: 0.9em; }
        .btn-primary { background: #4CAF50; color: white; }
        .btn-secondary { background: #2196F3; color: white; }
        .btn:hover { transform: translateY(-2px); }
        .card { background: rgba(255,255,255,0.15); padding: 25px; border-radius: 12px; margin-bottom: 20px; }
        .agent-box { display: flex; align-items: center; gap: 12px; background: rgba(0,0,0,0.3); border-radius: 8px; padding: 14px 18px; margin-bottom: 12px; }
        .dot { width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }
        .dot-green { background: #2ecc71; box-shadow: 0 0 8px #2ecc71; }
        .dot-gray  { background: #7f8c8d; }
        .result-item { background: rgba(0,0,0,0.3); padding: 12px 15px; margin-bottom: 8px; border-radius: 8px; font-size: 0.9em; }
        .divider { border: none; border-top: 1px solid rgba(255,255,255,0.2); margin: 20px 0; }
        #results { margin-top: 16px; text-align: left; }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>🔍 Scanner de Rede</h1>
        <div class="nav">
            <a href="/" class="btn btn-secondary">🏠 Câmeras</a>
            <a href="/cameras" class="btn btn-secondary">⚙️ Config</a>
            <a href="/recordings" class="btn" style="background:#e67e22;">🎞️ Gravações</a>
            <a href="/logout" class="btn" style="background:#e74c3c;">🚪 Sair</a>
        </div>
    </div>

    <!-- AGENTE LOCAL -->
    <div class="card">
        <h2>🤖 Agente Local <small style="font-size:0.6em; opacity:0.7;">(recomendado)</small></h2>
        <p style="margin: 12px 0; opacity: 0.85;">
            O agente roda na mesma rede das câmeras e escaneia localmente.<br>
            <strong>Como usar:</strong> edite <code>DVR_PASSWORD</code> em <code>agent.py</code> e rode:
            <code style="display:block; background:rgba(0,0,0,0.3); padding:8px 12px; border-radius:6px; margin-top:8px;">python agent.py</code>
        </p>
        <div id="agents-list"><p style="opacity:0.6;">Verificando agentes...</p></div>
        <button onclick="triggerAgentScan()" class="btn btn-primary" id="agentBtn" disabled style="margin-top:12px;">
            📡 Iniciar Scan via Agente
        </button>
        <div id="agent-status" style="margin-top:12px; opacity:0.8;"></div>
    </div>

    <hr class="divider">

    <!-- SCAN SERVIDOR (rede do servidor) -->
    <div class="card">
        <h2>🌐 Scan no Servidor <small style="font-size:0.6em; opacity:0.7;">(rede do servidor)</small></h2>
        <p style="margin: 12px 0; opacity: 0.8;">Escaneia a rede onde o servidor está hospedado — útil apenas se câmeras estiverem na mesma rede do servidor.</p>
        <button onclick="startServerScan()" class="btn btn-secondary" id="serverBtn">🔍 Scan no Servidor</button>
        <div id="results"></div>
    </div>
</div>

<script>
let selectedAgent = null;

async function loadAgents() {
    try {
        const r = await fetch('/api/agent/list');
        const data = await r.json();
        const list = document.getElementById('agents-list');
        const btn  = document.getElementById('agentBtn');

        if (data.agents.length === 0) {
            list.innerHTML = '<div class="agent-box"><span class="dot dot-gray"></span><span>Nenhum agente conectado. Rode <code>python agent.py</code> na rede das câmeras.</span></div>';
            btn.disabled = true;
        } else {
            list.innerHTML = data.agents.map(a => `
                <div class="agent-box" onclick="selectAgent('${a.name}', this)" style="cursor:pointer;" id="agent-${a.name}">
                    <span class="dot dot-green"></span>
                    <span><strong>${a.name}</strong> — conectado há ${a.since}s</span>
                </div>`).join('');
            selectedAgent = data.agents[0].name;
            document.getElementById('agent-' + selectedAgent).style.border = '2px solid #2ecc71';
            btn.disabled = false;
        }
    } catch(e) {}
}

function selectAgent(name, el) {
    document.querySelectorAll('.agent-box').forEach(b => b.style.border = 'none');
    el.style.border = '2px solid #2ecc71';
    selectedAgent = name;
    document.getElementById('agentBtn').disabled = false;
}

async function triggerAgentScan() {
    if (!selectedAgent) return;
    const btn = document.getElementById('agentBtn');
    const status = document.getElementById('agent-status');
    btn.disabled = true;
    btn.textContent = '⏳ Aguardando agente...';
    status.textContent = '';

    try {
        const r = await fetch('/api/agent/trigger', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({agent: selectedAgent})
        });
        const d = await r.json();
        if (!d.success) {
            status.textContent = '✗ ' + d.error;
            btn.disabled = false;
            btn.textContent = '📡 Iniciar Scan via Agente';
            return;
        }
        status.innerHTML = '⏳ Scan em andamento na rede local... aguarde o agente concluir (1-3 min)';
        // Verificar a cada 5s se novas câmeras foram adicionadas
        let checks = 0;
        const interval = setInterval(async () => {
            checks++;
            try {
                const cr = await fetch('/cameras');
                // Não há endpoint JSON de câmeras, apenas verificamos via poll simples
                status.innerHTML = `⏳ Aguardando resultados... (${checks * 5}s) — <a href="/cameras" style="color:#fff;">ver câmeras cadastradas</a>`;
            } catch(e) {}
            if (checks >= 36) {  // 3 minutos
                clearInterval(interval);
                status.innerHTML = '✅ Verifique as <a href="/cameras" style="color:#fff;">câmeras cadastradas</a>.';
                btn.disabled = false;
                btn.textContent = '📡 Iniciar Scan via Agente';
            }
        }, 5000);
    } catch(e) {
        status.textContent = '✗ Erro: ' + e;
        btn.disabled = false;
        btn.textContent = '📡 Iniciar Scan via Agente';
    }
}

async function startServerScan() {
    const btn = document.getElementById('serverBtn');
    const results = document.getElementById('results');
    btn.disabled = true;
    btn.textContent = '⏳ Escaneando...';
    results.innerHTML = '<p style="margin-top:12px; opacity:0.7;">Buscando câmeras na rede do servidor...</p>';
    try {
        const r = await fetch('/api/scan', { method: 'POST' });
        const data = await r.json();
        results.innerHTML = `<p style="margin-top:12px;"><strong>${data.cameras.length}</strong> câmera(s) encontrada(s) na rede ${data.network}:</p>`;
        data.cameras.forEach(cam => {
            results.innerHTML += `<div class="result-item"><strong>${cam.ip}:${cam.port}</strong> — ${cam.url}</div>`;
        });
        if (!data.cameras.length)
            results.innerHTML += '<p style="opacity:0.6; margin-top:8px;">Nenhuma câmera encontrada na rede do servidor.</p>';
    } catch(e) {
        results.innerHTML = `<p style="color:#e74c3c; margin-top:12px;">✗ Erro: ${e}</p>`;
    } finally {
        btn.disabled = false;
        btn.textContent = '🔍 Scan no Servidor';
    }
}

// Atualizar lista de agentes a cada 5s
loadAgents();
setInterval(loadAgents, 5000);
</script>
</body>
</html>
"""

# ─── API JSON: lista câmeras ────────────────────────────────────────────────
@app.route('/api/cameras', methods=['GET'])
@login_required
def api_cameras_list():
    config = load_config()
    cameras = config.get('cameras', {})
    result = []
    for cam_id, c in cameras.items():
        result.append({
            'id': cam_id,
            'name': c.get('name', cam_id),
            'ip': c.get('ip', ''),
            'port': c.get('port', 80),
            'enabled': c.get('enabled', True),
            'snapshot_url': c.get('snapshot_url', ''),
            'stream_url': c.get('stream_url', ''),
        })
    return jsonify({'cameras': result})


# ─── PWA manifest ────────────────────────────────────────────────────────────
@app.route('/manifest.json')
def pwa_manifest():
    manifest = {
        "name": "DVR Local",
        "short_name": "DVR",
        "description": "Monitore câmeras e escaneie a rede local",
        "start_url": "/pwa",
        "display": "standalone",
        "background_color": "#1a1a2e",
        "theme_color": "#667eea",
        "icons": [
            {"src": "/pwa-icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/pwa-icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    }
    from flask import Response
    return Response(json.dumps(manifest), mimetype='application/json')


# ─── PWA Service Worker ───────────────────────────────────────────────────────
@app.route('/sw.js')
def pwa_sw():
    sw = """
const CACHE = 'dvr-pwa-v1';
const OFFLINE = ['/pwa'];
self.addEventListener('install', e => e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(OFFLINE))
));
self.addEventListener('fetch', e => {
    e.respondWith(
        fetch(e.request).catch(() => caches.match(e.request))
    );
});
"""
    from flask import Response
    return Response(sw, mimetype='application/javascript')


# ─── PWA ícones simplificados (SVG→PNG inline) ────────────────────────────────
@app.route('/pwa-icon-192.png')
@app.route('/pwa-icon-512.png')
def pwa_icon():
    # Ícone SVG simples gerado como PNG via data URI não é possível puramente em Flask,
    # então retornamos um redirect para um SVG que os browsers aceitam como ícone
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" rx="20" fill="#667eea"/>
  <text x="50" y="68" font-size="55" text-anchor="middle" fill="white">&#128247;</text>
</svg>'''
    from flask import Response
    return Response(svg, mimetype='image/svg+xml')


# ─── PWA principal ────────────────────────────────────────────────────────────
PWA_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#667eea">
<link rel="manifest" href="/manifest.json">
<title>DVR Local</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#1a1a2e;--card:#16213e;--accent:#667eea;--accent2:#764ba2;--text:#eee;--sub:#aaa;--green:#2ecc71;--red:#e74c3c;--orange:#e67e22}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',Arial;min-height:100vh;padding-bottom:70px}
.topbar{background:linear-gradient(135deg,var(--accent),var(--accent2));padding:14px 18px;display:flex;justify-content:space-between;align-items:center}
.topbar h1{font-size:1.2em;letter-spacing:1px}
.topbar a{color:#fff;font-size:0.8em;text-decoration:none;background:rgba(0,0,0,0.25);padding:5px 10px;border-radius:20px}
.tabs{display:flex;background:var(--card);border-bottom:1px solid rgba(255,255,255,0.08)}
.tab{flex:1;padding:13px 6px;text-align:center;font-size:0.85em;cursor:pointer;transition:all .2s;border-bottom:3px solid transparent;color:var(--sub)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.page{display:none;padding:16px}
.page.active{display:block}
.card{background:var(--card);border-radius:12px;padding:16px;margin-bottom:12px}
.cam-img{width:100%;border-radius:8px;aspect-ratio:16/9;object-fit:cover;background:#000;display:block}
.cam-name{font-weight:bold;margin:8px 0 4px}
.cam-sub{font-size:0.8em;color:var(--sub)}
.btn{padding:10px 18px;border-radius:8px;border:none;cursor:pointer;font-weight:bold;font-size:0.9em;transition:all .2s;display:inline-block}
.btn-primary{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff}
.btn-success{background:var(--green);color:#fff}
.btn-danger{background:var(--red);color:#fff}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn:active{transform:scale(0.97)}
.btn-block{width:100%;text-align:center;margin-bottom:8px}
.badge{display:inline-block;padding:3px 8px;border-radius:20px;font-size:0.75em;font-weight:bold}
.badge-green{background:rgba(46,204,113,.2);color:var(--green)}
.badge-red{background:rgba(231,76,60,.2);color:var(--red)}
.progress-bar{height:6px;background:rgba(255,255,255,0.1);border-radius:3px;margin:12px 0;overflow:hidden}
.progress-fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));border-radius:3px;transition:width .3s}
.result-item{padding:10px 12px;background:rgba(255,255,255,0.05);border-radius:8px;margin-bottom:6px;font-size:0.85em}
.result-item strong{color:var(--green)}
.log{font-size:0.75em;color:var(--sub);margin-top:6px;max-height:120px;overflow-y:auto;font-family:monospace}
.row{display:flex;gap:8px;align-items:center;margin-bottom:8px}
.row .lbl{font-size:0.85em;color:var(--sub);width:80px;flex-shrink:0}
.row input{flex:1;background:rgba(255,255,255,0.1);border:1px solid rgba(255,255,255,0.2);border-radius:6px;padding:8px;color:#fff;font-size:0.9em}
.empty{text-align:center;color:var(--sub);padding:40px 20px;font-size:0.9em}
.rec-thumb{width:100%;border-radius:8px;aspect-ratio:16/9;object-fit:cover;background:#111}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
@media(min-width:600px){.grid2{grid-template-columns:repeat(3,1fr)}}
.fullscreen-overlay{display:none;position:fixed;inset:0;background:#000;z-index:999;align-items:center;justify-content:center}
.fullscreen-overlay.open{display:flex}
.fullscreen-overlay img{max-width:100%;max-height:100%}
.close-fs{position:absolute;top:16px;right:16px;background:rgba(0,0,0,.6);color:#fff;border:none;border-radius:50%;width:40px;height:40px;font-size:1.2em;cursor:pointer}
</style>
</head>
<body>

<div class="topbar">
  <h1>📷 DVR Local</h1>
  <a href="/logout">Sair</a>
</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('cameras',this)">📷 Câmeras</div>
  <div class="tab" onclick="switchTab('scanner',this)">📡 Scanner</div>
  <div class="tab" onclick="switchTab('recordings',this)">🎞️ Gravações</div>
</div>

<!-- ═══════════════════════════════ CÂMERAS ══════════════════════════════════ -->
<div id="page-cameras" class="page active">
  <div id="cam-list"><div class="empty">Carregando câmeras...</div></div>
  <button class="btn btn-primary btn-block" onclick="loadCameras()" style="margin-top:8px">↻ Atualizar</button>
</div>

<!-- ═══════════════════════════════ SCANNER ══════════════════════════════════ -->
<div id="page-scanner" class="page">
  <div class="card">
    <h3>🌐 Scan Local (este dispositivo)</h3>
    <p style="font-size:.85em;color:var(--sub);margin:8px 0 12px">
      Detecta automaticamente sua sub-rede Wi-Fi e escaneia em busca de câmeras HTTP.
      Os resultados são enviados para o servidor e as câmeras são cadastradas.
    </p>

    <div class="row"><span class="lbl">Agente ID</span><input id="agentId" placeholder="carregando..."></div>
    <div class="row"><span class="lbl">Usuário cam</span><input id="camUser" value="admin"></div>
    <div class="row"><span class="lbl">Senha cam</span><input id="camPass" type="password" placeholder="senha da câmera"></div>

    <button id="scanBtn" class="btn btn-success btn-block" onclick="startScan()">📡 Iniciar Scan</button>
    <div id="progress-wrap" style="display:none">
      <div class="progress-bar"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
      <p id="progress-label" style="font-size:.8em;color:var(--sub);text-align:center"></p>
    </div>
    <div id="scan-log" class="log"></div>
  </div>

  <div id="scan-results"></div>
</div>

<!-- ═══════════════════════════════ GRAVAÇÕES ═════════════════════════════════ -->
<div id="page-recordings" class="page">
  <div id="rec-grid" class="grid2"><div class="empty" style="grid-column:span 2">Carregando gravações...</div></div>
</div>

<!-- Fullscreen image viewer -->
<div class="fullscreen-overlay" id="fsOverlay" onclick="closeFull()">
  <img id="fsImg" src="">
  <button class="close-fs" onclick="closeFull()">✕</button>
</div>

<script>
// ─── Estado ──────────────────────────────────────────────────────────────────
const DVR = location.origin;
let agentName = localStorage.getItem('dvr_agent_id') || 'mobile_' + Math.random().toString(36).slice(2,8);
localStorage.setItem('dvr_agent_id', agentName);
let heartbeatTimer = null;

// ─── Tabs ─────────────────────────────────────────────────────────────────────
function switchTab(name, el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('page-' + name).classList.add('active');
  if (name === 'cameras') loadCameras();
  if (name === 'scanner') initScanner();
  if (name === 'recordings') loadRecordings();
}

// ─── Câmeras ──────────────────────────────────────────────────────────────────
async function loadCameras() {
  const list = document.getElementById('cam-list');
  try {
    const r = await fetch(DVR + '/api/cameras');
    if (r.status === 401) { list.innerHTML = '<div class="empty">Sessão expirada — <a href="/login" style="color:var(--accent)">entrar</a></div>'; return; }
    const {cameras} = await r.json();
    if (!cameras.length) { list.innerHTML = '<div class="empty">Nenhuma câmera cadastrada.<br>Use o Scanner para encontrar câmeras.</div>'; return; }
    list.innerHTML = cameras.map(c => `
      <div class="card">
        <img class="cam-img" src="${c.snapshot_url || '/api/camera/'+c.id+'/snapshot_img'}" alt="${c.name}"
          onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 320 180%22><rect fill=%22%23111%22 width=%22320%22 height=%22180%22/><text x=%22160%22 y=%2295%22 text-anchor=%22middle%22 fill=%22%23555%22 font-size=%2214%22>sem sinal</text></svg>'"
        >
        <div class="cam-name">${c.name} <span class="badge ${c.enabled ? 'badge-green' : 'badge-red'}">${c.enabled ? 'ativa' : 'inativa'}</span></div>
        <div class="cam-sub">${c.ip}:${c.port}</div>
        <div style="display:flex;gap:6px;margin-top:10px">
          <button class="btn btn-primary" style="flex:1" onclick="takeSnap('${c.id}')">📸 Foto</button>
          <a href="${c.stream_url || '#'}" target="_blank" class="btn" style="flex:1;background:var(--orange);color:#fff;text-align:center">▶ Stream</a>
        </div>
      </div>`).join('');
  } catch(e) {
    list.innerHTML = `<div class="empty">Erro ao carregar: ${e}</div>`;
  }
}

async function takeSnap(camId) {
  try {
    await fetch(DVR + '/api/camera/' + camId + '/snapshot', {method:'POST'});
    alert('Foto salva nas gravações!');
  } catch(e) { alert('Erro: ' + e); }
}

// ─── Scanner ──────────────────────────────────────────────────────────────────
function initScanner() {
  document.getElementById('agentId').value = agentName;
  startHeartbeat();
}

function startHeartbeat() {
  if (heartbeatTimer) return;
  sendHeartbeat();
  heartbeatTimer = setInterval(sendHeartbeat, 8000);
}

async function sendHeartbeat() {
  try {
    await fetch(DVR + '/api/agent/heartbeat', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({agent: agentName, platform: 'mobile-pwa'})
    });
  } catch(e) {}
}

// Detectar IP local via WebRTC
function getLocalIP() {
  return new Promise(resolve => {
    const pc = new RTCPeerConnection({iceServers:[]});
    pc.createDataChannel('');
    pc.createOffer().then(o => pc.setLocalDescription(o));
    const timeout = setTimeout(() => { pc.close(); resolve(null); }, 3000);
    pc.onicecandidate = e => {
      if (!e.candidate) return;
      const m = e.candidate.candidate.match(/([0-9]{1,3}(?:[.][0-9]{1,3}){3})/);
      if (m && !m[1].startsWith('127.')) {
        clearTimeout(timeout);
        pc.close();
        resolve(m[1]);
      }
    };
  });
}

function getSubnet(ip) {
  const parts = ip.split('.');
  return parts[0] + '.' + parts[1] + '.' + parts[2];
}

function log(msg) {
  const el = document.getElementById('scan-log');
  el.innerHTML += msg + '\\n';
  el.scrollTop = el.scrollHeight;
}

const PORTS = [80, 8080, 8000, 554, 81, 82, 8888];
const CAM_PATHS = ['/snapshot.jpg', '/cgi-bin/snapshot.cgi', '/image/jpeg.cgi', '/Streaming/Channels/1/picture'];

async function probeHost(ip, port, signal) {
  const urls = CAM_PATHS.map(p => `http://${ip}:${port}${p}`);
  for (const url of urls) {
    try {
      const r = await fetch(url, {signal, mode:'no-cors'});
      return {ip, port, url};
    } catch(e) {
      if (e.name === 'AbortError') return null;
    }
  }
  return null;
}

async function startScan() {
  const btn = document.getElementById('scanBtn');
  const pwrap = document.getElementById('progress-wrap');
  const fill  = document.getElementById('progressFill');
  const plabel = document.getElementById('progress-label');
  const results = document.getElementById('scan-results');
  const logEl  = document.getElementById('scan-log');
  logEl.innerHTML = '';

  btn.disabled = true;
  btn.textContent = '⏳ Detectando IP local...';
  pwrap.style.display = 'block';
  results.innerHTML = '';
  fill.style.width = '0%';

  // Atualizar agentName com valor do input
  agentName = document.getElementById('agentId').value || agentName;
  localStorage.setItem('dvr_agent_id', agentName);
  const camUser = document.getElementById('camUser').value;
  const camPass = document.getElementById('camPass').value;

  const localIP = await getLocalIP();
  if (!localIP) {
    log('⚠ Não foi possível detectar IP local. Tente conectar ao Wi-Fi das câmeras.');
    btn.disabled = false;
    btn.textContent = '📡 Iniciar Scan';
    return;
  }
  const subnet = getSubnet(localIP);
  log(`📍 IP local: ${localIP}  |  Sub-rede: ${subnet}.0/24`);
  btn.textContent = `⏳ Escaneando ${subnet}.0/24...`;

  const hosts = Array.from({length:254}, (_,i) => subnet + '.' + (i+1));
  const found = [];
  let done = 0;
  const BATCH = 15;

  for (let i = 0; i < hosts.length; i += BATCH) {
    const batch = hosts.slice(i, i + BATCH);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 1200);

    const promises = batch.flatMap(ip =>
      PORTS.map(port => probeHost(ip, port, controller.signal))
    );
    const batchResults = await Promise.all(promises);
    clearTimeout(timer);

    batchResults.forEach(r => {
      if (r) {
        found.push(r);
        log(`✅ ${r.ip}:${r.port} → ${r.url}`);
      }
    });

    done += batch.length;
    const pct = Math.round((done / hosts.length) * 100);
    fill.style.width = pct + '%';
    plabel.textContent = `${done}/254 hosts — ${found.length} encontrada(s)`;
  }

  plabel.textContent = `Concluído — ${found.length} câmera(s) encontrada(s)`;
  btn.disabled = false;
  btn.textContent = '📡 Iniciar Scan';

  if (!found.length) {
    results.innerHTML = '<div class="card"><div class="empty">Nenhuma câmera encontrada nesta rede.</div></div>';
    return;
  }

  // Exibir resultados
  results.innerHTML = `<div class="card"><h3 style="margin-bottom:10px">✅ ${found.length} câmera(s) encontrada(s)</h3>` +
    found.map(r => `<div class="result-item"><strong>${r.ip}:${r.port}</strong><br><span style="color:var(--sub)">${r.url}</span></div>`).join('') +
    `<button class="btn btn-success btn-block" style="margin-top:12px" onclick="registerAll()">☁️ Cadastrar no Servidor</button></div>`;

  window._scanFound = found;
  window._scanMeta  = {camUser, camPass, subnet, agentName};
}

async function registerAll() {
  const found = window._scanFound || [];
  const meta  = window._scanMeta  || {};
  if (!found.length) return;

  try {
    const r = await fetch(DVR + '/api/agent/results', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        agent: meta.agentName || agentName,
        cameras: found.map(c => ({
          ip: c.ip, port: c.port, url: c.url,
          user: meta.camUser, password: meta.camPass, model: 'auto'
        }))
      })
    });
    const d = await r.json();
    if (d.registered !== undefined) {
      alert(`✅ ${d.registered} câmera(s) cadastrada(s) no servidor!`);
    } else {
      alert('Enviado. Verifique /cameras no servidor.');
    }
  } catch(e) {
    alert('Erro ao cadastrar: ' + e);
  }
}

// ─── Gravações ────────────────────────────────────────────────────────────────
async function loadRecordings() {
  const grid = document.getElementById('rec-grid');
  try {
    const r = await fetch(DVR + '/api/recordings');
    if (r.status === 404) {
      // endpoint pode não existir — mostrar link
      grid.innerHTML = '<div class="empty" style="grid-column:span 2"><a href="/recordings" style="color:var(--accent)">Ver gravações no navegador</a></div>';
      return;
    }
    const {files} = await r.json();
    if (!files || !files.length) {
      grid.innerHTML = '<div class="empty" style="grid-column:span 2">Nenhuma gravação ainda.</div>';
      return;
    }
    grid.innerHTML = files.map(f => `
      <div onclick="openFull('${f.url}')">
        <img class="rec-thumb" src="${f.url}" alt="${f.name}" onerror="this.style.display='none'">
        <div style="font-size:.7em;color:var(--sub);margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${f.name}</div>
      </div>`).join('');
  } catch(e) {
    grid.innerHTML = `<div class="empty" style="grid-column:span 2">Erro: ${e}<br><a href="/recordings" style="color:var(--accent)">Abrir página de gravações</a></div>`;
  }
}

function openFull(url) {
  document.getElementById('fsImg').src = url;
  document.getElementById('fsOverlay').classList.add('open');
}
function closeFull() {
  document.getElementById('fsOverlay').classList.remove('open');
}

// ─── Init ─────────────────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
loadCameras();
</script>
</body>
</html>"""


@app.route('/pwa')
@login_required
def pwa():
    return PWA_TEMPLATE


# ─── API JSON: gravações (para PWA) ───────────────────────────────────────────
@app.route('/api/recordings', methods=['GET'])
@login_required
def api_recordings_list():
    import glob
    files = []
    if os.path.isdir(RECORDINGS_DIR):
        for path in sorted(glob.glob(os.path.join(RECORDINGS_DIR, '**', '*.jpg'), recursive=True), reverse=True)[:60]:
            rel = os.path.relpath(path, RECORDINGS_DIR).replace('\\', '/')
            parts = rel.split('/')
            cam_id = parts[0] if len(parts) > 1 else 'unknown'
            name = parts[-1]
            files.append({'name': name, 'cam_id': cam_id, 'url': f'/recordings/{cam_id}/{name}'})
    return jsonify({'files': files})


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
