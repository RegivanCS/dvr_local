import requests
import base64

print("=" * 60)
print("Investigar modelo e capacidades da câmera")
print("=" * 60)

# Tentar obter informações do devinfo
endpoints = [
    "/api/version",
    "/api/devinfo",
    "/cgi-bin/devinfo",
    "/dev/version",
    "/sys/version",
]

for endpoint in endpoints:
    try:
        url = f"http://192.168.1.3:8899{endpoint}"
        response = requests.get(url, auth=("admin", "Herb1745"), timeout=2)
        if response.status_code == 200:
            print(f"\n✓ {endpoint}: Status 200")
            print(f"  Content-Type: {response.headers.get('Content-Type')}")
            print(f"  Body: {response.text[:200]}")
    except:
        pass

# Tentar descobrir stream usando padrão simples
print("\n" + "=" * 60)
print("Tentando capture manual com HTTP streaming")
print("=" * 60)

import cv2
import threading
import time

def capture_frames():
    print("\nTentando capturar frames continuamente de /stream...")
    
    response = requests.get(
        "http://192.168.1.3:8899/stream",
        auth=("admin", "Herb1745"),
        stream=True,
        timeout=5
    )
    
    print(f"Status: {response.status_code}")
    print(f"Content-Type: {response.headers.get('Content-Type')}")
    
    # Procurar por boundary MJPEG
    frames_found = 0
    for chunk in response.iter_content(chunk_size=1024):
        if b'--' in chunk:
            print(f"✓ Encontra boundary MJPEG!")
            if b'Content-Type: image/jpeg' in chunk:
                print("✓ Contém JPEG!")
                frames_found += 1
                if frames_found >= 2:
                    break
        
        if frames_found == 0 and len(chunk) > 0:
            # Mostrar primeiros bytes
            print(f"Primeiros bytes: {chunk[:100]}")

try:
    capture_frames()
except Exception as e:
    print(f"✗ Erro: {e}")

print("\n" + "=" * 60)
print("Próximas ações:")
print("1. Qual é o modelo exato da câmera?")
print("2. Você consegue acessar a câmera via navegador normalmente?")
print("3. A câmera estava funcionando com a URL anterior (Herb1745@@)?")
print("=" * 60)
