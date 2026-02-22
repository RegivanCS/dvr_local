from flask import Flask, Response, render_template_string, request, redirect, url_for, jsonify
import requests
import json
import os
import logging
import time
from datetime import datetime
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = 'dvr_local_secret_key_2026'

# Arquivo de configuração
CONFIG_FILE = 'cameras_config.json'

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

def test_camera_connection(ip, port, user, password, model):
    """Testa conexão com câmera e retorna path funcional"""
    paths = CAMERA_MODELS.get(model, {}).get('paths', [])
    
    for path in paths:
        url = f"http://{ip}:{port}{path}"
        try:
            response = requests.get(url, auth=(user, password), timeout=3)
            if response.status_code == 200 and len(response.content) > 100:
                return {'success': True, 'path': path, 'url': url}
        except:
            continue
    
    return {'success': False, 'error': 'Nenhum path funcional encontrado'}

def gen_frames_from_camera(cam_id):
    """Captura frames do Agent DVR (localhost:8090)"""
    # Usar Agent DVR como fonte (câmeras não respondem em HTTP direto)
    agent_url = f"http://localhost:8090/grab.jpg?oid={cam_id}"
    
    logger.info(f"Conectando câmera {cam_id} via Agent DVR: {agent_url}")
    
    frame_count = 0
    error_count = 0
    
    while True:
        try:
            response = requests.get(agent_url, timeout=5)
            
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
def index():
    """Página principal com visualização das câmeras"""
    config = load_config()
    cameras = config.get('cameras', {})
    
    if not cameras:
        return redirect(url_for('config_page'))
    
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
        camera_boxes = '<div style="grid-column: 1/-1; text-align: center; color: #fff;"><h2>⚠️ Nenhuma câmera ativa</h2><p>Configure câmeras na <a href="/config" style="color: #4CAF50;">página de configuração</a></p></div>'
    
    return render_template_string(INDEX_TEMPLATE, camera_boxes=camera_boxes)

@app.route('/config')
def config_page():
    """Página de configuração"""
    config = load_config()
    cameras = config.get('cameras', {})
    
    return render_template_string(CONFIG_TEMPLATE, 
                                   cameras=cameras, 
                                   models=CAMERA_MODELS)

@app.route('/api/camera/add', methods=['POST'])
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
def get_camera(cam_id):
    """Retorna dados da câmera para edição"""
    config = load_config()
    
    if cam_id in config['cameras']:
        return jsonify({'success': True, 'camera': config['cameras'][cam_id]})
    
    return jsonify({'success': False}), 404

@app.route('/api/camera/delete/<cam_id>', methods=['POST'])
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
def toggle_camera(cam_id):
    """Ativa/desativa câmera"""
    config = load_config()
    
    if cam_id in config['cameras']:
        config['cameras'][cam_id]['enabled'] = not config['cameras'][cam_id].get('enabled', True)
        save_config(config)
        return jsonify({'success': True, 'enabled': config['cameras'][cam_id]['enabled']})
    
    return jsonify({'success': False}), 404

@app.route('/camera/<cam_id>')
def camera_stream(cam_id):
    """Stream MJPEG da câmera"""
    return Response(
        gen_frames_from_camera(cam_id),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/scan')
def scan_page():
    """Página de scanner de rede"""
    return render_template_string(SCAN_TEMPLATE)

@app.route('/api/scan', methods=['POST'])
def scan_network():
    """Scanner de rede para encontrar câmeras"""
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    network = '.'.join(local_ip.split('.')[:-1])
    
    common_ports = [80, 8080, 8899, 554]
    cameras_found = []
    
    def check_ip(ip, port):
        try:
            url = f"http://{ip}:{port}"
            response = requests.get(url, timeout=1)
            content = response.text.lower()
            
            if any(keyword in content for keyword in ['camera', 'video', 'stream', 'ipcam', 'webcam']):
                return {'ip': ip, 'port': port, 'url': url}
        except:
            pass
        return None
    
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = []
        for i in range(1, 255):
            ip = f"{network}.{i}"
            for port in common_ports:
                futures.append(executor.submit(check_ip, ip, port))
        
        for future in as_completed(futures):
            result = future.result()
            if result:
                cameras_found.append(result)
    
    return jsonify({'cameras': cameras_found, 'network': f"{network}.0/24"})

# Templates HTML (continuação no próximo bloco...)
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
            <a href="/config" class="btn btn-config">⚙️ Configurar</a>
            <a href="/scan" class="btn btn-scan">🔍 Buscar Câmeras</a>
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
            <a href="/" class="btn btn-secondary">← Voltar</a>
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
                        <button onclick="deleteCamera('{{ cam_id }}')" class="btn btn-danger">🗑️ Remover</button>
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <p style="text-align: center; opacity: 0.7;">Nenhuma câmera configurada ainda.</p>
            {% endif %}
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
            <a href="/config" class="btn btn-secondary">← Voltar</a>
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
