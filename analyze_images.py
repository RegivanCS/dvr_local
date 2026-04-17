#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Analisar o conteúdo das imagens retornadas pelas câmeras
"""

import requests
import binascii
import json
import os

# Carrega câmeras da configuração (IPs são definidos pela tela de configurações)
def _load_cameras():
    config_path = os.path.join(os.path.dirname(__file__), 'cameras_config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return list(json.load(f).get('cameras', {}).values())
    except:
        return []

cameras = _load_cameras()
if not cameras:
    print("[ERRO] Nenhuma câmera configurada. Adicione câmeras pela tela de configurações.")
    exit(1)

print("Analisando imagens das câmeras...\n")

# Câmera 1
print("="*70)
cam1 = cameras[0]
print(f"CÂMERA 1: {cam1['ip']}:{cam1['port']}/snapshot.cgi")
print("="*70)

url1 = f"http://{cam1['ip']}:{cam1['port']}/snapshot.cgi"
response1 = requests.get(url1, auth=(cam1.get('user', 'admin'), cam1.get('password', '')), timeout=3)

print(f"Status Code: {response1.status_code}")
print(f"Tamanho: {len(response1.content)} bytes")
print(f"Headers: {dict(response1.headers)}")
print(f"\nPrimeiros 200 bytes (hex):")
print(binascii.hexlify(response1.content[:200]).decode())
print(f"\nPrimeiros 200 bytes (texto):")
print(response1.content[:200])

# Verificar se é JPEG válido
if response1.content[:2] == b'\xff\xd8':
    print("\n✓ É um JPEG válido")
else:
    print("\n❌ NÃO é um JPEG válido")
    print("Pode ser HTML/erro. Conteúdo completo:")
    print(response1.text[:500])

# Câmera 2
print("\n" + "="*70)
cam2 = cameras[1] if len(cameras) > 1 else cameras[0]
print(f"CÂMERA 2: {cam2['ip']}:{cam2['port']}/snapshot.cgi")
print("="*70)

url2 = f"http://{cam2['ip']}:{cam2['port']}/snapshot.cgi"
response2 = requests.get(url2, auth=(cam2.get('user', 'admin'), cam2.get('password', '')), timeout=3)

print(f"Status Code: {response2.status_code}")
print(f"Tamanho: {len(response2.content)} bytes")
print(f"Headers: {dict(response2.headers)}")
print(f"\nPrimeiros 200 bytes (hex):")
print(binascii.hexlify(response2.content[:200]).decode())
print(f"\nPrimeiros 200 bytes (texto):")
print(response2.content[:200])

# Verificar se é JPEG válido
if response2.content[:2] == b'\xff\xd8':
    print("\n✓ É um JPEG válido")
else:
    print("\n❌ NÃO é um JPEG válido")
    print("Pode ser HTML/erro. Conteúdo completo:")
    print(response2.text[:500])

# Testar com User-Agent
print("\n" + "="*70)
print("TESTE COM USER-AGENT CUSTOMIZADO")
print("="*70)

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

print("\nCâmera 1 com User-Agent:")
r = requests.get(url1, auth=(cam1.get('user', 'admin'), cam1.get('password', '')), headers=headers, timeout=3)
print(f"Tamanho: {len(r.content)} bytes")

print("\nCâmera 2 com User-Agent:")
r = requests.get(url2, auth=(cam2.get('user', 'admin'), cam2.get('password', '')), headers=headers, timeout=3)
print(f"Tamanho: {len(r.content)} bytes")

# Salvar imagens para inspeção
print("\n" + "="*70)
print("SALVANDO IMAGENS PARA INSPEÇÃO")
print("="*70)

with open('debug_cam1.jpg', 'wb') as f:
    f.write(response1.content)
print("✓ Salvo: debug_cam1.jpg")

with open('debug_cam2.jpg', 'wb') as f:
    f.write(response2.content)
print("✓ Salvo: debug_cam2.jpg")
