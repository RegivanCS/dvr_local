import subprocess
import os
import requests

print("=" * 60)
print("TESTE: Descobrir URL correta da câmera")
print("=" * 60)

ffmpeg_path = r"C:\Program Files\Agent\dlls\x64\ffmpeg.exe"

# URLs comuns para câmeras IP
urls_to_test = [
    # HTTP/MJPEG
    "http://admin:Herb1745@192.168.1.3:8899/video",
    "http://admin:Herb1745@192.168.1.3:8899/video.cgi",
    "http://admin:Herb1745@192.168.1.3:8899/mjpg/video.mjpg",
    "http://192.168.1.3:8899/video",
    "http://192.168.1.3:8899/",
    
    # RTSP com tcp
    "rtsp://admin:Herb1745@192.168.1.3:8899/stream?transportmode=unicast",
    "rtsp://admin:Herb1745@192.168.1.3:8899",
    
    # Tentar acessar a web
]

# Primeiro, tentar ver o que a câmera responde em HTTP
print("\n1. Verificando HTTP básico:")
print("-" * 60)
try:
    response = requests.get("http://192.168.1.3:8899", timeout=2)
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
