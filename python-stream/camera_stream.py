from flask import Flask, Response
import cv2
import numpy as np
import logging
from threading import Thread, Lock
import time

app = Flask(__name__)

# configuracion
RTSP_URL = "rtsp://prueba:12341234@host.docker.internal:8554/live"
# RTSP_URL = "rtsp://prueba:12341234@localhost:8554/live"
#RTSP_URL = "rtsp://admin:FOCGNT@camezviz.duckdns.org:8554/h264/ch1/main/av_stream"
OUTPUT_SIZE = (640, 360)
MAX_FPS = 30
FLASK_PORT = 5000

# logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class VideoStream:
    def __init__(self):
        self.frame = None
        self.lock = Lock()
        self.running = False
        self.thread = None

    def start(self):
        self.running = True
        self.thread = Thread(target=self.update, daemon=True)
        self.thread.start()

    def update(self):
        cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
        #cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # buffer min
        cap.set(cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY) # gpu
        
        while self.running:
            try:
                ret, frame = cap.read()
                if not ret:
                    logger.warning("Frame vacío - reconectando...")
                    time.sleep(1)
                    cap.release()
                    cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
                    continue

                # procesamiento minimo
                with self.lock:
                    self.frame = cv2.resize(frame, OUTPUT_SIZE)

            except Exception as e:
                logger.error(f"Error en captura: {str(e)}")
                time.sleep(1)

        cap.release()

    def get_frame(self):
        with self.lock:
            if self.frame is None:
                return None
            _, jpeg = cv2.imencode('.jpg', self.frame, 
                                 [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            return jpeg.tobytes()

video_stream = VideoStream()
video_stream.start()

def generate_frames():
    last_time = time.time()
    while True:
        frame = video_stream.get_frame()
        
        if frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            
            # control de fps
            elapsed = time.time() - last_time
            delay = max(0, (1/MAX_FPS) - elapsed)
            time.sleep(delay)
            last_time = time.time()
        else:
            # frame de error estatico
            error_frame = np.zeros((OUTPUT_SIZE[1], OUTPUT_SIZE[0], 3), np.uint8)
            cv2.putText(error_frame, "Sin señal", (50, 100), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
            _, jpeg = cv2.imencode('.jpg', error_frame)
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            time.sleep(1)

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(),
                   mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=FLASK_PORT, threaded=True)
