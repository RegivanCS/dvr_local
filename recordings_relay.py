"""
Recordings Relay — serve as gravações locais via HTTP e expõe ao DVR remoto.

Fluxo:
  1. Inicia servidor Flask local (porta 8290) servindo recordings/
  2. Cria tunnel Cloudflare para expor publicamente
  3. Registra a URL no DVR remoto como recordings_tunnel_url
  4. O DVR em /recordings passa a redirecionar para este servidor

Uso: python recordings_relay.py
Requisito: pip install flask requests
"""

import os
import re
import subprocess
import threading
import time
import shutil
from datetime import datetime
from flask import Flask, send_file, render_template_string, abort

# ─────────────────────────────────────────────
DVR_URL      = 'https://dvr.regivan.tec.br'
DVR_USER     = 'admin'
DVR_PASSWORD = '!Rede!123'
PORT         = 8290
# ─────────────────────────────────────────────

_APP_DIR       = os.path.dirname(os.path.abspath(__file__))
RECORDINGS_DIR = os.path.join(_APP_DIR, 'recordings')

app = Flask(__name__)

TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gravações – DVR Local</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',Arial,sans-serif;background:#0f1923;color:#e0e0e0;min-height:100vh}
a{color:inherit;text-decoration:none}
.topbar{background:#1a2533;padding:14px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;border-bottom:2px solid #263548}
.topbar h1{font-size:1.1em;color:#fff}
.btn{padding:7px 16px;border-radius:6px;font-size:.85em;font-weight:600;cursor:pointer;border:none;display:inline-block;transition:opacity .2s;background:#2196F3;color:#fff}
.btn:hover{opacity:.85}
.wrap{max-width:1280px;margin:0 auto;padding:24px 16px}
.cam-tabs{display:flex;gap:8px;margin-bottom:24px;flex-wrap:wrap}
.cam-tab{padding:8px 20px;border-radius:20px;background:#1a2533;border:2px solid #263548;cursor:pointer;font-size:.9em;transition:all .2s}
.cam-tab.active{background:#2196F3;border-color:#2196F3;color:#fff}
.date-group{margin-bottom:32px}
.date-label{font-size:.8em;font-weight:700;color:#90a4ae;text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px;padding-left:4px}
.timeline{position:relative;padding-left:56px}
.timeline::before{content:'';position:absolute;left:20px;top:0;bottom:0;width:2px;background:#263548}
.hour-block{margin-bottom:18px;position:relative}
.hour-pin{position:absolute;left:-48px;width:26px;height:26px;border-radius:50%;background:#263548;border:2px solid #37474f;display:flex;align-items:center;justify-content:center;font-size:.65em;color:#90a4ae;font-weight:700;margin-top:3px}
.hour-label{font-size:.75em;color:#546e7a;margin-bottom:8px;padding-left:4px}
.clips-row{display:flex;flex-wrap:wrap;gap:10px}
.clip{width:180px;border-radius:8px;overflow:hidden;background:#1a2533;border:1px solid #263548;cursor:pointer;transition:transform .15s,border-color .15s}
.clip:hover{transform:translateY(-3px);border-color:#2196F3}
.clip-thumb{width:180px;height:105px;background:#0d1520;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden}
.clip-play{position:absolute;width:44px;height:44px;border-radius:50%;background:rgba(33,150,243,.85);display:flex;align-items:center;justify-content:center;font-size:1.2em}
.clip-info{padding:8px 10px}
.clip-time{font-size:.8em;font-weight:600;color:#e0e0e0}
.clip-size{font-size:.7em;color:#546e7a;margin-top:2px}
.clip-dl{float:right;font-size:.7em;padding:3px 8px;border-radius:4px;background:#263548;color:#90a4ae;margin-top:-2px}
.clip-dl:hover{background:#2196F3;color:#fff}
.snap{width:140px;border-radius:8px;overflow:hidden;background:#1a2533;border:1px solid #263548;cursor:pointer;transition:transform .15s,border-color .15s}
.snap:hover{transform:translateY(-3px);border-color:#4caf50}
.snap-thumb{width:140px;height:84px;object-fit:cover;display:block}
.snap-info{padding:6px 8px}
.snap-time{font-size:.75em;color:#90a4ae}
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:9999;align-items:center;justify-content:center;flex-direction:column;gap:12px}
.modal.open{display:flex}
.modal video,.modal img{max-width:94vw;max-height:82vh;border-radius:8px}
.modal-close{position:fixed;top:16px;right:24px;font-size:2em;color:#fff;cursor:pointer}
.modal-title{color:#ccc;font-size:.85em;max-width:94vw;text-align:center}
.modal-dl{margin-top:4px;padding:7px 18px;border-radius:6px;background:#2196F3;color:#fff;font-size:.85em;font-weight:600}
.empty{text-align:center;color:#546e7a;padding:48px;font-size:.95em}
.cam-panel{display:none}.cam-panel.active{display:block}
::-webkit-scrollbar{width:6px}::-webkit-scrollbar-track{background:#0f1923}::-webkit-scrollbar-thumb{background:#263548;border-radius:3px}
</style>
</head>
<body>
<div class="topbar">
  <h1>&#x23F1;&#xFE0F; Gravações – DVR Local</h1>
  <a href="{{ dvr_url }}" class="btn">&#x1F4F9; Ao Vivo</a>
</div>
<div class="wrap">
{% if files_by_cam %}
<div class="cam-tabs">
  {% for cam_id, data in files_by_cam.items() %}
  <div class="cam-tab {% if loop.first %}active{% endif %}"
       onclick="switchCam('{{ cam_id }}')">
    &#x1F4F7; {{ data.name }}
    <span style="opacity:.6;font-size:.8em">({{ data.total }})</span>
  </div>
  {% endfor %}
</div>
{% for cam_id, data in files_by_cam.items() %}
<div class="cam-panel {% if loop.first %}active{% endif %}" id="panel-{{ cam_id }}">
  {% if data.by_date %}
    {% for date_str, hours in data.by_date.items() %}
    <div class="date-group">
      <div class="date-label">&#x1F4C5; {{ date_str }}</div>
      <div class="timeline">
        {% for hour, items in hours.items() %}
        <div class="hour-block">
          <div class="hour-pin">{{ hour }}h</div>
          <div class="clips-row">
            {% for item in items %}
            {% if item.is_video %}
            <div class="clip" onclick="playVideo('/rec/{{ cam_id }}/{{ item.fname }}','{{ item.label }}','/rec/{{ cam_id }}/{{ item.fname }}')">
              <div class="clip-thumb">
                <div style="color:#546e7a;font-size:2em">&#x1F3AC;</div>
                <div class="clip-play">&#x25B6;</div>
              </div>
              <div class="clip-info">
                <div class="clip-time">{{ item.label }}</div>
                <div class="clip-size">{{ item.size }}</div>
                <a href="/rec/{{ cam_id }}/{{ item.fname }}" download onclick="event.stopPropagation()" class="clip-dl">&#x2B07;</a>
              </div>
            </div>
            {% else %}
            <div class="snap" onclick="showImg('/rec/{{ cam_id }}/{{ item.fname }}','{{ item.label }}','/rec/{{ cam_id }}/{{ item.fname }}')">
              <img class="snap-thumb" src="/rec/{{ cam_id }}/{{ item.fname }}" loading="lazy" alt="{{ item.fname }}">
              <div class="snap-info"><div class="snap-time">{{ item.label }}</div></div>
            </div>
            {% endif %}
            {% endfor %}
          </div>
        </div>
        {% endfor %}
      </div>
    </div>
    {% endfor %}
  {% else %}
    <p class="empty">Nenhuma gravação ainda.</p>
  {% endif %}
</div>
{% endfor %}
{% else %}
<p class="empty">Nenhuma gravação encontrada. O motion_recorder.py precisa estar rodando.</p>
{% endif %}
</div>

<div class="modal" id="modal" onclick="closeModal(event)">
  <span class="modal-close" onclick="closeModal()">&#x2715;</span>
  <video id="modal-video" controls style="display:none"></video>
  <img id="modal-img" style="display:none" alt="">
  <div class="modal-title" id="modal-title"></div>
  <a id="modal-dl" href="#" download class="modal-dl">&#x2B07; Baixar</a>
</div>

<script>
function switchCam(id){
  document.querySelectorAll('.cam-tab').forEach((t,i)=>{
    const panels=document.querySelectorAll('.cam-panel');
    const match=t.getAttribute('onclick').includes(id);
    t.classList.toggle('active',match);
    panels[i].classList.toggle('active',match);
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
  openModal(title,dl);v.play();
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
</html>"""


def _parse_dt(fname):
    m = re.search(r'(\d{8})_(\d{6})', fname)
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S')
        except Exception:
            pass
    return None


def _build_files_by_cam():
    CAM_NAMES = {'cam1': 'Câmera 1 (192.168.1.5)', 'cam2': 'Câmera 2 (192.168.1.6)'}
    result = {}
    if not os.path.isdir(RECORDINGS_DIR):
        return result
    for cam_id in sorted(os.listdir(RECORDINGS_DIR)):
        cam_dir = os.path.join(RECORDINGS_DIR, cam_id)
        if not os.path.isdir(cam_dir):
            continue
        raw = sorted(os.listdir(cam_dir), reverse=True)[:300]
        by_date = {}
        for fname in raw:
            fp = os.path.join(cam_dir, fname)
            if not os.path.isfile(fp):
                continue
            dt = _parse_dt(fname)
            date_str = dt.strftime('%d/%m/%Y') if dt else 'Data desconhecida'
            hour     = f'{dt.hour:02d}' if dt else '??'
            label    = dt.strftime('%H:%M:%S') if dt else fname
            size_b   = os.path.getsize(fp)
            size_s   = f'{size_b/1024/1024:.1f} MB' if size_b > 1024*1024 else f'{size_b//1024} KB'
            is_video = fname.lower().endswith(('.mp4', '.avi'))
            item = {'fname': fname, 'label': label, 'size': size_s, 'is_video': is_video}
            by_date.setdefault(date_str, {}).setdefault(hour, []).append(item)
        for d in by_date:
            by_date[d] = dict(sorted(by_date[d].items(), reverse=True))
        result[cam_id] = {
            'name': CAM_NAMES.get(cam_id, cam_id),
            'total': len(raw),
            'by_date': dict(sorted(by_date.items(), key=lambda x: x[0][::-1], reverse=True)),
        }
    return result


@app.route('/')
def index():
    files_by_cam = _build_files_by_cam()
    return render_template_string(TEMPLATE, files_by_cam=files_by_cam, dvr_url=DVR_URL)


@app.route('/rec/<cam_id>/<filename>')
def serve_file(cam_id, filename):
    filename = os.path.basename(filename)  # evita path traversal
    cam_id   = os.path.basename(cam_id)
    path     = os.path.join(RECORDINGS_DIR, cam_id, filename)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path)


# ── Tunnel + registro DVR ──────────────────────────────────────

def find_cloudflared():
    cf = shutil.which('cloudflared')
    if cf:
        return cf
    local = os.path.join(_APP_DIR, 'cloudflared.EXE')
    if os.path.exists(local):
        return local
    local2 = os.path.join(_APP_DIR, 'cloudflared.exe')
    if os.path.exists(local2):
        return local2
    return None


def register_recordings_url_powershell(tunnel_url):
    """Usa PowerShell para registrar a URL no DVR (Python 3.14 tem bug TLS).
    O endpoint /api/set-recordings-url autentica via token sha256('dvr-clear:{password}').
    """
    import hashlib
    token = hashlib.sha256(f'dvr-clear:{DVR_PASSWORD}'.encode()).hexdigest()
    script = f"""
$ErrorActionPreference = 'Stop'
$body = "token={token}&url={tunnel_url}"
$reg = Invoke-WebRequest -Uri '{DVR_URL}/api/set-recordings-url' `
    -Method POST -Body $body -ContentType 'application/x-www-form-urlencoded' `
    -UseBasicParsing -ErrorAction Stop
Write-Output "RESULT:$($reg.Content)"
"""
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', script],
            capture_output=True, text=True, timeout=30,
        )
        out = (result.stdout + result.stderr).strip()
        if 'RESULT:' in out:
            content = out.split('RESULT:', 1)[1].strip()
            if '"success": true' in content or '"success":true' in content:
                print(f'✓ URL de gravações registrada no DVR: {tunnel_url}')
                return True
            else:
                print(f'✗ Resposta do DVR: {content[:200]}')
        else:
            print(f'✗ PowerShell: {out[:300]}')
    except subprocess.TimeoutExpired:
        print('✗ Timeout ao registrar no DVR')
    except Exception as e:
        print(f'✗ Erro ao registrar: {e}')
    return False


def start_tunnel(cloudflared, tunnel_result):
    local_url = f'http://127.0.0.1:{PORT}'
    print(f'  🚇 Iniciando tunnel para {local_url}...')
    proc = subprocess.Popen(
        [cloudflared, 'tunnel', '--url', local_url],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    tunnel_result['proc'] = proc
    url_pattern = re.compile(r'https://[a-z0-9\-]+\.trycloudflare\.com')
    for line in proc.stdout:
        m = url_pattern.search(line)
        if m:
            tunnel_result['url'] = m.group(0)
            print(f'  ✓ Tunnel: {tunnel_result["url"]}')
            break
    for _ in proc.stdout:
        pass


# ── MAIN ──────────────────────────────────────────────────────

if __name__ == '__main__':
    from werkzeug.serving import make_server

    print('=' * 60)
    print('🎞️  Recordings Relay — Servidor de Gravações')
    print('=' * 60)
    print(f'Gravações em: {RECORDINGS_DIR}')

    # Inicia servidor Flask em thread
    srv = make_server('0.0.0.0', PORT, app)
    srv_thread = threading.Thread(target=srv.serve_forever, daemon=True)
    srv_thread.start()
    print(f'✓ Servidor HTTP em http://127.0.0.1:{PORT}')

    # Aguarda servidor estar pronto
    time.sleep(1)

    # Verifica cloudflared
    cf = find_cloudflared()
    if not cf:
        print('⚠️  cloudflared não encontrado. Instale em https://developers.cloudflare.com/cloudflared/')
        print(f'   Servidor local acessível em http://127.0.0.1:{PORT}')
        print('   Pressione Ctrl+C para parar.')
        try:
            while True:
                time.sleep(10)
        except KeyboardInterrupt:
            pass
        raise SystemExit(0)

    print(f'✓ cloudflared: {cf}')

    # Inicia tunnel em thread
    tunnel_result = {}
    t = threading.Thread(target=start_tunnel, args=(cf, tunnel_result), daemon=True)
    t.start()

    # Aguarda URL do tunnel (máx 60s)
    print('⏳ Aguardando tunnel Cloudflare...')
    deadline = time.time() + 60
    while time.time() < deadline and not tunnel_result.get('url'):
        time.sleep(1)

    if not tunnel_result.get('url'):
        print('⚠️  Tunnel não respondeu. Verifique cloudflared.')
        print(f'   Servidor local: http://127.0.0.1:{PORT}')
    else:
        tunnel_url = tunnel_result['url']
        print()
        print('⏳ Registrando URL no DVR (via PowerShell)...')
        register_recordings_url_powershell(tunnel_url)

        print()
        print('=' * 60)
        print(f'✅ Gravações disponíveis em:')
        print(f'   {tunnel_url}')
        print(f'   (ou localmente: http://127.0.0.1:{PORT})')
        print(f'   DVR: https://dvr.regivan.tec.br/recordings')
        print('=' * 60)

    print('\nPressione Ctrl+C para parar.\n')
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        proc = tunnel_result.get('proc')
        if proc:
            proc.terminate()
        print('\nEncerrado.')
