"""
DVR Local - Versão Simplificada
"""
from flask import Flask, Response, render_template_string, jsonify, request, redirect, url_for
import cv2
import threading
import time
import logging
import json
import os
from urllib.parse import quote

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'cameras_config.json')

camera_streams = {}
stream_locks = {}

# ====== FUNÇÕES DE CONFIGURAÇÃO ======

def save_config(config):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def load_config():
    if not os.path.exists(CONFIG_PATH):
        config = {
            'cameras': [
                {'id': 'cam1', 'name': 'Câmera 1', 'ip': '192.168.1.3', 'port': 554, 'user': 'dcmk', 'password': 'Herb1745@'},
                {'id': 'cam2', 'name': 'Câmera 2', 'ip': '192.168.1.10', 'port': 554, 'user': 'dcmk', 'password': 'Herb1745@'}
            ]
        }
        save_config(config)
        return config
    
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        config = {
            'cameras': [
                {'id': 'cam1', 'name': 'Câmera 1', 'ip': '192.168.1.3', 'port': 554, 'user': 'dcmk', 'password': 'Herb1745@'},
                {'id': 'cam2', 'name': 'Câmera 2', 'ip': '192.168.1.10', 'port': 554, 'user': 'dcmk', 'password': 'Herb1745@'}
            ]
        }
        save_config(config)
        return config

def build_rtsp_url(cam):
    user = cam.get('user', '').strip()
    password = cam.get('password', '')
    ip = cam.get('ip', '').strip()
    port = int(cam.get('port', 554))
    path = cam.get('path', '/stream')
    if not path.startswith('/'):
        path = '/' + path
    
    if user:
        auth = f"{user}:{quote(password, safe='')}"
        return f"rtsp://{auth}@{ip}:{port}{path}"
    else:
        return f"rtsp://{ip}:{port}{path}"

def load_cameras_dict():
    config = load_config()
    cams = {}
    for cam in config.get('cameras', []):
        cam_id = cam.get('id', f"cam{len(cams)+1}")
        cams[cam_id] = {
            'name': cam.get('name', cam_id),
            'ip': cam.get('ip', ''),
            'port': cam.get('port', 554),
            'user': cam.get('user', ''),
            'password': cam.get('password', ''),
            'rtsp_url': build_rtsp_url(cam),
            'active': True
        }
    return cams

# ====== CLASSE RTSP ======

class RTSPStream:
    def __init__(self, cam_id, rtsp_url):
        self.cam_id = cam_id
        self.rtsp_url = rtsp_url
        self.cap = None
        self.last_frame = None
        self.is_running = False
        self.thread = None
        self.frame_count = 0
        self.last_frame_time = 0
        self.reconnect_attempts = 0
    
    def connect(self):
        logger.info(f"{self.cam_id}: Conectando {self.rtsp_url}")
        try:
            cap = cv2.VideoCapture(self.rtsp_url)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            cap.set(cv2.CAP_PROP_TIMEOUT, 5000)  # 5 segundos timeout
            ret, frame = cap.read()
            if ret and frame is not None:
                logger.info(f"{self.cam_id}: ✓ Conectado!")
                self.cap = cap
                self.reconnect_attempts = 0
                return True
            cap.release()
        except Exception as e:
            logger.error(f"{self.cam_id}: Erro {e}")
        return False
    
    def start(self):
        if self.is_running:
            return
        if not self.connect():
            return False
        self.is_running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()
        return True
    
    def _capture_loop(self):
        consecutive_failures = 0
        while self.is_running:
            try:
                ret, frame = self.cap.read()
                if ret and frame is not None:
                    self.last_frame = frame
                    self.frame_count += 1
                    self.last_frame_time = time.time()
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    logger.warning(f"{self.cam_id}: Falha ao ler frame ({consecutive_failures})")
                    
                    if consecutive_failures >= 10:
                        logger.error(f"{self.cam_id}: Muitas falhas, tentando reconectar...")
                        if self.cap:
                            self.cap.release()
                        time.sleep(2)
                        if self.connect():
                            consecutive_failures = 0
                            logger.info(f"{self.cam_id}: Reconectado com sucesso!")
                        else:
                            self.reconnect_attempts += 1
                            if self.reconnect_attempts >= 5:
                                logger.error(f"{self.cam_id}: Reconexão falhou 5 vezes, parando stream")
                                break
                
                time.sleep(0.033)
            except Exception as e:
                logger.error(f"{self.cam_id}: Exceção: {e}")
                consecutive_failures += 1
                if consecutive_failures >= 10:
                    break
                time.sleep(1)
        
        self.is_running = False
        if self.cap:
            self.cap.release()
        logger.info(f"{self.cam_id}: Stream finalizado")
    
    def get_frame(self):
        return self.last_frame
    
    def stop(self):
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=2)
        if self.cap:
            self.cap.release()

# ====== ROTAS ======

