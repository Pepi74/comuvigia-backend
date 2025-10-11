import signal
import eventlet
eventlet.monkey_patch()
import tarfile
import pika
from flask_socketio import SocketIO, emit
import cv2
import numpy as np
import logging
from threading import Thread, Lock
import threading
from collections import deque
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse
import boto3
from botocore.client import Config
from boto3.s3.transfer import TransferConfig
from botocore.config import Config as BotoCoreConfig
from io import BytesIO
import json
import uuid
from pathlib import Path
import requests
import os
import base64
import hashlib
import tempfile
import subprocess, shlex
import queue
from flask import Flask, Response, jsonify, request
from video_reconstructor import video_bp, video_reconstructor
from flask_cors import CORS
import socketio as socketio_client
SOCKETIO_BACKEND_URL = "http://backend:3000"


app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": os.environ["FRONTEND_URL"]}})
socketio = SocketIO(app, cors_allowed_origins="*")
app.register_blueprint(video_bp, url_prefix='/')
start_time = time.time()
camera_queue = queue.Queue()


# Cliente Socket.IO
sio = socketio_client.Client()

class SocketIOClientManager:
    def __init__(self):
        self.connected = False
        self.video_streams_ref = None
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        
    def set_video_streams(self, video_streams):
        self.video_streams_ref = video_streams
        
    def start(self):
        """Iniciar el cliente Socket.IO"""
        thread = threading.Thread(target=self._connect_loop, daemon=True)
        thread.start()
        logger.info("Cliente Socket.IO iniciado")
     
    def _connect_loop(self):
        """Loop principal de conexión"""
        while True:
            try:
                if sio.connected:
                    logger.info("✅ Cliente Socket.IO ya está conectado")
                    time.sleep(5)  # Verificar cada 5 segundos
                    continue
                    
                logger.info(f"🔄 Conectando a {SOCKETIO_BACKEND_URL} (intento {self.reconnect_attempts + 1})")
                
                # Configurar event handlers
                sio.on('connect', self.on_connect)
                sio.on('disconnect', self.on_disconnect)
                sio.on('camera_status_update', self.on_camera_status_update)
                sio.on('camera_control', self.on_camera_control)
                
                # Conectar con timeout
                sio.connect(
                    SOCKETIO_BACKEND_URL,
                    wait_timeout=10,
                    transports=['websocket', 'polling'],
                    namespaces=['/']
                )
                
                # Esperar activamente por la conexión
                wait_start = time.time()
                while not sio.connected and (time.time() - wait_start) < 15:
                    time.sleep(0.5)
                
                if sio.connected:
                    logger.info("✅ Conexión Socket.IO establecida")
                    self.reconnect_attempts = 0
                    sio.wait()  # Mantener la conexión
                else:
                    logger.warning("❌ Timeout en conexión Socket.IO")
                    raise Exception("Timeout de conexión")
                
            except Exception as e:
                logger.error(f"❌ Error en conexión Socket.IO: {e}")
                self.connected = False
                
                # Estrategia de reconexión exponencial
                delay = min(2 ** self.reconnect_attempts, 30)  # Máximo 30 segundos
                self.reconnect_attempts += 1
                
                logger.info(f"🔄 Reintentando en {delay} segundos...")
                time.sleep(delay)
    
    def on_connect(self):
        """Cuando se conecta al backend"""
        self.connected = True
        self.reconnect_attempts = 0
        logger.info("✅ Conectado al backend via Socket.IO")
        
        # Enviar estado inicial de todas las cámaras
        self.send_initial_status()
    
    def on_disconnect(self):
        """Cuando se desconecta del backend"""
        self.connected = False
        logger.warning("❌ Desconectado del backend Socket.IO")
        
        # Intentar reconexión inmediata
        logger.info("🔄 Intentando reconexión inmediata...")
        time.sleep(2)

    def on_camera_status_update(self, data):
        """Manejar actualización de estado de cámara"""
        logger.info(f"📡 Socket.IO: Actualización recibida - {data}")
        
        # Ejecutar en thread separado
        thread = threading.Thread(
            target=self._handle_camera_update,
            args=(data,),
            daemon=True
        )
        thread.start()
    
    def _handle_camera_update(self, data):
        """Manejar actualización de cámara"""
        try:
            camera_id = data.get('camera_id')
            new_status = data.get('status')
            config = data.get('config', {})
            
            if not camera_id:
                logger.error("❌ Socket.IO: camera_id no proporcionado")
                return
                
            # Convertir camera_id a int
            try:
                camera_id_int = int(camera_id)
            except (ValueError, TypeError):
                logger.error(f"❌ Socket.IO: ID de cámara inválido: {camera_id}")
                return
            
            logger.info(f"🔄 Socket.IO: Actualizando cámara {camera_id_int} a {new_status}")
            
            # Buscar la cámara en cameras_data
            camara_encontrada = None
            for camara in cameras_data:
                if camara["id"] == camera_id_int:
                    camara_encontrada = camara
                    break
            
            if not camara_encontrada:
                logger.error(f"❌ Socket.IO: Cámara {camera_id_int} no encontrada")
                return
                
            estado_anterior = camara_encontrada.get("estado_camara")
            
            # Actualizar cameras_data
            actualizar_por_id(cameras_data, camera_id_int, "estado_camara", new_status)
            
            # Actualizar configuración si se proporciona
            if config and 'link_camara' in config:
                actualizar_por_id(cameras_data, camera_id_int, "link_camara", config['link_camara'])
            
            # Manejar el stream
            manejar_stream_camara(camera_id_int, new_status, estado_anterior, camara_encontrada)
            
            logger.info(f"✅ Socket.IO: Cámara {camera_id_int} actualizada exitosamente")
            
        except Exception as e:
            logger.error(f"❌ Socket.IO: Error actualizando cámara: {e}")
    
    def on_camera_control(self, data):
        """Manejar comandos de control directo"""
        logger.info(f"🎛️ Socket.IO: Control recibido - {data}")
        
        thread = threading.Thread(
            target=self._handle_camera_control,
            args=(data,),
            daemon=True
        )
        thread.start()
    
    def _handle_camera_control(self, data):
        """Manejar control de cámara"""
        try:
            camera_id = data.get('camera_id')
            action = data.get('action')
            params = data.get('params', {})
            
            if not camera_id or not action:
                logger.error("❌ Socket.IO: Datos de control incompletos")
                return
                
            # Convertir camera_id a int
            try:
                camera_id_int = int(camera_id)
            except (ValueError, TypeError):
                logger.error(f"❌ Socket.IO: ID de cámara inválido: {camera_id}")
                return
            
            if camera_id_int not in self.video_streams_ref:
                logger.error(f"❌ Socket.IO: Cámara {camera_id_int} no encontrada en video_streams")
                return
                
            stream = self.video_streams_ref[camera_id_int]
            
            if action == 'restart':
                logger.info(f"🔄 Socket.IO: Reiniciando cámara {camera_id_int}")
                if stream.running:
                    stream.running = False
                    time.sleep(1)
                stream.running = True
                stream.reconnect_attempts = 0
                stream.alert_sent = False
                stream.disabled = False
                stream.reconnect_camera()
                
            elif action == 'stop':
                logger.info(f"⏹️ Socket.IO: Deteniendo cámara {camera_id_int}")
                stream.running = False
                stream.disabled = True
                if hasattr(stream, 'segmenter'):
                    stream.segmenter.stop()
                if hasattr(stream, 'cap') and stream.cap:
                    stream.cap.release()
                    stream.cap = None
                    
            elif action == 'enable':
                logger.info(f"✅ Socket.IO: Habilitando cámara {camera_id_int}")
                if stream.enable_camera():
                    logger.info(f"✅ Socket.IO: Cámara {camera_id_int} habilitada")
                else:
                    logger.info(f"ℹ️ Socket.IO: Cámara {camera_id_int} ya estaba habilitada")
                    
            elif action == 'save_batch':
                logger.info(f"💾 Socket.IO: Guardando batch cámara {camera_id_int}")
                stream.save_batch()
                
            else:
                logger.warning(f"⚠️ Socket.IO: Acción no reconocida: {action}")
                
        except Exception as e:
            logger.error(f"❌ Socket.IO: Error en control de cámara: {e}")
    
    def send_initial_status(self):
        """Enviar estado inicial de todas las cámaras"""
        try:
            if not self.video_streams_ref:
                return
                
            for camera_id, stream in self.video_streams_ref.items():
                status_data = {
                    'camera_id': camera_id,
                    'status': 'active' if stream.running and not stream.disabled else 'inactive',
                    'running': stream.running,
                    'disabled': stream.disabled,
                    'reconnect_attempts': stream.reconnect_attempts,
                    'timestamp': datetime.now().isoformat()
                }
                sio.emit('streaming_status', status_data)
                
            logger.info("📤 Socket.IO: Estado inicial enviado")
            
        except Exception as e:
            logger.error(f"❌ Socket.IO: Error enviando estado inicial: {e}")
    
    def send_camera_status(self, camera_id, status, additional_data=None):
        """Enviar estado de una cámara específica"""
        if not self.connected:
            return
            
        try:
            data = {
                'camera_id': camera_id,
                'status': status,
                'timestamp': datetime.now().isoformat(),
                'service': 'streaming'
            }
            
            if additional_data:
                data.update(additional_data)
                
            sio.emit('camera_status', data)
            logger.debug(f"📤 Socket.IO: Estado enviado - cámara {camera_id} -> {status}")
            
        except Exception as e:
            logger.error(f"❌ Socket.IO: Error enviando estado: {e}")

