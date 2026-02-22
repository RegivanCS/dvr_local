#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Investigação completa das câmeras ISCEE
Testa todos os protocolos, portas e autenticações possíveis
"""

import requests
import subprocess
import cv2
import time
from urllib.parse import quote
import json

# Configuração das câmeras
CAMERAS = [
    {"name": "Entrada", "ip": "192.168.1.3"},
    {"name": "Frente", "ip": "192.168.1.10"},
]

USER = "admin"
PASS = "Herb1745@"
PASS_ENCODED = quote(PASS, safe='')

def test_http_basic(ip, port=80):
    """Testa conectividade básica HTTP"""
    print(f"\n{'='*60}")
    print(f"TESTE 1: HTTP básico em {ip}:{port}")
    print('='*60)
    
    try:
        url = f"http://{ip}:{port}/"
        print(f"\nGET {url}")
        response = requests.get(url, timeout=3)
        print(f"✓ Status: {response.status_code}")
        print(f"  Headers principais:")
        for h in ['Server', 'Content-Type', 'Connection']:
            if h in response.headers:
                print(f"    {h}: {response.headers[h]}")
        
        # Procurar por informações de modelo
        if 'iscee' in response.text.lower() or 'model' in response.text.lower():
            print(f"\n✓ Encontrado 'iscee' ou 'model' na resposta")
        
        return True
    except Exception as e:
        print(f"✗ Erro: {e}")
        return False

def test_snapshot_paths(ip, port=80):
    """Testa diferentes paths de snapshot"""
    print(f"\n{'='*60}")
    print(f"TESTE 2: Paths de snapshot em {ip}:{port}")
    print('='*60)
    
    paths = [
        '/snapshot.cgi',
        '/image.cgi',
        '/image.jpg',
        '/tmpfs/auto.jpg',
        '/cgi-bin/snapshot.cgi',
        '/snap.jpg',
        '/jpg',
        '/picture.jpg',
    ]
    
    results = []
    for path in paths:
        url = f"http://{ip}:{port}{path}"
        try:
            response = requests.get(
                url,
                auth=(USER, PASS),
                timeout=3
            )
            
            # Verificar se é realmente JPEG
            is_jpeg = response.content[:2] == b'\xff\xd8'
            
            print(f"\n  {path}:")
            print(f"    Status: {response.status_code}")
            print(f"    Tamanho: {len(response.content)} bytes")
            
            if response.status_code == 200 and is_jpeg:
                print(f"    ✓ É JPEG válido!")
                results.append((path, url, 'SUCESSO', len(response.content)))
            elif response.status_code == 200:
                print(f"    ✓ Status 200 mas não é JPEG puro")
                results.append((path, url, 'POSSÍVEL', len(response.content)))
            else:
                print(f"    ✗ Status {response.status_code}")
                # Mostrar erro se houver
                if response.status_code in [401, 403]:
                    print(f"      Erro de autenticação")
                elif response.status_code == 404:
                    print(f"      Path não encontrado")
                    
        except requests.Timeout:
            print(f"  {path}: ✗ Timeout")
        except Exception as e:
            print(f"  {path}: ✗ {str(e)[:50]}")
    
    return results

def test_stream_paths(ip, port=80):
    """Testa diferentes paths de stream (MJPEG contínuo)"""
    print(f"\n{'='*60}")
    print(f"TESTE 3: Paths de stream em {ip}:{port}")
    print('='*60)
    
    paths = [
        '/stream',
        '/mjpeg',
        '/video.cgi',
        '/mjpg/video.mjpg',
        '/stream.cgi',
        '/mjpeg.cgi',
    ]
    
    results = []
    for path in paths:
        url = f"http://{ip}:{port}{path}"
        print(f"\n  {path}:")
        
        try:
            response = requests.get(
                url,
                auth=(USER, PASS),
                timeout=5,
                stream=True
            )
            
            print(f"    Status: {response.status_code}")
            print(f"    Content-Type: {response.headers.get('Content-Type', 'N/A')}")
            
            if response.status_code == 200:
                # Ler primeiros bytes
                chunk = next(response.iter_content(chunk_size=500), None)
                if chunk:
                    has_jpeg = b'\xff\xd8' in chunk
                    has_boundary = b'--' in chunk or b'boundary' in str(response.headers).lower()
                    has_content_type = b'Content-Type' in chunk
                    
                    print(f"    Primeiros bytes: {len(chunk)} lidos")
                    
                    if has_jpeg:
                        print(f"    ✓ Contém JPEG")
                    if has_boundary:
                        print(f"    ✓ Contém boundary (MJPEG)")
                    if has_content_type:
                        print(f"    ✓ Multipart (boundaries encontradas)")
                    
                    if has_boundary or has_content_type or 'multipart' in response.headers.get('Content-Type', '').lower():
                        print(f"    ✓ É stream MJPEG!")
                        results.append((path, url, 'MJPEG STREAM'))
                    elif has_jpeg:
                        print(f"    ✓ É stream JPEG contínuo!")
                        results.append((path, url, 'JPEG STREAM'))
                else:
                    print(f"    ✗ Sem dados")
            else:
                print(f"    ✗ Status {response.status_code}")
                
        except Exception as e:
            print(f"    ✗ {str(e)[:50]}")
    
    return results

def test_rtsp_urls(ip):
    """Testa diferentes URLs RTSP"""
    print(f"\n{'='*60}")
    print(f"TESTE 4: RTSP em {ip}:554")
    print('='*60)
    
    rtsp_urls = [
        f"rtsp://{USER}:{PASS}@{ip}:554/stream",
        f"rtsp://{USER}:{PASS_ENCODED}@{ip}:554/stream",
        f"rtsp://{USER}:{PASS}@{ip}:554/",
        f"rtsp://{USER}@{ip}:554/stream",
        f"rtsp://{ip}:554/stream",
        f"rtsp://{USER}:{PASS}@{ip}:554/ch0",
        f"rtsp://{USER}:{PASS}@{ip}:554/ch1",
    ]
    
    results = []
    for url in rtsp_urls:
        print(f"\n  {url[:60]}...")
        
        # Simplificar para display
        display_url = url.replace(PASS, "****")
        display_url = display_url.replace(PASS_ENCODED, "****")
        
        try:
            # Tentar com cv2.VideoCapture
            cap = cv2.VideoCapture(url)
            time.sleep(0.5)  # Dar tempo para tentar conectar
            
            if cap.isOpened():
                print(f"    ✓ cv2.VideoCapture conectado!")
                # Tentar ler um frame
                ret, frame = cap.read()
                if ret:
                    print(f"    ✓ Frame lido: {frame.shape}")
                    results.append((url, 'SUCESSO'))
                else:
                    print(f"    ✓ Conectado mas Frame falhou")
                    results.append((url, 'CONECTADO'))
            else:
                print(f"    ✗ cv2.VideoCapture falhou")
            
            cap.release()
            
        except Exception as e:
            print(f"    ✗ {str(e)[:50]}")
    
    return results

def test_ffmpeg_urls(ip):
    """Testa URLs com ffmpeg"""
    print(f"\n{'='*60}")
    print(f"TESTE 5: ffmpeg em {ip}")
    print('='*60)
    
    urls = [
        f"http://{USER}:{PASS}@{ip}:80/stream",
        f"http://{USER}:{PASS}@{ip}:8899/stream",
        f"rtsp://{USER}:{PASS}@{ip}:554/stream",
    ]
    
    ffmpeg_cmds = [
        # Detectar com ffprobe
        ['ffprobe', '-v', 'error', '-show_format', '-show_streams'],
        # Transcode para MJPEG
        ['ffmpeg', '-rtsp_transport', 'tcp', '-i', None, '-vframes', '1', '-f', 'image2', '-'],
    ]
    
    for url in urls:
        print(f"\n  ffmpeg -i {url[:50]}...")
        try:
            cmd = ['ffmpeg', '-rtsp_transport', 'tcp', '-connect_timeout', '3000000', '-i', url, '-vframes', '1', '-f', 'image2', '-']
            result = subprocess.run(cmd, capture_output=True, timeout=5, text=False)
            
            stderr = result.stderr.decode('utf-8', errors='ignore')
            
            if b'Stream mapping' in result.stderr or len(result.stdout) > 100:
                print(f"    ✓ ffmpeg conseguiu conectar")
                for line in stderr.split('\n'):
                    if 'Video:' in line or 'Stream #' in line:
                        print(f"      {line.strip()}")
            else:
                if 'Unauthorized' in stderr or '401' in stderr:
                    print(f"    ✗ 401 Unauthorized")
                else:
                    print(f"    ✗ Falhou")
                    
        except subprocess.TimeoutExpired:
            print(f"    ✗ Timeout")
        except FileNotFoundError:
            print(f"    ? ffmpeg não encontrado")
        except Exception as e:
            print(f"    ✗ {str(e)[:50]}")

def test_authentication_variants(ip, port=80):
    """Testa variações de autenticação"""
    print(f"\n{'='*60}")
    print(f"TESTE 6: Variações de autenticação em {ip}:{port}")
    print('='*60)
    
    tests = [
        ("Basic Auth", lambda: requests.get(
            f"http://{ip}:{port}/image.cgi",
            auth=(USER, PASS),
            timeout=3
        )),
        ("No Auth", lambda: requests.get(
            f"http://{ip}:{port}/image.cgi",
            timeout=3
        )),
        ("URL encoded password", lambda: requests.get(
            f"http://{USER}:{PASS_ENCODED}@{ip}:{port}/image.cgi",
            timeout=3
        )),
    ]
    
    for name, test_func in tests:
        print(f"\n  {name}:")
        try:
            response = test_func()
            print(f"    Status: {response.status_code}")
            
            if response.status_code == 200:
                is_jpeg = response.content[:2] == b'\xff\xd8'
                print(f"    ✓ Funcionou! {'(JPEG)' if is_jpeg else ''}")
            elif response.status_code == 401:
                print(f"    ✗ 401 Unauthorized")
            elif response.status_code == 403:
                print(f"    ✗ 403 Forbidden")
            elif response.status_code == 404:
                print(f"    ✗ 404 Not Found")
                
        except Exception as e:
            print(f"    ✗ {str(e)[:50]}")

# Executar testes
if __name__ == "__main__":
    print("="*60)
    print("INVESTIGAÇÃO COMPLETA - CÂMERAS ISCEE")
    print("="*60)
    
    for cam in CAMERAS:
        print(f"\n\n{'#'*60}")
        print(f"# CÂMERA: {cam['name']} ({cam['ip']})")
        print(f"{'#'*60}")
        
        ip = cam['ip']
        
        # Teste em porta 80
        test_http_basic(ip, 80)
        results_80 = test_snapshot_paths(ip, 80)
        results_stream_80 = test_stream_paths(ip, 80)
        
        # Teste em porta 8899
        test_http_basic(ip, 8899)
        results_8899 = test_snapshot_paths(ip, 8899)
        results_stream_8899 = test_stream_paths(ip, 8899)
        
        # Teste autenticação
        test_authentication_variants(ip, 80)
        test_authentication_variants(ip, 8899)
        
        # Teste RTSP
        test_rtsp_urls(ip)
        
        # Teste ffmpeg se disponível
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=1)
            test_ffmpeg_urls(ip)
        except:
            print(f"\n{'='*60}")
            print("TESTE 5: ffmpeg - NÃO DISPONÍVEL")
            print(f"{'='*60}")
    
    # Resumo final
    print(f"\n\n{'='*60}")
    print("RESUMO - URLs MAIS PROMISSORAS")
    print(f"{'='*60}")
    print("""
Para câmeras ISCEE, tipicamente:
1. HTTP JPEG: http://admin:Herb1745@[IP]:80/image.cgi ou /image.jpg
2. HTTP MJPEG: http://admin:Herb1745@[IP]:80/stream ou /video.cgi
3. RTSP: rtsp://admin:Herb1745@[IP]:554/stream

Se retornar 401 em RTSP:
- Tentar sem autenticação: rtsp://[IP]:554/stream
- Tentar digest auth via ffmpeg
- Tentar porta 8899 em vez de 554

Se usar com OpenCV:
import cv2
cap = cv2.VideoCapture("http://admin:Herb1745@192.168.1.3:80/stream")
ret, frame = cap.read()

Com timeout:
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Buffer pequeno
cap.set(cv2.CAP_PROP_FPS, 5)  # FPS baixo
    """)
