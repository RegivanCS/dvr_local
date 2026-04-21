"""
DVR Local — Sistema 100% independente
Abre no navegador: http://localhost:5100

- Escaneia a rede local por câmeras IP
- Exibe o vídeo ao vivo diretamente (sem DVR remoto)
- Suporta câmeras HTTP (Basic/Digest) e RTSP (via OpenCV)
- Duplo-clique em start_dvr_local.bat para iniciar
"""
import os, json, socket, threading, time, webbrowser
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, Response, jsonify, render_template_string, request

# ── Configuração ──────────────────────────────────────────────
PORT         = 5100
CONFIG_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dvr_local_config.json')
CAMERA_PORTS = [80, 8080, 8899, 554, 8081, 8090]
CAM_KEYWORDS = ['camera', 'video', 'stream', 'dvr', 'ipcam', 'webcam', 'snapshot', 'cgi-bin']
HTTP_HEADERS = {'User-Agent': 'Mozilla/5.0 (DVR-Local/1.0)'}
# ─────────────────────────────────────────────────────────────

app = Flask(__name__)

state = {
    'status': 'idle',   # idle | scanning | ready | error
    'log': [],
    'cameras': [],      # lista de dicts com ip, port, path, user, pass, cam_id
    'streams': {},      # cam_id -> threading.Event (stop)
}


def log(msg: str):
    ts = time.strftime('%H:%M:%S')
    entry = f'[{ts}] {msg}'
    print(entry)
    state['log'].append(entry)
    if len(state['log']) > 100:
        state['log'].pop(0)


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def get_network_prefix() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return '.'.join(ip.split('.')[:3])
    except Exception:
        return '192.168.0'


def tcp_open(ip: str, port: int) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.5)
        ok = sock.connect_ex((ip, port)) == 0
        sock.close()
        return ok
    except Exception:
        return False


def detect_snapshot_path(ip: str, port: int, user: str, password: str) -> str:
    """Detecta o path de snapshot da câmera. Retorna 'rtsp://' para câmeras RTSP-only."""
    if port == 554:
        return 'rtsp://'

    from requests.auth import HTTPBasicAuth, HTTPDigestAuth
    paths = [
        '/LAPI/V1.0/Channel/0/Media/Video/ShotFrame',
        '/LAPI/V1.0/Channel/0/Media/JPEG/ShotFrame',
        '/LAPI/V1.0/Channel/0/Media/MainStream/ShotFrame',
        '/snapshot.cgi', '/snapshot.jpg', '/image.jpg',
        '/tmpfs/auto.jpg', '/cgi-bin/snapshot.cgi',
        '/jpg/image.jpg', '/snap.jpg',
    ]
    auths = [HTTPBasicAuth(user, password), HTTPDigestAuth(user, password)] if user else [None]
    for path in paths:
        for auth in auths:
            try:
                r = requests.get(f'http://{ip}:{port}{path}', auth=auth,
                                 timeout=3, headers=HTTP_HEADERS)
                if r.status_code == 200 and len(r.content) > 500:
                    ct = r.headers.get('Content-Type', '')
                    if 'image' in ct or r.content[:2] == b'\xff\xd8':
                        return path
            except Exception:
                pass

    # Fallback: testa RTSP via OpenCV
    try:
        import cv2
        creds = f'{user}:{password}@' if user else ''
        cap = cv2.VideoCapture(f'rtsp://{creds}{ip}:554', cv2.CAP_FFMPEG)
        ret, _ = cap.read()
        cap.release()
        if ret:
            return 'rtsp://'
    except Exception:
        pass

    return ''  # não encontrou


