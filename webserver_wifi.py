from flask import Flask, Response, render_template_string
import requests
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Câmeras ISCEE descobertas na rede WiFi
network_cameras = {
    1: {
        'name': 'ISCEE Camera 1 (192.168.1.3)',
        'ip': '192.168.1.3',
        'port': 80,
        'user': 'admin',
        'password': 'Herb1745@'
    },
    2: {
        'name': 'ISCEE Camera 2 (192.168.1.10)',
        'ip': '192.168.1.10',
        'port': 80,
        'user': 'admin',
        'password': 'Herb1745@'
    }
}

@app.route('/')
def index():
    """Página inicial com câmeras diretas da rede"""
    camera_boxes = ""
    for cam_id, info in network_cameras.items():
        camera_boxes += f"""
            <div class="camera-box" onclick="openFullscreen({cam_id})">
                <h3>📹 {info['name']}</h3>
                <img src="/camera/{cam_id}" alt="{info['name']}" id="cam-{cam_id}">
                <div class="status">IP: {info['ip']} - Clique para tela cheia</div>
            </div>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>DVR WiFi - Câmeras Diretas</title>
        <meta charset="utf-8">
        <meta http-equiv="Cache-Control" content="no-cache">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ 
                font-family: 'Segoe UI', Arial; 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                min-height: 100vh;
            }}
            .header {{
                background: rgba(0, 0, 0, 0.3);
                padding: 25px;
                text-align: center;
                backdrop-filter: blur(10px);
            }}
            h1 {{ 
                font-size: 2.5em;
                text-shadow: 3px 3px 6px rgba(0,0,0,0.5);
            }}
            .cameras {{ 
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(550px, 1fr));
                gap: 30px;
                max-width: 1800px;
                margin: 40px auto;
                padding: 0 30px;
            }}
            .camera-box {{ 
                background: rgba(255, 255, 255, 0.15);
                padding: 25px;
                border-radius: 15px;
                backdrop-filter: blur(10px);
                box-shadow: 0 8px 32px rgba(0,0,0,0.3);
                cursor: pointer;
                transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
                border: 2px solid rgba(255, 255, 255, 0.2);
            }}
            .camera-box:hover {{
                transform: translateY(-10px) scale(1.02);
                box-shadow: 0 15px 40px rgba(0,0,0,0.4);
                border-color: rgba(255, 255, 255, 0.4);
            }}
            .camera-box h3 {{ 
                margin: 0 0 15px 0;
                font-size: 1.4em;
                text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
            }}
            .camera-box img {{ 
                width: 100%;
                height: auto;
                min-height: 350px;
                border-radius: 10px;
                background: #000;
                border: 3px solid rgba(255, 255, 255, 0.1);
                box-shadow: 0 4px 15px rgba(0,0,0,0.3);
            }}
            .status {{
                font-size: 0.9em;
                margin-top: 12px;
                text-align: center;
                opacity: 0.9;
            }}
            .fullscreen {{
                display: none;
                position: fixed;
                top: 0; left: 0;
                width: 100vw; height: 100vh;
                background: #000;
                z-index: 9999;
                flex-direction: column;
            }}
            .fullscreen.active {{ display: flex; }}
            .fullscreen-header {{
                background: linear-gradient(135deg, rgba(102, 126, 234, 0.9), rgba(118, 75, 162, 0.9));
                padding: 20px 30px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .fullscreen-title {{ font-size: 1.8em; }}
            .fullscreen-close {{
                background: #e74c3c;
                color: white;
                border: none;
                padding: 12px 30px;
                border-radius: 8px;
                font-size: 1.1em;
                cursor: pointer;
                transition: all 0.3s;
            }}
            .fullscreen-close:hover {{
                background: #c0392b;
                transform: scale(1.1);
            }}
            .fullscreen-content {{
                flex: 1;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }}
            .fullscreen-content img {{
                max-width: 100%;
                max-height: 100%;
                object-fit: contain;
            }}
            @media (max-width: 768px) {{
                .cameras {{ grid-template-columns: 1fr; }}
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>📡 DVR WiFi - Câmeras da Rede Local</h1>
            <p style="margin-top: 10px; opacity: 0.9;">Câmeras ISCEE direto da rede • Clique para tela cheia</p>
        </div>
        <div class="cameras">{camera_boxes}</div>
        <div id="fullscreen" class="fullscreen">
            <div class="fullscreen-header">
                <div class="fullscreen-title" id="fullscreen-title">Câmera</div>
                <button class="fullscreen-close" onclick="closeFullscreen()">✕ Fechar</button>
            </div>
            <div class="fullscreen-content">
                <img id="fullscreen-img" src="" alt="Camera fullscreen">
            </div>
        </div>
        <script>
            let currentCameraId = null;
            const cameraNames = {{{', '.join([f'{k}: "{v["name"]}"' for k, v in network_cameras.items()])}}};
            
            function openFullscreen(cameraId) {{
                currentCameraId = cameraId;
                document.getElementById('fullscreen-title').textContent = '📹 ' + cameraNames[cameraId];
                document.getElementById('fullscreen-img').src = '/camera/' + cameraId + '?t=' + Date.now();
                document.getElementById('fullscreen').classList.add('active');
            }}
            function closeFullscreen() {{
                document.getElementById('fullscreen').classList.remove('active');
                currentCameraId = null;
            }}
            document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeFullscreen(); }});
            
            // Auto-refresh a cada 30s
            setInterval(() => {{
                document.querySelectorAll('.camera-box img').forEach(img => {{
                    img.src = img.src.split('?')[0] + '?t=' + Date.now();
                }});
                if (currentCameraId) {{
                    document.getElementById('fullscreen-img').src = '/camera/' + currentCameraId + '?t=' + Date.now();
                }}
            }}, 30000);
        </script>
    </body>
    </html>
    """
    return html

