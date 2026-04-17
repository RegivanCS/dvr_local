#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Testar todos os paths conhecidos de câmeras IP para encontrar a imagem
"""

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os

# Lista extensa de paths conhecidos para câmeras IP
PATHS_TO_TEST = [
    # ISCEE
    '/snapshot.cgi',
    '/tmpfs/auto.jpg',
    '/cgi-bin/snapshot.cgi',
    '/image.jpg',
    
    # Hikvision
    '/ISAPI/Streaming/channels/1/picture',
    '/Streaming/channels/1/picture',
    '/Image/Jpeg',
    '/Image/Jpeg/Channels/1',
    
    # Dahua
    '/cgi-bin/snapshot.cgi',
    '/onvif/snapshot',
    '/cgi-bin/snapshot',
    
    # Intelbras
    '/snapshot.jpg',
    '/img/snapshot.jpg',
    '/get_snapshot.cgi',
    '/video.cgi',
    
    # Genérico
    '/snap.jpg',
    '/jpg/image.jpg',
    '/jpg',
    '/jpg/',
    '/snapshot',
    '/picture',
    '/picture.jpg',
    '/live.jpg',
    '/nphMotionJpeg',
    '/motion.jpg',
    '/axis-cgi/jpg/image.cgi',
    '/axis-cgi/mjpg/video.cgi',
    '/videofeed',
    '/stream',
    '/stream.jpg',
    '/stream.cgi',
    '/mjpeg.cgi',
    '/encode?type=snapshot',
    '/img/video.asf',
    '/cgi/jpeg.cgi',
    '/camimg.jpg',
    '/getimage.cgi',
    '/stw-cgi-bin/getimage.cgi',
    '/GetJpeg.dcgi',
    '/cgi-bin/GetJpeg.dcgi',
    '/webcam.jpg',
    '/webcam',
    '/image',
    '/shoot.jpg',
    '/capture',
    '/capture.jpg',
    '/screencap.jpg',
    '/screen.jpg',
    '/photo',
    '/photo.jpg',
    '/pic',
    '/pic.jpg',
    '/jpeg',
    '/live',
    '/view.jpg',
    '/view',
    '/snap',
    '/capture.cgi',
    '/SnapshotJPEG'
]

def test_path(ip, port, user, password, path):
    """Testar um path específico"""
    url = f"http://{ip}:{port}{path}"
    try:
        response = requests.get(url, auth=(user, password), timeout=2)
        if response.status_code == 200:
            # Verificar se é JPEG válido ou arquivo com tamanho razoável
            if response.content[:2] == b'\xff\xd8' or len(response.content) > 1000:
                return {
                    'path': path,
                    'status': 'SUCESSO',
                    'size': len(response.content),
                    'is_jpeg': response.content[:2] == b'\xff\xd8'
                }
            elif len(response.content) > 100 and len(response.content) < 1000:
                return {
                    'path': path,
                    'status': 'POSSÍVEL',
                    'size': len(response.content),
                    'is_jpeg': response.content[:2] == b'\xff\xd8'
                }
    except:
        pass
    return None

print("🔍 Testando câmeras para encontrar o path correto da imagem...\n")

# Carrega câmeras da configuração (IPs são definidos pela tela de configurações)
def _load_cameras():
    config_path = os.path.join(os.path.dirname(__file__), 'cameras_config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            raw = json.load(f).get('cameras', {})
        return [{'ip': c.get('ip'), 'port': c.get('port', 80), 'name': c.get('name', 'Câmera'),
                 'user': c.get('user', 'admin'), 'password': c.get('password', '')} for c in raw.values()]
    except:
        return []

cameras = _load_cameras()
if not cameras:
    print("[ERRO] Nenhuma câmera configurada. Adicione câmeras pela tela de configurações.")
    exit(1)

for camera in cameras:
    print(f"=" * 70)
    print(f"🎥 {camera['name']} - {camera['ip']}:{camera['port']}")
    print(f"=" * 70)
    
    results_sucesso = []
    results_possivel = []
    
    # Testar com ThreadPoolExecutor para ir mais rápido
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for path in PATHS_TO_TEST:
            futures.append(executor.submit(test_path, camera['ip'], camera['port'], camera.get('user', 'admin'), camera.get('password', ''), path))
        
        for future in as_completed(futures):
            result = future.result()
            if result:
                if result['status'] == 'SUCESSO':
                    results_sucesso.append(result)
                else:
                    results_possivel.append(result)
    
    if results_sucesso:
        print("\n✓ PATHS FUNCIONANDO (IMAGEM VÁLIDA):\n")
        for r in sorted(results_sucesso, key=lambda x: x['size'], reverse=True):
            print(f"  {r['path']}")
            print(f"    - Size: {r['size']} bytes")
            print(f"    - JPEG válido: {'Sim' if r['is_jpeg'] else 'Não'}")
    
    if results_possivel:
        print("\n~ PATHS POSSÍVEIS (VERIFICAR):\n")
        for r in sorted(results_possivel, key=lambda x: x['size'], reverse=True)[:5]:
            print(f"  {r['path']}")
            print(f"    - Size: {r['size']} bytes")
            print(f"    - JPEG válido: {'Sim' if r['is_jpeg'] else 'Não'}")
    
    if not results_sucesso and not results_possivel:
        print("\n❌ Nenhum path funcionando encontrado!")
        print("\nTestando acessibilidade básica...")
        
        try:
            response = requests.get(f"http://{camera['ip']}:{camera['port']}/", auth=(user, password), timeout=3)
            print(f"  Raiz: Status {response.status_code}")
            
            # Verificar se é página web
            content_type = response.headers.get('Content-Type', '')
            print(f"  Content-Type: {content_type}")
            print(f"  Tamanho: {len(response.content)} bytes")
            
            # Mostrar alguns links ou formulários encontrados
            if 'text/html' in content_type:
                content = response.text.lower()
                print("\n  Possíveis recursos encontrados:")
                for keyword in ['jpg', 'jpeg', 'snapshot', 'image', 'stream', 'video', 'cgi', 'live']:
                    if keyword in content:
                        print(f"    - Menção a '{keyword}' encontrada")
        except Exception as e:
            print(f"  Erro: {e}")
    
    print()

print("=" * 70)
print("✓ Teste concluído!")
print("=" * 70)
