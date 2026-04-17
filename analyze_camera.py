import requests
from bs4 import BeautifulSoup
import re
import json
import os

# Carrega IP/porta/credenciais da configuração (definidos pela tela de configurações)
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
print("Analisando HTML da câmera para descobrir stream URL")
print("=" * 60)

try:
    # Fazer requisição para a câmera
    response = requests.get(f"http://{_ip}:{_port}/", timeout=5, auth=(_user, _password))
    print(f"✓ Conectado com sucesso (Status: {response.status_code})")
    print(f"Tipo: {response.headers.get('Content-Type', 'desconhecido')}")
    
    # Parse HTML
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Procurar por tags que contenham URLs de stream
    print("\n" + "=" * 60)
    print("Procurando por URLs de stream no HTML...")
    print("=" * 60)
    
    # Procurar em img src
    imgs = soup.find_all('img')
    print(f"\nEncontradas {len(imgs)} imagens:")
    for img in imgs[:5]:
        src = img.get('src', 'sem src')
        print(f"  - {src}")
    
    # Procurar por src/href que contenham stream, video, mjpeg
    print("\n\nProcurando por links relacionados a stream/video:")
    for element in soup.find_all(['a', 'script', 'source', 'video']):
        for attr in ['src', 'href', 'data']:
            val = element.get(attr, '')
            if any(x in str(val).lower() for x in ['stream', 'video', 'mjpeg', 'rtsp']):
                print(f"  [{element.name}] {attr}: {val}")
    
    # Procurar em scripts por URLs
    print("\n\nProcurando em scripts por URLs:")
    scripts = soup.find_all('script')
    for script in scripts:
        if script.string:
            # Procurar por padrões de URL
            urls = re.findall(r'(?:rtsp|http)://[^\s"\'<>]+', script.string)
            for url in urls:
                print(f"  - {url}")
    
    # Salvar HTML para análise manual
    print("\n\nSalvando HTML completo em 'camera_page.html' para análise...")
    with open('camera_page.html', 'w', encoding='utf-8') as f:
        f.write(response.text)
    print("✓ Arquivo salvo!")
    
except requests.exceptions.ConnectionError:
    print("✗ Erro ao conectar")
except Exception as e:
    print(f"✗ Erro: {e}")

# Também procurar por paths comuns de câmeras IP
print("\n\n" + "=" * 60)
print("Testando paths comuns de câmeras IP...")
print("=" * 60)

common_paths = [
    "/axis-cgi/mjpg/video.cgi",
    "/nphMotionJpeg",
    "/image.cgi",
    "/stream",
    "/streaming/channels/1",
    "/h264",
    "/cgi-bin/realmonitor",
    "/onvif/device_service",
]

for path in common_paths:
    try:
        url = f"http://{_ip}:{_port}{path}"
        response = requests.head(url, timeout=2, auth=(_user, _password))
        print(f"\u2713 {path}: {response.status_code}")
    except:
        pass
