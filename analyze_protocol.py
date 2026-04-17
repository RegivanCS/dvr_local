#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tentar descobrir como as câmeras servem o vídeo usando análise JavaScript
"""

import requests
import re
import json
import os

# Carrega IP/credenciais da configuração (definidos pela tela de configurações)
def _load_camera(index=0):
    config_path = os.path.join(os.path.dirname(__file__), 'cameras_config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            cams = list(json.load(f).get('cameras', {}).values())
        if cams and index < len(cams):
            c = cams[index]
            return c.get('ip', ''), c.get('port', 554), c.get('user', 'admin'), c.get('password', '')
    except:
        pass
    return None, None, None, None

_ip, _port, _user, _password = _load_camera(1)  # usa segunda câmera por padrão
if not _ip:
    _ip, _port, _user, _password = _load_camera(0)
if not _ip:
    print("[ERRO] Nenhuma câmera configurada. Adicione câmeras pela tela de configurações.")
    exit(1)

print("🔍 Analisando como as câmeras servem vídeo...\n")

# Câmera configurada via tela de configurações
print("=" * 70)
print(f"🎥 Testando Câmera na porta {_port}")
print("=" * 70)

base_url = f"http://{_ip}:{_port}"
auth = (_user, _password)

# Obter os arquivos JavaScript
print("\n1️⃣ Obtendo informações dos arquivos JavaScript...\n")

js_files = ['pluginVersion.js', 'js/main.js']

for js_file in js_files:
    try:
        url = f"{base_url}/{js_file}"
        print(f"📄 {js_file}:")
        response = requests.get(url, auth=auth, timeout=3)
        
        if response.status_code == 200 and response.content:
            print(f"   ✓ Status 200, {len(response.content)} bytes")
            
            # Mostrar primeiros 500 caracteres
            content = response.text[:500]
            print(f"   Conteúdo:\n{content}\n")
            
            # Procurar por URLs ou paths interessantes
            urls = re.findall(r'(["\']http[^"\']*["\'])', response.text)
            if urls:
                print(f"   URLs encontradas:")
                for url_match in urls[:5]:
                    print(f"     - {url_match}")
        else:
            print(f"   ❌ Status {response.status_code}\n")
    except Exception as e:
        print(f"   ❌ Erro: {e}\n")

# Tentar acessar outros resources conhecidos
print("\n2️⃣ Tentando acessar outros resources HTTP...\n")

resources = [
    '/api/stream',
    '/api/mjpstream',
    '/api/video',
    '/stream.cgi',
    '/mjpeg.cgi',
    '/VideoStreaming.cgi',
    '/videostream.asf',
    '/axis-cgi/mjpg/video.cgi',
    '/nphMotionJpeg',
    '/vod/hls',
    '/h264',
    '/mjpg',
    '/rtsp',
    '/cgi-bin/admin/param.cgi',
    '/cgi-bin/querySysInfo',
    '/cgi-bin/queryConfig',
]

for resource in resources[:10]:
    try:
        url = f"{base_url}{resource}"
        r = requests.get(url, auth=auth, timeout=1, allow_redirects=False)
        if r.status_code in [200, 302, 304]:
            print(f"✓ {resource} -> Status {r.status_code}")
            if r.status_code == 302:
                print(f"  Redirect to: {r.headers.get('Location', 'N/A')}")
    except:
        pass

# Tentar RTSP
print("\n3️⃣ Tentando RTSP (alternativa ao HTTP)...\n")

rtsp_urls = [
    f'rtsp://{_ip}:554/stream',
    f'rtsp://{_ip}:554/stream/ch0',
    f'rtsp://{_user}:{_password}@{_ip}:554/stream',
    f'rtsp://{_ip}:{_port}/stream',
]

print("RTSP URLs para tentar manualmente:")
for url in rtsp_urls:
    print(f"  - {url}")

# Tentar acessar como GET request (algumas câmeras usam VideoStreaming.cgi)
print("\n4️⃣ Testando VideoStreaming.cgi (se houver)...\n")

try:
    url = f"{base_url}/cgi-bin/VideoStreaming.cgi"
    r = requests.get(url, auth=auth, timeout=2, params={'action': 'play'})
    print(f"Status: {r.status_code}")
    print(f"Content-Type: {r.headers.get('Content-Type', 'N/A')}")
    print(f"Tamanho: {len(r.content)} bytes")
    
    if r.content[:4] == b'ID3=':
        print("✓ Isso parece ser um stream MJPEG!")
except Exception as e:
    print(f"Erro: {e}")

print("\n5️⃣ Comparando com webserver.py (se estiver rodando)...\n")

print("Se Agent DVR estiver rodando, as imagens estão em:")
print("  http://localhost:8090/grab.jpg?oid=X")
print("\nVocê poderia usar o webserver.py existente que já funciona!")
print("Ele acessa as câmeras através do Agent DVR.")

print("\n" + "=" * 70)
