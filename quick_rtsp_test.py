import cv2

# Testes rápidos
tests = [
    'rtsp://192.168.1.3:554/',
    'rtsp://192.168.1.3:554/stream',
    'rtsp://admin:admin@192.168.1.3:554/',
    'rtsp://admin:admin@192.168.1.3:554/stream',
]

print("Testes rapidos RTSP:\n")
for url in tests:
    cap = cv2.VideoCapture(url)
    if cap.isOpened():
        print(f"OK: {url}")
        cap.release()
    else:
        print(f"FAIL: {url}")
