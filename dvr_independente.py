"""
DVR Local - Sistema Independente de Câmeras
Captura vídeo + áudio via RTSP sem dependências externas
"""

from flask import Flask, Response, render_template_string, jsonify, request, redirect, url_for
import cv2
import threading
import time
import logging
from datetime import datetime
import json
import os
from urllib.parse import quote

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'cameras_config.json')


def _normalize_path(path):
    if not path:
        return '/stream'
    if not path.startswith('/'):
        return '/' + path
    return path


def build_rtsp_urls(cam_info):
    user = cam_info.get('user', '').strip()
    password = cam_info.get('password', '')
    auth = f"{user}:{quote(password, safe='')}" if user else ''
    host = cam_info['ip'].strip()
    port = int(cam_info.get('port') or 554)
    paths = cam_info.get('paths') or [cam_info.get('path') or '/stream']
    urls = []
    for path in paths:
        norm = _normalize_path(path)
        if auth:
            urls.append(f"rtsp://{auth}@{host}:{port}{norm}")
        else:
            urls.append(f"rtsp://{host}:{port}{norm}")
    return urls


def default_config():
    return {
        'cameras': [
            {
                'id': 'cam1',
                'name': 'Câmera 1 (Entrada)',
                'ip': '192.168.1.3',
                'port': 554,
                'user': 'dcmk',
                'password': 'Herb1745@',
                'paths': ['/stream', '/ch0', '/'],
                'enabled': True
            },
            {
                'id': 'cam2',
                'name': 'Câmera 2 (Frente)',
                'ip': '192.168.1.10',
                'port': 554,
                'user': 'dcmk',
                'password': 'Herb1745@',
                'paths': ['/stream', '/ch0', '/'],
                'enabled': True
            }
        ]
    }


def load_config():
    if not os.path.exists(CONFIG_PATH):
        config = default_config()
        save_config(config)
        return config

    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        config = default_config()
        save_config(config)
        return config

    cameras = data.get('cameras')
    if isinstance(cameras, dict):
        # Migrar formato antigo para lista RTSP
        migrated = []
        for key, cam in cameras.items():
            ip = cam.get('ip', '').strip()
            if not ip:
                continue
            port = int(cam.get('port') or 554)
            path = cam.get('path', '/stream')
            if 'snapshot' in path or port == 80:
                path = '/stream'
                port = 554
            migrated.append({
                'id': f"cam{len(migrated) + 1}",
                'name': cam.get('name', f"Camera {len(migrated) + 1}"),
                'ip': ip,
                'port': port,
                'user': cam.get('user', ''),
                'password': cam.get('password', ''),
                'paths': [path, '/ch0', '/'],
                'enabled': cam.get('enabled', True)
            })
        data = {'cameras': migrated}
        save_config(data)
        return data

    if isinstance(cameras, list):
        return data

    config = default_config()
    save_config(config)
    return config


def save_config(config):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_cameras():
    config = load_config()
    cameras = {}
    for cam in config.get('cameras', []):
        cam_id = cam.get('id') or f"cam{len(cameras) + 1}"
        cameras[cam_id] = {
            'name': cam.get('name', cam_id),
            'ip': cam.get('ip', ''),
            'rtsp_urls': build_rtsp_urls(cam) if cam.get('ip') else [],
            'active': cam.get('enabled', True)
        }
    return cameras


# Configuração das câmeras
CAMERAS = load_cameras()

# Cache de streams abertos
camera_streams = {}
stream_locks = {}

