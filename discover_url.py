import subprocess
import os
import requests
import json

# Carrega IP/credenciais da configuração (definidos pela tela de configurações)
def _load_first_camera():
    config_path = os.path.join(os.path.dirname(__file__), 'cameras_config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            cameras = json.load(f).get('cameras', {})
        if cameras:
            cam = next(iter(cameras.values()))
            return cam.get('ip', ''), cam.get('port', 80), cam.get('user', 'admin'), cam.get('password', '')
    except:
        pass
    return None, None, None, None

_ip, _port, _user, _password = _load_first_camera()
if not _ip:
    print("[ERRO] Nenhuma câmera configurada. Adicione câmeras pela tela de configurações.")
    exit(1)

print("=" * 60)
print("TESTE: Descobrir URL correta da câmera")
print("=" * 60)

ffmpeg_path = r"C:\Program Files\Agent\dlls\x64\ffmpeg.exe"

# URLs geradas a partir das configurações (não use IPs fixos aqui)
urls_to_test = [
    # HTTP/MJPEG
    f"http://{_user}:{_password}@{_ip}:{_port}/video",
    f"http://{_user}:{_password}@{_ip}:{_port}/video.cgi",
    f"http://{_user}:{_password}@{_ip}:{_port}/mjpg/video.mjpg",
    f"http://{_ip}:{_port}/video",
    f"http://{_ip}:{_port}/",

    # RTSP
    f"rtsp://{_user}:{_password}@{_ip}:{_port}/stream?transportmode=unicast",
    f"rtsp://{_user}:{_password}@{_ip}:{_port}",
]

# Primeiro, tentar ver o que a câmera responde em HTTP
print("\n1. Verificando HTTP básico:")
print("-" * 60)
try:
    response = requests.get(f"http://{_ip}:{_port}", timeout=2)
    print(f"Status: {response.status_code}")
    print(f"Headers: {dict(response.headers)}")
    print(f"Content (primeiros 200 chars): {response.text[:200]}")
except requests.exceptions.ConnectionError:
    print("✗ Não conseguiu conectar em HTTP")
except Exception as e:
    print(f"✗ Erro: {e}")

# Testar cada URL com ffmpeg
print("\n\n2. Testando URLs com ffmpeg (máx 3 segundos cada):")
print("=" * 60)

for url in urls_to_test:
    print(f"\nTestando: {url}")
    print("-" * 40)
    
    cmd = [
        ffmpeg_path,
        '-rtsp_transport', 'tcp',
        '-connect_timeout', '3000000',
        '-i', url,
        '-vframes', '1',
        '-f', 'image2',
        '-'
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=5, text=False)
        stderr_str = result.stderr.decode('utf-8', errors='ignore')
        
        if b'Stream mapping' in result.stderr or b'Output' in result.stderr or len(result.stdout) > 100:
            print("✓ SUCESSO! Conseguiu conectar")
            # Extrair informações
            for line in stderr_str.split('\n'):
                if any(x in line for x in ['Video:', 'Stream', 'fps', 'bitrate', 'Duration']):
                    print(f"  {line.strip()}")
        else:
            print("✗ Falhou")
            # Mostrar erro principal
            for line in stderr_str.split('\n'):
                if 'Error' in line or 'error' in line or 'Invalid' in line:
                    print(f"  {line.strip()}")
                    break
                    
    except subprocess.TimeoutExpired:
        print("✗ Timeout")
    except Exception as e:
        print(f"✗ Erro: {e}")

print("\n" + "=" * 60)
