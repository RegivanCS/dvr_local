import cv2

urls = [
    "rtsp://admin:Herb1745%40@192.168.1.3:8899/stream",
    "rtsp://admin:Herb1745%40@192.168.1.10:8899/stream"
]

def detect_motion():
    caps = [cv2.VideoCapture(url) for url in urls]
    subtractors = [cv2.createBackgroundSubtractorMOG2() for _ in urls]

    while True:
        for i, cap in enumerate(caps):
            ret, frame = cap.read()
            if not ret:
                continue

            fgmask = subtractors[i].apply(frame)
            count = cv2.countNonZero(fgmask)

            if count > 5000:
                print(f"Movimento detectado na câmera {i+1}")
                cv2.imwrite(f"alert_cam{i+1}.jpg", frame)

            cv2.imshow(f"Camera {i+1}", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    for cap in caps:
        cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    detect_motion()