# Instancia global del manager
socketio_manager = SocketIOClientManager()

# Configuración
DEFAULT_OUTPUT_SIZE = (640, 360)
FLASK_PORT = 5000
MAX_FPS = 30
BATCH_DURATION_MIN = 5  # minutos por batch
BATCH_SIZE = BATCH_DURATION_MIN * 60 * MAX_FPS  # 9000 frames
OVERLAP_SECONDS = 2  # 2 segundos de overlap
OVERLAP_FRAMES = int(MAX_FPS * OVERLAP_SECONDS)  # 60 frames
BATCH_INTERVAL = BATCH_DURATION_MIN * 60  # 300 segundos

# Configuración S3
S3_ENDPOINT = "http://minio:9000"  # Para MinIO local
S3_ACCESS_KEY = os.environ["S3_ACCESS_KEY"]
S3_SECRET_KEY = os.environ["S3_SECRET_KEY"]
S3_BUCKET_NAME = os.environ["S3_BUCKET_NAME"]
S3_REGION = "us-east-1"

# Obtención de cámaras
url = "http://backend:3000/api/camaras"
payload = {}
headers = {}
response = requests.request("GET", url, headers=headers, data=payload)
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
    def __init__(self, prefetch_count=10):
        # Configuración RabbitMQ
        self.rabbit_host = os.getenv('RABBIT_HOST', 'rabbitmq')
        self.rabbit_port = int(os.getenv('RABBIT_PORT', 5672))
        self.rabbit_user = os.getenv('RABBITMQ_DEFAULT_USER', 'guest')
        self.rabbit_password = os.getenv('RABBITMQ_DEFAULT_PASS', 'guest')
        self.exchange = 'frames'

        self._stop = threading.Event()
        self.connection = None
        self.channel = None
        self.prefetch_count = prefetch_count

        self._lock = threading.Lock()
        self.connect()

    def connect(self):
        """Conecta a RabbitMQ y declara exchange"""
        credentials = pika.PlainCredentials(self.rabbit_user, self.rabbit_password)
        parameters = pika.ConnectionParameters(
            host=self.rabbit_host,
            port=self.rabbit_port,
            credentials=credentials,
            heartbeat=600,
            blocked_connection_timeout=300,
        )
        while not self._stop.is_set():
            try:
                self.connection = pika.BlockingConnection(parameters)
                self.channel = self.connection.channel()
                self.channel.exchange_declare(exchange=self.exchange, exchange_type='direct')
                # Control de prefetch para no saturar al consumidor
                self.channel.basic_qos(prefetch_count=self.prefetch_count)
                print("Conectado a RabbitMQ")
                break
            except Exception as e:
                print(f"No se pudo conectar a RabbitMQ, reintentando en 5s: {e}")
                time.sleep(5)

    def publish_frame(self, camera_id, frame_bytes):
        """Publica un frame JPEG directamente como mensaje"""
        if not isinstance(frame_bytes, (bytes, bytearray)):
            raise TypeError(f"publish_frame requiere bytes, no {type(frame_bytes)}")

        routing_key = f"camera_{camera_id}"
        with self._lock:
            try:
                self.channel.basic_publish(
                    exchange=self.exchange,
                    routing_key=routing_key,
                    body=frame_bytes
                )
            except (pika.exceptions.AMQPConnectionError, pika.exceptions.ChannelClosedByBroker) as e:
                
                print(f"Conexión perdida al publicar, reconectando...: {e}")
                self.connect()
                self.channel.basic_publish(
                    exchange=self.exchange,
                    routing_key=routing_key,
                    body=frame_bytes
                )

    def stop(self):
        self._stop.set()
        with self._lock:
            if self.connection and not self.connection.is_closed:
                self.connection.close()

publisher = RabbitPublisher(prefetch_count=10)


