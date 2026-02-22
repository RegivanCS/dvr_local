import cv2

urls = [
    "rtsp://admin:Herb1745%40@192.168.1.3:8899/stream",
    "rtsp://admin:Herb1745%40@192.168.1.10:8899/stream"
]

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