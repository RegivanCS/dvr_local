"""
Tunnel Relay — expõe câmeras locais via Cloudflare Quick Tunnels.

Fluxo:
  1. Para cada câmera HTTP (porta 80) na rede local, inicia um cloudflared tunnel
  2. Captura a URL pública gerada (ex: https://xyz.trycloudflare.com)
  3. Atualiza automaticamente o cadastro das câmeras no DVR remoto
  4. Mantém os túneis ativos (Ctrl+C para parar)

Requisito: cloudflared instalado (executado automaticamente se não encontrado)
Uso: python tunnel_relay.py
"""

import subprocess
import threading
import re
import time
import requests
import json
import sys
import os
import shutil

# ─────────────────────────────────────────────
# CONFIGURAÇÕES
# ─────────────────────────────────────────────
DVR_URL      = 'https://dvr.regivan.tec.br'
DVR_USER     = 'admin'
DVR_PASSWORD = '!Rede!123'

CAM_USER     = 'admin'
CAM_PASSWORD = ''
CAM_MODEL    = 'generic'

# Aponta para o proxy local (rtsp_proxy.py) em vez das c\u00e2meras diretamente
# O path /snapshot.jpg \u00e9 servido pelo proxy
CAMERAS = [
    {'id': None, 'ip': '127.0.0.1', 'port': 8191, 'name': 'C\u00e2mera 1 (192.168.1.5)', 'path': '/snapshot.jpg'},
    {'id': None, 'ip': '127.0.0.1', 'port': 8192, 'name': 'C\u00e2mera 2 (192.168.1.6)', 'path': '/snapshot.jpg'},
]

HTTP_HEADERS = {'User-Agent': 'Mozilla/5.0 (DVR-Camera-Viewer/1.0)'}
# ─────────────────────────────────────────────

CLOUDFLARED_URL = (
    'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
)


def find_or_install_cloudflared():
    """Localiza cloudflared no PATH ou faz download."""
    cf = shutil.which('cloudflared')
    if cf:
        return cf
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cloudflared.exe')
    if os.path.exists(local):
        return local
    print('⬇️  cloudflared não encontrado. Baixando...')
    r = requests.get(CLOUDFLARED_URL, stream=True, timeout=60)
    r.raise_for_status()
    with open(local, 'wb') as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    print(f'✓ cloudflared salvo em {local}')
    return local