@app.route('/')
def index():
    cams = load_cameras_dict()
    return render_template_string(TEMPLATE_HTML, cameras=cams)

@app.route('/settings')
def settings():
    config = load_config()
    return render_template_string(TEMPLATE_SETTINGS, cameras=config.get('cameras', []))

@app.route('/video/<cam_id>')
def video(cam_id):
    cams = load_cameras_dict()
    if cam_id not in camera_streams:
        if cam_id not in cams:
            return "Câmera não encontrada", 404
        stream = RTSPStream(cam_id, cams[cam_id]['rtsp_url'])
        if stream.start():
            camera_streams[cam_id] = stream
        else:
            return "Erro ao conectar", 503
    
    return Response(generate_frames(cam_id), mimetype='multipart/x-mixed-replace; boundary=frame')

def generate_frames(cam_id):
    stream = camera_streams.get(cam_id)
    if not stream:
        return
    while True:
        try:
            frame = stream.get_frame()
            if frame is not None:
                ret, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ret:
                    yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n'
            time.sleep(0.033)
        except GeneratorExit:
            break
        except:
            time.sleep(1)

@app.route('/api/status')
def api_status():
    cams = load_cameras_dict()
    status = {}
    for cam_id, cam in cams.items():
        stream = camera_streams.get(cam_id)
        is_connected = stream is not None and stream.is_running
        status[cam_id] = {
            'name': cam['name'],
            'connected': is_connected,
            'frames': stream.frame_count if stream else 0,
            'last_frame_age': time.time() - stream.last_frame_time if (stream and stream.last_frame_time > 0) else None,
            'reconnect_attempts': stream.reconnect_attempts if stream else 0
        }
    return jsonify(status)