def check_camera(ip: str, port: int) -> dict | None:
    """Verifica se o endpoint é uma câmera. Retorna dict ou None."""
    if port == 554:
        if tcp_open(ip, 554):
            return {'ip': ip, 'port': 554, 'server': 'RTSP'}
        return None
    try:
        r = requests.get(f'http://{ip}:{port}', timeout=2,
                         headers=HTTP_HEADERS, allow_redirects=True)
        content = r.text.lower()
        server = r.headers.get('Server', '')
        is_cam = (
            any(kw in content for kw in CAM_KEYWORDS) or
            any(kw in server.lower() for kw in ['ipc', 'dvr', 'cam', 'hikvision', 'dahua', 'iscee', 'nvr']) or
            tcp_open(ip, 554)
        )
        if is_cam:
            return {'ip': ip, 'port': port, 'server': server or 'HTTP'}
    except Exception:
        pass
    return None


def scan_and_configure(cam_user: str, cam_pass: str) -> list:
    prefix = get_network_prefix()
    log(f'Escaneando {prefix}.0/24 ...')
    state['status'] = 'scanning'

    # 1. Descobrir portas abertas
    open_eps = []
    with ThreadPoolExecutor(max_workers=400) as ex:
        futs = {ex.submit(tcp_open, f'{prefix}.{i}', p): (f'{prefix}.{i}', p)
                for i in range(1, 255) for p in CAMERA_PORTS}
        for f in as_completed(futs):
            if f.result():
                open_eps.append(futs[f])

    log(f'{len(open_eps)} porta(s) abertas')

    # 2. Identificar câmeras — deduplicar por IP (mesma câmera, vários ports)
    found: dict[str, dict] = {}  # ip -> melhor candidate
    with ThreadPoolExecutor(max_workers=60) as ex:
        futs = [ex.submit(check_camera, ip, port) for ip, port in open_eps]
        for f in as_completed(futs):
            r = f.result()
            if r:
                ip = r['ip']
                # Preferir HTTP sobre RTSP alone (mais info)
                if ip not in found or found[ip]['port'] == 554:
                    found[ip] = r
                log(f'Candidato: {r["ip"]}:{r["port"]} ({r["server"]})')

    cameras = []
    for cam_id, cam in enumerate(found.values()):
        log(f'Identificando camera {cam["ip"]}:{cam["port"]} ...')
        path = detect_snapshot_path(cam['ip'], cam['port'], cam_user, cam_pass)
        if path == '':
            log(f'  -> sem path valido, ignorando')
            continue
        cam['path']     = path
        cam['user']     = cam_user
        cam['password'] = cam_pass
        cam['cam_id']   = str(cam_id)
        cameras.append(cam)
        log(f'  -> OK  path={path}')

    log(f'Total: {len(cameras)} camera(s) configurada(s)')
    return cameras


# ── Streaming de vídeo ───────────────────────────────────────

def _gen_rtsp(cam: dict):
    """Generator MJPEG via RTSP + OpenCV."""
    import cv2
    user     = cam.get('user', '')
    password = cam.get('password', '')
    ip       = cam['ip']
    creds    = f'{user}:{password}@' if user else ''
    rtsp_url = f'rtsp://{creds}{ip}:554'
    cap      = None
    errors   = 0

    while True:
        try:
            if cap is None or not cap.isOpened():
                cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            ret, frame = cap.read()
            if ret and frame is not None:
                errors = 0
                ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ok:
                    yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
                           + bytes(buf) + b'\r\n')
                time.sleep(0.08)
            else:
                errors += 1
                if cap:
                    cap.release()
                cap = None
                if errors > 5:
                    return
                time.sleep(2)
        except GeneratorExit:
            if cap:
                cap.release()
            return
        except Exception as e:
            errors += 1
            if errors > 5:
                return
            time.sleep(1)