class RTSPStream:
    """Gerenciador de stream RTSP"""
    
    def __init__(self, camera_id, rtsp_urls):
        self.camera_id = camera_id
        self.rtsp_urls = rtsp_urls
        self.cap = None
        self.last_frame = None
        self.is_running = False
        self.thread = None
        self.frame_count = 0
        
    def connect(self):
        """Tenta conectar usando múltiplas URLs RTSP"""
        for url in self.rtsp_urls:
            logger.info(f"{self.camera_id}: Tentando {url}")
            try:
                cap = cv2.VideoCapture(url)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Reduzir latência
                
                # Testar leitura
                ret, frame = cap.read()
                if ret and frame is not None:
                    logger.info(f"{self.camera_id}: ✓ Conectado via {url}")
                    self.cap = cap
                    return True
                else:
                    cap.release()
            except Exception as e:
                logger.warning(f"{self.camera_id}: Erro em {url}: {e}")
                
        logger.error(f"{self.camera_id}: ✗ Nenhuma URL RTSP funcionou")
        return False
    
    def start(self):
        """Inicia captura em thread separada"""
        if self.is_running:
            return
        
        if not self.connect():
            return False
        
        self.is_running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()
        return True
    
    def _capture_loop(self):
        """Loop de captura de frames"""
        error_count = 0
        
        while self.is_running:
            try:
                ret, frame = self.cap.read()
                
                if ret and frame is not None:
                    self.last_frame = frame
                    self.frame_count += 1
                    error_count = 0
                    
                    if self.frame_count % 100 == 0:
                        logger.debug(f"{self.camera_id}: {self.frame_count} frames")
                else:
                    error_count += 1
                    if error_count > 30:
                        logger.error(f"{self.camera_id}: Muitos erros, reconectando...")
                        self.cap.release()
                        if not self.connect():
                            break
                        error_count = 0
                    
                time.sleep(0.03)  # ~30 FPS
                
            except Exception as e:
                logger.error(f"{self.camera_id}: Erro no loop: {e}")
                error_count += 1
                if error_count > 30:
                    break
                time.sleep(1)
        
        self.is_running = False
        if self.cap:
            self.cap.release()
    
    def get_frame(self):
        """Retorna último frame capturado"""
        return self.last_frame
    
    def stop(self):
        """Para a captura"""
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=2)
        if self.cap:
            self.cap.release()


def get_camera_stream(camera_id):
    """Obtém ou cria stream para câmera"""
    if camera_id not in camera_streams:
        if camera_id not in CAMERAS:
            return None
        
        stream = RTSPStream(camera_id, CAMERAS[camera_id]['rtsp_urls'])
        if stream.start():
            camera_streams[camera_id] = stream
            stream_locks[camera_id] = threading.Lock()
        else:
            return None
    
    return camera_streams.get(camera_id)


def add_camera_to_config(data):
    config = load_config()
    cameras = config.get('cameras', [])
    existing_ids = {cam.get('id') for cam in cameras}
    next_num = 1
    while f"cam{next_num}" in existing_ids:
        next_num += 1

    cam_id = f"cam{next_num}"
    name = (data.get('name') or f"Camera {next_num}").strip()
    ip = (data.get('ip') or '').strip()
    port = int(data.get('port') or 554)
    user = (data.get('user') or '').strip()
    password = data.get('password') or ''
    path = (data.get('path') or '/stream').strip()
    paths = [path, '/ch0', '/']

    camera_info = {
        'id': cam_id,
        'name': name,
        'ip': ip,
        'port': port,
        'user': user,
        'password': password,
        'paths': paths,
        'enabled': True
    }
    cameras.append(camera_info)
    config['cameras'] = cameras
    save_config(config)

    CAMERAS[cam_id] = {
        'name': name,
        'ip': ip,
        'rtsp_urls': build_rtsp_urls(camera_info),
        'active': True
    }
    return cam_id


def delete_camera_from_config(camera_id):
    config = load_config()
    cameras = config.get('cameras', [])
    config['cameras'] = [cam for cam in cameras if cam.get('id') != camera_id]
    save_config(config)
    
    if camera_id in CAMERAS:
        stream = camera_streams.pop(camera_id, None)
        if stream:
            stream.stop()
        stream_locks.pop(camera_id, None)
        del CAMERAS[camera_id]
    
    return True


def generate_frames(camera_id):
    """Gera frames MJPEG para streaming"""
    stream = get_camera_stream(camera_id)
    
    if not stream:
        logger.error(f"Stream não disponível para {camera_id}")
        # Retornar frame de erro
        yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + create_error_frame(camera_id) + b'\r\n'
        return
    
    while True:
        try:
            frame = stream.get_frame()
            
            if frame is not None:
                # Redimensionar se necessário
                height, width = frame.shape[:2]
                if width > 1280:
                    scale = 1280 / width
                    frame = cv2.resize(frame, (1280, int(height * scale)))
                
                # Codificar JPEG
                ret, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                
                if ret:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            
            time.sleep(0.033)  # ~30 FPS
            
        except GeneratorExit:
            break
        except Exception as e:
            logger.error(f"Erro ao gerar frame {camera_id}: {e}")
            time.sleep(1)