@app.route('/api/camera/<cam_id>', methods=['PUT'])
def update_camera(cam_id):
    try:
        data = request.json or {}
        config = load_config()
        
        for cam in config.get('cameras', []):
            if cam.get('id') == cam_id:
                cam['name'] = data.get('name', cam.get('name'))
                cam['ip'] = data.get('ip', cam.get('ip'))
                cam['port'] = int(data.get('port', cam.get('port', 554)))
                cam['user'] = data.get('user', cam.get('user'))
                cam['password'] = data.get('password', cam.get('password'))
                
                save_config(config)
                
                # Resetar stream
                if cam_id in camera_streams:
                    camera_streams[cam_id].stop()
                    del camera_streams[cam_id]
                
                return jsonify({'success': True})
        
        return jsonify({'error': 'Not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/camera/<cam_id>', methods=['DELETE'])
def delete_camera(cam_id):
    try:
        config = load_config()
        config['cameras'] = [c for c in config.get('cameras', []) if c.get('id') != cam_id]
        save_config(config)
        
        if cam_id in camera_streams:
            camera_streams[cam_id].stop()
            del camera_streams[cam_id]
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ====== TEMPLATES ======

TEMPLATE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>DVR Local</title>
    <meta charset="utf-8">
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{font-family:Arial;background:#1e3c72;color:white}
        .header{background:rgba(0,0,0,0.3);padding:20px;text-align:center}
        .header h1{font-size:2.5em;margin-bottom:10px}
        .header a{display:inline-block;margin-top:10px;background:#FFA500;color:white;padding:10px 20px;border-radius:5px;text-decoration:none;font-weight:bold}
        .cameras{display:grid;grid-template-columns:repeat(auto-fit,minmax(600px,1fr));gap:30px;padding:30px;max-width:1800px;margin:0 auto}
        .camera-card{background:rgba(255,255,255,0.1);border-radius:15px;padding:20px;backdrop-filter:blur(10px);position:relative}
        .camera-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:15px}
        .camera-card h2{margin:0;font-size:1.5em}
        .camera-card img{width:100%;border-radius:10px;background:#000;min-height:400px}
        .delete-btn{background:rgba(244,67,54,0.7);border:none;border-radius:50%;width:40px;height:40px;cursor:pointer;font-size:20px;display:flex;align-items:center;justify-content:center;color:white}
        .delete-btn:hover{background:rgba(244,67,54,1);transform:scale(1.1)}
        .camera-status{margin-top:10px;padding:10px;background:rgba(0,0,0,0.3);border-radius:5px;text-align:center}
        .status-online{color:#4CAF50}
        .status-offline{color:#f44336}
    </style>
</head>
<body>
    <div class="header">
        <h1>🎥 DVR Local</h1>
        <p>Streaming RTSP independente</p>
        <a href="/settings">⚙️ Configurar Câmeras</a>
    </div>
    <div class="cameras">
        {% for cam_id, cam in cameras.items() %}
        <div class="camera-card">
            <div class="camera-header">
                <h2>{{ cam.name }}</h2>
                <button class="delete-btn" onclick="if(confirm('Deletar?')){fetch('/api/camera/{{ cam_id }}',{method:'DELETE'}).then(()=>location.reload())}">🗑️</button>
            </div>
            <img src="/video/{{ cam_id }}" alt="{{ cam.name }}">
            <div class="camera-status">
                <span id="status-{{ cam_id }}">Conectando...</span>
            </div>
        </div>
        {% endfor %}
    </div>
    <script>
        setInterval(()=>{
            fetch('/api/status').then(r=>r.json()).then(data=>{
                Object.keys(data).forEach(id=>{
                    const el=document.getElementById('status-'+id);
                    if(el){
                        const info=data[id];
                        el.className=info.connected?'status-online':'status-offline';
                        el.textContent=info.connected?`✓ ${info.frames} frames`:'✗ Desconectado';
                    }
                });
            });
        },5000);
    </script>
</body>
</html>
"""

TEMPLATE_SETTINGS = """
<!DOCTYPE html>
<html>
<head>
    <title>Configurações</title>
    <meta charset="utf-8">
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{font-family:Arial;background:#1e3c72;color:white;min-height:100vh}
        .header{background:rgba(0,0,0,0.3);padding:20px;text-align:center}
        .header a{display:inline-block;background:#4CAF50;color:white;padding:10px 20px;border-radius:5px;text-decoration:none;margin-bottom:10px}
        .container{max-width:1200px;margin:0 auto;padding:20px}
        .settings-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(500px,1fr));gap:20px}
        .camera-form{background:rgba(255,255,255,0.1);border-radius:10px;padding:20px}
        .form-group{margin-bottom:12px}
        .form-group label{display:block;margin-bottom:5px;opacity:0.9}
        .form-group input{width:100%;padding:8px;border:1px solid rgba(255,255,255,0.2);border-radius:5px;background:rgba(255,255,255,0.1);color:white}
        .btn-group{display:flex;gap:10px;margin-top:15px}
        .btn{flex:1;padding:10px;border:none;border-radius:5px;cursor:pointer;font-weight:bold;color:white}
        .btn-save{background:#4CAF50}
        .btn-delete{background:#f44336}
        .msg{padding:10px;margin-top:10px;border-radius:5px;text-align:center;display:none}
        .msg.show{display:block}
        .success{background:rgba(76,175,80,0.3);color:#4CAF50}
        .error{background:rgba(244,67,54,0.3);color:#f44336}
    </style>
</head>
<body>
    <div class="header">
        <h1>⚙️ Configurações</h1>
        <a href="/">← Voltar</a>
    </div>
    <div class="container">
        <h2>📹 Câmeras</h2>
        <div class="settings-grid">
            {% for cam in cameras %}
            <div class="camera-form">
                <h3>{{ cam.name }}</h3>
                <form onsubmit="save(event,'{{ cam.id }}')">
                    <div class="form-group">
                        <label>Nome</label>
                        <input type="text" name="name" value="{{ cam.name }}">
                    </div>
                    <div class="form-group">
                        <label>IP</label>
                        <input type="text" name="ip" value="{{ cam.ip }}" required>
                    </div>
                    <div class="form-group">
                        <label>Porta</label>
                        <input type="number" name="port" value="{{ cam.port }}">
                    </div>
                    <div class="form-group">
                        <label>Usuário</label>
                        <input type="text" name="user" value="{{ cam.user }}">
                    </div>
                    <div class="form-group">
                        <label>Senha</label>
                        <input type="password" name="password" value="{{ cam.password }}">
                    </div>
                    <div class="btn-group">
                        <button type="submit" class="btn btn-save">💾 Salvar</button>
                        <button type="button" class="btn btn-delete" onclick="del('{{ cam.id }}')">🗑️ Deletar</button>
                    </div>
                    <div id="msg-{{ cam.id }}" class="msg"></div>
                </form>
            </div>
            {% endfor %}
        </div>
    </div>
    <script>
        function save(e,id){
            e.preventDefault();
            const form=e.target;
            const data={name:form.name.value,ip:form.ip.value,port:form.port.value,user:form.user.value,password:form.password.value};
            fetch(`/api/camera/${id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(r=>r.json()).then(d=>{
                const msg=document.getElementById(`msg-${id}`);
                if(d.success){msg.textContent='✓ Salvo!';msg.className='msg show success';setTimeout(()=>location.reload(),1500);}else{msg.textContent='✗ Erro';msg.className='msg show error';}
            });
        }
        function del(id){
            if(!confirm('Deletar?'))return;
            fetch(`/api/camera/${id}`,{method:'DELETE'}).then(()=>location.reload());
        }
    </script>
</body>
</html>
"""

# ====== MAIN ======

if __name__ == '__main__':
    logger.info("🚀 DVR Local iniciando...")
    cams = load_cameras_dict()
    logger.info(f"📹 Câmeras: {len(cams)}")
    logger.info("🌐 http://localhost:8000")
    app.run(host='0.0.0.0', port=8000, threaded=True, debug=False)
