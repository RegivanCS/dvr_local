import requests
import json

print("=" * 60)
print("Listando câmeras do Agent DVR")
print("=" * 60)

try:
    response = requests.get("http://localhost:8090/command.cgi?cmd=getObjects", timeout=5)
    if response.status_code == 200:
        data = response.json()
        
        print("\nCâmeras encontradas:\n")
        for obj in data.get('objectList', []):
            if obj.get('typeID') == 2:  # typeID 2 = câmera
                print(f"  ID: {obj['id']}")
                print(f"  Nome: {obj['name']}")
                print(f"  URL para usar: /camera/{obj['id']}")
                print(f"  Teste direto: http://localhost:8090/grab.jpg?oid={obj['id']}")
                print("-" * 40)
        
        print("\n✓ Use esses IDs no navegador!")
        
except Exception as e:
    print(f"✗ Erro: {e}")
