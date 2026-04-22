"""
DVR Local Agent — roda na rede local onde estão as câmeras.
Faz polling no servidor por comandos e executa scans localmente.

Uso:
    python agent.py

O agente aparece na tela de Scanner do DVR como "Agente conectado".
Clique "Iniciar Scan" no browser — o agente escaneia a rede local
e envia os resultados de volta ao servidor automaticamente.
"""
import socket
import requests
import time
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── CONFIGURAÇÕES ───────────────────────────────────────────
# Detecta se está sendo usado localmente (localhost) ou remotamente
# Use: python agent.py http://127.0.0.1:8000  (local)
#   ou: python agent.py https://dvr.regivan.tec.br  (remoto)
import sys
DVR_URL      = sys.argv[1] if len(sys.argv) > 1 else os.getenv('DVR_URL', 'http://127.0.0.1:8000')
DVR_USER     = 'admin'
DVR_PASSWORD = '!Rede!123'           # senha do DVR app

AGENT_NAME   = socket.gethostname()   # identificador deste agente
POLL_INTERVAL = 3                     # segundos entre polls

CAM_USER     = 'admin'
CAM_PASSWORD = ''
CAM_MODEL    = 'generic'
# ─────────────────────────────────────────────────────────────

CAMERA_PORTS    = [80, 8080, 8899, 554, 8081, 8090]
CAMERA_KEYWORDS = ['camera', 'video', 'stream', 'dvr', 'ipcam', 'webcam', 'snapshot', 'cgi-bin']
HTTP_HEADERS    = {'User-Agent': 'Mozilla/5.0 (DVR-Agent/1.0)'}

session = requests.Session()
session.headers.update(HTTP_HEADERS)

# Desabilita aviso de SSL para URLs remotas com certificado autoassinado
if DVR_URL.startswith('https'):
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    session.verify = False

def login():
    """Autentica no DVR remoto"""
    try:
        r = session.post(f'{DVR_URL}/login',
                         data={'user': DVR_USER, 'password': DVR_PASSWORD, 'next': '/'},
                         timeout=10, allow_redirects=True)
        if '/login' in r.url:
            print('✗ Falha no login. Verifique DVR_USER e DVR_PASSWORD.')
            return False
        print(f'✓ Login OK como {DVR_USER}')
        return True
    except Exception as e:
        print(f'✗ Erro ao conectar ao servidor: {e}')
        return False

def get_local_network():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
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
    if port == 554:
        return {'ip': ip, 'port': port, 'url': f'rtsp://{ip}:{port}', 'server': 'RTSP'}
    try:
        url = f'http://{ip}:{port}'
        r = requests.get(url, timeout=2, headers=HTTP_HEADERS, allow_redirects=True)
        content = r.text.lower()
        server = r.headers.get('Server', '')
        if (any(kw in content for kw in CAMERA_KEYWORDS) or
                any(kw in server.lower() for kw in ['ipc', 'dvr', 'cam', 'hikvision', 'dahua'])):
            return {'ip': ip, 'port': port, 'url': url, 'server': server}
    except Exception:
        pass
    return None

def do_scan():
    """Executa scan na rede local e retorna lista de câmeras"""
    local_ip, network = get_local_network()
    print(f'  Escaneando {network}.0/24...')

    # Estágio 1: TCP
    open_endpoints = []
    with ThreadPoolExecutor(max_workers=300) as ex:
        futs = {ex.submit(tcp_open, f'{network}.{i}', p): (f'{network}.{i}', p)
                for i in range(1, 255) for p in CAMERA_PORTS}
        for f in as_completed(futs):
            if f.result():
                open_endpoints.append(futs[f])
    print(f'  {len(open_endpoints)} porta(s) abertas')

    # Estágio 2: HTTP
    cameras = []
    with ThreadPoolExecutor(max_workers=50) as ex:
        futs = [ex.submit(check_camera, ip, port) for ip, port in open_endpoints]
        for f in as_completed(futs):
            r = f.result()
            if r:
                cameras.append(r)
                print(f'  📹 {r["ip"]}:{r["port"]}')

    print(f'  Total: {len(cameras)} câmera(s)')
    return cameras, local_ip, network

def send_heartbeat():
    """Envia heartbeat ao servidor para indicar que o agente está vivo"""
    try:
        session.post(f'{DVR_URL}/api/agent/heartbeat',
                     json={'agent': AGENT_NAME},
                     timeout=5)
    except Exception:
        pass

def poll_command():
    """Verifica se há comando pendente no servidor"""
    try:
        r = session.get(f'{DVR_URL}/api/agent/command',
                        params={'agent': AGENT_NAME}, timeout=5)
        if r.status_code == 200:
            return r.json().get('command')
    except Exception:
        pass
    return None

def post_results(cameras, local_ip, network):
    """Envia resultados do scan ao servidor"""
    try:
        r = session.post(f'{DVR_URL}/api/agent/results',
                         json={
                             'agent': AGENT_NAME,
                             'cameras': cameras,
                             'local_ip': local_ip,
                             'network': network,
                             'cam_user': CAM_USER,
                             'cam_password': CAM_PASSWORD,
                             'cam_model': CAM_MODEL,
                         }, timeout=10)
        return r.json()
    except Exception as e:
        print(f'  ✗ Erro ao enviar resultados: {e}')
        return None

# ── MAIN LOOP ─────────────────────────────────────────────────
print('=' * 55)
print('🤖 DVR Local Agent')
print(f'   Servidor : {DVR_URL}')
print(f'   Host     : {AGENT_NAME}')
print('=' * 55)

if not login():
    print('Configure DVR_USER e DVR_PASSWORD no topo do arquivo.')
    exit(1)

local_ip, network = get_local_network()
print(f'\nRede local: {network}.0/24')
print('Aguardando comandos do servidor... (Ctrl+C para parar)\n')

scanning = False

try:
    while True:
        send_heartbeat()
        cmd = poll_command()

        if cmd == 'scan' and not scanning:
            scanning = True
            print('\n▶ Comando de scan recebido!')

            def run_and_report():
                global scanning
                try:
                    cameras, lip, net = do_scan()
                    result = post_results(cameras, lip, net)
                    if result and result.get('registered'):
                        print(f'  ✓ {result["registered"]} câmera(s) cadastrada(s) no DVR')
                    else:
                        print(f'  → Resultado: {result}')
                except Exception as e:
                    print(f'  ✗ Erro no scan: {e}')
                finally:
                    scanning = False

            threading.Thread(target=run_and_report, daemon=True).start()

        elif cmd == 'scan' and scanning:
            print('  (scan já em andamento, ignorando)')

        time.sleep(POLL_INTERVAL)

except KeyboardInterrupt:
    print('\nAgente encerrado.')
