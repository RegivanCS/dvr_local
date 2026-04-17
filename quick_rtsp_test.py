import cv2
import json
import os
from urllib.parse import quote

# Carrega URLs RTSP da configuração (IPs são definidos pela tela de configurações)
def _build_rtsp_tests():
    config_path = os.path.join(os.path.dirname(__file__), 'cameras_config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            cameras = json.load(f).get('cameras', {}).values()
        result = []
        for cam in cameras:
            ip = cam.get('ip', '')
            port = cam.get('port', 554)
            user = cam.get('user', '')
            password = cam.get('password', '')
            result += [
                f'rtsp://{ip}:{port}/',
                f'rtsp://{ip}:{port}/stream',
                f'rtsp://{user}:{quote(password, safe="")}@{ip}:{port}/',
                f'rtsp://{user}:{quote(password, safe="")}@{ip}:{port}/stream',
            ]
        return result
    except:
        return []

# IPs removidos do código; URLs são construídas a partir do cameras_config.json
# tests = ['rtsp://192.168.x.x...']  # não use IPs fixos aqui
tests = _build_rtsp_tests()
if not tests:
    print("[ERRO] Nenhuma câmera configurada. Adicione câmeras pela tela de configurações.")
    exit(1)

print("Testes rapidos RTSP:\n")
for url in tests:
    cap = cv2.VideoCapture(url)
    if cap.isOpened():
        print(f"OK: {url}")
        cap.release()
    else:
        print(f"FAIL: {url}")
