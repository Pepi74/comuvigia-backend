from flask import Flask, Response, jsonify, request
import cv2
import numpy as np
import logging
from threading import Thread, Lock
import time
from datetime import datetime
import boto3
from botocore.client import Config
import io
import json
import uuid
from pathlib import Path

app = Flask(__name__)
start_time = time.time()

# Configuración
DEFAULT_OUTPUT_SIZE = (640, 360)
MAX_FPS = 30
FLASK_PORT = 5000
BATCH_SIZE = 300  # Número de frames por batch
BATCH_INTERVAL = 5  # Segundos entre batches

# Configuración S3
S3_ENDPOINT = "http://minio:9000"  # Para MinIO local
S3_ACCESS_KEY = "miniocomuvigia"
S3_SECRET_KEY = "comuvigiaminio123"
S3_BUCKET_NAME = "comuvigia-video-batches"
S3_REGION = "us-east-1"

# Configuración de cámaras / luego que vengan de una api
CAMERAS = {
    "cam1": {
        "rtsp_url": "rtsp://prueba:12341234@host.docker.internal:8554/live",
        "output_size": (640, 360),
        "enabled": True
    }
    # Agrega más cámaras aquí
}

# Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class S3Client:
    def __init__(self):
        self.client = None
        self.connected = False
        self.connect()
    
    def connect(self):
        try:
            self.client = boto3.client(
                's3',
                endpoint_url=S3_ENDPOINT,
                aws_access_key_id=S3_ACCESS_KEY,
                aws_secret_access_key=S3_SECRET_KEY,
                config=Config(signature_version='s3v4'),
                region_name=S3_REGION
            )
            # Crear bucket si no existe
            try:
                self.client.head_bucket(Bucket=S3_BUCKET_NAME)
            except:
                self.client.create_bucket(Bucket=S3_BUCKET_NAME)
            self.connected = True
            logger.info("Conectado a S3/MinIO exitosamente")
        except Exception as e:
            logger.error(f"Error conectando a S3: {str(e)}")
            self.connected = False
    
    def upload_batch(self, camera_id, frames, timestamp):
        if not self.connected:
            self.connect()
            if not self.connected:
                return False
        
        try:
            # Crear archivo comprimido con los frames
            batch_data = {
                "camera_id": camera_id,
                "timestamp": timestamp.isoformat(),
                "frames_count": len(frames),
                "frames": []
            }
            
            # Convertir frames a base64 o guardar como imágenes individuales
            for i, frame in enumerate(frames):
                _, jpeg = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                batch_data["frames"].append({
                    "index": i,
                    "data": jpeg.tobytes().hex()  # Guardar como hexadecimal
                })
            
            # Subir a S3
            batch_id = f"{camera_id}_{timestamp.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
            key = f"batches/{camera_id}/{timestamp.strftime('%Y/%m/%d')}/{batch_id}.json"
            
            self.client.put_object(
                Bucket=S3_BUCKET_NAME,
                Key=key,
                Body=json.dumps(batch_data),
                ContentType='application/json'
            )
            
            logger.info(f"Batch subido: {key} con {len(frames)} frames")
            return True
            
        except Exception as e:
            logger.error(f"Error subiendo batch a S3: {str(e)}")
            return False

