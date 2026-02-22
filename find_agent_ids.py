import requests
import json

r = requests.get('http://localhost:8090/command.cgi?cmd=getObjects')
data = r.json()

print("=== Agent DVR Câmeras ===\n")

# objectList contém os objetos (câmeras e microfones)
for obj in data.get('objectList', []):
    if obj.get('typeID') == 2:  # 2 = câmera
        print(f"ID: {obj.get('id')}")
        print(f"Nome: {obj.get('name')}")
        print(f"URL: http://localhost:8090/grab.jpg?oid={obj.get('id')}")
        print("-" * 60)
