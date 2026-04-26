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
import glob
from datetime import datetime
from flask import Flask, send_file, render_template_string, abort, request, Response

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(errors='replace')

# ─────────────────────────────────────────────
DVR_URL_LOCAL  = 'http://127.0.0.1:8000'
DVR_URL_REMOTE = 'https://dvr.regivan.tec.br'
DVR_URL        = DVR_URL_LOCAL   # padrão: local
DVR_USER     = 'admin'
DVR_PASSWORD = '!Rede!123'
PORT         = 8290
# ─────────────────────────────────────────────

_APP_DIR       = os.path.dirname(os.path.abspath(__file__))


def _find_ffmpeg():
    ff = shutil.which('ffmpeg')
    if ff:
        return ff
    for pattern in ['C:/ffmpeg/*/bin/ffmpeg.exe', 'C:/ffmpeg/bin/ffmpeg.exe']:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None


FFMPEG = _find_ffmpeg()
RECORDINGS_DIR = os.path.join(_APP_DIR, 'recordings')

app = Flask(__name__)

TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
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
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:9999;align-items:center;justify-content:center;padding:16px}
.modal.open{display:flex}
.modal-box{background:#1a2533;border-radius:12px;overflow:hidden;max-width:96vw;width:900px;display:flex;flex-direction:column;box-shadow:0 24px 64px rgba(0,0,0,.7)}
.modal-header{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;background:#0f1923;gap:12px}
.modal-title{color:#e0e0e0;font-size:.9em;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.modal-close{background:none;border:none;color:#90a4ae;font-size:1.4em;cursor:pointer;padding:2px 6px;border-radius:4px;flex-shrink:0}
.modal-close:hover{background:#263548;color:#fff}
.modal-body{background:#000;display:flex;align-items:center;justify-content:center;min-height:200px}
.modal-body video{width:100%;max-height:72vh;display:block;outline:none}
.modal-body img{max-width:100%;max-height:72vh;display:block}
.modal-footer{padding:10px 16px;display:flex;justify-content:flex-end;background:#0f1923}
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

<div class="modal" id="modal">
  <div class="modal-box" id="modal-box">
    <div class="modal-header">
      <span id="modal-title" class="modal-title"></span>
      <button class="modal-close" onclick="closeModal()" title="Fechar">&#x2715;</button>
    </div>
    <div class="modal-body">
      <video id="modal-video" controls preload="metadata" style="display:none"
             onerror="onVideoError(this)"></video>
      <img id="modal-img" style="display:none" alt="">
      <div id="modal-error" style="display:none;color:#ef5350;padding:24px;text-align:center;font-size:.9em"></div>
    </div>
    <div class="modal-footer">
      <a id="modal-dl" href="#" download class="btn">&#x2B07; Baixar arquivo</a>
    </div>
  </div>
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
  document.getElementById('modal-dl').download=dlUrl.split('/').pop();
  document.getElementById('modal-error').style.display='none';
  document.body.style.overflow='hidden';
}
function closeModal(){
  const m=document.getElementById('modal');
  if(!m.classList.contains('open'))return;
  m.classList.remove('open');
  const v=document.getElementById('modal-video');
  v.pause();v.removeAttribute('src');v.load();v.style.display='none';
  document.getElementById('modal-img').style.display='none';
  document.body.style.overflow='';
}
function playVideo(src,title,dl){
  const v=document.getElementById('modal-video');
  const err=document.getElementById('modal-error');
  document.getElementById('modal-img').style.display='none';
  err.style.display='none';
  v.style.display='block';
  v.removeAttribute('src');
  v.load();
  // Usa source element para melhor compatibilidade
  v.innerHTML='';
  const s=document.createElement('source');
  s.src=src;
  s.type='video/mp4';
  v.appendChild(s);
  v.load();
  openModal(title,dl);
  v.play().catch(function(e){
    if(e.name!=='AbortError'){
      err.textContent='Erro ao reproduzir: '+e.message+'. Use o botão Baixar para ver o vídeo.';
      err.style.display='block';
    }
  });
}
function onVideoError(v){
  const err=document.getElementById('modal-error');
  err.textContent='Formato não suportado pelo navegador. Use o botão Baixar para ver o vídeo.';
  err.style.display='block';
  v.style.display='none';
}
function showImg(src,title,dl){
  const i=document.getElementById('modal-img');
  const v=document.getElementById('modal-video');
  v.pause();v.removeAttribute('src');v.load();v.style.display='none';
  i.style.display='block';i.src=src;
  openModal(title,dl);
}
document.getElementById('modal').addEventListener('click',function(e){
  if(e.target===this)closeModal();
});
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeModal();});
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

    if filename.lower().endswith(('.mp4', '.avi')):
        # Verifica se já é H.264 (gravado pelo novo motion_recorder)
        cached = _h264_cache_path(path)
        if os.path.isfile(cached):
            return _range_response(cached, 'video/mp4')
        if FFMPEG:
            return _transcode_stream(path)
        return _range_response(path, 'video/mp4')
    return send_file(path)


def _h264_cache_path(src_path: str) -> str:
    """Caminho do arquivo H.264 em cache (mesmo nome, pasta recordings_h264/)."""
    rel  = os.path.relpath(src_path, RECORDINGS_DIR)
    return os.path.join(_APP_DIR, 'recordings_h264', rel)


def _transcode_stream(path: str) -> Response:
    """Transcodifica MPEG-4→H.264 via ffmpeg e envia como stream fragmentado.
    Usa frag_keyframe+empty_moov para que o browser consiga tocar sem seek.
    """
    cached = _h264_cache_path(path)

    def generate():
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        # Grava cache ao mesmo tempo que envia ao browser
        cache_tmp = cached + '.tmp'
        proc = subprocess.Popen(
            [
                FFMPEG, '-y', '-i', path,
                '-vcodec', 'libx264', '-preset', 'ultrafast', '-crf', '23',
                '-pix_fmt', 'yuv420p',
                '-movflags', 'frag_keyframe+empty_moov',
                '-an',  # sem áudio (câmeras RTSP geralmente não têm)
                '-f', 'mp4', 'pipe:1',
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        buf = bytearray()
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                buf.extend(chunk)
                yield bytes(chunk)
        finally:
            proc.stdout.close()
            proc.wait()
            # Salva cache apenas se a transcodificação terminou com sucesso
            if proc.returncode == 0 and buf:
                try:
                    with open(cache_tmp, 'wb') as f:
                        f.write(buf)
                    os.replace(cache_tmp, cached)
                except Exception:
                    pass

    return Response(
        generate(),
        mimetype='video/mp4',
        headers={'Cache-Control': 'no-cache', 'Accept-Ranges': 'none'},
    )


def _range_response(path: str, mime: str) -> Response:
    """Serve arquivo com suporte a HTTP 206 Partial Content (Range Requests)."""
    file_size = os.path.getsize(path)
    range_header = request.headers.get('Range')

    if not range_header:
        with open(path, 'rb') as f:
            data = f.read()
        resp = Response(data, 200, mimetype=mime)
        resp.headers['Accept-Ranges'] = 'bytes'
        resp.headers['Content-Length'] = str(file_size)
        return resp

    # Parseia "bytes=start-end"
    m = re.match(r'bytes=(\d+)-(\d*)', range_header)
    if not m:
        abort(416)
    start = int(m.group(1))
    end   = int(m.group(2)) if m.group(2) else file_size - 1
    end   = min(end, file_size - 1)
    length = end - start + 1

    with open(path, 'rb') as f:
        f.seek(start)
        data = f.read(length)

    resp = Response(data, 206, mimetype=mime)
    resp.headers['Content-Range']  = f'bytes {start}-{end}/{file_size}'
    resp.headers['Accept-Ranges']  = 'bytes'
    resp.headers['Content-Length'] = str(length)
    return resp


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
    Tenta local primeiro, depois remoto.
    """
    import hashlib
    token = hashlib.sha256(f'dvr-clear:{DVR_PASSWORD}'.encode()).hexdigest()
    for dvr_url in [DVR_URL_LOCAL, DVR_URL_REMOTE]:
        script = f"""
$ErrorActionPreference = 'Stop'
$body = "token={token}&url={tunnel_url}"
$reg = Invoke-WebRequest -Uri '{dvr_url}/api/set-recordings-url' `
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
                    print(f'✓ URL de gravações registrada no DVR ({dvr_url}): {tunnel_url}')
                    return True
                else:
                    print(f'✗ Resposta do DVR ({dvr_url}): {content[:200]}')
            else:
                print(f'✗ PowerShell ({dvr_url}): {out[:300]}')
        except subprocess.TimeoutExpired:
            print(f'✗ Timeout ao registrar em {dvr_url}')
        except Exception as e:
            print(f'✗ Erro ao registrar em {dvr_url}: {e}')
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
