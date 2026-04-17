"""
Scanner local de câmeras IP com cadastro automático no DVR remoto.

Fluxo:
  1. Detecta rede local e IP público do roteador
  2. TCP scan + verificação HTTP em toda a subnet /24
  3. Salva cameras_config.json local (IPs internos — para rodar o app localmente)
  4. Cadastra no DVR remoto (dvr.regivan.tec.br) com IP público + port forwarding

Pré-requisito para acesso remoto/móvel:
  Configure port forwarding no roteador:
    porta_externa → IP_interno_câmera:80
  Use PORT_FORWARD_START para definir a primeira porta.

Uso: python discover_cameras.py
"""
import socket
import requests
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─────────────────────────────────────────────
# CONFIGURAÇÕES — edite antes de rodar
# ─────────────────────────────────────────────
DVR_URL      = 'https://dvr.regivan.tec.br'   # URL do app remoto
DVR_USER     = 'admin'                          # usuário do DVR app
DVR_PASSWORD = ''                               # senha do DVR app

CAM_USER     = 'admin'                          # usuário padrão das câmeras
CAM_PASSWORD = ''                               # senha padrão das câmeras
CAM_MODEL    = 'generic'                        # iscee | hikvision | dahua | intelbras | generic

# Port forwarding: porta externa do roteador para cada câmera
# Ex: roteador:8081 → 192.168.1.101:80, roteador:8082 → 192.168.1.102:80
# O script atribui portas sequenciais a partir de PORT_FORWARD_START
PORT_FORWARD_START = 8081   # primeira porta externa do roteador

AUTO_REGISTER      = True   # cadastra no DVR remoto com IP público
SAVE_LOCAL_CONFIG  = True   # salva também no cameras_config.json local (IP local)

LOOP_MODE          = False  # True = rodar em loop contínuo (útil deixar rodando em background)
LOOP_INTERVAL      = 300    # segundos entre cada scan no modo loop
# ─────────────────────────────────────────────

CAMERA_PORTS = [80, 8080, 8899, 554, 8081, 8090]
CAMERA_KEYWORDS = ['camera', 'video', 'stream', 'dvr', 'ipcam', 'webcam', 'snapshot', 'cgi-bin']
HTTP_HEADERS = {'User-Agent': 'Mozilla/5.0 (DVR-Camera-Viewer/1.0)'}

