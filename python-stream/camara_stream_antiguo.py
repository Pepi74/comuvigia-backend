from flask import Flask, Response
import imageio_ffmpeg as ffmpeg
import cv2
import numpy as np
import subprocess

app = Flask(__name__)

rtsp_url = "rtsp://admin:FOCGNT@camezviz.duckdns.org:8554/h264/ch1/main/av_stream"
width, height = 1920, 1080

def generate():
    cmd = [
        ffmpeg.get_ffmpeg_exe(), "-rtsp_transport", "tcp", "-i", rtsp_url,
        "-f", "image2pipe", "-pix_fmt", "bgr24", "-vcodec", "rawvideo", "-"
    ]
    pipe = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10**8)
    try:
        while True:
            raw_image = pipe.stdout.read(width * height * 3)
            if not raw_image:
                break
            image = np.frombuffer(raw_image, dtype=np.uint8).reshape((height, width, 3))
            scaled_image = cv2.resize(image, (640, 360))
            # Codifica en JPEG para MJPEG
            ret, jpeg = cv2.imencode('.jpg', scaled_image)
            if not ret:
                continue
            frame = jpeg.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    finally:
        pipe.terminate()

@app.route('/video_feed')
def video_feed():
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