def start_tunnel(cloudflared, cam, result_dict):
    """Inicia um tunnel para uma câmera e captura a URL pública."""
    local_url = f'http://{cam["ip"]}:{cam["port"]}'
    print(f'  🚇 Iniciando tunnel para {local_url}...')
    proc = subprocess.Popen(
        [cloudflared, 'tunnel', '--url', local_url],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    result_dict['proc'] = proc
    url_pattern = re.compile(r'https://[a-z0-9\-]+\.trycloudflare\.com')
    for line in proc.stdout:
        m = url_pattern.search(line)
        if m:
            result_dict['url'] = m.group(0)
            print(f'  ✓ Tunnel ativo: {local_url} → {result_dict["url"]}')
            break
    # Continua lendo stdout para manter o processo vivo
    for _ in proc.stdout:
        pass


def login_dvr():
    s = requests.Session()
    s.headers.update(HTTP_HEADERS)
    r = s.post(f'{DVR_URL}/login', data={
        'user': DVR_USER, 'password': DVR_PASSWORD, 'next': '/'
    }, allow_redirects=True, timeout=10)
    if '/login' in r.url:
        print('✗ Falha no login do DVR.')
        return None
    print('✓ Login no DVR OK')
    return s


def clear_all_cameras(session):
    """Remove todas as câmeras cadastradas no DVR antes de recadastrar."""
    try:
        r = session.get(f'{DVR_URL}/api/cameras', timeout=10)
        payload = r.json() if r.ok else {}
        if isinstance(payload, dict) and isinstance(payload.get('cameras'), list):
            cameras = payload.get('cameras', [])
        elif isinstance(payload, dict):
            cameras = list(payload.values())
        else:
            cameras = []
    except Exception as e:
        print(f'  ⚠️  Não foi possível listar câmeras: {e}')
        return
    if not cameras:
        print('  (nenhuma câmera existente)')
        return
    print(f'  Removendo {len(cameras)} câmera(s) anterior(es)...')
    for cam in cameras:
        cid = str(cam.get('id', ''))
        if not cid:
            continue
        try:
            rd = session.post(f'{DVR_URL}/api/camera/delete/{cid}', timeout=10)
            ok = rd.json().get('success', False)
            print(f'    {"✓" if ok else "✗"} {cam.get("name")} [{cid}]')
        except Exception as e:
            print(f'    ✗ Erro ao remover {cid}: {e}')


def register_tunnel_camera(session, cam, tunnel_url):
    """Cadastra/atualiza câmera no DVR com a URL do tunnel."""
    host = tunnel_url.replace('https://', '').replace('http://', '').rstrip('/')
    path = cam.get('path', '/snapshot.jpg')
    data = {
        'name': cam['name'],
        'ip': host,
        'port': '443',
        'user': CAM_USER,
        'password': CAM_PASSWORD,
        'model': CAM_MODEL,
        'path': path,
        'skip_test': 'true',
    }
    # Tenta editar se já tiver ID, senão adiciona
    if cam.get('id'):
        r = session.post(f'{DVR_URL}/api/camera/edit/{cam["id"]}', data=data, timeout=15)
    else:
        r = session.post(f'{DVR_URL}/api/camera/add', data=data, timeout=15)
    result = r.json()
    if result.get('success'):
        cam_id = result.get('cam_id') or cam.get('id')
        cam['id'] = cam_id
        print(f'  ✓ DVR atualizado! ID: {cam_id}')
    else:
        print(f'  ✗ Falha no DVR: {result.get("error")}')


# ── MAIN ─────────────────────────────────────
print('=' * 60)
print('🚇 Cloudflare Tunnel Relay — DVR Câmeras')
print('=' * 60)

cloudflared = find_or_install_cloudflared()
print(f'\n✓ cloudflared: {cloudflared}\n')

tunnels = [{} for _ in CAMERAS]
threads = []

for i, cam in enumerate(CAMERAS):
    t = threading.Thread(target=start_tunnel, args=(cloudflared, cam, tunnels[i]), daemon=True)
    t.start()
    threads.append(t)

# Aguarda todas as URLs aparecerem (máx 60s)
print('\n⏳ Aguardando URLs dos tunnels...')
deadline = time.time() + 60
while time.time() < deadline:
    if all(t.get('url') for t in tunnels):
        break
    time.sleep(1)

missing = [i for i, t in enumerate(tunnels) if not t.get('url')]
if missing:
    print(f'⚠️  Timeout: túneis {missing} não responderam. Verifique se as câmeras estão ligadas.')

active = [(CAMERAS[i], tunnels[i]['url']) for i in range(len(CAMERAS)) if tunnels[i].get('url')]

if active:
    print(f'\n🌐 Registrando {len(active)} câmera(s) no DVR...')
    session = login_dvr()
    if session:
        print('\n🧹 Limpando câmeras antigas...')
        clear_all_cameras(session)
        for cam, url in active:
            print(f'\n  {cam["name"]} → {url}')
            register_tunnel_camera(session, cam, url)

    print('\n' + '=' * 60)
    print('📋 Tunnels ativos:')
    for cam, url in active:
        print(f'  {cam["ip"]}:{cam["port"]}  →  {url}')
    print('=' * 60)
    print('\n✅ DVR atualizado! Acesse https://dvr.regivan.tec.br')
    print('   Pressione Ctrl+C para encerrar os tunnels.\n')
else:
    print('\n✗ Nenhum tunnel ativo. Encerrando.')
    sys.exit(1)

try:
    while True:
        time.sleep(10)
except KeyboardInterrupt:
    print('\n\nEncerrando tunnels...')
    for t in tunnels:
        proc = t.get('proc')
        if proc:
            proc.terminate()
    print('Tunnels encerrados.')
