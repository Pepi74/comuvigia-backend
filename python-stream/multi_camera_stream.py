import tarfile
from flask import Flask, Response, jsonify, request
import cv2
import numpy as np
import logging
from threading import Thread, Lock
import time
from datetime import datetime, timedelta
import boto3
from botocore.client import Config
from io import BytesIO
import json
import uuid
from pathlib import Path
import requests
import os
from video_reconstructor import video_bp, video_reconstructor

app = Flask(__name__)
app.register_blueprint(video_bp, url_prefix='/')
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
'''CAMERAS = {
    "cam1": {
        "link_camara": "rtsp://prueba:12341234@host.docker.internal:8554/live",
        "estado_camara": True
    }
    # Agrega más cámaras aquí
}'''
url = "http://backend:3000/api/camaras"

payload = {}
headers = {}

response = requests.request("GET", url, headers=headers, data=payload)

print(response.text)
CAMERAS = json.loads(response.text)

# Logs
os.makedirs("/logs", exist_ok=True)
from logging.handlers import RotatingFileHandler

logger = logging.getLogger("multi_camera_stream")
logger.setLevel(logging.INFO)

fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')

fh = RotatingFileHandler("/logs/multi_camera_stream.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
fh.setFormatter(fmt)
logger.addHandler(fh)

ch = logging.StreamHandler()
ch.setFormatter(fmt)
logger.addHandler(ch)

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
        try:
            if not frames:
                logger.warning(f"Cámara {camera_id}: No hay frames para guardar")
                return False
            # Crear metadata con información crucial
            metadata = {
                "camera_id": str(camera_id),
                "timestamp": timestamp.isoformat(),
                "frames_count": len(frames),
                "resolution": f"{frames[0].shape[1]}x{frames[0].shape[0]}",
                "fps": MAX_FPS,
                "codec": "h264",
                "version": "1.0"
            }
            
            # Crear archivo tar con frames individuales
            tar_buffer = BytesIO()
            
            with tarfile.open(fileobj=tar_buffer, mode='w:gz') as tar:
                # Agregar metadata
                metadata_json = json.dumps(metadata)
                metadata_bytes = metadata_json.encode('utf-8')
                metadata_info = tarfile.TarInfo("metadata.json")
                metadata_info.size = len(metadata_bytes)
                tar.addfile(metadata_info, BytesIO(metadata_bytes))
                
                # Agregar frames como archivos JPEG individuales
                for i, frame in enumerate(frames):
                    # Verificar que el frame sea válido
                    if frame is None or not isinstance(frame, np.ndarray):
                        logger.warning(f"Cámara {camera_id}: Frame {i} inválido, omitiendo")
                        continue
                        
                    try:
                        _, jpeg = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                        if jpeg is None:
                            continue
                            
                        frame_bytes = jpeg.tobytes()
                        frame_info = tarfile.TarInfo(f"frame_{i:06d}.jpg")
                        frame_info.size = len(frame_bytes)
                        tar.addfile(frame_info, BytesIO(frame_bytes))
                        
                    except Exception as e:
                        logger.error(f"Cámara {camera_id}: Error procesando frame {i}: {str(e)}")
                        continue
            
            # Subir a S3
            tar_buffer.seek(0)
            date_path = timestamp.strftime('%Y/%m/%d/%H')
            batch_id = f"{camera_id}_{timestamp.strftime('%Y%m%d_%H%M%S')}"
            key = f"batches/{camera_id}/{date_path}/{batch_id}.tar.gz"
            
            s3_metadata = {k: str(v) for k, v in metadata.items()} 

            self.client.put_object(
                Bucket=S3_BUCKET_NAME,
                Key=key,
                Body=tar_buffer.getvalue(),
                ContentType='application/gzip',
                Metadata=s3_metadata  # Metadata adicional para S3
            )
            size_mb = len(tar_buffer.getvalue()) / (1024 * 1024)
            logger.info(f"Cámara {camera_id}: Batch subido {key} - {size_mb:.2f} MB")
            return True
            
        except Exception as e:
            logger.error(f"Cámara {camera_id}: Error subiendo batch optimizado: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())  # ← Para más detalles del error
            return False

class VideoStream:
    def __init__(self, camera_id, link_camara):
        self.camera_id = camera_id
        self.link_camara = link_camara
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
        cap = cv2.VideoCapture(self.link_camara, cv2.CAP_FFMPEG)
        #cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # buffer min
        cap.set(cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY) #gpu
        
        while self.running:
            try:
                ret, frame = cap.read()
                if not ret:
                    logger.warning(f"Cámara {self.camera_id}: Frame vacío - reconectando...")
                    time.sleep(1)
                    cap.release()
                    cap = cv2.VideoCapture(self.link_camara, cv2.CAP_FFMPEG)
                    continue

                # Procesamiento
                processed_frame = cv2.resize(frame, DEFAULT_OUTPUT_SIZE)
                
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
if isinstance(CAMERAS, str):
    cameras_data = json.loads(CAMERAS)
else:
    cameras_data = CAMERAS
for camera in cameras_data:
    cam_id = camera["id"]
    config = camera
    
    if config["estado_camara"] and config["link_camara"]:  # Verificar que tenga link
        video_streams[cam_id] = VideoStream(
            cam_id, 
            config["link_camara"], 
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
    if isinstance(CAMERAS, str):
        cameras_data = json.loads(CAMERAS)
    else:
        cameras_data = CAMERAS
    
    for camera in cameras_data:
        cameras_list.append({
            "id": camera["id"],
            "estado_camara": camera["estado_camara"],
            "nombre": camera["nombre"],
            "posicion": camera["posicion"],
            "direccion": camera["direccion"],
            "estado_camara": camera["estado_camara"]
        })
    return jsonify(cameras_list)

@app.route('/video_feed/preview/<camera_id>')
def video_preview(camera_id):
    """Obtener preview de los últimos frames"""
    try:
        # Buscar el batch más reciente
        end_time = datetime.now()
        start_time = end_time - timedelta(minutes=5)
        
        batches = video_reconstructor._find_batches_in_range(camera_id, start_time, end_time)
        
        if not batches:
            return jsonify({"error": "No hay datos recientes"}), 404
        
        # Tomar el batch más reciente
        latest_batch = batches[-1]
        
        # Extraer primer frame para preview
        response = S3Client.get_object(
            Bucket=S3_BUCKET_NAME,
            Key=latest_batch['key']
        )
        
        tar_bytes = BytesIO(response['Body'].read())
        
        with tarfile.open(fileobj=tar_bytes, mode='r:gz') as tar:
            frame_files = [m for m in tar.getmembers() if m.name.startswith('frame_')]
            if frame_files:
                first_frame = frame_files[0]
                frame_data = tar.extractfile(first_frame).read()
                
                # Devolver como imagen
                return Response(frame_data, mimetype='image/jpeg')
        
        return jsonify({"error": "No se pudo obtener preview"}), 404
        
    except Exception as e:
        return jsonify({"error": str(e)}), 400

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