import cv2
import json
import os
from urllib.parse import quote

# Carrega URLs RTSP da configuração (IPs são definidos pela tela de configurações)
def _build_rtsp_urls():
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
            path = cam.get('path', '/stream')
            if user:
                result.append(f"rtsp://{user}:{quote(password, safe='')}@{ip}:{port}{path}")
            else:
                result.append(f"rtsp://{ip}:{port}{path}")
        return result
    except:
        return []

# IPs removidos do código; URLs são construídas a partir do cameras_config.json
# urls = ["rtsp://...", ...]  # não use IPs fixos aqui
urls = _build_rtsp_urls()
if not urls:
    print("[ERRO] Nenhuma câmera configurada. Adicione câmeras pela tela de configurações.")
    exit(1)

def capture_and_record():
    caps = [cv2.VideoCapture(url) for url in urls]
    outs = []
    for i, cap in enumerate(caps):
        fourcc = cv2.VideoWriter_fourcc(*'XVID')
        outs.append(cv2.VideoWriter(f'camera{i+1}.avi', fourcc, 20.0, (640,480)))

    while True:
        for i, cap in enumerate(caps):
            ret, frame = cap.read()
            if ret:
                outs[i].write(frame)
                cv2.imshow(f"Camera {i+1}", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    for cap in caps:
        cap.release()
    for out in outs:
        out.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    capture_and_record()