class S3Client:
    def __init__(self):
        self.client = None
        self.connected = False
        self.transfer_cfg = TransferConfig(multipart_threshold=8*1024*1024, multipart_chunksize=8*1024*1024, max_concurrency=2)
        self.connect()
    
    def connect(self):
        try:
            self.client = boto3.client(
                's3',
                endpoint_url=S3_ENDPOINT,
                aws_access_key_id=S3_ACCESS_KEY,
                aws_secret_access_key=S3_SECRET_KEY,
                region_name=S3_REGION,
                config=BotoCoreConfig(s3={'addressing_style': 'path'}, signature_version='s3v4')
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
    
    def upload_file_path(self, local_path, key, metadata=None, content_type="video/x-matroska"):
        extra = {"ContentType": content_type}
        if metadata:
            extra["Metadata"] = {k: str(v) for k, v in metadata.items()}
        self.client.upload_file(
            Filename=local_path,
            Bucket=S3_BUCKET_NAME,
            Key=key,
            ExtraArgs=extra,
            Config=self.transfer_cfg
        )

    def upload_batch(self, camera_id, frames, timestamp, custom_metadata=None, flag=False, fps=30):
        try:
            if not frames:
                logger.warning(f"Cámara {camera_id}: No hay frames para guardar")
                return False
            
            # CALCULAR DURACIÓN REAL
            start_time = None
            end_time = None
            duration_seconds = len(frames) / MAX_FPS  # Valor por defecto
            expected_duration = BATCH_SIZE / MAX_FPS
            if duration_seconds < expected_duration * 0.5:  # Menos del 50% de lo esperado
                logger.warning(f"Cámara {camera_id}: Duración sospechosa: "
                            f"{duration_seconds:.1f}s vs esperado: {expected_duration:.1f}s")
        
            # Procesar tiempos desde custom_metadata si están disponibles
            if custom_metadata:
                start_time = custom_metadata.get('start_time')
                end_time = custom_metadata.get('end_time')
                
                # Convertir strings a datetime si es necesario
                if isinstance(start_time, str):
                    try:
                        start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                    except ValueError:
                        start_time = None
                        logger.warning(f"Cámara {camera_id}: Formato inválido para start_time")
                
                if isinstance(end_time, str):
                    try:
                        end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                    except ValueError:
                        end_time = None
                        logger.warning(f"Cámara {camera_id}: Formato inválido para end_time")
                
                # Calcular duración real si tenemos ambos tiempos
                if isinstance(start_time, datetime) and isinstance(end_time, datetime):
                    duration_seconds = (end_time - start_time).total_seconds()
                    # Asegurar duración positiva
                    if duration_seconds <= 0:
                        duration_seconds = len(frames) / MAX_FPS
                        logger.warning(f"Cámara {camera_id}: Duración inválida, usando cálculo por frames")
            
            # Asegurar que timestamp sea datetime
            if isinstance(timestamp, dict) or not isinstance(timestamp, datetime):
                logger.warning(f"Cámara {camera_id}: timestamp inválido, usando ahora()")
                timestamp = datetime.now()

            # Crear metadata con información crucial
            base_metadata = {
                "camera_id": str(camera_id),
                "timestamp": timestamp.isoformat(),
                "frames_count": len(frames),
                "resolution": f"{frames[0].shape[1]}x{frames[0].shape[0]}",
                "fps": fps,
                "duration_seconds": round(duration_seconds, 3),
                "codec": "h264",
                "version": "1.1",
                "batch_type": "clip" if flag else "continuous"
            }
            
            # Agregar tiempos de inicio/fin si están disponibles
            if start_time and isinstance(start_time, datetime):
                base_metadata["recording_start"] = start_time.isoformat()
            if end_time and isinstance(end_time, datetime):
                base_metadata["recording_end"] = end_time.isoformat()
            
            # Fusionar y Normalizar metadata personalizada
            if custom_metadata:
                normalized_custom = {}
                for k, v in custom_metadata.items():
                    if k in ['start_time', 'end_time']:
                        continue
                    elif isinstance(v, datetime) or hasattr(v, 'isoformat'):
                        normalized_custom[k] = v.isoformat()
                    elif isinstance(v, (dict, list)):
                        try:
                            normalized_custom[k] = json.dumps(v, ensure_ascii=False)
                        except (TypeError, ValueError):
                            normalized_custom[k] = str(v)
                    else:
                        normalized_custom[k] = str(v)
                metadata = {**base_metadata, **normalized_custom}
            else:
                metadata = base_metadata
            
            # Crear tar.gz en archivo temporal
            with tempfile.NamedTemporaryFile(suffix='.tar.gz', delete=False) as temp_file:
                temp_path = temp_file.name
            
            try:
                with tarfile.open(temp_path, 'w:gz') as tar:
                    # Agregar metadata
                    metadata_json = json.dumps(metadata, ensure_ascii=False, indent=2)
                    metadata_bytes = metadata_json.encode('utf-8')
                    metadata_info = tarfile.TarInfo("metadata.json")
                    metadata_info.size = len(metadata_bytes)
                    metadata_info.mtime = time.time()
                    tar.addfile(metadata_info, BytesIO(metadata_bytes))
                    
                    # Agregar frames como archivos JPEG individuales
                    for i, frame in enumerate(frames):
                        if frame is None or not isinstance(frame, np.ndarray):
                            logger.warning(f"Cámara {camera_id}: Frame {i} inválido, omitiendo")
                            continue
                            
                        try:
                            # Codificar frame como JPEG
                            success, jpeg = cv2.imencode('.jpg', frame, [
                                int(cv2.IMWRITE_JPEG_QUALITY), 85,
                                int(cv2.IMWRITE_JPEG_OPTIMIZE), 1
                            ])
                            
                            if not success or jpeg is None:
                                logger.warning(f"Cámara {camera_id}: Error codificando frame {i}")
                                continue
                                
                            frame_bytes = jpeg.tobytes()
                            frame_info = tarfile.TarInfo(f"frame_{i:06d}.jpg")
                            frame_info.size = len(frame_bytes)
                            frame_info.mtime = time.time()
                            tar.addfile(frame_info, BytesIO(frame_bytes))
                            
                        except Exception as e:
                            logger.error(f"Cámara {camera_id}: Error procesando frame {i}: {str(e)}")
                            continue
                
                # Verificar integridad del tar.gz
                try:
                    with tarfile.open(temp_path, 'r:gz') as test_tar:
                        members = test_tar.getnames()
                        frame_files = [m for m in members if m.startswith('frame_')]
                        if len(frame_files) != len(frames):
                            logger.warning(f"Cámara {camera_id}: Número de frames inconsistente: {len(frame_files)} vs {len(frames)}")
                except tarfile.ReadError as e:
                    logger.error(f"Cámara {camera_id}: Archivo tar.gz corrupto: {str(e)}")
                    os.unlink(temp_path)
                    return False
                
                # Leer archivo temporal para upload
                with open(temp_path, 'rb') as f:
                    tar_data = f.read()
                
                # Calcular checksum
                checksum = hashlib.md5(tar_data).hexdigest()
                metadata["checksum"] = checksum
                
                # Preparar path y key
                date_path = timestamp.strftime('%Y/%m/%d/%H')
                batch_id = f"{camera_id}_{timestamp.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
                
                if flag:
                    key = f"clips/{camera_id}/{date_path}/{batch_id}.tar.gz"
                else:
                    key = f"batches/{camera_id}/{date_path}/{batch_id}.tar.gz"
                
                # Preparar metadata para S3
                s3_metadata = {}
                for k, v in metadata.items():
                    if isinstance(v, (str, int, float, bool)):
                        s3_metadata[k] = str(v)
                    elif v is not None:
                        s3_metadata[k] = str(v)
                
                # Subir a S3 desde archivo temporal
                with open(temp_path, 'rb') as f:
                    self.client.put_object(
                        Bucket=S3_BUCKET_NAME,
                        Key=key,
                        Body=f,
                        ContentType='application/gzip',
                        Metadata=s3_metadata,
                        ContentMD5=base64.b64encode(hashlib.md5(tar_data).digest()).decode('utf-8')
                    )
                
                # Log detallado
                size_mb = len(tar_data) / (1024 * 1024)
                logger.info(
                    f"Cámara {camera_id}: Batch {key} - "
                    f"{size_mb:.2f}MB, {len(frames)} frames, "
                    f"{duration_seconds:.1f}s, checksum: {checksum[:8]}, "
                    f"integrity: OK"
                )
                
                return {
                    'key': key,
                    'bucket': S3_BUCKET_NAME,
                    'size_mb': round(size_mb, 2),
                    'frames_count': len(frames),
                    'timestamp': timestamp.isoformat(),
                    'duration_seconds': round(duration_seconds, 3),
                    's3_url': f"{S3_ENDPOINT}/{S3_BUCKET_NAME}/{key}",
                    'metadata': metadata,
                    'checksum': checksum
                }
                
            finally:
                try:
                    os.unlink(temp_path)
                except:
                    pass
                    
        except Exception as e:
            logger.error(f"Cámara {camera_id}: Error subiendo batch: {str(e)}")
            try:
                if 'temp_path' in locals() and os.path.exists(temp_path):
                    os.unlink(temp_path)
            except Exception as e2:
                logger.warning(f"Cámara {camera_id}: Error eliminando archivo temporal: {str(e2)}")
            return False

S3 = S3Client()

# HTTP
class VideoStream:
    def __init__(self, camera_id, link_camara):
        self.camera_id = camera_id
        self.link_camara = link_camara
        self.frame = None
        self.lock = Lock()
        self.running = False
        self.thread = None
        self.frames_buffer = deque(maxlen=OVERLAP_FRAMES + MAX_FPS * 3)  # p.ej. 60 + 90 = 150 frames
        self.buffer_timestamps = deque(maxlen=OVERLAP_FRAMES + MAX_FPS * 3)  # Timestamps
        self.buffer_lock = threading.RLock()
        self.last_batch_time = time.time()
        self.s3_client = S3
        self.cap = None
        self.last_frame_time = time.time()
        self.last_check_time = time.time() 
        self.segmenter = None
        self.fps_win = deque(maxlen=30)
        self.last_fps_log = 0
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 3
        self.alert_sent = False
        self.disabled = False
        self.last_reconnect_time = None
        self.socketio_manager = socketio_manager

    def start(self):
        """Iniciar stream con conexión directa + grabación en paralelo"""
        self.running = True
        self.reconnect_attempts = 0
        self.alert_sent = False
        self.disabled = False
        
        # ✅ FFMPEG PARA GRABACIÓN (en segundo plano)
        if self.segmenter is None:
            self.segmenter = FFmpegSegmenter(self.camera_id, self.link_camara, seg_seconds=BATCH_INTERVAL)
        self.segmenter.start()
        
        # ✅ OPENCV DIRECTO PARA ANÁLISIS
        self.thread = Thread(target=self.update, daemon=True)
        self.thread.start()
        
        logger.info(f"🎬 Stream cámara {self.camera_id} - Directo + Grabación paralela")
        
        def inicializar_componentes_pesados():
            try:
                # ✅ INICIAR FFMPEG
                self.segmenter.start()
                
                # ✅ ESPERAR QUE PROC ESTÉ LISTO
                time.sleep(1)
                
                # Intentar primera conexión
                self.reconnect_camera()
                
                logger.info(f"✅ Componentes pesados inicializados cámara {self.camera_id}")
            except Exception as e:
                logger.error(f"❌ Error inicializando componentes pesados cámara {self.camera_id}: {str(e)}")
        
        threading.Thread(target=inicializar_componentes_pesados, daemon=True).start()
    
    def update(self):
        """Loop principal con conexión directa RTSP"""
        last_publish_time = 0
        MAX_PUBLISH_FPS = 15
        
        while self.running:
            try:
                if self.disabled:
                    time.sleep(5)
                    continue

                # ✅ CONEXIÓN DIRECTA RTSP
                if not self.is_capture_active():
                    logger.info(f"Cámara {self.camera_id}: Conectando directo RTSP...")
                    success = self.connect_direct_rtsp()
                    if not success:
                        time.sleep(2)
                        continue
                
                # Capturar frame DIRECTAMENTE del RTSP
                ret, frame = self.cap.read()
                if not ret:
                    logger.warning(f"Cámara {self.camera_id}: Frame vacío directo")
                    self.reconnect_direct()
                    time.sleep(1)
                    continue
                logger.info(f"Cámara {self.camera_id}: Frame capturado - Shape: {frame.shape}, Tipo: {type(frame)}")
                # ✅ RESETEAR CONTADOR SI LA CAPTURA ES EXITOSA
                if self.reconnect_attempts > 0:
                    self.reconnect_attempts = 0
                    self.alert_sent = False
                    self.socketio_manager.send_camera_status(
                        self.camera_id, 
                        'active',
                        {'reason': 'connection_restored'}
                    )

                camera_id_int = self.camera_id
                
                # Publicar a RabbitMQ
                current_time = time.time()
                if current_time - last_publish_time >= 1.0 / MAX_PUBLISH_FPS:
                    _, jpeg_buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                    jpeg_bytes = jpeg_buffer.tobytes()
                    publisher.publish_frame(camera_id_int, jpeg_bytes)
                    last_publish_time = current_time
                
                # Buffer para batches
                processed_frame = cv2.resize(frame, DEFAULT_OUTPUT_SIZE)
                frame_time = time.time()
                
                with self.buffer_lock:
                    self.frames_buffer.append(processed_frame)
                    self.buffer_timestamps.append(frame_time)
                    self.frame = processed_frame
                
                # Monitoreo FPS
                current_time = time.time()
                if hasattr(self, 'last_frame_time'):
                    frame_interval = current_time - self.last_frame_time
                    if frame_interval > 0:
                        current_fps = 1 / frame_interval
                        self.fps_win.append(current_fps)
                        if current_fps < MAX_FPS * 0.5:
                            logger.warning(f"Cámara {self.camera_id}: FPS bajo: {current_fps:.1f}")
                
                self.last_frame_time = current_time

                # Log de promedio cada ~10s
                if current_time - self.last_fps_log > 10:
                    if len(self.fps_win) > 10:
                        avg_fps = sum(self.fps_win) / len(self.fps_win)
                        if avg_fps < MAX_FPS * 0.5:
                            logger.warning(f"Cámara {self.camera_id}: FPS bajo promedio: {avg_fps:.1f}")
                    self.last_fps_log = current_time

                # Control FPS
                elapsed = time.time() - start_time
                target_frame_time = 1.0 / MAX_FPS
                sleep_time = max(0, target_frame_time - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                            
            except Exception as e:
                logger.error(f"Cámara {self.camera_id}: Error en captura directa: {str(e)}")
                time.sleep(2)
    
    def connect_direct_rtsp(self):
        """Conectar directamente al RTSP - NUEVO MÉTODO"""
        try:
            # Liberar conexión anterior
            if hasattr(self, 'cap') and self.cap is not None:
                self.cap.release()
                self.cap = None
            
            logger.info(f"Cámara {self.camera_id}: Conectando DIRECTAMENTE a {self.link_camara}")
            
            # ✅ CONEXIÓN DIRECTA AL RTSP ORIGINAL
            self.cap = cv2.VideoCapture(self.link_camara, cv2.CAP_FFMPEG)
            
            if self.cap is None or not self.cap.isOpened():
                logger.error(f"Cámara {self.camera_id}: No se pudo abrir RTSP directo")
                return False
            
            # Configuración optimizada para RTSP
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.cap.set(cv2.CAP_PROP_FPS, 30)
            # self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'H264'))
            
            # Verificar que realmente funcione
            ret, frame = self.cap.read()
            if ret and frame is not None:
                logger.info(f"✅ Cámara {self.camera_id}: RTSP directo CONECTADO - Frame: {frame.shape}")
                return True
            else:
                logger.warning(f"Cámara {self.camera_id}: RTSP conectado pero sin frames")
                return True  # Devolver True igual para intentar
                
        except Exception as e:
            logger.error(f"Cámara {self.camera_id}: Error en conexión directa RTSP: {str(e)}")
            return False

    def reconnect_direct(self):
        """Reconexión para stream directo"""
        self.reconnect_attempts += 1
        logger.info(f"Cámara {self.camera_id}: Reconexión directa {self.reconnect_attempts}/{self.max_reconnect_attempts}")
        
        if self.reconnect_attempts >= self.max_reconnect_attempts and not self.alert_sent:
            self.handle_reconnection_failure()
            return False
        
        return self.connect_direct_rtsp()

    def save_batch(self):
        """Guardar batch con overlap inteligente"""
        with self.buffer_lock:
            total_frames = len(self.frames_buffer)
            total_timestamps = len(self.buffer_timestamps)
            
            # Verificar mínimo de frames
            if total_frames < BATCH_SIZE // 2:
                logger.info(f"Cámara {self.camera_id}: Muy pocos frames ({total_frames})")
                return
            
            # Calcular frames a tomar (batch + overlap)
            frames_to_take = min(total_frames, BATCH_SIZE + OVERLAP_FRAMES)
            
            # Extraer frames y timestamps
            batch_frames = list(self.frames_buffer)[-frames_to_take:]
            batch_timestamps = list(self.buffer_timestamps)[-frames_to_take:]
            
            if not batch_frames or not batch_timestamps:
                return
            
            # Calcular timestamps exactos
            start_time = datetime.fromtimestamp(batch_timestamps[0])
            end_time = datetime.fromtimestamp(batch_timestamps[-1])
            actual_duration = (end_time - start_time).total_seconds()
            
            # Preparar metadata
            custom_metadata = {
                'start_time': start_time,
                'end_time': end_time,
                'actual_duration': actual_duration,
                'total_frames': len(batch_frames),
                'overlap_frames': OVERLAP_FRAMES,
                'theoretical_frames': BATCH_SIZE,
                'expected_duration': BATCH_SIZE / MAX_FPS,
                'source': 'continuous_with_overlap'
            }
            
            # 🔄 MANTENER OVERLAP PARA EL PRÓXIMO BATCH
            frames_to_keep = min(OVERLAP_FRAMES, total_frames)
            self.frames_buffer = deque(
                list(self.frames_buffer)[-frames_to_keep:], 
                maxlen=BATCH_SIZE * 3
            )
            self.buffer_timestamps = deque(
                list(self.buffer_timestamps)[-frames_to_keep:], 
                maxlen=BATCH_SIZE * 3
            )
            
            self.last_batch_time = time.time()
            
            logger.info(f"Cámara {self.camera_id}: Batch guardado - "
                    f"{len(batch_frames)} frames, {actual_duration:.1f}s, "
                    f"keep: {frames_to_keep} frames")
            
            # Subir a S3
            Thread(target=self.s3_client.upload_batch, 
                args=(self.camera_id, batch_frames, end_time, custom_metadata, False), 
                daemon=True).start()
            
    def get_frame(self):
        with self.lock:
            if self.frame is None:
                logger.warning(f"Cámara {self.camera_id}: get_frame() devuelve None")
                return None
                
            try:
                # Verificar que el frame sea valido
                if not isinstance(self.frame, np.ndarray):
                    logger.error(f"Cámara {self.camera_id}: Frame no es numpy array: {type(self.frame)}")
                    return None
                    
                if self.frame.size == 0:
                    logger.error(f"Cámara {self.camera_id}: Frame vacío")
                    return None
                    
                _, jpeg = cv2.imencode('.jpg', self.frame, [
                    int(cv2.IMWRITE_JPEG_QUALITY), 80
                ])
                
                logger.info(f"Cámara {self.camera_id}: JPEG encoded - Size: {len(jpeg.tobytes())} bytes")
                return jpeg.tobytes()
                
            except Exception as e:
                logger.error(f"Cámara {self.camera_id}: Error en get_frame(): {str(e)}")
                return None

    def reconnect_camera(self):
        """Reconectar directamente al RTSP - REEMPLAZAR"""
        try:
            self.reconnect_attempts += 1
            self.last_reconnect_time = datetime.now()
            
            logger.info(f"Cámara {self.camera_id}: Intento de reconexión DIRECTA {self.reconnect_attempts}/{self.max_reconnect_attempts}")

            if self.reconnect_attempts >= self.max_reconnect_attempts and not self.alert_sent:
                self.handle_reconnection_failure()
                return False
            
            # ✅ USAR CONEXIÓN DIRECTA
            return self.connect_direct_rtsp()
                
        except Exception as e:
            logger.error(f"Cámara {self.camera_id}: Error en reconexión directa: {str(e)}")
            return False

    def handle_reconnection_failure(self):
        """Manejar fallo de reconexión después de múltiples intentos"""
        logger.error(f"Cámara {self.camera_id}: SUPERADO LÍMITE DE RECONEXIONES ({self.max_reconnect_attempts} intentos)")
        self.disabled = True
        self.alert_sent = True
        
        # Enviar alerta a la API
        self.send_reconnection_alert()
        
        # Notificar via Socket.IO
        self.socketio_manager.send_camera_status(
            self.camera_id, 
            'failed',
            {
                'reason': 'max_reconnection_attempts',
                'attempts': self.reconnect_attempts,
                'last_attempt': self.last_reconnect_time.isoformat() if self.last_reconnect_time else datetime.now().isoformat()
            }
        )
        
        # Detener componentes
        self.stop_components()

    def send_reconnection_alert(self):
        """Enviar alerta a la API cuando fallan las reconexiones"""
        try:
            alert_data = {
                "camera_id": self.camera_id,
                "alert_type": 4,
                "message": f"Cámara {self.camera_id} superó el límite de {self.max_reconnect_attempts} intentos de reconexión",
                "reconnect_attempts": self.reconnect_attempts,
                "max_attempts": self.max_reconnect_attempts,
                "last_attempt_time": self.last_reconnect_time.isoformat() if self.last_reconnect_time else datetime.now().isoformat(),
                "timestamp": datetime.now().isoformat(),
                "status": "disabled",
            }
            logger.info(alert_data)
            api_url = "http://backend:3000/api/alertas/cam-reconnection-failure/nueva-alerta"
            
            response = requests.post(
                api_url,
                json=alert_data,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            if response.status_code == 200 or response.status_code == 201:
                logger.info(f"Cámara {self.camera_id}: Alerta enviada exitosamente a la API")
            else:
                logger.error(f"Cámara {self.camera_id}: Error al enviar alerta. Código: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Cámara {self.camera_id}: Error enviando alerta a API: {str(e)}")

    def stop_components(self):
        """Detener componentes de la cámara de manera segura"""
        try:
            if self.cap:
                self.cap.release()
                self.cap = None
            
            if self.segmenter:
                self.segmenter.stop()
                
            self.frame = None
            logger.info(f"Cámara {self.camera_id}: Componentes detenidos por falla de reconexión")
        except Exception as e:
            logger.error(f"Cámara {self.camera_id}: Error deteniendo componentes: {str(e)}")

    def enable_camera(self):
        """Rehabilitar la cámara manualmente"""
        if self.disabled:
            self.disabled = False
            self.reconnect_attempts = 0
            self.alert_sent = False
            self.running = True
            # Notificar via Socket.IO
            self.socketio_manager.send_camera_status(
                self.camera_id, 
                'active',
                {'reason': 'manual_enable'}
            )
            self.reconnect_camera()
            logger.info(f"Cámara {self.camera_id}: Rehabilitada manualmente")
            return True
        return False

    def is_capture_active(self):
        """Verificar si la captura está realmente activa"""
        try:
            if not hasattr(self, 'cap') or self.cap is None:
                return False
            
            if not self.cap.isOpened():
                return False
            
            # Intentar leer un frame para verificar que realmente funciona
            if hasattr(self, '_last_successful_frame') and time.time() - self._last_successful_frame < 10:
                return True  # Asumir que sigue activa si tuvo frames recientemente
            
            # Verificación más agresiva
            ret, frame = self.cap.read()
            if ret and frame is not None:
                self._last_successful_frame = time.time()
                return True
            else:
                return False
                
        except Exception as e:
            logger.debug(f"Error verificando captura cámara {self.camera_id}: {e}")
            return False
        
class FFmpegSegmenter:
    def __init__(self, cam_id, stream_url, out_dir="/tmp/segments", seg_seconds=300, base_port=12000):
        self.cam_id = cam_id
        self.stream_url = stream_url
        self.out_dir = Path(out_dir) / str(cam_id)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.seg_seconds = seg_seconds
        self.proc = None
        self.port = base_port + int(cam_id)  # puerto único por cámara
        self.preview_url = (
            f"udp://127.0.0.1:{self.port}"
            f"?pkt_size=1316"                # tamaño típico para TS
            f"&fifo_size=5000000"            # 5 MB de buffer (se puede ajustar)
            f"&overrun_nonfatal=1"           # no matar el stream si se llena
            f"&reuse=1"                      # reusar socket
        )

    def start(self):
        """FFmpeg solo para grabación, sin output UDP"""
        args = [
            "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error",
            "-i", self.stream_url,
            "-c", "copy",  # Copy sin re-encode
            "-f", "segment",
            "-segment_time", str(self.seg_seconds),
            "-reset_timestamps", "1",
            "-strftime", "1",
            f"{str(self.out_dir)}/%Y%m%d_%H%M%S.mkv"
        ]
        
        # SIN output UDP - solo grabación a archivo
        try:
            ffmpeg_log = open(f"/logs/ffmpeg_record_{self.cam_id}.log", "ab", buffering=0)
            self.proc = subprocess.Popen(args, stdout=ffmpeg_log, stderr=ffmpeg_log)
            logger.info(f"🎥 FFmpeg grabación iniciada cámara {self.cam_id} (PID: {self.proc.pid})")
        except Exception as e:
            logger.error(f"❌ Error FFmpeg grabación cámara {self.cam_id}: {e}")

    def stop(self):
        """Detener FFmpeg de manera más agresiva"""
        if self.proc and self.proc.poll() is None:
            try:
                # ✅ TERMINACIÓN MÁS AGRESIVA
                self.proc.terminate()
                self.proc.wait(timeout=3)  # ✅ Timeout más corto
            except subprocess.TimeoutExpired:
                logger.warning(f"⚠️ FFmpeg no respondió a terminate, usando kill")
                try:
                    # ✅ FORZAR TERMINACIÓN
                    if hasattr(os, 'killpg'):
                        os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                    else:
                        self.proc.kill()
                    self.proc.wait(timeout=2)
                except:
                    pass

# Inicializar streams para todas las cámaras
video_streams = {}
cameras_data = json.loads(CAMERAS) if isinstance(CAMERAS, str) else CAMERAS
# Configurar el Socket.IO manager con la referencia a video_streams + iniciar el cliente Socket.IO
socketio_manager.set_video_streams(video_streams)
socketio_manager.start()
for camera in cameras_data:
    cam_id = int(camera["id"])
    config = camera
    
    if config["estado_camara"] and config["link_camara"] and config["link_camara_externo"]:
        logger.info(f"Intentando inicializar cámara {cam_id}: {config['link_camara']}")
        try:
            video_streams[cam_id] = VideoStream(cam_id, config["link_camara"])
            video_streams[cam_id].start()
            logger.info(f"Cámara {cam_id} inicializada - Estado: {video_streams[cam_id].running}")
            
        except Exception as e:
            logger.error(f"Error inicializando cámara {cam_id}: {str(e)}")
logger.info(f"Cámaras en video_streams: {list(video_streams.keys())}")

def actualizar_por_id(lista_json, id_buscar, campo, nuevo_valor):
    """Actualiza un campo específico de una cámara por ID"""
    for item in lista_json:
        if item["id"] == id_buscar:
            item[campo] = nuevo_valor
            return True  # Indica que se actualizó
    return False  # Indica que no se encontró

def generate_frames(camera_id):
    try:
        camera_id_int = int(camera_id)
    except ValueError:
        yield error_frame("Invalid camera ID")
        return
    
    if camera_id_int not in video_streams:
        yield error_frame("Camera not found")
        return
        
    stream = video_streams[camera_id_int]
    last_time = time.time()
    
    while True:
        frame = stream.get_frame()
        if frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')            
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
    cameras_data = json.loads(CAMERAS) if isinstance(CAMERAS, str) else CAMERAS
    for camera in cameras_data:
        cameras_list.append({
            "id": camera["id"],
            "estado_camara": camera["estado_camara"],
            "nombre": camera["nombre"],
            "posicion": camera["posicion"],
            "direccion": camera["direccion"],
        })
    return jsonify(cameras_list)

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
    Soporta: base64 con/ sin header data:image y formato estructurado {"image_data": ...}
    """
    try:
        fps_data = 30
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No se proporcionaron datos JSON'}), 400
        #print(data['frames'])
        # Campos obligatorios
        required_fields = ['camera_id', 'frames','fps']
        
        # Verificar campos obligatorios
        required_fields = ['camera_id', 'frames', 'fps']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Campo requerido faltante: {field}'}), 400
        
        camera_id = data['camera_id']
        frames_data = data['frames']
        fps_data = data['fps']
        metadata = data.get('metadata', {})
        
        # Procesar los frames
        processed_frames = []
        
        for frame_data in frames_data:
            frame = None
            # Diferentes formatos de frame
            if isinstance(frame_data, str) and frame_data.startswith('data:image'):
                # Base64 con header data:image
                frame = decode_base64_frame(frame_data)
            elif isinstance(frame_data, str):
                # Base64 simple
                frame = decode_base64_simple(frame_data)
            elif isinstance(frame_data, dict) and 'image_data' in frame_data:
                # Formato estructurado
                frame = decode_structured_frame(frame_data)
            else:
                return jsonify({'error': 'Formato de frame no soportado'}), 400
            
            if frame is not None:
                processed_frames.append(frame)
        
        if not processed_frames:
            return jsonify({'error': 'No se pudieron procesar los frames'}), 400
        
        # Subir batch a S3
        timestamp = datetime.now()
        recording_duration = len(processed_frames) / MAX_FPS
        recording_start = timestamp - timedelta(seconds=recording_duration)
        recording_end = timestamp

        # Metadata adicional
        full_metadata = {
            **metadata,
            'source': 'api-save-frames',
            'received_timestamp': datetime.now().isoformat(),
            'frames_count': len(processed_frames),
            'camera_id': camera_id,
            'start_time': recording_start,
            'end_time': recording_end
        }

        success = S3.upload_batch(camera_id, processed_frames, timestamp, full_metadata, True,fps=fps_data)
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
        
        # Subir a S3 y obtener información
        timestamp = datetime.now()

        recording_duration = 1 / MAX_FPS  # Duración de 1 frame
        recording_start = timestamp - timedelta(seconds=recording_duration)
        recording_end = timestamp

        full_metadata = {
            **metadata,
            'source': 'api-save-single-frame',
            'received_timestamp': datetime.now().isoformat(),
            'camera_id': camera_id,
            'start_time': recording_start,
            'end_time': recording_end
        }

        s3_client = S3
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

# Debugging y estado
@app.route('/debug/camera/<camera_id>')
def debug_camera(camera_id):
    """Diagnóstico completo de cámara"""
    try:
        camera_id_int = int(camera_id)
        if camera_id_int not in video_streams:
            return jsonify({"error": f"Cámara {camera_id} no encontrada"}), 404
        
        stream = video_streams[camera_id_int]
        with stream.buffer_lock:
            status = {
                "camera_id": camera_id_int,
                "stream_url": stream.link_camara,
                "running": stream.running,
                "capture_active": stream.is_capture_active(),
                "has_frame": stream.frame is not None,
                "frames_buffer_size": len(stream.frames_buffer),
                "buffer_duration": f"{(len(stream.frames_buffer) / MAX_FPS):.1f}s",
                "time_since_last_batch": round(time.time() - stream.last_batch_time, 1),
                "reconnection_status": {
                    "attempts": stream.reconnect_attempts,
                    "max_attempts": stream.max_reconnect_attempts,
                    "disabled": stream.disabled,
                    "alert_sent": stream.alert_sent,
                    "last_attempt_time": stream.last_reconnect_time.isoformat() if stream.last_reconnect_time else None
                },
                "batch_config": {
                    "size_frames": BATCH_SIZE,
                    "size_seconds": BATCH_SIZE / MAX_FPS,
                    "overlap_frames": OVERLAP_FRAMES,
                    "overlap_seconds": OVERLAP_SECONDS,
                    "interval_seconds": BATCH_INTERVAL
                },
                "current_buffer_health": f"{len(stream.frames_buffer)}/{BATCH_SIZE}",
                "s3_connected": stream.s3_client.connected if hasattr(stream, 's3_client') else False,
            }
        
        return jsonify(status)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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

@app.route('/socketio/status')
def socketio_status():
    """Endpoint para verificar estado de Socket.IO"""
    status = {
        'socketio_connected': socketio_manager.connected,
        'backend_url': SOCKETIO_BACKEND_URL,
        'active_cameras': len([cam for cam in video_streams.values() if cam.running]),
        'total_cameras': len(video_streams)
    }
    return jsonify(status)

@app.route('/socketio/debug')
def socketio_debug():
    """Endpoint para debugging de Socket.IO"""
    debug_info = {
        'cliente': {
            'conectado': socketio_manager.connected,
            'sid': sio.sid if hasattr(sio, 'sid') else None,
            'transport': sio.transport() if hasattr(sio, 'transport') else None,
        },
        'servidor': {
            'async_mode': socketio.async_mode,
        },
        'video_streams': len(video_streams) if video_streams else 0,
        'backend_url': SOCKETIO_BACKEND_URL
    }
    return jsonify(debug_info)

@app.route('/api/cameras/<camera_id>/enable', methods=['POST'])
def enable_camera(camera_id):
    """Endpoint para rehabilitar una cámara manualmente"""
    try:
        camera_id_int = int(camera_id)
        if camera_id_int not in video_streams:
            return jsonify({"error": f"Cámara {camera_id} no encontrada"}), 404
        
        stream = video_streams[camera_id_int]
        
        if stream.enable_camera():
            return jsonify({
                "success": True,
                "message": f"Cámara {camera_id} rehabilitada exitosamente",
                "camera_id": camera_id,
                "status": "enabled"
            }), 200
        else:
            return jsonify({
                "success": False,
                "message": f"Cámara {camera_id} no estaba deshabilitada",
                "camera_id": camera_id,
                "status": "already_enabled"
            }), 200
            
    except Exception as e:
        logger.error(f"Error rehabilitando cámara {camera_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500

def manejar_stream_camara(camera_id, nuevo_estado, estado_anterior, camara_config):
    """
    Maneja el inicio/detención del stream según el estado de la cámara
    """
    try:
        if estado_anterior == nuevo_estado:
            return
        
        logger.info(f"🔄 Cámara {camera_id}: {estado_anterior} -> {nuevo_estado}")
        
        # Confirmar cambio de estado a los clientes
        status_str = 'active' if nuevo_estado else 'inactive'
        socketio_manager.send_camera_status(
            camera_id, 
            status_str,
            {'reason': 'state_change'}
        )
        
        # Iniciar thread para manejo simple
        threading.Thread(
            target=procesar_camara_simple,
            args=(camera_id, nuevo_estado, camara_config),
            daemon=True
        ).start()
            
    except Exception as e:
        logger.error(f"❌ Error manejando cámara {camera_id}: {str(e)}")

def procesar_camara_simple(camera_id, nuevo_estado, camara_config):
    """Procesamiento simple y robusto de cámara"""
    try:
        if not nuevo_estado:
            detener_camara_simple(camera_id)
        else:
            activar_camara_simple(camera_id, camara_config)
    except Exception as e:
        logger.error(f"❌ Error procesando cámara {camera_id}: {e}")

def activar_camara_simple(camera_id, camara_config):
    """Activar cámara de manera simple y robusta"""
    try:
        logger.info(f"🚀 Activando cámara {camera_id} (simple)")
        
        # Crear o obtener stream
        if camera_id not in video_streams:
            video_streams[camera_id] = VideoStream(camera_id, camara_config.get("link_camara"))
            video_streams[camera_id].socketio_manager = socketio_manager
        
        stream = video_streams[camera_id]
        
        # Configuración básica
        stream.running = True
        stream.disabled = False
        stream.reconnect_attempts = 0
        stream.alert_sent = False

        # Conectar directamente al RTSP
        success = stream.connect_direct_rtsp()
        
        if success:
            # Iniciar el thread principal
            stream.thread = Thread(target=stream.update, daemon=True)
            stream.thread.start()
            logger.info(f"✅ Cámara {camera_id} iniciada con RTSP directo")
        else:
            logger.error(f"❌ Cámara {camera_id}: No se pudo conectar RTSP directo")
        
        # Inicializar segmenter si no existe
        if stream.segmenter is None:
            stream.segmenter = FFmpegSegmenter(
                camera_id, 
                camara_config.get("link_camara"), 
                seg_seconds=BATCH_INTERVAL
            )
        
        # Iniciar FFmpeg con timeout
        stream.segmenter.start()
        
        # Confirmar estado final
        socketio_manager.send_camera_status(
            camera_id, 
            'active' if success else 'inactive',
            {'reason': 'stream_ready'}
        )
        
    except Exception as e:
        logger.error(f"❌ Error activando cámara {camera_id}: {e}")
        socketio_manager.send_camera_status(
            camera_id, 
            'error',
            {'reason': 'activation_error', 'error': str(e)}
        )

def detener_camara_simple(camera_id):
    """Detener cámara de manera simple"""
    try:
        if camera_id in video_streams:
            stream = video_streams[camera_id]
            logger.info(f"🛑 Deteniendo cámara {camera_id}")
            
            stream.running = False
            stream.disabled = True
            
            # Detener componentes
            if hasattr(stream, 'segmenter') and stream.segmenter:
                stream.segmenter.stop()
            
            if hasattr(stream, 'cap') and stream.cap:
                stream.cap.release()
                stream.cap = None
            
            logger.info(f"✅ Cámara {camera_id} detenida")
            
    except Exception as e:
        logger.error(f"❌ Error deteniendo cámara {camera_id}: {e}")

@app.route('/camaras/<int:camera_id>/estado', methods=['PUT'])
def actualizar_estado_camara(camera_id):
    try:
        data = request.get_json()
        nuevo_estado = data.get('estado')
        
        camara_encontrada = None
        for camara in cameras_data:
            if camara["id"] == camera_id:
                camara_encontrada = camara
                break
        
        if not camara_encontrada:
            return jsonify({
                'success': False,
                'message': f'Cámara {camera_id} no encontrada'
            }), 404
        
        estado_anterior = camara_encontrada.get("estado_camara")

        # Actualización interna de la cámara
        actualizar_por_id(cameras_data, camera_id, "estado_camara", nuevo_estado)
        manejar_stream_camara(camera_id, nuevo_estado, estado_anterior, camara_encontrada)
        try:
            camara = {
                "estado": nuevo_estado,
            }
            logger.info(camara)
            api_url = f"http://backend:3000/api/camaras/{camera_id}"
            
            response = requests.put(
                api_url,
                json=camara,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            if response.status_code == 200 or response.status_code == 201:
                logger.info(f"Cámara {camera_id}: Cámara actualizada exitosamente")
            else:
                logger.error(f"Cámara {camera_id}: Error al actualizar cámara. Código: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Cámara {camera_id}: Error al actualizar: {str(e)}")
        
        # Emitir a todos los clientes
        socketio.emit('estado-camara', {
            'cameraId': camera_id,
            'estado': nuevo_estado,
            'ultima_conexion': datetime.utcnow().isoformat() + 'Z'
        })
        
        return jsonify({
            'success': True,
            'message': f'Estado de cámara actualizado a {nuevo_estado}'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

def verificar_estado_real_camaras():
    """Verificar el estado real de las cámaras vs estado reportado"""
    try:
        for camera_id, stream in video_streams.items():
            estado_reportado = stream.running and not stream.disabled
            estado_real = stream.is_capture_active()
            
            if estado_reportado != estado_real:
                logger.warning(f"📡 Cámara {camera_id}: Estado inconsistente - "
                             f"Reportado: {estado_reportado}, Real: {estado_real}")
                
                # ✅ CORREGIR ESTADO INCONSISTENTE
                if estado_reportado and not estado_real:
                    # La cámara debería estar activa pero no está capturando
                    logger.info(f"🔄 Reintentando conexión para cámara {camera_id}")
                    stream.reconnect_camera()
                    
    except Exception as e:
        logger.error(f"Error verificando estado de cámaras: {e}")

# Ejecutar verificación periódica cada 30 segundos
def iniciar_verificador_estado():
    while True:
        time.sleep(30)
        verificar_estado_real_camaras()

threading.Thread(target=iniciar_verificador_estado, daemon=True).start()

if __name__ == '__main__':
    socketio.run(
        app, 
        host='0.0.0.0', 
        port=FLASK_PORT, 
        debug=False,
        allow_unsafe_werkzeug=True
    )