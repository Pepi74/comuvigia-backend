import tarfile
import threading
import pika

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
import base64
from flask import Flask, Response, jsonify, request
from video_reconstructor import video_bp, video_reconstructor
from flask_cors import CORS
import queue

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": os.environ["FRONTEND_URL"]}})
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
S3_ACCESS_KEY = os.environ["S3_ACCESS_KEY"]
S3_SECRET_KEY = os.environ["S3_SECRET_KEY"]
S3_BUCKET_NAME = os.environ["S3_BUCKET_NAME"]
S3_REGION = "us-east-1"



# Configuración de cámaras
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


class RabbitPublisher:
    def __init__(self, batch_time=1.0, max_frames=50):
        # Configuración RabbitMQ
        self.rabbit_host = os.getenv('RABBIT_HOST', 'rabbitmq')
        self.rabbit_port = int(os.getenv('RABBIT_PORT', 5672))
        self.rabbit_user = os.getenv('RABBITMQ_DEFAULT_USER', 'guest')
        self.rabbit_password = os.getenv('RABBITMQ_DEFAULT_PASS', 'guest')
        self.exchange = 'frames'

        # Cola interna para batching
        self.frame_batches = {}  # {camera_id: [frames]}
        self.batch_time = batch_time
        self.max_frames = max_frames
        self.last_send_time = {}  # {camera_id: timestamp}

        self._stop = threading.Event()
        self.connection = None
        self.channel = None
        self.connect()

        # Hilo para publicar batches sin bloquear
        self.publish_thread = threading.Thread(target=self._publish_loop, daemon=True)
        self.publish_thread.start()

    def connect(self):
        credentials = pika.PlainCredentials(self.rabbit_user, self.rabbit_password)
        parameters = pika.ConnectionParameters(
            host=self.rabbit_host,
            port=self.rabbit_port,
            credentials=credentials,
            heartbeat=600,
            blocked_connection_timeout=300
        )
        while not self._stop.is_set():
            try:
                self.connection = pika.BlockingConnection(parameters)
                self.channel = self.connection.channel()
                self.channel.exchange_declare(exchange=self.exchange, exchange_type='direct')
                print("Conectado a RabbitMQ")
                break
            except Exception as e:
                print(f"No se pudo conectar a RabbitMQ, reintentando en 5s: {e}")
                time.sleep(5)

    def publish_frame(self, camera_id, frame_bytes):
        """Agrega un frame a la lista de batching"""
        if camera_id not in self.frame_batches:
            self.frame_batches[camera_id] = []
            self.last_send_time[camera_id] = time.time()
        self.frame_batches[camera_id].append(base64.b64encode(frame_bytes).decode('utf-8'))

        # Si se alcanza el máximo de frames, enviamos inmediatamente
        if len(self.frame_batches[camera_id]) >= self.max_frames:
            self._send_batch(camera_id)

    def _send_batch(self, camera_id):
        frames = self.frame_batches.get(camera_id, [])
        if not frames:
            return

        message = json.dumps({
            "camera_id": camera_id,
            "frames": frames,
            "timestamp": time.time()
        })

        routing_key = f"camera_{camera_id}"

        try:
            self.channel.basic_publish(exchange=self.exchange, routing_key=routing_key, body=message)
        except pika.exceptions.AMQPConnectionError:
            print("Conexión perdida, reconectando...")
            self.connect()
            self.channel.basic_publish(exchange=self.exchange, routing_key=routing_key, body=message)

        # Reset de batch
        self.frame_batches[camera_id] = []
        self.last_send_time[camera_id] = time.time()

    def _publish_loop(self):
        """Revisa periódicamente si hay batches que deben enviarse por tiempo"""
        while not self._stop.is_set():
            now = time.time()
            for camera_id in list(self.frame_batches.keys()):
                if self.frame_batches[camera_id] and (now - self.last_send_time[camera_id] >= self.batch_time):
                    self._send_batch(camera_id)
            time.sleep(0.1)  # Evita busy wait

    def stop(self):
        self._stop.set()
        self.publish_thread.join()
        if self.connection and not self.connection.is_closed:
            self.connection.close()

