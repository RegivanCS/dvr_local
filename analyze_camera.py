import requests
from bs4 import BeautifulSoup
import re

print("=" * 60)
print("Analisando HTML da câmera para descobrir stream URL")
print("=" * 60)

try:
    # Fazer requisição para a câmera
    response = requests.get("http://192.168.1.3:8899/", timeout=5, auth=("admin", "Herb1745"))
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
        url = f"http://192.168.1.3:8899{path}"
        response = requests.head(url, timeout=2, auth=("admin", "Herb1745"))
        print(f"✓ {path}: {response.status_code}")
    except:
        pass
