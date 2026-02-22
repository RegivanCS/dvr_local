import requests
import json

print("=" * 60)
print("Verificando configuração das câmeras no Agent DVR")
print("=" * 60)

try:
    response = requests.get("http://localhost:8090/command.cgi?cmd=getObjects", timeout=5)
    if response.status_code == 200:
        data = response.json()
        
        print("\nDetalhes das câmeras:\n")
        for obj in data.get('objectList', []):
            if obj.get('typeID') == 2:  # câmera
                print(f"ID: {obj['id']}")
                print(f"Nome: {obj.get('name', 'N/A')}")
                print(f"Diretório: {obj.get('directory', 'N/A')}")
                
                # Tentar pegar configurações detalhadas
                try:
                    config_response = requests.get(
                        f"http://localhost:8090/command.cgi?cmd=getObject&oid={obj['id']}&ot=2",
                        timeout=3
                    )
                    if config_response.status_code == 200:
                        config = config_response.json()
                        print(f"Tipo de fonte: {config.get('typeID', 'N/A')}")
                        print(f"Source: {config.get('settings', {}).get('videosourcestring', 'N/A')}")
                except:
                    pass
                
                print("-" * 40)
        
except Exception as e:
    print(f"Erro: {e}")
