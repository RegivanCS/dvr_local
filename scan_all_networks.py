import socket
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed

def scan_network(network='192.168.1.0/24', ports=[80, 554, 8080, 8899]):
    """Escaneia toda a rede procurando câmeras"""
    found = []
    
    def check_port(ip, port):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            result = sock.connect_ex((str(ip), port))
            sock.close()
            if result == 0:
                return (str(ip), port)
        except:
            pass
        return None
    
    network_obj = ipaddress.ip_network(network, strict=False)
    
    print(f"Escaneando rede {network}...")
    print(f"Portas: {ports}")
    print("-" * 60)
    
    with ThreadPoolExecutor(max_workers=100) as executor:
        futures = []
        for ip in network_obj.hosts():
            for port in ports:
                futures.append(executor.submit(check_port, ip, port))
        
        for future in as_completed(futures):
            result = future.result()
            if result:
                ip, port = result
                print(f"✓ Encontrado: {ip}:{port}")
                found.append(result)
    
    return found

# Escanear redes comuns
networks = [
    '192.168.1.0/24',    # Rede principal
    '192.168.0.0/24',    # Comum em extensores
    '192.168.2.0/24',    # Alternativa
    '10.0.0.0/24',       # Algumas extensões
]

all_found = []
for net in networks:
    try:
        results = scan_network(net)
        all_found.extend(results)
    except:
        pass

print("\n" + "=" * 60)
print(f"Total encontrado: {len(all_found)} dispositivos")
print("=" * 60)

# Agrupar por IP
from collections import defaultdict
by_ip = defaultdict(list)
for ip, port in all_found:
    by_ip[ip].append(port)

for ip, ports in sorted(by_ip.items()):
    print(f"{ip}: {sorted(ports)}")
