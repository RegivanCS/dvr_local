from flask import Flask, Response, jsonify
import requests
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Descobrir câmeras automaticamente do Agent DVR
def get_agent_cameras():
    """Obtém lista de câmeras do Agent DVR"""
    try:
        response = requests.get("http://localhost:8090/command.cgi?cmd=getObjects", timeout=3)
        if response.status_code == 200:
            data = response.json()
            cameras = {}
            for obj in data.get('objectList', []):
                if obj.get('typeID') == 2:  # typeID 2 = câmera
                    cam_id = obj['id']
                    cameras[cam_id] = {
                        'id': cam_id,
                        'name': obj.get('name', f'Camera {cam_id}'),
                        'url': f"http://localhost:8090/grab.jpg?oid={cam_id}"
                    }
            return cameras
    except Exception as e:
        logger.error(f"Erro ao obter câmeras: {e}")
    return {}

# Carregar câmeras dinamicamente
agent_cameras = get_agent_cameras()
logger.info(f"Câmeras detectadas: {len(agent_cameras)}")
for cam_id, info in agent_cameras.items():
    logger.info(f"  - ID {cam_id}: {info['name']}")

@app.route('/')
def index():
    """Página inicial com lista de câmeras"""
    # Gerar HTML dinâmico baseado nas câmeras detectadas
    camera_boxes = ""
    for cam_id, info in agent_cameras.items():
        camera_boxes += f"""
            <div class="camera-box" onclick="openFullscreen({cam_id})">
                <h3>📹 {info['name']}</h3>
                <img src="/camera/{cam_id}" alt="{info['name']}" id="cam-{cam_id}">
                <div class="status">Camera ID: {cam_id} - Clique para tela cheia</div>
            </div>
        """
    
    if not camera_boxes:
        camera_boxes = """
            <div style="grid-column: 1 / -1; text-align: center; color: #ff6b6b;">
                <h2>⚠️ Nenhuma câmera detectada</h2>
                <p>Verifique se o Agent DVR está rodando em localhost:8090</p>
            </div>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>DVR Local - Câmeras</title>
        <meta charset="utf-8">
        <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
        <meta http-equiv="Pragma" content="no-cache">
        <meta http-equiv="Expires" content="0">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            
            body {{ 
                font-family: 'Segoe UI', Arial, sans-serif;
                background: #0d1117;
                color: white;
                overflow-x: hidden;
            }}
            
            .header {{
                background: linear-gradient(135deg, #1a1f2e 0%, #2d3748 100%);
                padding: 20px;
                text-align: center;
                box-shadow: 0 4px 6px rgba(0,0,0,0.3);
            }}
            
            h1 {{ 
                color: #fff;
                font-size: 2em;
                text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
            }}
            
            .cameras {{ 
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
                gap: 25px;
                max-width: 1600px;
                margin: 30px auto;
                padding: 0 20px;
            }}
            
            .camera-box {{ 
                background: linear-gradient(145deg, #1f2937 0%, #374151 100%);
                padding: 20px;
                border-radius: 12px;
                box-shadow: 0 8px 16px rgba(0,0,0,0.4);
                cursor: pointer;
                transition: all 0.3s ease;
                border: 2px solid transparent;
            }}
            
            .camera-box:hover {{
                transform: translateY(-5px);
                box-shadow: 0 12px 24px rgba(76, 175, 80, 0.3);
                border-color: #4CAF50;
            }}
            
            .camera-box h3 {{ 
                margin: 0 0 15px 0;
                color: #4CAF50;
                font-size: 1.3em;
                text-shadow: 1px 1px 2px rgba(0,0,0,0.5);
            }}
            
            .camera-box img {{ 
                width: 100%;
                height: auto;
                min-height: 300px;
                border-radius: 8px;
                display: block;
                background: #000;
                border: 2px solid #2d3748;
            }}
            
            .status {{
                font-size: 0.85em;
                color: #9ca3af;
                margin-top: 10px;
                text-align: center;
            }}
            
            /* Modo tela cheia */
            .fullscreen {{
                display: none;
                position: fixed;
                top: 0;
                left: 0;
                width: 100vw;
                height: 100vh;
                background: #000;
                z-index: 9999;
                flex-direction: column;
            }}
            
            .fullscreen.active {{
                display: flex;
            }}
            
            .fullscreen-header {{
                background: rgba(0, 0, 0, 0.8);
                padding: 15px 20px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            
            .fullscreen-title {{
                color: #4CAF50;
                font-size: 1.5em;
            }}
            
            .fullscreen-close {{
                background: #ff4757;
                color: white;
                border: none;
                padding: 10px 25px;
                border-radius: 6px;
                font-size: 1em;
                cursor: pointer;
                transition: all 0.3s;
            }}
            
            .fullscreen-close:hover {{
                background: #ff3838;
                transform: scale(1.05);
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
                .cameras {{
                    grid-template-columns: 1fr;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🎥 DVR Local - Sistema de Câmeras</h1>
            <p style="color: #9ca3af; margin-top: 10px;">Clique em uma câmera para visualizar em tela cheia</p>
        </div>
        
        <div class="cameras">
            {camera_boxes}
        </div>
        
        <!-- Tela cheia -->
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
            
            function openFullscreen(cameraId) {{
                currentCameraId = cameraId;
                const fullscreenDiv = document.getElementById('fullscreen');
                const fullscreenImg = document.getElementById('fullscreen-img');
                const fullscreenTitle = document.getElementById('fullscreen-title');
                
                // Obter nome da câmera
                const cameraNames = {{{', '.join([f'{cam_id}: "{info["name"]}"' for cam_id, info in agent_cameras.items()])}}};
                
                fullscreenTitle.textContent = '📹 ' + (cameraNames[cameraId] || 'Câmera ' + cameraId);
                fullscreenImg.src = '/camera/' + cameraId + '?t=' + new Date().getTime();
                fullscreenDiv.classList.add('active');
            }}
            
            function closeFullscreen() {{
                document.getElementById('fullscreen').classList.remove('active');
                currentCameraId = null;
            }}
            
            // Fechar com ESC
            document.addEventListener('keydown', function(e) {{
                if (e.key === 'Escape') {{
                    closeFullscreen();
                }}
            }});
            
            // Atualizar imagens a cada 30 segundos para evitar cache
            setInterval(function() {{
                const imgs = document.querySelectorAll('.camera-box img');
                imgs.forEach(function(img) {{
                    const src = img.src.split('?')[0];
                    img.src = src + '?t=' + new Date().getTime();
                }});
                
                // Atualizar tela cheia se estiver aberta
                if (currentCameraId) {{
                    const fullscreenImg = document.getElementById('fullscreen-img');
                    fullscreenImg.src = '/camera/' + currentCameraId + '?t=' + new Date().getTime();
                }}
            }}, 30000);
        </script>
    </body>
    </html>
    """
    return html

