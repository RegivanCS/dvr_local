import requests

print("=" * 60)
print("Descobrindo paths de stream do Agent DVR")
print("=" * 60)

agent_url = "http://localhost:8090"

# Paths comuns do Agent DVR
paths = [
    "/live.mp4?oid=1",
    "/live.jpeg?oid=1",
    "/live.mjpeg?oid=1",
    "/grab.jpg?oid=1",
    "/snapshot.jpg?oid=1",
    "/feed/1",
    "/video/1",
    "/h264/1",
]

for path in paths:
    url = f"{agent_url}{path}"
    try:
        response = requests.head(url, timeout=2)
        print(f"✓ {path}: {response.status_code} - {response.headers.get('Content-Type', 'N/A')}")
    except Exception as e:
        print(f"✗ {path}: {e}")

# Tentar obter lista de objetos
print("\n" + "=" * 60)
print("Tentando obter lista de objetos:")
try:
    response = requests.get(f"{agent_url}/command.cgi?cmd=getObjects", timeout=5)
    if response.status_code == 200:
        print("✓ Sucesso!")
        print(response.text[:500])
except Exception as e:
    print(f"✗ Erro: {e}")
