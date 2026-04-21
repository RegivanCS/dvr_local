"""
DVR Local Agent — Interface Gráfica Local
Abre no navegador: http://localhost:5100

Uso simples (para leigos):
  - Dê duplo-clique em start_agent.bat
  - Preencha o endereço do DVR, usuário e senha
  - Clique em Conectar e Escanear
  - As câmeras aparecem automaticamente no DVR remoto com vídeo ao vivo

Internamente:
  - Roda um servidor Flask local (porta 5100)
  - Escaneia a rede local por câmeras
  - Faz login no DVR remoto e cadastra as câmeras encontradas
  - Relaya snapshots das câmeras locais para o DVR remoto em tempo real
"""
import socket
import threading
import time
import json
import os
import webbrowser
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, jsonify, render_template_string

# ── Config ────────────────────────────────────────────────────
CONFIG_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agent_config.json')
CAMERA_PORTS  = [80, 8080, 8899, 554, 8081, 8090]
CAM_KEYWORDS  = ['camera', 'video', 'stream', 'dvr', 'ipcam', 'webcam', 'snapshot', 'cgi-bin']
HTTP_HEADERS  = {'User-Agent': 'Mozilla/5.0 (DVR-Agent/1.0)'}
PORT          = 5100
# ─────────────────────────────────────────────────────────────

app = Flask(__name__)

# Estado global da aplicação
state = {
    'status': 'idle',      # idle | connecting | scanning | relaying | error
    'message': '',
    'cameras': [],         # câmeras encontradas
    'relays': {},          # cam_id -> threading.Event
    'dvr_session': None,
    'config': {},
    'log': [],
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
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def get_local_network():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = socket.gethostbyname(socket.gethostname())
    return ip, '.'.join(ip.split('.')[:-1])


def tcp_open(ip: str, port: int) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.3)
        ok = sock.connect_ex((ip, port)) == 0
        sock.close()
        return ok
    except Exception:
        return False