def gen_frames_from_agent(camera_id):
    """Gera frames do Agent DVR continuamente"""
    # Agent DVR usa oid começando em 1
    url = f"http://localhost:8090/grab.jpg?oid={camera_id}"
    
    logger.info(f"Iniciando stream da Câmera {camera_id} (URL: {url})")
    
    import time
    
    frame_count = 0
    
    while True:
        try:
            # Capturar frame atual
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                frame_count += 1
                if frame_count % 30 == 0:  # Log a cada 30 frames
                    logger.info(f"Câmera {camera_id}: {frame_count} frames enviados")
                
                # Enviar frame como MJPEG
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + response.content + b'\r\n')
                
                # Pequeno delay para ~10 FPS (pode ajustar)
                time.sleep(0.1)
            else:
                logger.warning(f"Câmera {camera_id}: Status {response.status_code}")
                time.sleep(0.5)
                
        except Exception as e:
            logger.error(f"Erro na câmera {camera_id}: {e}")
            time.sleep(0.5)  # Aguardar antes de tentar novamente

@app.route('/camera/<int:cam_id>')
def video(cam_id):
    if cam_id not in agent_cameras:
        return "Câmera não encontrada", 404
    
    logger.info(f"Requisição para câmera {cam_id} ({agent_cameras[cam_id]['name']})")
    return Response(
        gen_frames_from_agent(cam_id),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/api/cameras')
def api_cameras():
    """API para listar câmeras disponíveis"""
    return jsonify(agent_cameras)

if __name__ == "__main__":
    logger.info("Iniciando servidor na porta 8000...")
    logger.info("Acesse: http://localhost:8000/camera/1")
    app.run(host='0.0.0.0', port=8000, threaded=True)