import socket
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress

print("=" * 60)
print("🔍 Buscando câmeras na rede local...")
print("=" * 60)

# Descobrir IP da rede local
hostname = socket.gethostname()
local_ip = socket.gethostbyname(hostname)
print(f"\nSeu IP: {local_ip}")

# Extrair rede (assumindo /24)
network = '.'.join(local_ip.split('.')[:-1])
print(f"Rede: {network}.0/24\n")

# Portas comuns de câmeras IP
common_ports = [80, 8080, 8899, 554, 8000, 8081, 8090]

cameras_found = []

def check_camera(ip, port):
    """Verifica se há uma câmera no IP:porta"""
    try:
        # Tentar HTTP
        url = f"http://{ip}:{port}"
        response = requests.get(url, timeout=1)
        
        # Verificar se parece ser uma câmera
        content = response.text.lower()
        if any(keyword in content for keyword in ['camera', 'video', 'stream', 'dvr', 'ipcam', 'webcam', 'snapshot']):
            return {
                'ip': ip,
                'port': port,
                'status': response.status_code,
                'server': response.headers.get('Server', 'Unknown'),
                'url': url
            }
    except:
        pass
    return None

# Escanear IPs de 1 a 254
print("Escaneando rede (isso pode levar alguns minutos)...\n")

with ThreadPoolExecutor(max_workers=50) as executor:
    futures = []
    
    for i in range(1, 255):
        ip = f"{network}.{i}"
        for port in common_ports:
            futures.append(executor.submit(check_camera, ip, port))
    
    completed = 0
    for future in as_completed(futures):
        completed += 1
        if completed % 100 == 0:
            print(f"Progresso: {completed}/{len(futures)} verificações...")
        
        result = future.result()
        if result:
            cameras_found.append(result)
            print(f"\n✓ CÂMERA ENCONTRADA!")
            print(f"  IP: {result['ip']}")
            print(f"  Porta: {result['port']}")
            print(f"  URL: {result['url']}")
            print(f"  Server: {result['server']}")

print("\n" + "=" * 60)
print(f"RESUMO: {len(cameras_found)} câmera(s) encontrada(s)")
print("=" * 60)

if cameras_found:
    print("\nLista de câmeras encontradas:")
    for i, cam in enumerate(cameras_found, 1):
        print(f"\n{i}. {cam['ip']}:{cam['port']}")
        print(f"   URL: {cam['url']}")
        
    # Salvar em arquivo
    with open('cameras_found.txt', 'w') as f:
        for cam in cameras_found:
            f.write(f"{cam['ip']}:{cam['port']}\n")
    print("\n✓ Lista salva em 'cameras_found.txt'")
else:
    print("\n⚠ Nenhuma câmera encontrada na varredura automática.")
    print("Isso pode significar que:")
    print("  - As câmeras estão em portas diferentes")
    print("  - As câmeras bloqueiam varreduras")
    print("  - As câmeras estão em outra sub-rede")