class VideoStream:
    def __init__(self, camera_id, rtsp_url, output_size):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.output_size = output_size
        self.frame = None
        self.lock = Lock()
        self.running = False
        self.thread = None
        self.frames_buffer = []
        self.last_batch_time = time.time()
        self.s3_client = S3Client()

    def start(self):
        self.running = True
        self.thread = Thread(target=self.update, daemon=True)
        self.thread.start()
        logger.info(f"Stream de cámara {self.camera_id} iniciado")

    def update(self):
        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        #cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # buffer min
        cap.set(cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY) #gpu
        
        while self.running:
            try:
                ret, frame = cap.read()
                if not ret:
                    logger.warning(f"Cámara {self.camera_id}: Frame vacío - reconectando...")
                    time.sleep(1)
                    cap.release()
                    cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                    continue

                # Procesamiento
                processed_frame = cv2.resize(frame, self.output_size)
                
                with self.lock:
                    self.frame = processed_frame
                    
                    # Agregar frame al buffer
                    self.frames_buffer.append(processed_frame.copy())
                    
                    # Verificar si es tiempo de guardar batch
                    current_time = time.time()
                    if (len(self.frames_buffer) >= BATCH_SIZE or 
                        current_time - self.last_batch_time >= BATCH_INTERVAL):
                        self.save_batch()
                        
            except Exception as e:
                logger.error(f"Cámara {self.camera_id}: Error en captura: {str(e)}")
                time.sleep(1)

        cap.release()

    def save_batch(self):
        if not self.frames_buffer:
            return
            
        try:
            # Guardar batch actual
            batch_to_save = self.frames_buffer.copy()
            timestamp = datetime.now()
            
            # Limpiar buffer
            self.frames_buffer = []
            self.last_batch_time = time.time()
            
            # Subir a S3 en un hilo separado para no bloquear
            Thread(target=self.s3_client.upload_batch, 
                  args=(self.camera_id, batch_to_save, timestamp), 
                  daemon=True).start()
            
        except Exception as e:
            logger.error(f"Cámara {self.camera_id}: Error guardando batch: {str(e)}")

    def get_frame(self):
        with self.lock:
            if self.frame is None:
                return None
            _, jpeg = cv2.imencode('.jpg', self.frame, 
                                 [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            return jpeg.tobytes()

# Inicializar streams para todas las cámaras
video_streams = {}
for cam_id, config in CAMERAS.items():
    if config["enabled"]:
        video_streams[cam_id] = VideoStream(
            cam_id, 
            config["rtsp_url"], 
            config["output_size"]
        )
        video_streams[cam_id].start()

def generate_frames(camera_id):
    if camera_id not in video_streams:
        yield error_frame("Camera not found")
        return
        
    stream = video_streams[camera_id]
    last_time = time.time()
    
    while True:
        frame = stream.get_frame()
        
        if frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            
            # Control de FPS
            elapsed = time.time() - last_time
            delay = max(0, (1/MAX_FPS) - elapsed)
            time.sleep(delay)
            last_time = time.time()
        else:
            yield error_frame("No signal")
            time.sleep(1)

def error_frame(message):
    error_frame = np.zeros((DEFAULT_OUTPUT_SIZE[1], DEFAULT_OUTPUT_SIZE[0], 3), np.uint8)
    cv2.putText(error_frame, message, (50, 100), 
                cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
    _, jpeg = cv2.imencode('.jpg', error_frame)
    return (b'--frame\r\n'
           b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')

@app.route('/video_feed/<camera_id>')
def video_feed(camera_id):
    return Response(generate_frames(camera_id),
                   mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/cameras')
def list_cameras():
    cameras_list = []
    for cam_id, config in CAMERAS.items():
        cameras_list.append({
            "id": cam_id,
            "enabled": config["enabled"],
            "output_size": config["output_size"]
        })
    return jsonify(cameras_list)

@app.route('/health')
def health_check():
    health_status = {
        "status": "healthy",
        "service": "multi_camara_stream",
        "timestamp": datetime.now().isoformat(),
        "uptime_seconds": round(time.time() - start_time, 2),
        "active_cameras": len([cam for cam in video_streams.values() if cam.running]),
        "s3_connected": any(stream.s3_client.connected for stream in video_streams.values())
    }
    return jsonify(health_status), 200

@app.route('/config', methods=['GET', 'POST'])
def manage_config():
    if request.method == 'POST':
        # Aca agregar logica para actualizar configuracion
        return jsonify({"message": "Configuración actualizada"}), 200
    return jsonify(CAMERAS)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=FLASK_PORT, threaded=True)