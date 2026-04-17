import requests
import json
import os

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

camera_ip, camera_port, _user, _password = _load_first_camera()
if not camera_ip:
    print("[ERRO] Nenhuma câmera configurada. Adicione câmeras pela tela de configurações.")
    exit(1)

auth = (_user, _password)

paths = [
    '/snapshot.cgi', '/image.jpg', '/tmpfs/auto.jpg', '/cgi-bin/snapshot.cgi',
    '/ISAPI/Streaming/channels/1/picture', '/Streaming/channels/1/picture',
    '/Image/Jpeg', '/onvif/snapshot', '/snapshot.jpg', '/img/snapshot.jpg',
    '/snap.jpg', '/jpg/image.jpg', '/jpg', '/snapshot', '/picture',
    '/picture.jpg', '/live.jpg', '/nphMotionJpeg', '/motion.jpg',
    '/videofeed', '/stream', '/stream.jpg', '/video', '/mjpeg', '/?action=snapshot'
]

print("Testando paths...\n")
found_results = []

for path in paths:
    url = f"http://{camera_ip}:{camera_port}{path}"
    try:
        r = requests.get(url, auth=auth, timeout=1)
        is_jpeg = r.content[:2] == b'\xff\xd8'
        if is_jpeg and len(r.content) > 1000:
            found_results.append((path, len(r.content)))
            print(f"[JPEG] {path} - {len(r.content)} bytes")
        elif r.status_code == 200 and len(r.content) > 100:
            content_type = r.headers.get('Content-Type', '')
            print(f"[OK] {path} - {len(r.content)} bytes - {content_type}")
    except:
        pass

print(f"\n=== Resumo ===")
print(f"Encontrados {len(found_results)} resultados com JPEG real")
for path, size in found_results:
    print(f"  {path}: {size} bytes")
