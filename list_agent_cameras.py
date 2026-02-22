import requests
import json

# Consultar câmeras do Agent DVR
r = requests.get('http://localhost:8090/command.cgi?cmd=getObjects')
data = r.json()

print("=== Câmeras no Agent DVR ===\n")

if 'cameras' in data:
    for cam in data['cameras']:
        print(f"ID: {cam.get('id', 'N/A')}")
        print(f"Nome: {cam.get('name', 'N/A')}")
        print(f"Tipo: {cam.get('typeID', 'N/A')}")
        print(f"URL Test: http://localhost:8090/grab.jpg?oid={cam.get('id', 0)}")
        print("-" * 50)
