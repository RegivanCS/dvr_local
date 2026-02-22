#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Investigar como a interface web ISCEE acessa a câmera
"""

import requests
import re
import json

CAMERAS = [
    {"name": "Entrada", "ip": "192.168.1.3"},
    {"name": "Frente", "ip": "192.168.1.10"},
]

def investigate_web_interface(ip):
    """Investigar a interface web para encontrar endpoints"""
    print(f"\n{'='*60}")
    print(f"ANALISANDO INTERFACE WEB: {ip}")
    print('='*60)
    
    try:
        # Obter página inicial
        r = requests.get(f"http://{ip}", auth=("admin", "Herb1745@"), timeout=3)
        html = r.text
        
        # Procurar por padrões de URL/API
        print("\n1. Procurando por patterns de URL/API no HTML...")
        
        # Padrões a procurar
        patterns = [
            (r'"([^"]*snapshot[^"]*)"', 'snapshot URLs'),
            (r'"([^"]*stream[^"]*)"', 'stream URLs'),
            (r'"([^"]*video[^"]*)"', 'video URLs'),
            (r'"([^"]*image[^"]*)"', 'image URLs'),
            (r'"([^"]*rtsp[^"]*)"', 'RTSP URLs'),
            (r'"([^"]*mjpeg[^"]*)"', 'MJPEG URLs'),
            (r'"api/([^"]*)"', 'API endpoints'),
            (r'"cgi-bin/([^"]*)"', 'CGI endpoints'),
            (r'url\s*:\s*["\']([^"\']*)["\']', 'AJAX URLs'),
        ]
        
        found_urls = set()
        for pattern, desc in patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            if matches:
                print(f"\n  {desc}:")
                for match in matches[:5]:  # Primeiros 5
                    if match.strip():
                        found_urls.add(match)
                        print(f"    - {match[:80]}")
        
        # Procurar por JavaScript específico
        print("\n2. Arquivos JavaScript carregados:")
        js_files = re.findall(r'src="([^"]*(?:\.js)?)"', html)
        for js in js_files[:10]:
            if js and 'js' in js.lower():
                print(f"    - {js}")
        
        # Procurar por dados inline
        print("\n3. Procurando por dados hardcoded no HTML...")
        
        # Buscar por window.xxx = ...
        window_vars = re.findall(r'window\.(\w+)\s*=\s*["\']([^"\']+)["\']', html)
        if window_vars:
            print(f"  Variáveis globais encontradas:")
            for name, value in window_vars[:5]:
                print(f"    - {name} = {value[:60]}")
        
        # Buscar por configurações JSON
        json_patterns = re.findall(r'(\{[^}]*(?:url|stream|image|rtsp)[^}]*\})', html)
        if json_patterns:
            print(f"\n  Possíveis objetos JSON com config:")
            for obj in json_patterns[:3]:
                try:
                    print(f"    - {json.loads(obj)}")
                except:
                    print(f"    - {obj[:100]}...")
        
    except Exception as e:
        print(f"✗ Erro ao acessar: {e}")

def test_web_api_endpoints(ip):
    """Testar endpoints de API que câmeras ISCEE podem ter"""
    print(f"\n{'='*60}")
    print(f"TESTANDO ENDPOINTS DE API: {ip}")
    print('='*60)
    
    endpoints = [
        # Informações de câmera
        "/api/device/info",
        "/api/camera/info",
        "/api/system/config",
        
        # Stream
        "/api/stream/live",
        "/api/video/live",
        "/api/stream/start",
        
        # Outras
        "/api/user/login",
        "/mjpeg/start",
        "/stream/start",
        
        # CGI padrão
        "/cgi-bin/videostream.cgi",
        "/cgi-bin/videostream.asf",
        "/cgi-bin/video.cgi",
        "/cgi-bin/mjpeg",
    ]
    
    for endpoint in endpoints:
        try:
            url = f"http://{ip}{endpoint}"
            r = requests.get(url, auth=("admin", "Herb1745@"), timeout=2)
            
            if r.status_code != 404:
                print(f"\n  ✓ {endpoint}")
                print(f"    Status: {r.status_code}")
                print(f"    Content-Type: {r.headers.get('Content-Type', 'N/A')}")
                print(f"    Tamanho: {len(r.content)} bytes")
                
                if r.status_code == 200:
                    # Mostrar primeiros bytes
                    sample = r.content[:100]
                    if sample:
                        print(f"    Primeiros bytes: {sample[:50]}")
        except:
            pass

def test_javascript_api_calls(ip):
    """Baixar JavaScript e procurar por chamadas de API"""
    print(f"\n{'='*60}")
    print(f"ANALISANDO JAVASCRIPT: {ip}")
    print('='*60)
    
    js_files = [
        "/js/main.js",
        "/js/player.js",
        "/js/stream.js",
        "/js/video.js",
        "/js/api.js",
    ]
    
    for js_file in js_files:
        try:
            url = f"http://{ip}{js_file}"
            r = requests.get(url, auth=("admin", "Herb1745@"), timeout=2)
            
            if r.status_code == 200:
                print(f"\n  ✓ {js_file} - Encontrado")
                js_content = r.text
                
                # Procurar por padrões de URL
                urls = re.findall(r'["\']([/\w\-\.]+\.[a-z]{2,4}["\']', js_content)
                if urls:
                    print(f"    URLs encontradas:")
                    for url in list(set(urls))[:10]:
                        print(f"      - {url}")
                
                # Procurar por chamadas fetch/ajax
                ajax_calls = re.findall(r'(fetch|ajax|XMLHttpRequest).*?["\']([^"\']+)["\']', js_content)
                if ajax_calls:
                    print(f"    Chamadas AJAX encontradas:")
                    for method, endpoint in ajax_calls[:5]:
                        print(f"      - {method}: {endpoint}")
                        
        except:
            pass

def test_rtsp_without_auth(ip):
    """Testar RTSP sem autenticação (alguns modelos não usam auth em RTSP)"""
    print(f"\n{'='*60}")
    print(f"TESTANDO RTSP SEM AUTENTICAÇÃO: {ip}:554")
    print('='*60)
    
    import cv2
    import time
    
    urls = [
        f"rtsp://{ip}:554/",
        f"rtsp://{ip}:554/stream",
        f"rtsp://{ip}:554/ch0",
        f"rtsp://{ip}:554/ch1",
        f"rtsp://{ip}:554/stream1",
        f"rtsp://{ip}:554/streaming/channels/101",
    ]
    
    for url in urls:
        try:
            cap = cv2.VideoCapture(url)
            time.sleep(0.5)
            
            if cap.isOpened():
                print(f"\n  ✓ SUCESSO: {url}")
                ret, frame = cap.read()
                if ret:
                    print(f"    ✓ Frame lido: {frame.shape}")
                cap.release()
            else:
                print(f"\n  ✗ {url}")
                
        except Exception as e:
            print(f"\n  ✗ {url} - {str(e)[:40]}")

def test_http_streaming(ip, port=80):
    """Testar se HTTP streaming funciona com diferentes métodos"""
    print(f"\n{'='*60}")
    print(f"TESTANDO HTTP STREAMING: {ip}:{port}")
    print('='*60)
    
    # Teste 1: Range request
    print("\n1. Testando Range Request:")
    try:
        r = requests.get(
            f"http://{ip}:{port}/stream",
            auth=("admin", "Herb1745@"),
            headers={"Range": "bytes=0-1000"},
            timeout=2
        )
        print(f"  Status: {r.status_code}")
        if 206 in [r.status_code]:
            print(f"  ✓ Suporta partial content!")
    except Exception as e:
        print(f"  ✗ {e}")
    
    # Teste 2: Accept encoding
    print("\n2. Testando diferentes Accept headers:")
    accepts = [
        "image/jpeg",
        "multipart/x-mixed-replace",
        "video/mp4",
        "*/*"
    ]
    
    for accept in accepts:
        try:
            r = requests.get(
                f"http://{ip}:{port}/stream",
                auth=("admin", "Herb1745@"),
                headers={"Accept": accept},
                timeout=2,
                stream=True
            )
            print(f"  Accept: {accept}")
            chunk = next(r.iter_content(chunk_size=100), b'')
            if chunk:
                if b'\xff\xd8' in chunk:
                    print(f"    ✓ Contém JPEG!")
                if b'--' in chunk:
                    print(f"    ✓ É MJPEG (multipart)!")
                print(f"    Content-Type: {r.headers.get('Content-Type')}")
        except Exception as e:
            print(f"  ✗ {str(e)[:40]}")

# Executar
if __name__ == "__main__":
    for cam in CAMERAS:
        investigate_web_interface(cam['ip'])
        test_web_api_endpoints(cam['ip'])
        test_javascript_api_calls(cam['ip'])
        test_rtsp_without_auth(cam['ip'])
        test_http_streaming(cam['ip'], 80)
        test_http_streaming(cam['ip'], 8899)
        
        break  # Apenas primeira câmera para não demorar
    
    print(f"\n\n{'='*60}")
    print("CONCLUSÕES E PRÓXIMOS PASSOS")
    print('='*60)
    print("""
Se nenhum endpoint CGI tradicional funciona:
1. A câmera pode estar usando um protocolo proprietário
2. Pode exigir conexão via interface web (plugin/applet Java antigo)
3. O stream RTSP pode exigir autenticação especial

PRÓXIMAS AÇÕES:
1. Verificar se a câmera ISCEE usa plugin Java/ActiveX antigo
2. Pesquisar documentação específica de câmeras ISCEE
3. Tentar acessar via Agent DVR que já funciona
4. Considerar gravar stream via ffmpeg se disponível
    """)
