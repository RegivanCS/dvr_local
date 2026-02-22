#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Analisar o conteúdo das imagens retornadas pelas câmeras
"""

import requests
import binascii

print("Analisando imagens das câmeras...\n")

# Câmera 1
print("="*70)
print("CÂMERA 1: 192.168.1.3:80/snapshot.cgi")
print("="*70)

url1 = "http://192.168.1.3:80/snapshot.cgi"
response1 = requests.get(url1, auth=('admin', 'Herb1745@'), timeout=3)

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
print("CÂMERA 2: 192.168.1.10:80/snapshot.cgi")
print("="*70)

url2 = "http://192.168.1.10:80/snapshot.cgi"
response2 = requests.get(url2, auth=('admin', 'Herb1745@'), timeout=3)

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
r = requests.get(url1, auth=('admin', 'Herb1745@'), headers=headers, timeout=3)
print(f"Tamanho: {len(r.content)} bytes")

print("\nCâmera 2 com User-Agent:")
r = requests.get(url2, auth=('admin', 'Herb1745@'), headers=headers, timeout=3)
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
