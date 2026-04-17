#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Explorar a interface web das câmeras
"""

import requests
from bs4 import BeautifulSoup
import re
import json
import os

# Carrega câmeras da configuração (IPs são definidos pela tela de configurações)
def _load_cameras():
    config_path = os.path.join(os.path.dirname(__file__), 'cameras_config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            raw = json.load(f).get('cameras', {})
        return [{'ip': c.get('ip'), 'port': c.get('port', 80), 'name': c.get('name', 'Câmera'),
                 'user': c.get('user', ''), 'password': c.get('password', '')} for c in raw.values()]
    except:
        return []

print("🔍 Explorando interface web das câmeras...\n")

cameras = _load_cameras()
if not cameras:
    print("[ERRO] Nenhuma câmera configurada. Adicione câmeras pela tela de configurações.")
    exit(1)

for camera in cameras:
    print(f"=" * 70)
    print(f"🎥 {camera['name']} - {camera['ip']}:{camera['port']}")
    print(f"=" * 70)
    
    url = f"http://{camera['ip']}:{camera['port']}/"
    
    try:
        # Teste 1: Sem autenticação
        print("\n1️⃣ Teste SEM autenticação:")
        r = requests.get(url, timeout=3)
        print(f"   Status: {r.status_code}")
        print(f"   Content-Type: {r.headers.get('Content-Type', 'N/A')}")
        
        # Teste 2: Com autenticação
        user = camera.get('user', 'admin')
        password = camera.get('password', '')
        print(f"\n2\ufe0f\u20e3 Teste COM autenticação ({user}/***):")
        r = requests.get(url, auth=(user, password), timeout=3)
        print(f"   Status: {r.status_code}")
        print(f"   Content-Type: {r.headers.get('Content-Type', 'N/A')}")
        print(f"   Tamanho: {len(r.content)} bytes")
        
        # Mostrar conteúdo
        print(f"\n3️⃣ Conteúdo da página:")
        content = r.text[:2000]  # Primeiros 2000 caracteres
        print(content)
        
        # Procurar por métodos de acesso a imagens
        print(f"\n4️⃣ Buscando referências a imagens/streams:")
        
        patterns = [
            (r'src=["\']([^"\']*jpg[^"\']*)["\']', 'Images em src'),
            (r'href=["\']([^"\']*jpg[^"\']*)["\']', 'Links JPG'),
            (r'<img[^>]*>', 'Tags img'),
            (r'\.cgi[\'"]?', 'Referências .cgi'),
            (r'/stream[\'"]?', 'Stream'),
            (r'/video[\'"]?', 'Video'),
            (r'snapshot', 'Snapshot'),
        ]
        
        found_anything = False
        for pattern, desc in patterns:
            matches = re.findall(pattern, r.text, re.IGNORECASE)
            if matches:
                print(f"   ✓ {desc}:")
                for match in matches[:3]:  # Mostrar máximo 3 resultados
                    print(f"     - {match[:100]}")
                found_anything = True
        
        if not found_anything:
            print("   ❌ Nenhuma referência encontrada")
        
        # Teste 3: Verificar headers de resposta
        print(f"\n5️⃣ Headers da resposta:")
        for key, value in r.headers.items():
            print(f"   {key}: {value}")
        
        # Teste 4: Diferentes portas alternativas
        print(f"\n6️⃣ Testando portas alternativas:")
        alt_ports = [8080, 8888, 8899, 554, 9000, 5000]
        for port in alt_ports[:3]:
            try:
                r = requests.get(f"http://{camera['ip']}:{port}/", auth=(camera.get('user', 'admin'), camera.get('password', '')), timeout=1)
                print(f"   ✓ Porta {port}: Status {r.status_code}")
            except:
                print(f"   - Porta {port}: Não responde")
        
    except Exception as e:
        print(f"❌ Erro: {e}")
    
    print()

print("=" * 70)
print("💡 DICA: Se nenhum path funcionar, a câmera pode:")
print("   1. Não ter endpoint de snapshot HTTP")
print("   2. Usar RTSP ou outro protocolo")
print("   3. Estar em um modo que requer acesso especial")
print("   4. Ter credenciais diferentes")
print("=" * 70)