def get_local_network():
    """Detecta a subnet local de forma confiável"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = socket.gethostbyname(socket.gethostname())
    return ip, '.'.join(ip.split('.')[:-1])

def tcp_open(ip, port):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.3)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except Exception:
        return False

def check_camera(ip, port):
    """Verifica via HTTP se o endpoint parece ser câmera"""
    if port == 554:
        # RTSP — se TCP abriu, já é candidata
        return {'ip': ip, 'port': port, 'url': f'rtsp://{ip}:{port}', 'server': 'RTSP'}
    try:
        url = f'http://{ip}:{port}'
        r = requests.get(url, timeout=2, headers=HTTP_HEADERS, allow_redirects=True)
        content = r.text.lower()
        server = r.headers.get('Server', '')
        if any(kw in content for kw in CAMERA_KEYWORDS) or any(kw in server.lower() for kw in ['ipc', 'dvr', 'cam', 'hikvision', 'dahua']):
            return {'ip': ip, 'port': port, 'url': url, 'server': server}
    except Exception:
        pass
    return None

def get_public_ip():
    """Detecta o IP público do roteador"""
    for service in ['https://api.ipify.org', 'https://ifconfig.me/ip', 'https://icanhazip.com']:
        try:
            r = requests.get(service, timeout=5, headers=HTTP_HEADERS)
            ip = r.text.strip()
            if ip:
                return ip
        except Exception:
            continue
    return None

def save_local_config(cameras_found):
    """Salva cameras_config.json local com os IPs internos encontrados"""
    config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cameras_config.json')
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception:
        config = {'cameras': {}, 'auth': {'user': 'admin', 'password': ''}}

    for i, cam in enumerate(cameras_found, 1):
        cam_id = str(int(time.time() * 1000) + i)
        config['cameras'][cam_id] = {
            'name': f'Câmera {i} ({cam["ip"]})',
            'ip': cam['ip'],
            'port': cam['port'],
            'user': CAM_USER,
            'password': CAM_PASSWORD,
            'model': CAM_MODEL,
            'path': '/snapshot.cgi',
            'enabled': True,
        }

    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print(f'✓ cameras_config.json local atualizado ({len(cameras_found)} câmera(s))')


    """Faz login no DVR remoto e retorna sessão autenticada"""
    s = requests.Session()
    s.headers.update(HTTP_HEADERS)
    r = s.post(f'{DVR_URL}/login', data={
        'user': DVR_USER, 'password': DVR_PASSWORD, 'next': '/'
    }, allow_redirects=True, timeout=10)
    if '/login' in r.url:
        print('✗ Falha no login do DVR. Verifique DVR_USER e DVR_PASSWORD no script.')
        return None
    print('✓ Login no DVR remoto OK')
    return s

def register_camera(session, cam, index, public_ip):
    """Cadastra câmera no DVR remoto — usa IP público + porta redirecionada"""
    ext_port = PORT_FORWARD_START + index - 1
    reg_ip   = public_ip if public_ip else cam['ip']
    reg_port = ext_port  if public_ip else cam['port']

    data = {
        'name': f'Câmera {index} ({cam["ip"]})',
        'ip': reg_ip,
        'port': reg_port,
        'user': CAM_USER,
        'password': CAM_PASSWORD,
        'model': CAM_MODEL,
    }
    if public_ip:
        print(f'  → Registrando com IP público {reg_ip}:{reg_port}')
        print(f'    ⚠️  Configure no roteador: porta {reg_port} → {cam["ip"]}:{cam["port"]}')
    else:
        print(f'  → IP público não detectado, registrando com IP local {reg_ip}:{reg_port}')
    try:
        r = session.post(f'{DVR_URL}/api/camera/add', data=data, timeout=15)
        result = r.json()
        if result.get('success'):
            print(f'  ✓ Cadastrada! ID: {result.get("cam_id")}')
        else:
            print(f'  ✗ Falha: {result.get("error")}')
    except Exception as e:
        print(f'  ✗ Erro: {e}')

# ── MAIN ─────────────────────────────────────
print('=' * 60)
print('🔍 Scanner Local de Câmeras IP')
print('=' * 60)


def run_scan():
    """Executa um ciclo completo de scan + registro."""
    local_ip, network = get_local_network()
    print(f'\nSeu IP local : {local_ip}')
    print(f'Rede escaneada: {network}.0/24')
    print(f'Portas: {CAMERA_PORTS}')

    print('\n⏳ Detectando IP público...')
    public_ip = get_public_ip()
    if public_ip:
        print(f'  IP público: {public_ip}')
    else:
        print('  ⚠️  IP público não detectado (sem internet?)')

    print('\n⏳ Estágio 1: TCP scan rápido...')
    open_endpoints = []
    with ThreadPoolExecutor(max_workers=300) as ex:
        futs = {ex.submit(tcp_open, f'{network}.{i}', p): (f'{network}.{i}', p)
                for i in range(1, 255) for p in CAMERA_PORTS}
        for f in as_completed(futs):
            if f.result():
                open_endpoints.append(futs[f])
    print(f'  {len(open_endpoints)} porta(s) abertas')

    print('\n⏳ Estágio 2: Verificando HTTP...')
    cameras_found = []
    with ThreadPoolExecutor(max_workers=50) as ex:
        futs = [ex.submit(check_camera, ip, port) for ip, port in open_endpoints]
        for f in as_completed(futs):
            result = f.result()
            if result:
                cameras_found.append(result)
                print(f'\n  📹 CÂMERA: {result["ip"]}:{result["port"]} | {result["server"]}')

    print('\n' + '=' * 60)
    print(f'TOTAL: {len(cameras_found)} câmera(s) encontrada(s)')
    print('=' * 60)

    if not cameras_found:
        print('\n⚠️  Nenhuma câmera encontrada. Verifique se estão ligadas e na mesma rede.')
        return

    # Salvar lista local
    with open('cameras_found.txt', 'w') as f:
        for cam in cameras_found:
            f.write(f'{cam["ip"]}:{cam["port"]}\n')
    print('\n📄 Lista salva em cameras_found.txt')

    if SAVE_LOCAL_CONFIG:
        print('\n💾 Salvando configuração local...')
        save_local_config(cameras_found)

    if AUTO_REGISTER:
        print(f'\n🌐 Cadastrando no DVR remoto ({DVR_URL})...')
        dvr_session = login_dvr()
        if dvr_session:
            for i, cam in enumerate(cameras_found, 1):
                print(f'\n  [{i}/{len(cameras_found)}] {cam["ip"]}:{cam["port"]}')
                register_camera(dvr_session, cam, i, public_ip)

        if public_ip:
            print('\n' + '=' * 60)
            print('📋 RESUMO DE PORT FORWARDING — configure no roteador:')
            print('=' * 60)
            for i, cam in enumerate(cameras_found, 1):
                ext_port = PORT_FORWARD_START + i - 1
                print(f'  {public_ip}:{ext_port}  →  {cam["ip"]}:{cam["port"]}')
            print('=' * 60)
        print('\n✅ Concluído!')
    else:
        print('\nAUTO_REGISTER=False — câmeras não cadastradas automaticamente.')


# ── MAIN ─────────────────────────────────────
print('=' * 60)
print('🔍 Scanner Local de Câmeras IP')
if LOOP_MODE:
    print(f'   Modo loop ativo — intervalo: {LOOP_INTERVAL}s  (Ctrl+C para parar)')
print('=' * 60)

try:
    while True:
        run_scan()
        if not LOOP_MODE:
            break
        print(f'\n⏳ Próximo scan em {LOOP_INTERVAL}s... (Ctrl+C para parar)')
        time.sleep(LOOP_INTERVAL)
except KeyboardInterrupt:
    print('\n\nInterrompido pelo usuário.')