def detect_snapshot_path(ip: int, port: int, user: str, password: str) -> str:
    if port == 554:
        return 'rtsp://'
    paths = [
        # LAPI (Longse/Hikvision-like)
        '/LAPI/V1.0/Channel/0/Media/Video/ShotFrame',
        '/LAPI/V1.0/Channel/0/Media/JPEG/ShotFrame',
        '/LAPI/V1.0/Channel/0/Media/MainStream/ShotFrame',
        # Comuns
        '/snapshot.cgi', '/snapshot.jpg', '/image.jpg',
        '/tmpfs/auto.jpg', '/cgi-bin/snapshot.cgi',
        '/jpg/image.jpg', '/snap.jpg',
    ]
    from requests.auth import HTTPBasicAuth, HTTPDigestAuth
    auths = []
    if user:
        auths = [HTTPBasicAuth(user, password), HTTPDigestAuth(user, password)]
    else:
        auths = [None]
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
    # Nenhum path HTTP funcionou — tenta RTSP como fallback
    try:
        import cv2
        creds = f'{user}:{password}@' if user else ''
        cap = cv2.VideoCapture(f'rtsp://{creds}{ip}:554', cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
        ret, _ = cap.read()
        cap.release()
        if ret:
            return 'rtsp://'
    except Exception:
        pass
    return '/snapshot.cgi'


def check_camera(ip: str, port: int) -> dict | None:
    if port == 554:
        return {'ip': ip, 'port': port, 'server': 'RTSP', 'path': 'rtsp://'}
    try:
        url = f'http://{ip}:{port}'
        r = requests.get(url, timeout=2, headers=HTTP_HEADERS, allow_redirects=True)
        content = r.text.lower()
        server = r.headers.get('Server', '')
        is_cam = (
            any(kw in content for kw in CAM_KEYWORDS) or
            any(kw in server.lower() for kw in ['ipc', 'dvr', 'cam', 'hikvision', 'dahua', 'iscee']) or
            tcp_open(ip, 554)
        )
        if is_cam:
            return {'ip': ip, 'port': port, 'server': server or 'HTTP', 'path': '/snapshot.cgi'}
    except Exception:
        pass
    return None


def scan_network(cam_user: str, cam_password: str) -> list:
    local_ip, network = get_local_network()
    log(f'Escaneando rede {network}.0/24...')
    state['status'] = 'scanning'

    open_endpoints = []
    with ThreadPoolExecutor(max_workers=300) as ex:
        futs = {ex.submit(tcp_open, f'{network}.{i}', p): (f'{network}.{i}', p)
                for i in range(1, 255) for p in CAMERA_PORTS}
        for f in as_completed(futs):
            if f.result():
                open_endpoints.append(futs[f])

    log(f'{len(open_endpoints)} porta(s) abertas encontradas')

    cameras = []
    with ThreadPoolExecutor(max_workers=50) as ex:
        futs = [ex.submit(check_camera, ip, port) for ip, port in open_endpoints]
        for f in as_completed(futs):
            r = f.result()
            if r:
                # Detectar caminho real do snapshot
                r['path'] = detect_snapshot_path(r['ip'], r['port'], cam_user, cam_password)
                cameras.append(r)
                log(f'📹 Câmera encontrada: {r["ip"]}:{r["port"]} ({r["server"]})')

    log(f'Total: {len(cameras)} câmera(s) encontrada(s)')
    return cameras


def login_dvr(dvr_url: str, dvr_user: str, dvr_password: str):
    s = requests.Session()
    s.headers.update(HTTP_HEADERS)
    try:
        r = s.post(f'{dvr_url}/login',
                   data={'user': dvr_user, 'password': dvr_password, 'next': '/'},
                   timeout=10, allow_redirects=True)
        if '/login' in r.url:
            return None, 'Usuário ou senha incorretos.'
        return s, None
    except Exception as e:
        return None, f'Não foi possível conectar ao DVR: {e}'


def register_cameras(dvr_session, dvr_url: str, cameras: list,
                     cam_user: str, cam_password: str, cam_model: str) -> list:
    """Cadastra câmeras no DVR via /api/camera/add (skip_test, não depende de acesso às câmeras pelo servidor)."""
    # Limpa câmeras antigas primeiro
    try:
        r = dvr_session.get(f'{dvr_url}/api/cameras', timeout=10)
        existing = r.json() if r.ok else {}
        for cid in existing:
            dvr_session.post(f'{dvr_url}/api/camera/delete/{cid}', timeout=10)
        if existing:
            log(f'🧹 {len(existing)} câmera(s) antiga(s) removida(s)')
    except Exception as e:
        log(f'⚠️ Não foi possível limpar câmeras antigas: {e}')

    cam_ids = []
    registered = 0
    for cam in cameras:
        try:
            data = {
                'name': f'Câmera ({cam["ip"]})',
                'ip': cam['ip'],
                'port': cam['port'],
                'user': cam_user,
                'password': cam_password,
                'model': cam_model,
                'path': cam.get('path', '/snapshot.cgi'),
                'skip_test': 'true',
            }
            r = dvr_session.post(f'{dvr_url}/api/camera/add', data=data, timeout=15)
            result = r.json()
            if result.get('success'):
                cam_ids.append(result.get('cam_id'))
                registered += 1
        except Exception as e:
            log(f'Erro ao cadastrar {cam["ip"]}: {e}')

    log(f'Cadastradas: {registered} / {len(cameras)}')
    return cam_ids


def start_relay(cam_id: str, cam_ip: str, cam_port: int, cam_path: str,
                cam_user: str, cam_password: str, dvr_session, dvr_url: str):
    """Thread que faz polling na câmera local e envia snapshots ao DVR remoto."""
    if cam_id in state['relays']:
        state['relays'][cam_id].set()

    stop_ev = threading.Event()
    state['relays'][cam_id] = stop_ev

    if cam_path == 'rtsp://':
        def _loop_rtsp():
            import cv2
            push = f'{dvr_url}/api/agent/push_snapshot/{cam_id}'
            creds = f'{cam_user}:{cam_password}@' if cam_user else ''
            rtsp_url = f'rtsp://{creds}{cam_ip}:554'
            cap = None
            while not stop_ev.is_set():
                try:
                    if cap is None or not cap.isOpened():
                        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        ok, buf = cv2.imencode('.jpg', frame)
                        if ok:
                            dvr_session.post(push, data=bytes(buf),
                                             headers={'Content-Type': 'image/jpeg'}, timeout=5)
                    else:
                        if cap:
                            cap.release()
                        cap = None
                        stop_ev.wait(timeout=3)
                        continue
                except Exception:
                    pass
                stop_ev.wait(timeout=0.5)
            if cap:
                cap.release()
        threading.Thread(target=_loop_rtsp, daemon=True).start()
        log(f'Relay RTSP ativo: {cam_ip}:554 -> DVR')
        return

    def _loop():
        from requests.auth import HTTPBasicAuth, HTTPDigestAuth
        url   = f'http://{cam_ip}:{cam_port}{cam_path}'
        auth  = HTTPBasicAuth(cam_user, cam_password) if cam_user else None
        push  = f'{dvr_url}/api/agent/push_snapshot/{cam_id}'
        while not stop_ev.is_set():
            try:
                r = requests.get(url, auth=auth, timeout=5, headers=HTTP_HEADERS)
                if r.status_code == 401 and cam_user:
                    auth = HTTPDigestAuth(cam_user, cam_password)
                    r = requests.get(url, auth=auth, timeout=5, headers=HTTP_HEADERS)
                if r.status_code == 200 and len(r.content) > 500:
                    dvr_session.post(push, data=r.content,
                                     headers={'Content-Type': 'image/jpeg'}, timeout=5)
            except Exception:
                pass
            stop_ev.wait(timeout=2)

    threading.Thread(target=_loop, daemon=True).start()
    log(f'Relay HTTP ativo: {cam_ip}:{cam_port}{cam_path} -> DVR')


def stop_all_relays():
    for ev in state['relays'].values():
        ev.set()
    state['relays'].clear()


# ── Flask routes ──────────────────────────────────────────────

SETUP_HTML = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DVR Local Agent</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f0f1a;
    color: #e0e0e0;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 24px 16px;
  }
  .card {
    background: #1a1a2e;
    border-radius: 16px;
    padding: 32px;
    width: 100%;
    max-width: 520px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
  }
  h1 { font-size: 1.5rem; color: #7eb8f7; margin-bottom: 6px; }
  .subtitle { color: #888; font-size: 0.9rem; margin-bottom: 28px; }
  label { display: block; font-size: 0.85rem; color: #aaa; margin-bottom: 4px; margin-top: 14px; }
  input, select {
    width: 100%; padding: 10px 14px;
    background: #0f0f1a; border: 1px solid #333;
    border-radius: 8px; color: #e0e0e0; font-size: 0.95rem;
    outline: none;
  }
  input:focus, select:focus { border-color: #7eb8f7; }
  .section-title {
    font-size: 0.75rem; font-weight: 700; text-transform: uppercase;
    color: #555; letter-spacing: 1px; margin-top: 24px; margin-bottom: 4px;
    border-top: 1px solid #222; padding-top: 18px;
  }
  button {
    width: 100%; margin-top: 28px; padding: 14px;
    background: #7eb8f7; color: #0f0f1a; font-size: 1rem;
    font-weight: 700; border: none; border-radius: 10px;
    cursor: pointer; transition: background .2s;
  }
  button:hover { background: #a8d0ff; }
  button:disabled { background: #333; color: #666; cursor: default; }
  #status-area {
    margin-top: 28px; padding: 16px;
    background: #0f0f1a; border-radius: 10px;
    border: 1px solid #222; display: none;
  }
  #status-title { font-size: 0.85rem; color: #7eb8f7; font-weight: 700; margin-bottom: 10px; }
  #log-box { font-family: monospace; font-size: 0.78rem; color: #aaa;
             max-height: 200px; overflow-y: auto; line-height: 1.6; }
  .cameras-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 14px; margin-top: 20px; width: 100%; max-width: 860px;
  }
  .cam-card {
    background: #1a1a2e; border-radius: 12px; overflow: hidden;
    box-shadow: 0 4px 16px rgba(0,0,0,0.4);
  }
  .cam-card img { width: 100%; aspect-ratio: 16/9; object-fit: cover; background: #000; }
  .cam-info { padding: 10px 12px; }
  .cam-info h3 { font-size: 0.85rem; color: #e0e0e0; }
  .cam-info span { font-size: 0.75rem; color: #7eb8f7; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
         background: #2ecc71; margin-right: 6px; animation: pulse 1.5s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
</style>
</head>
<body>

<div class="card">
  <h1>🎥 DVR Local Agent</h1>
  <p class="subtitle">Configure uma vez. As câmeras aparecem automaticamente no DVR.</p>

  <div class="section-title">Servidor DVR</div>
  <label>Endereço do DVR</label>
  <input id="dvr_url" type="text" placeholder="https://dvr.regivan.tec.br" value="">
  <label>Usuário</label>
  <input id="dvr_user" type="text" placeholder="admin" value="">
  <label>Senha</label>
  <input id="dvr_pass" type="password" placeholder="••••••••">

  <div class="section-title">Câmeras (opcional)</div>
  <label>Usuário das câmeras</label>
  <input id="cam_user" type="text" placeholder="admin" value="admin">
  <label>Senha das câmeras</label>
  <input id="cam_pass" type="password" placeholder="Deixe vazio se não houver">
  <label>Modelo</label>
  <select id="cam_model">
    <option value="generic">Genérico / Desconhecido</option>
    <option value="iscee">ISCEE</option>
    <option value="hikvision">Hikvision</option>
    <option value="dahua">Dahua</option>
    <option value="intelbras">Intelbras</option>
  </select>

  <button id="btn-start" onclick="startAgent()">Conectar e Escanear Rede</button>

  <div id="status-area">
    <div id="status-title">⏳ Iniciando...</div>
    <div id="log-box"></div>
  </div>
</div>

<div class="cameras-grid" id="cameras-grid"></div>

<script>
const cfg = {{ config | tojson }};
if (cfg.dvr_url)   document.getElementById('dvr_url').value  = cfg.dvr_url;
if (cfg.dvr_user)  document.getElementById('dvr_user').value = cfg.dvr_user;
if (cfg.cam_user)  document.getElementById('cam_user').value = cfg.cam_user;
if (cfg.cam_model) document.getElementById('cam_model').value= cfg.cam_model;

let polling = null;

async function startAgent() {
  const payload = {
    dvr_url:   document.getElementById('dvr_url').value.trim(),
    dvr_user:  document.getElementById('dvr_user').value.trim(),
    dvr_pass:  document.getElementById('dvr_pass').value,
    cam_user:  document.getElementById('cam_user').value.trim(),
    cam_pass:  document.getElementById('cam_pass').value,
    cam_model: document.getElementById('cam_model').value,
  };
  if (!payload.dvr_url || !payload.dvr_user) {
    alert('Preencha o endereço do DVR e o usuário.');
    return;
  }
  document.getElementById('btn-start').disabled = true;
  document.getElementById('status-area').style.display = 'block';
  document.getElementById('log-box').textContent = '';
  document.getElementById('status-title').textContent = '⏳ Conectando...';

  const r = await fetch('/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  const data = await r.json();
  if (!data.ok) {
    document.getElementById('status-title').textContent = '❌ ' + data.error;
    document.getElementById('btn-start').disabled = false;
    return;
  }
  pollStatus();
}

function pollStatus() {
  if (polling) clearInterval(polling);
  polling = setInterval(async () => {
    const r = await fetch('/status');
    const d = await r.json();
    document.getElementById('log-box').textContent = d.log.join('\\n');
    document.getElementById('log-box').scrollTop = 9999;

    const icons = {idle:'💤', connecting:'🔗', scanning:'🔍', relaying:'📡', error:'❌'};
    const labels = {idle:'Aguardando', connecting:'Conectando ao DVR...', scanning:'Escaneando rede local...', relaying:'Ativo — transmitindo vídeo', error:'Erro'};
    document.getElementById('status-title').textContent = (icons[d.status]||'') + ' ' + (labels[d.status]||d.status);

    if (d.status === 'error') {
      document.getElementById('btn-start').disabled = false;
      clearInterval(polling);
    }
    if (d.cameras.length > 0) renderCameras(d.cameras, d.dvr_url);
  }, 1500);
}

function renderCameras(cameras, dvr_url) {
  const grid = document.getElementById('cameras-grid');
  grid.innerHTML = '';
  cameras.forEach(c => {
    const div = document.createElement('div');
    div.className = 'cam-card';
    const snapUrl = c.cam_id
      ? `${dvr_url}/api/camera/${c.cam_id}/snapshot_img`
      : '';
    div.innerHTML = `
      ${snapUrl ? `<img src="${snapUrl}" onerror="this.src='/static/no-signal.png'" id="img-${c.cam_id}">` : '<div style="aspect-ratio:16/9;background:#000"></div>'}
      <div class="cam-info">
        <h3><span class="dot"></span>${c.ip}:${c.port}</h3>
        <span>${c.server}</span>
      </div>`;
    grid.appendChild(div);
  });
  // Atualiza imagens a cada 2s
  cameras.forEach(c => {
    if (!c.cam_id) return;
    setInterval(() => {
      const img = document.getElementById('img-'+c.cam_id);
      if (img) img.src = `${dvr_url}/api/camera/${c.cam_id}/snapshot_img?t=${Date.now()}`;
    }, 2000);
  });
}

// Se já havia sessão ativa, retoma polling
fetch('/status').then(r=>r.json()).then(d=>{
  if (d.status !== 'idle') {
    document.getElementById('status-area').style.display = 'block';
    pollStatus();
  }
});
</script>
</body>
</html>
"""


@app.route('/')
def index():
    cfg = load_config()
    return render_template_string(SETUP_HTML, config=cfg)


@app.route('/start', methods=['POST'])
def start():
    data = request.get_json() or {}
    dvr_url   = data.get('dvr_url', '').rstrip('/')
    dvr_user  = data.get('dvr_user', '')
    dvr_pass  = data.get('dvr_pass', '')
    cam_user  = data.get('cam_user', 'admin')
    cam_pass  = data.get('cam_pass', '')
    cam_model = data.get('cam_model', 'generic')

    if not dvr_url or not dvr_user:
        return jsonify({'ok': False, 'error': 'DVR URL e usuário são obrigatórios.'})

    # Salvar config (sem senha — segurança)
    save_config({'dvr_url': dvr_url, 'dvr_user': dvr_user,
                 'cam_user': cam_user, 'cam_model': cam_model})

    stop_all_relays()
    state['status']   = 'connecting'
    state['message']  = ''
    state['cameras']  = []
    state['log']      = []
    state['dvr_session'] = None

    def _run():
        log('Conectando ao DVR remoto...')
        s, err = login_dvr(dvr_url, dvr_user, dvr_pass)
        if err:
            state['status']  = 'error'
            state['message'] = err
            log(f'❌ {err}')
            return

        state['dvr_session'] = s
        log(f'✓ Login OK como {dvr_user}')

        cameras = scan_network(cam_user, cam_pass)
        state['cameras'] = cameras

        if not cameras:
            state['status']  = 'error'
            state['message'] = 'Nenhuma câmera encontrada na rede local.'
            log('⚠️  Nenhuma câmera encontrada.')
            return

        log('Cadastrando câmeras no DVR...')
        cam_ids = register_cameras(s, dvr_url, cameras, cam_user, cam_pass, cam_model)

        # Enriquecer lista de câmeras com cam_id
        for i, cid in enumerate(cam_ids):
            if i < len(cameras):
                cameras[i]['cam_id'] = cid

        # Iniciar relay de snapshots
        state['status'] = 'relaying'
        for cam in cameras:
            cid = cam.get('cam_id')
            if not cid:
                continue
            start_relay(cid, cam['ip'], cam['port'],
                        cam['path'], cam_user, cam_pass, s, dvr_url)

        log(f'✅ Pronto! {len(cam_ids)} câmera(s) ao vivo no DVR.')

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'ok': True})


@app.route('/status')
def status():
    cfg = load_config()
    return jsonify({
        'status':   state['status'],
        'message':  state['message'],
        'cameras':  state['cameras'],
        'log':      state['log'][-30:],
        'dvr_url':  cfg.get('dvr_url', ''),
    })


if __name__ == '__main__':
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print('=' * 55)
    print('DVR Local Agent - Interface')
    print('   Abrindo navegador em http://localhost:5100')
    print('   Pressione Ctrl+C para encerrar')
    print('=' * 55)
    # Abre o browser após 1.5s (tempo para o servidor subir)
    threading.Timer(1.5, lambda: webbrowser.open('http://localhost:5100')).start()
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)