def create_error_frame(camera_id):
    """Cria frame de erro"""
    import numpy as np
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(frame, f"Erro: {camera_id}", (50, 240), 
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
    ret, jpeg = cv2.imencode('.jpg', frame)
    return jpeg.tobytes()


@app.route('/')
def index():
    """Página principal"""
    return render_template_string(HTML_TEMPLATE, cameras=CAMERAS)


@app.route('/video/<camera_id>')
def video(camera_id):
    """Stream de vídeo MJPEG"""
    return Response(generate_frames(camera_id),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/api/status')
def api_status():
    """Status das câmeras"""
    status = {}
    for cam_id, cam_info in CAMERAS.items():
        stream = camera_streams.get(cam_id)
        status[cam_id] = {
            'name': cam_info['name'],
            'connected': stream is not None and stream.is_running,
            'frames': stream.frame_count if stream else 0
        }
    return jsonify(status)


@app.route('/api/cameras', methods=['GET'])
def api_cameras():
    return jsonify(load_config())


@app.route('/add_camera', methods=['POST'])
def add_camera():
    data = request.form if request.form else request.json or {}
    if not data.get('ip'):
        return jsonify({'error': 'IP obrigatorio'}), 400
    add_camera_to_config(data)
    return redirect(url_for('index'))


@app.route('/api/camera/<camera_id>', methods=['DELETE'])
def delete_camera(camera_id):
    try:
        delete_camera_from_config(camera_id)
        return jsonify({'success': True, 'message': f'Camera {camera_id} removida'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/settings')
def settings():
    """Página de configurações"""
    config = load_config()
    return render_template_string(SETTINGS_TEMPLATE, cameras=config.get('cameras', []))


@app.route('/api/discover', methods=['GET'])
def api_discover():
    """Descobrir cameras na rede"""
    import socket
    import ipaddress
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    found = []
    
    def check_port(ip, port):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.3)
            result = sock.connect_ex((str(ip), port))
            sock.close()
            if result == 0:
                return (str(ip), port)
        except:
            pass
        return None
    
    # Detectar subnet atual
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        
        # Extrair subnet
        parts = local_ip.split('.')
        subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
        
        network_obj = ipaddress.ip_network(subnet, strict=False)
        
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = []
            for ip in network_obj.hosts():
                for port in [554, 8899, 8554]:
                    futures.append(executor.submit(check_port, ip, port))
            
            for future in as_completed(futures):
                result = future.result()
                if result:
                    found.append({'ip': result[0], 'port': result[1]})
        
        # Remover duplicatas
        unique = {f['ip']: f for f in found}.values()
        return jsonify({'discovered': list(unique), 'subnet': subnet})
    
    except Exception as e:
        logger.error(f"Erro discovering: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/camera/<camera_id>', methods=['PUT'])
def update_camera(camera_id):
    """Atualizar configuração de câmera"""
    try:
        data = request.json or {}
        config = load_config()
        cameras = config.get('cameras', [])
        
        for cam in cameras:
            if cam.get('id') == camera_id:
                cam['name'] = data.get('name', cam.get('name'))
                cam['ip'] = data.get('ip', cam.get('ip'))
                cam['port'] = int(data.get('port', cam.get('port', 554)))
                cam['user'] = data.get('user', cam.get('user'))
                cam['password'] = data.get('password', cam.get('password'))
                cam['paths'] = data.get('paths', cam.get('paths', ['/stream']))
                
                save_config(config)
                
                # Recarregar CAMERAS
                global CAMERAS
                CAMERAS = load_cameras()
                
                # Reconectar stream
                if camera_id in camera_streams:
                    stream = camera_streams[camera_id]
                    stream.stop()
                    del camera_streams[camera_id]
                
                return jsonify({'success': True, 'message': 'Camera atualizada'})
        
        return jsonify({'error': 'Camera não encontrada'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Template de Configurações
SETTINGS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Configurações - DVR Local</title>
    <meta charset="utf-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            min-height: 100vh;
            color: white;
        }
        .header {
            background: rgba(0,0,0,0.3);
            padding: 20px;
        }
        .header h1 { font-size: 2em; margin-bottom: 10px; }
        .nav {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        .nav a {
            background: #4CAF50;
            color: white;
            padding: 10px 20px;
            border-radius: 5px;
            text-decoration: none;
        }
        .nav a:hover { background: #45a049; }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        .settings-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 20px;
        }
        .camera-form {
            background: rgba(255,255,255,0.1);
            border-radius: 10px;
            padding: 20px;
            backdrop-filter: blur(10px);
        }
        .camera-form h3 {
            margin-bottom: 15px;
            font-size: 1.3em;
        }
        .form-group {
            margin-bottom: 12px;
            display: flex;
            flex-direction: column;
        }
        .form-group label {
            font-size: 0.9em;
            margin-bottom: 5px;
            opacity: 0.9;
        }
        .form-group input {
            padding: 8px;
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 5px;
            background: rgba(255,255,255,0.1);
            color: white;
        }
        .form-group input::placeholder {
            opacity: 0.6;
        }
        .btn-group {
            display: flex;
            gap: 10px;
            margin-top: 15px;
        }
        .btn {
            flex: 1;
            padding: 10px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-weight: bold;
            color: white;
        }
        .btn-save { background: #4CAF50; }
        .btn-save:hover { background: #45a049; }
        .btn-delete { background: #f44336; }
        .btn-delete:hover { background: #da190b; }
        .btn-test { background: #008CBA; }
        .btn-test:hover { background: #007399; }
        .discover-section {
            background: rgba(255,255,255,0.1);
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .discover-list {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
            gap: 10px;
            margin-top: 10px;
        }
        .discover-item {
            background: rgba(76,175,80,0.2);
            border: 2px solid #4CAF50;
            border-radius: 5px;
            padding: 10px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s;
        }
        .discover-item:hover {
            background: rgba(76,175,80,0.4);
            transform: scale(1.05);
        }
        .status-msg {
            padding: 10px;
            margin-top: 10px;
            border-radius: 5px;
            text-align: center;
        }
        .status-success { background: rgba(76,175,80,0.3); color: #4CAF50; }
        .status-error { background: rgba(244,67,54,0.3); color: #f44336; }
        .status-loading { background: rgba(0,150,200,0.3); color: #00b8d4; }
    </style>
</head>
<body>
    <div class="header">
        <h1>⚙️ Configurações - DVR Local</h1>
        <div class="nav">
            <a href="/">← Voltar ao DVR</a>
        </div>
    </div>
    
    <div class="container">
        <div class="discover-section">
            <h2>🔍 Auto-Discovery de Câmeras</h2>
            <p>Clique para buscar câmeras disponíveis na rede atual</p>
            <button class="btn btn-test" onclick="discoverCameras()" style="width: 100%; margin-top: 10px;">Buscar Câmeras</button>
            <div id="discover-status"></div>
            <div id="discover-list" class="discover-list"></div>
        </div>
        
        <h2>📹 Configurar Câmeras</h2>
        <div class="settings-grid">
            {% for cam in cameras %}
            <div class="camera-form">
                <h3>{{ cam.name }}</h3>
                <form onsubmit="saveCam(event, '{{ cam.id }}')">
                    <div class="form-group">
                        <label>Nome da Câmera</label>
                        <input type="text" value="{{ cam.name }}" class="name-{{ cam.id }}">
                    </div>
                    <div class="form-group">
                        <label>IP</label>
                        <input type="text" value="{{ cam.ip }}" class="ip-{{ cam.id }}" placeholder="192.168.1.10">
                    </div>
                    <div class="form-group">
                        <label>Porta RTSP</label>
                        <input type="number" value="{{ cam.port }}" class="port-{{ cam.id }}" placeholder="554">
                    </div>
                    <div class="form-group">
                        <label>Usuário</label>
                        <input type="text" value="{{ cam.user }}" class="user-{{ cam.id }}" placeholder="dcmk">
                    </div>
                    <div class="form-group">
                        <label>Senha</label>
                        <input type="password" value="{{ cam.password }}" class="password-{{ cam.id }}">
                    </div>
                    <div class="btn-group">
                        <button type="submit" class="btn btn-save">💾 Salvar</button>
                        <button type="button" class="btn btn-delete" onclick="deleteCam('{{ cam.id }}', '{{ cam.name }}')">🗑️ Deletar</button>
                    </div>
                    <div id="msg-{{ cam.id }}"></div>
                </form>
            </div>
            {% endfor %}
        </div>
    </div>
    
    <script>
        function saveCam(e, camId) {
            e.preventDefault();
            
            const name = document.querySelector('.name-' + camId).value;
            const ip = document.querySelector('.ip-' + camId).value;
            const port = document.querySelector('.port-' + camId).value;
            const user = document.querySelector('.user-' + camId).value;
            const password = document.querySelector('.password-' + camId).value;
            
            if (!ip) {
                showMsg(camId, 'IP é obrigatório', 'error');
                return;
            }
            
            showMsg(camId, 'Salvando...', 'loading');
            
            fetch(`/api/camera/${camId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, ip, port, user, password, paths: ['/stream'] })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    showMsg(camId, '✓ Salvo com sucesso!', 'success');
                    setTimeout(() => location.reload(), 1500);
                } else {
                    showMsg(camId, '✗ Erro: ' + (data.error || 'desconhecido'), 'error');
                }
            })
            .catch(e => showMsg(camId, '✗ Erro: ' + e.message, 'error'));
        }
        
        function deleteCam(camId, camName) {
            if (!confirm(`Deletar "${camName}"?`)) return;
            fetch(`/api/camera/${camId}`, { method: 'DELETE' })
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        location.reload();
                    }
                })
                .catch(e => alert('Erro: ' + e.message));
        }
        
        function showMsg(camId, msg, type) {
            const elem = document.getElementById('msg-' + camId);
            elem.textContent = msg;
            elem.className = 'status-msg status-' + type;
        }
        
        function discoverCameras() {
            const status = document.getElementById('discover-status');
            const list = document.getElementById('discover-list');
            status.innerHTML = '<div class="status-msg status-loading">Buscando câmeras na rede...</div>';
            list.innerHTML = '';
            
            fetch('/api/discover')
                .then(r => r.json())
                .then(data => {
                    if (data.error) {
                        status.innerHTML = `<div class="status-msg status-error">${data.error}</div>`;
                        return;
                    }
                    
                    status.innerHTML = `<div class="status-msg status-success">Encontrados ${data.discovered.length} dispositivo(s) na rede ${data.subnet}</div>`;
                    
                    data.discovered.forEach(dev => {
                        const item = document.createElement('div');
                        item.className = 'discover-item';
                        item.innerHTML = `<strong>${dev.ip}</strong><br/>Porta ${dev.port}`;
                        item.onclick = () => {
                            const ipInputs = document.querySelectorAll('[class*="ip-"]');
                            if (ipInputs.length > 0) {
                                ipInputs[0].value = dev.ip;
                            }
                        };
                        list.appendChild(item);
                    });
                })
                .catch(e => {
                    status.innerHTML = `<div class="status-msg status-error">Erro: ${e.message}</div>`;
                });
        }
    </script>
</body>
</html>
"""

# Template HTML
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>DVR Local - Independente</title>
    <meta charset="utf-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            min-height: 100vh;
            color: white;
        }
        .header {
            background: rgba(0,0,0,0.3);
            padding: 20px;
            text-align: center;
        }
        .header h1 { font-size: 2.5em; margin-bottom: 10px; }
        .header p { opacity: 0.8; }
        .cameras {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(600px, 1fr));
            gap: 30px;
            padding: 30px;
            max-width: 1800px;
            margin: 0 auto;
        }
        .camera-card {
            background: rgba(255,255,255,0.1);
            border-radius: 15px;
            padding: 20px;
            backdrop-filter: blur(10px);
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        }
        .camera-card {
            position: relative;
        }
        .camera-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        .camera-card h2 {
            margin: 0;
            font-size: 1.5em;
            flex: 1;
        }
        .delete-btn {
            background: rgba(244, 67, 54, 0.7);
            border: none;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            cursor: pointer;
            font-size: 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.3s;
            color: white;
        }
        .delete-btn:hover {
            background: rgba(244, 67, 54, 1);
            transform: scale(1.1);
        }
        .camera-card img {
            width: 100%;
            border-radius: 10px;
            background: #000;
            min-height: 400px;
        }
        .camera-status {
            margin-top: 10px;
            padding: 10px;
            background: rgba(0,0,0,0.3);
            border-radius: 5px;
            text-align: center;
        }
        .add-form {
            max-width: 900px;
            margin: 20px auto 0;
            padding: 20px;
            background: rgba(0,0,0,0.25);
            border-radius: 12px;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 12px;
        }
        .add-form input {
            padding: 10px;
            border-radius: 8px;
            border: 1px solid rgba(255,255,255,0.2);
            background: rgba(255,255,255,0.1);
            color: white;
        }
        .add-form button {
            padding: 10px 16px;
            border: none;
            border-radius: 8px;
            background: #4CAF50;
            color: white;
            font-weight: bold;
            cursor: pointer;
        }
        .add-form small {
            opacity: 0.8;
        }
        .status-online { color: #4CAF50; }
        .status-offline { color: #f44336; }
        @media (max-width: 768px) {
            .cameras { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🎥 DVR Local - Sistema Independente</h1>
        <p>Streaming RTSP direto das câmeras (sem Agent DVR)</p>
        <a href="/settings" style="display: inline-block; margin-top: 10px; background: #FFA500; color: white; padding: 10px 20px; border-radius: 5px; text-decoration: none; font-weight: bold;">⚙️ Configurar Câmeras</a>
    </div>

    <form class="add-form" action="/add_camera" method="post">
        <input type="text" name="name" placeholder="Nome da camera (opcional)">
        <input type="text" name="ip" placeholder="IP da camera (ex: 192.168.1.10)" required>
        <input type="number" name="port" placeholder="Porta RTSP (554)" value="554">
        <input type="text" name="user" placeholder="Usuario (ex: dcmk)">
        <input type="password" name="password" placeholder="Senha">
        <input type="text" name="path" placeholder="Path RTSP (ex: /stream)" value="/stream">
        <button type="submit">Adicionar camera</button>
        <small>Voce pode adicionar quantas cameras quiser.</small>
    </form>
    
    <div class="cameras">
        {% for cam_id, cam in cameras.items() %}
        {% if cam.active %}
        <div class="camera-card">
            <div class="camera-header">
                <h2>{{ cam.name }}</h2>
                <button class="delete-btn" onclick="deleteCamera('{{ cam_id }}', '{{ cam.name }}')" title="Remover camera">🗑️</button>
            </div>
            <img src="/video/{{ cam_id }}" alt="{{ cam.name }}">
            <div class="camera-status">
                <span id="status-{{ cam_id }}">Conectando...</span>
            </div>
        </div>
        {% endif %}
        {% endfor %}
    </div>
    
    <script>
        // Atualizar status a cada 5s
        setInterval(() => {
            fetch('/api/status')
                .then(r => r.json())
                .then(data => {
                    Object.keys(data).forEach(camId => {
                        const elem = document.getElementById('status-' + camId);
                        if (elem) {
                            const info = data[camId];
                            if (info.connected) {
                                elem.className = 'status-online';
                                elem.textContent = `✓ Online - ${info.frames} frames`;
                            } else {
                                elem.className = 'status-offline';
                                elem.textContent = '✗ Desconectado';
                            }
                        }
                    });
                });
        }, 5000);
        
        // Deletar camera
        function deleteCamera(camId, camName) {
            if (!confirm(`Tem certeza que deseja remover a camera "${camName}"?`)) {
                return;
            }
            fetch(`/api/camera/${camId}`, { method: 'DELETE' })
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        location.reload();
                    } else {
                        alert('Erro ao remover camera: ' + (data.error || 'desconhecido'));
                    }
                })
                .catch(e => alert('Erro: ' + e.message));
        }
    </script>
</body>
</html>
"""


if __name__ == '__main__':
    logger.info("=" * 70)
    logger.info("🚀 DVR Local - Sistema Independente")
    logger.info("=" * 70)
    logger.info("")
    logger.info("📹 Câmeras configuradas:")
    for cam_id, cam in CAMERAS.items():
        logger.info(f"  - {cam['name']}: {cam['ip']}")
    logger.info("")
    logger.info("🌐 Acesse: http://localhost:8000/")
    logger.info("=" * 70)
    
    app.run(host='0.0.0.0', port=8000, threaded=True, debug=False)