def _gen_http(cam: dict):
    """Generator MJPEG via HTTP snapshot polling."""
    from requests.auth import HTTPBasicAuth, HTTPDigestAuth
    ip   = cam['ip']
    port = cam['port']
    path = cam['path']
    user = cam.get('user', '')
    pw   = cam.get('password', '')
    url  = f'http://{ip}:{port}{path}'
    auth = HTTPBasicAuth(user, pw) if user else None
    errors = 0

    while True:
        try:
            r = requests.get(url, auth=auth, timeout=6, headers=HTTP_HEADERS)
            if r.status_code == 401 and user:
                auth = HTTPDigestAuth(user, pw)
                r = requests.get(url, auth=auth, timeout=6, headers=HTTP_HEADERS)
            if r.status_code == 200 and len(r.content) > 500:
                errors = 0
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
                       + r.content + b'\r\n')
                time.sleep(0.15)
            else:
                errors += 1
                if errors > 10:
                    return
                time.sleep(0.5)
        except GeneratorExit:
            return
        except Exception:
            errors += 1
            if errors > 10:
                return
            time.sleep(1)


@app.route('/video/<cam_id>')
def video_feed(cam_id: str):
    cam = next((c for c in state['cameras'] if c['cam_id'] == cam_id), None)
    if cam is None:
        return Response(status=404)

    if cam['path'] == 'rtsp://':
        gen = _gen_rtsp(cam)
    else:
        gen = _gen_http(cam)

    return Response(gen, mimetype='multipart/x-mixed-replace; boundary=frame')


# ── API ──────────────────────────────────────────────────────

@app.route('/api/scan', methods=['POST'])
def api_scan():
    data      = request.get_json() or {}
    cam_user  = data.get('cam_user', 'admin')
    cam_pass  = data.get('cam_pass', '')

    save_config({'cam_user': cam_user})

    state['log']     = []
    state['cameras'] = []
    state['status']  = 'scanning'

    def _run():
        try:
            state['cameras'] = scan_and_configure(cam_user, cam_pass)
            state['status']  = 'ready' if state['cameras'] else 'error'
            if not state['cameras']:
                log('Nenhuma camera encontrada.')
        except Exception as e:
            log(f'Erro: {e}')
            state['status'] = 'error'

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'ok': True})


@app.route('/api/status')
def api_status():
    return jsonify({
        'status':  state['status'],
        'cameras': state['cameras'],
        'log':     state['log'][-40:],
    })


@app.route('/api/config')
def api_config():
    return jsonify(load_config())