publisher = RabbitPublisher(batch_time=1.0, max_frames=50)

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
    
    def upload_batch(self, camera_id, frames, timestamp, custom_metadata=None, flag=False, fps=30):
        try:
            if not frames:
                logger.warning(f"Cámara {camera_id}: No hay frames para guardar")
                return False
            
            # Asegurar que timestamp sea datetime
            if isinstance(timestamp, dict):
                # Si es diccionario, intentar convertirlo
                logger.warning(f"Cámara {camera_id}: timestamp es diccionario, convirtiendo")
                # Aquí puedes agregar lógica para extraer el timestamp del diccionario si es necesario
                timestamp = datetime.now()  # O usar datetime.fromisoformat() si el dict tiene un campo de fecha
            elif not isinstance(timestamp, datetime):
                logger.warning(f"Cámara {camera_id}: timestamp no es datetime, usando ahora")
                timestamp = datetime.now()
            # Crear metadata con información crucial
            base_metadata  = {
                "camera_id": str(camera_id),
                "timestamp": timestamp.isoformat(),
                "frames_count": len(frames),
                "resolution": f"{frames[0].shape[1]}x{frames[0].shape[0]}",
                "fps": fps,
                "codec": "h264",
                "version": "1.0"
            }

            # Fusionar con metadata personalizada si existe
            # Normalizar custom_metadata
            if custom_metadata:
                normalized_custom = {}
                for k, v in custom_metadata.items():
                    if isinstance(v, datetime):
                        normalized_custom[k] = v.isoformat()
                    elif isinstance(v, dict):
                        normalized_custom[k] = json.dumps(v)
                    else:
                        normalized_custom[k] = str(v)
                metadata = {**base_metadata, **normalized_custom}
            else:
                metadata = base_metadata
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
            if(flag):
                key = f"clips/{camera_id}/{date_path}/{batch_id}.tar.gz"
            else:
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
            return {
                'key': key,
                'bucket': S3_BUCKET_NAME,
                'size_mb': size_mb,
                'frames_count': len(frames),
                'timestamp': timestamp.isoformat(),
                's3_url': f"{S3_ENDPOINT}/{S3_BUCKET_NAME}/{key}",
                'metadata': metadata
            }
            
        except Exception as e:
            logger.error(f"Cámara {camera_id}: Error subiendo batch optimizado: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False

# HTTP
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
        self.cap = None
        self.connection_attempts = 0
        self.last_error = None

    def start(self):
        self.running = True
        self.thread = Thread(target=self.update, daemon=True)
        self.thread.start()
        logger.info(f"Stream de cámara {self.camera_id} iniciado")

    def update(self):
        while self.running:
            try:
                if self.cap is None:
                    self.connection_attempts += 1
                    logger.info(f"Cámara {self.camera_id}: Intentando conexión #{self.connection_attempts}")
                    
                    self.cap = cv2.VideoCapture(self.link_camara)
                    self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    self.cap.set(cv2.CAP_PROP_FPS, MAX_FPS)
                    
                    if not self.cap.isOpened():
                        logger.error(f"Cámara {self.camera_id}: No se pudo abrir el stream")
                        self.cap.release()
                        self.cap = None
                        time.sleep(2)
                        continue
                    
                    logger.info(f"Cámara {self.camera_id}: Conectada exitosamente")
                
                ret, frame = self.cap.read()
                if not ret:
                    logger.warning(f"Cámara {self.camera_id}: Frame vacío - reconectando...")
                    self.cap.release()
                    self.cap = None
                    time.sleep(1)
                    continue

                # Procesamiento
                processed_frame = cv2.resize(frame, DEFAULT_OUTPUT_SIZE)
                
                with self.lock:
                    self.frame = processed_frame
                    self.frames_buffer.append(processed_frame.copy())
                    
                    current_time = time.time()
                    if (len(self.frames_buffer) >= BATCH_SIZE or 
                        current_time - self.last_batch_time >= BATCH_INTERVAL):
                        self.save_batch()
                        
            except Exception as e:
                self.last_error = str(e)
                logger.error(f"Cámara {self.camera_id}: Error en captura: {str(e)}")
                if self.cap:
                    self.cap.release()
                    self.cap = None
                time.sleep(2)
    
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

# RTSP
class VideoStreamOpenCV:
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
        logger.info(f"Stream de cámara {self.camera_id} iniciado (OpenCV)")

    def update(self):
        # Para streams HTTP con OpenCV
        cap = cv2.VideoCapture(self.link_camara)
        
        while self.running:
            try:
                ret, frame = cap.read()
                if not ret:
                    logger.warning(f"Cámara {self.camera_id}: Frame vacío - reconectando...")
                    time.sleep(1)
                    cap.release()
                    cap = cv2.VideoCapture(self.link_camara)
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
                try:
                    cap.release()
                except:
                    pass
                cap = cv2.VideoCapture(self.link_camara)

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
    
    if config["estado_camara"] and config["link_camara"]:
        logger.info(f"Intentando inicializar cámara {cam_id}: {config['link_camara']}")
        
        try:
            # NO usar requests.head() para streams de video - usualmente falla
            # Inicializar directamente la cámara
            video_streams[cam_id] = VideoStream(cam_id, config["link_camara"])
            video_streams[cam_id].start()
            logger.info(f"Cámara {cam_id} inicializada - Estado: {video_streams[cam_id].running}")
            
        except Exception as e:
            logger.error(f"Error inicializando cámara {cam_id}: {str(e)}")
            # Aún así crear el objeto para debugging
            video_streams[cam_id] = VideoStream(cam_id, config["link_camara"])
            logger.warning(f"Cámara {cam_id} creada pero no iniciada debido a error")

# Log todas las cámaras inicializadas
logger.info(f"Cámaras en video_streams: {list(video_streams.keys())}")

def generate_frames(camera_id):
    # Convertir camera_id a entero
    try:
        camera_id_int = int(camera_id)
    except ValueError:
        logger.error(f"Camera ID inválido: {camera_id}")
        yield error_frame("Invalid camera ID")
        return
        
    if camera_id_int not in video_streams:
        logger.error(f"Camera {camera_id_int} not found in video_streams. Available: {list(video_streams.keys())}")
        yield error_frame(f"Camera {camera_id_int} not found")
        return
        
    stream = video_streams[camera_id_int]
    last_time = time.time()
    error_count = 0
    

    no_frame_count = 0       # Contador de frames repetidos o inexistentes

    logger.info(f"Starting video feed fasdfasdffasfor camera {camera_id_int}")
    while True:

        try:
            frame = stream.get_frame()
            if frame is not None:
                # Publicar a RabbitMQ
                publisher.publish_frame(camera_id_int, frame)

                # Reset de error_count porque se recibió un frame válido
                error_count = 0
                
                # Control de FPS
                elapsed = time.time() - last_time
                delay = max(0, (1/MAX_FPS) - elapsed)
                time.sleep(delay)
                last_time = time.time()

                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            else:
                error_count += 1
                if error_count > 5:
                    logger.warning(f"Camera {camera_id_int}: No frame available ({error_count} attempts)")
                yield error_frame("No signal")
                time.sleep(0.5)
                    
        except Exception as e:
            logger.error(f"Error generating frames for {camera_id_int}: {str(e)}")
            yield error_frame("Stream error")
            time.sleep(1)

def error_frame(message):
    error_frame = np.zeros((DEFAULT_OUTPUT_SIZE[1], DEFAULT_OUTPUT_SIZE[0], 3), np.uint8)
    cv2.putText(error_frame, message, (50, 100), 
                cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
    _, jpeg = cv2.imencode('.jpg', error_frame)
    return (b'--frame\r\n'
           b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')

@app.route('/camera_status/<camera_id>')
def camera_status(camera_id):
    """Endpoint para diagnosticar el estado de una cámara específica"""
    try:
        camera_id_int = int(camera_id)
    except ValueError:
        return jsonify({"error": "Camera ID must be a number"}), 400
        
    if camera_id_int not in video_streams:
        return jsonify({
            "error": f"Cámara {camera_id_int} no encontrada en video_streams",
            "available_cameras": list(video_streams.keys()),
            "configured_cameras": [cam["id"] for cam in (json.loads(CAMERAS) if isinstance(CAMERAS, str) else CAMERAS)]
        }), 404
    
    stream = video_streams[camera_id_int]
    status = {
        "camera_id": camera_id_int,
        "stream_url": stream.link_camara,
        "running": stream.running,
        "has_frame": stream.frame is not None,
        "buffer_size": len(stream.frames_buffer),
        "connection_attempts": stream.connection_attempts,
        "last_error": stream.last_error,
        "s3_connected": stream.s3_client.connected if hasattr(stream, 's3_client') else False
    }
    return jsonify(status)

@app.route('/all_cameras_status')
def all_cameras_status():
    """Estado de todas las cámaras"""
    status = {}
    for cam_id, stream in video_streams.items():
        status[cam_id] = {
            "running": stream.running,
            "has_frame": stream.frame is not None,
            "stream_url": stream.link_camara,
            "connection_attempts": stream.connection_attempts,
            "last_error": stream.last_error
        }
    return jsonify(status)

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

# APIs para guardar frames desde servicio de inteligencia artificial
@app.route('/save-frames', methods=['POST'])
def save_frames():
    """
    API para guardar frames en el bucket S3/MinIO
    Espera: JSON con camera_id y frames (lista de base64 JPEG)
    """
    try:
        fps_data = 30
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No se proporcionaron datos JSON'}), 400
        #print(data['frames'])
        # Campos obligatorios
        required_fields = ['camera_id', 'frames','fps']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Campo requerido faltante: {field}'}), 400
        
        camera_id = data['camera_id']
        frames_data = data['frames']  # lista de strings base64
        fps_data = data['fps']
        metadata = data.get('metadata', {})
        
        # Procesar frames: base64 JPEG → OpenCV Mat
        processed_frames = []
        for frame_base64 in frames_data:
            try:
                # Decodificar base64 a bytes
                frame_bytes = base64.b64decode(frame_base64)
                # Convertir bytes a NumPy array
                np_arr = np.frombuffer(frame_bytes, np.uint8)
                # Decodificar JPEG a frame (cv2 Mat)
                frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                
                if frame is not None:
                    processed_frames.append(frame)
                else:
                    logger.warning('No se pudo decodificar un frame')
            except Exception as e:
                logger.warning(f'Error decodificando frame: {str(e)}')
        
        if not processed_frames:
            return jsonify({'error': 'No se pudieron procesar los frames'}), 400
        
        # Metadata adicional
        full_metadata = {
            **metadata,
            'source': 'api-save-frames',
            'received_timestamp': datetime.now().isoformat(),
            'frames_count': len(processed_frames),
            'camera_id': camera_id
        }
        
        # Subir batch a S3
        timestamp = datetime.now()
        s3_client = S3Client() 
        success = s3_client.upload_batch(camera_id, processed_frames, timestamp, full_metadata, True,fps=fps_data)
        
        if success:
            return jsonify({
                'success': True,
                'message': f'{len(processed_frames)} frames guardados exitosamente',
                's3_info': {
                    'key': success['key'],
                    'bucket': success['bucket'],
                    's3_url': success['s3_url'],
                    'size_mb': success['size_mb'],
                    'frames_count': success['frames_count'],
                    'timestamp': success['timestamp']
                },
                'metadata': success['metadata'],
                'camera_id': camera_id
            }), 200
        else:
            return jsonify({'error': 'Error al subir frames a S3'}), 500
        
    except Exception as e:
        logger.error(f"Error en API save-frames: {str(e)}")
        return jsonify({'error': 'Error interno del servidor'}), 500

@app.route('/api/save-single-frame', methods=['POST'])

def save_single_frame():
    """
    API para guardar un solo frame y retornar el key
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No se proporcionaron datos JSON'}), 400
        
        required_fields = ['camera_id', 'frame_data']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Campo requerido faltante: {field}'}), 400
        
        camera_id = data['camera_id']
        frame_data = data['frame_data']
        metadata = data.get('metadata', {})
        
        # Decodificar el frame
        frame = None
        
        if isinstance(frame_data, str) and frame_data.startswith('data:image'):
            frame = decode_base64_frame(frame_data)
        elif isinstance(frame_data, str):
            frame = decode_base64_simple(frame_data)
        elif isinstance(frame_data, dict) and 'image_data' in frame_data:
            frame = decode_structured_frame(frame_data)
        
        if frame is None:
            return jsonify({'error': 'No se pudo decodificar el frame'}), 400
        
        # Crear un batch con un solo frame
        processed_frames = [frame]
        
        full_metadata = {
            **metadata,
            'source': 'api-save-single-frame',
            'received_timestamp': datetime.now().isoformat(),
            'camera_id': camera_id
        }
        
        # Subir a S3 y obtener información
        timestamp = datetime.now()
        s3_client = S3Client()
        upload_result = s3_client.upload_batch(camera_id, processed_frames, timestamp, full_metadata)
        
        if upload_result:
            return jsonify({
                'success': True,
                'message': 'Frame guardado exitosamente',
                's3_info': {
                    'key': upload_result['key'],
                    'bucket': upload_result['bucket'],
                    's3_url': upload_result['s3_url'],
                    'size_mb': upload_result['size_mb'],
                    'frames_count': upload_result['frames_count'],
                    'timestamp': upload_result['timestamp']
                },
                'metadata': upload_result['metadata'],
                'camera_id': camera_id
            }), 200
        else:
            return jsonify({'error': 'Error al subir frame a S3'}), 500
            
    except Exception as e:
        logger.error(f"Error en API save-single-frame: {str(e)}")
        return jsonify({'error': 'Error interno del servidor'}), 500

def decode_base64_frame(base64_string):
    """Decodifica frame en formato base64 con header data:image"""
    try:
        # Remover el header "data:image/jpeg;base64,"
        if ',' in base64_string:
            base64_string = base64_string.split(',')[1]
        
        image_data = base64.b64decode(base64_string)
        nparr = np.frombuffer(image_data, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return frame
    except Exception as e:
        logger.error(f"Error decodificando base64: {str(e)}")
        return None

def decode_base64_simple(base64_string):
    """Decodifica base64 simple"""
    try:
        image_data = base64.b64decode(base64_string)
        nparr = np.frombuffer(image_data, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return frame
    except Exception as e:
        logger.error(f"Error decodificando base64 simple: {str(e)}")
        return None

def decode_structured_frame(frame_data):
    """Decodifica frame en formato estructurado"""
    try:
        if 'image_data' in frame_data:
            image_data = base64.b64decode(frame_data['image_data'])
            nparr = np.frombuffer(image_data, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            return frame
    except Exception as e:
        logger.error(f"Error decodificando frame estructurado: {str(e)}")
        return None

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=FLASK_PORT, threaded=True)