def gen_frames_from_iscee(camera_id):
    """Gera frames de câmera ISCEE na rede"""
    cam_info = network_cameras.get(camera_id)
    if not cam_info:
        return
    
    # Testar diferentes paths comuns de câmeras ISCEE
    paths = [
        '/snapshot.cgi',
        '/tmpfs/auto.jpg',
        '/cgi-bin/snapshot.cgi',
        '/image.jpg',
        '/snap.jpg'
    ]
    
    url = None
    for path in paths:
        test_url = f"http://{cam_info['ip']}:{cam_info['port']}{path}"
        try:
            response = requests.get(
                test_url,
                auth=(cam_info['user'], cam_info['password']),
                timeout=2
            )
            if response.status_code == 200:
                url = test_url
                logger.info(f"Câmera {camera_id}: Path encontrado {path}")
                break
        except:
            continue
    
    if not url:
        logger.error(f"Câmera {camera_id}: Nenhum path funcional encontrado")
        return
    
    frame_count = 0
    while True:
        try:
            response = requests.get(url, auth=(cam_info['user'], cam_info['password']), timeout=5)
            if response.status_code == 200:
                frame_count += 1
                if frame_count % 30 == 0:
                    logger.info(f"Câmera {camera_id} ({cam_info['ip']}): {frame_count} frames")
                
                yield (b'--frame\\r\\n'
                       b'Content-Type: image/jpeg\\r\\n\\r\\n' + response.content + b'\\r\\n')
                time.sleep(0.1)  # ~10 FPS
            else:
                time.sleep(0.5)
        except Exception as e:
            logger.error(f"Erro câmera {camera_id}: {e}")
            time.sleep(1)

@app.route('/camera/<int:cam_id>')
def video(cam_id):
    if cam_id not in network_cameras:
        return "Câmera não encontrada", 404
    
    return Response(
        gen_frames_from_iscee(cam_id),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

if __name__ == "__main__":
    logger.info("🚀 Servidor DVR WiFi iniciando...")
    logger.info(f"📡 {len(network_cameras)} câmeras ISCEE na rede")
    logger.info("🌐 Acesse: http://localhost:9000/")
    app.run(host='0.0.0.0', port=9000, threaded=True)