# ── Interface HTML ────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DVR Local</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#0d0d0d;color:#e0e0e0;min-height:100vh;padding:20px 16px}
  h1{font-size:1.4rem;color:#7eb8f7;margin-bottom:4px}
  .sub{color:#666;font-size:.85rem;margin-bottom:24px}
  .row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;margin-bottom:20px}
  .field{display:flex;flex-direction:column;gap:4px;flex:1;min-width:140px}
  label{font-size:.8rem;color:#888}
  input{padding:9px 12px;background:#1a1a1a;border:1px solid #2a2a2a;
        border-radius:7px;color:#e0e0e0;font-size:.9rem;outline:none}
  input:focus{border-color:#7eb8f7}
  button{padding:9px 22px;background:#7eb8f7;color:#0d0d0d;border:none;
         border-radius:7px;font-weight:700;cursor:pointer;white-space:nowrap}
  button:disabled{background:#2a2a2a;color:#555}
  button:hover:not(:disabled){background:#a8d0ff}
  #log{background:#111;border:1px solid #1e1e1e;border-radius:8px;
       padding:12px;font-family:monospace;font-size:.75rem;color:#aaa;
       max-height:160px;overflow-y:auto;white-space:pre-wrap;margin-bottom:24px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}
  .cam{background:#111;border-radius:10px;overflow:hidden;border:1px solid #1e1e1e}
  .cam img{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:#000}
  .cam-info{padding:10px 12px}
  .cam-info h3{font-size:.85rem;color:#e0e0e0}
  .cam-info small{color:#7eb8f7;font-size:.75rem}
  .badge{display:inline-block;border-radius:20px;font-size:.7rem;padding:2px 8px;
         background:#1e3a1e;color:#4ade80;margin-right:6px}
  .status-bar{display:flex;align-items:center;gap:10px;margin-bottom:16px;
              font-size:.85rem;color:#888}
  .dot{width:8px;height:8px;border-radius:50%;background:#4ade80;flex-shrink:0}
  .dot.idle{background:#555}
  .dot.scanning{background:#f5a623;animation:pulse 0.8s infinite}
  .dot.error{background:#e74c3c}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
</style>
</head>
<body>

<h1>DVR Local</h1>
<p class="sub">Sistema de cameras independente — sem internet necessaria</p>

<div class="row">
  <div class="field">
    <label>Usuario das cameras</label>
    <input id="cam_user" value="admin" placeholder="admin">
  </div>
  <div class="field">
    <label>Senha das cameras</label>
    <input id="cam_pass" type="password" placeholder="Deixe vazio se nao houver">
  </div>
  <button id="btn-scan" onclick="scan()">Escanear Rede</button>
</div>

<div id="log">Aguardando...</div>

<div class="status-bar">
  <div class="dot idle" id="dot"></div>
  <span id="status-text">Aguardando</span>
</div>

<div class="grid" id="grid"></div>

<script>
let pollTimer = null;

async function scan() {
  document.getElementById('btn-scan').disabled = true;
  document.getElementById('log').textContent = '';
  document.getElementById('grid').innerHTML = '';
  setStatus('scanning', 'Escaneando rede...');

  await fetch('/api/scan', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      cam_user: document.getElementById('cam_user').value.trim(),
      cam_pass: document.getElementById('cam_pass').value,
    }),
  });
  startPolling();
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const d = await fetch('/api/status').then(r => r.json());
    document.getElementById('log').textContent = d.log.join('\n');
    document.getElementById('log').scrollTop = 99999;

    if (d.status === 'scanning') {
      setStatus('scanning', 'Escaneando...');
    } else if (d.status === 'ready') {
      setStatus('ready', `${d.cameras.length} camera(s) ao vivo`);
      document.getElementById('btn-scan').disabled = false;
      clearInterval(pollTimer);
      renderCameras(d.cameras);
    } else if (d.status === 'error') {
      setStatus('error', 'Nenhuma camera encontrada');
      document.getElementById('btn-scan').disabled = false;
      clearInterval(pollTimer);
    }
  }, 1200);
}

function setStatus(type, text) {
  const dot = document.getElementById('dot');
  dot.className = 'dot ' + type;
  document.getElementById('status-text').textContent = text;
}

function renderCameras(cameras) {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  cameras.forEach(c => {
    const d = document.createElement('div');
    d.className = 'cam';
    const proto = c.path === 'rtsp://' ? 'RTSP' : 'HTTP';
    d.innerHTML = `
      <img src="/video/${c.cam_id}" alt="Camera ${c.ip}"
           onerror="this.alt='Sem sinal';this.style.background='#1a1a1a'">
      <div class="cam-info">
        <h3>${c.ip}:${c.port}</h3>
        <small>
          <span class="badge">${proto}</span>
          ${c.server || ''}
        </small>
      </div>`;
    grid.appendChild(d);
  });
}

// Ao abrir a pagina, verifica se ha cameras ja configuradas
fetch('/api/status').then(r=>r.json()).then(d=>{
  fetch('/api/config').then(r=>r.json()).then(cfg=>{
    if (cfg.cam_user) document.getElementById('cam_user').value = cfg.cam_user;
  });
  if (d.cameras && d.cameras.length > 0) {
    setStatus('ready', `${d.cameras.length} camera(s) ao vivo`);
    renderCameras(d.cameras);
  }
});
</script>
</body>
</html>
"""


@app.route('/')
def index():
    return HTML


# ── Inicialização ─────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print('=' * 50)
    print('DVR Local - Sistema de cameras independente')
    print(f'  Acesse: http://localhost:{PORT}')
    print('  Ctrl+C para encerrar')
    print('=' * 50)
    threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{PORT}')).start()
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False, threaded=True)
