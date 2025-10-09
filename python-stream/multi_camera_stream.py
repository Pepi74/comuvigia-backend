import tarfile
import threading
import pika
from flask_socketio import SocketIO, emit
import re
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
from flask import Flask, Response, jsonify, request
from video_reconstructor import video_bp, video_reconstructor
from flask_cors import CORS
import queue

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": os.environ["FRONTEND_URL"]}})
socketio = SocketIO(app, cors_allowed_origins="*")
app.register_blueprint(video_bp, url_prefix='/')
start_time = time.time()

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
        self.segmenter = FFmpegSegmenter(camera_id, link_camara, seg_seconds=BATCH_INTERVAL)
        self.preview_source = None
        self.fps_win = deque(maxlen=60)  # ~6–10s según tu delay
        self.last_fps_log = 0
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.alert_sent = False
        self.disabled = False
        self.last_reconnect_time = None

    def start(self):
        self.segmenter.start()
        self.preview_source = self.segmenter.preview_url
        self.running = True
        self.reconnect_attempts = 0  # Reset contador al iniciar
        self.alert_sent = False
        self.disabled = False
        self.reconnect_camera()
        self.thread = Thread(target=self.update, daemon=True)
        self.thread.start()
        logger.info(f"Stream de cámara {self.camera_id} iniciado")

    def update(self):
        last_publish_time = 0
        MAX_PUBLISH_FPS = 15  # ajustar según lo que quieras enviar a RabbitMQ
        while self.running:
            try:
                # Verificar si la cámara está deshabilitada por demasiados intentos fallidos
                if self.disabled:
                    logger.warning(f"Cámara {self.camera_id}: DESHABILITADA - Esperando intervención manual")
                    time.sleep(10)  # Esperar antes de revisar nuevamente
                    continue

                start_time = time.time() 

                # Si FFmpeg murió, reiniciar
                if self.segmenter.proc and self.segmenter.proc.poll() is not None:
                    logger.warning(f"Cámara {self.camera_id}: FFmpeg caído, reiniciando")
                    if self.cap:
                        try: 
                            self.cap.release()
                        except Exception:
                            pass
                        self.cap = None
                    self.segmenter.start()
                    self.preview_source = self.segmenter.preview_url
                    time.sleep(0.5)
                    self.reconnect_camera()
                    continue


                # Verificar conexión
                if not self.is_capture_active():
                    logger.warning(f"Cámara {self.camera_id}: Captura inactiva, reconectando...")
                    self.maintain_buffer_during_reconnection()
                    self.reconnect_camera()
                    time.sleep(2)
                    continue
                
                # Capturar frame
                ret, frame = self.cap.read()
                if not ret:
                    logger.warning(f"Cámara {self.camera_id}: Frame vacío, reconectando...")
                    self.maintain_buffer_during_reconnection()
                    self.reconnect_camera()
                    time.sleep(1)
                    continue
                    
                # Resetear contador de reconexiones si la captura es exitosa
                if self.reconnect_attempts > 0:
                    logger.info(f"Cámara {self.camera_id}: Conexión restaurada, resetear contador de reconexiones")
                    self.reconnect_attempts = 0
                    self.alert_sent = False

                # Procesar frame exitoso
                camera_id_int = self.camera_id
                # 🔹 Control de FPS para publicar
                current_time = time.time()
                if current_time - last_publish_time >= 1.0 / MAX_PUBLISH_FPS:
                    _, jpeg_buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                    jpeg_bytes = jpeg_buffer.tobytes()
                    publisher.publish_frame(camera_id_int, jpeg_bytes)
                    last_publish_time = current_time  
                
                processed_frame = cv2.resize(frame, DEFAULT_OUTPUT_SIZE)
                frame_time = time.time()
                
                with self.buffer_lock:
                    self.frames_buffer.append(processed_frame)
                    self.buffer_timestamps.append(frame_time)
                    self.frame = processed_frame
                
                # Monitorear FPS
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
                        if avg_fps < MAX_FPS * 0.5:  # ejemplo: <15 si MAX_FPS=30
                            logger.warning(
                                f"Cámara {self.camera_id}: FPS bajo promedio: {avg_fps:.1f}"
                            )
                    self.last_fps_log = current_time

                # Recolectar y subir segmentos .mkv generados por ffmpeg
                if current_time - self.last_check_time > 10:
                    self.collect_and_upload_segments()
                    self.last_check_time = current_time

                elapsed = time.time() - start_time
                target_frame_time = 1.0 / MAX_FPS  # Tiempo por frame
                sleep_time = max(0, target_frame_time - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                            
            except Exception as e:
                logger.error(f"Cámara {self.camera_id}: Error en captura: {str(e)}")
                self.maintain_buffer_during_reconnection()
                time.sleep(2)
    
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
                return None
            _, jpeg = cv2.imencode('.jpg', self.frame, 
                                 [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            return jpeg.tobytes()

    def reconnect_camera(self):
        """Reconectar a la cámara de manera segura"""
        try:
            # Incrementar contador de intentos
            self.reconnect_attempts += 1
            self.last_reconnect_time = datetime.now()
            
            logger.info(f"Cámara {self.camera_id}: Intento de reconexión {self.reconnect_attempts}/{self.max_reconnect_attempts}")

            # Verificar si se superó el límite de intentos
            if self.reconnect_attempts >= self.max_reconnect_attempts and not self.alert_sent:
                self.handle_reconnection_failure()
                return False
            
            # Liberar captura anterior
            if hasattr(self, 'cap') and self.cap is not None:
                try:
                    self.cap.release()
                except Exception as e:
                    logger.warning(f"Cámara {self.camera_id}: Error liberando captura: {str(e)}")
                finally:
                    self.cap = None
            
            time.sleep(1)  # Pausa antes de reconectar
            
            # Intentar reconexión
            logger.info(f"Cámara {self.camera_id}: Reconectando preview a {self.preview_source}")
            self.cap = cv2.VideoCapture(self.preview_source, cv2.CAP_FFMPEG)
            
            if self.cap is None or not self.cap.isOpened():
                logger.error(f"Cámara {self.camera_id}: Reconexión preview fallida")
                self.cap = None
                # Verificar si es el último intento fallido
                if self.reconnect_attempts >= self.max_reconnect_attempts and not self.alert_sent:
                    self.handle_reconnection_failure()
                
                return False
            
            # Configurar propiedades
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            #self.cap.set(cv2.CAP_PROP_FPS, MAX_FPS)
            logger.info(f"Cámara {self.camera_id}: Preview reconectado exitosamente")
            return True
            
        except Exception as e:
            logger.error(f"Cámara {self.camera_id}: Error en reconexión preview: {str(e)}")
            self.cap = None
            # Verificar si es el último intento fallido
            if self.reconnect_attempts >= self.max_reconnect_attempts and not self.alert_sent:
                self.handle_reconnection_failure()
            return False

    def handle_reconnection_failure(self):
        """Manejar fallo de reconexión después de múltiples intentos"""
        logger.error(f"Cámara {self.camera_id}: SUPERADO LÍMITE DE RECONEXIONES ({self.max_reconnect_attempts} intentos)")
        self.disabled = True
        self.alert_sent = True
        
        # Enviar alerta a la API
        self.send_reconnection_alert()
        
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
            self.reconnect_camera()
            logger.info(f"Cámara {self.camera_id}: Rehabilitada manualmente")
            return True
        return False

    def is_capture_active(self):
        """Verificar si la captura está activa"""
        return hasattr(self, 'cap') and self.cap is not None and self.cap.isOpened()

    def maintain_buffer_during_reconnection(self):
        """Mantener buffer durante reconexión"""
        reconection_duration = 3  # Segundos estimados de reconexión
        frames_to_maintain = int(MAX_FPS * reconection_duration) + OVERLAP_FRAMES
        
        with self.buffer_lock:
            current_size = len(self.frames_buffer)
            if current_size > frames_to_maintain:
                self.frames_buffer = deque(list(self.frames_buffer)[-frames_to_maintain:], 
                                           maxlen=OVERLAP_FRAMES + MAX_FPS * 3)
                self.buffer_timestamps = deque(list(self.buffer_timestamps)[-frames_to_maintain:], 
                                               maxlen=OVERLAP_FRAMES + MAX_FPS * 3)
                logger.info(f"Cámara {self.camera_id}: Buffer mantenido ({frames_to_maintain} frames)")

    def collect_and_upload_segments(self):
        try:
            cutoff = time.time() - 15  # evita archivos aún abiertos
            for p in sorted(self.segmenter.out_dir.glob("*.mkv")):
                if p.stat().st_mtime > cutoff:
                    continue
                # arma la key por fecha
                dt = datetime.strptime(p.stem, "%Y%m%d_%H%M%S")
                date_path = dt.strftime("%Y/%m/%d/%H")
                key = f"batches/{self.camera_id}/{date_path}/{p.name}"

                meta = {
                    "camera_id": str(self.camera_id),
                    "timestamp": dt.isoformat(),
                    "codec": "h264",
                    "segment_seconds": BATCH_INTERVAL,
                    "version": "2.0",
                    "batch_type": "continuous"
                }
                try:
                    self.s3_client.upload_file_path(str(p), key, metadata=meta, content_type="video/x-matroska")
                    os.remove(p)
                    logger.info(f"Cámara {self.camera_id}: subido y borrado {p.name}")
                except Exception as e:
                    logger.error(f"Cámara {self.camera_id}: fallo upload {p.name}: {e}")
        except Exception as e:
            logger.error(f"Cámara {self.camera_id}: colector error: {e}")

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
        u = urlparse(self.stream_url)
        is_hls = self.stream_url.endswith(".m3u8") or ".m3u8" in self.stream_url

        args = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error"]

        if is_hls:
            # Fuente HLS/HTTP
            args += [
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_on_network_error", "1",
                "-reconnect_delay_max", "2",
                "-fflags", "+genpts",
                "-probesize", "500k",
                "-analyzeduration", "500k",
                "-i", self.stream_url,
            ]
        else:
            # Fuente RTSP
            if u.scheme.lower() == "rtsp":
                args += ["-rtsp_transport", "tcp"]
            args += ["-i", self.stream_url]

        tee_out = (
            f"[f=segment:segment_time={self.seg_seconds}:reset_timestamps=1:strftime=1]"
            f"{str(self.out_dir)}/%Y%m%d_%H%M%S.mkv"
            f"|[f=mpegts]{self.preview_url}"
        )

        args += ["-map", "0:v:0", "-c", "copy", "-f", "tee", tee_out]

        # Log de ffmpeg a archivo (muy útil para ver errores finos)
        ffmpeg_log = open(f"/logs/ffmpeg_cam_{self.cam_id}.log", "ab", buffering=0)
        self.proc = subprocess.Popen(args, stdout=ffmpeg_log, stderr=ffmpeg_log)
    
    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=5)

# Inicializar streams para todas las cámaras
video_streams = {}
cameras_data = json.loads(CAMERAS) if isinstance(CAMERAS, str) else CAMERAS
print(cameras_data)
for camera in cameras_data:
    cam_id = int(camera["id"])
    # cam_id = camera["id"]
    config = camera
    
    if config["estado_camara"] and config["link_camara"] and config["link_camara_externo"]:
        logger.info(f"Intentando inicializar cámara {cam_id}: {config['link_camara']}")
        try:
            video_streams[cam_id] = VideoStream(cam_id, config["link_camara"])
            video_streams[cam_id].start()
            logger.info(f"Cámara {cam_id} inicializada - Estado: {video_streams[cam_id].running}")
            
        except Exception as e:
            logger.error(f"Error inicializando cámara {cam_id}: {str(e)}")
            #video_streams[cam_id] = VideoStream(cam_id, config["link_camara"])
            #logger.warning(f"Cámara {cam_id} creada pero no iniciada debido a error")
logger.info(f"Cámaras en video_streams: {list(video_streams.keys())}")

# Función auxiliar para actualizar cámaras
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
        # Si el estado no cambió, no hacer nada
        if estado_anterior == nuevo_estado:
            logger.info(f"Cámara {camera_id}: Estado sin cambios ({nuevo_estado})")
            return
        
        logger.info(f"Cámara {camera_id}: Cambio de estado {estado_anterior} -> {nuevo_estado}")
        
        # Detener stream si existe
        if camera_id in video_streams:
            stream_actual = video_streams[camera_id]
            
            if nuevo_estado in [False, "false", "inactiva", 0]:
                # 🔴 DETENER stream
                logger.info(f"Cámara {camera_id}: Deteniendo stream...")
                stream_actual.running = False
                stream_actual.disabled = True
                
                # Detener componentes
                if hasattr(stream_actual, 'segmenter'):
                    stream_actual.segmenter.stop()
                
                if hasattr(stream_actual, 'cap') and stream_actual.cap:
                    stream_actual.cap.release()
                    stream_actual.cap = None
                
                logger.info(f"Cámara {camera_id}: Stream detenido")
                
            else:
                # 🟢 ACTIVAR/REINICIAR stream
                logger.info(f"Cámara {camera_id}: Activando/reiniciando stream...")
                
                # Si el stream ya estaba corriendo pero en mal estado, detenerlo primero
                if stream_actual.running:
                    logger.info(f"Cámara {camera_id}: Reiniciando stream existente...")
                    stream_actual.running = False
                    
                    # Detener componentes actuales
                    if hasattr(stream_actual, 'segmenter'):
                        stream_actual.segmenter.stop()
                    
                    if hasattr(stream_actual, 'cap') and stream_actual.cap:
                        stream_actual.cap.release()
                        stream_actual.cap = None
                    
                    time.sleep(1)  # Pequeña pausa antes de reiniciar
                
                # Actualizar configuración si es necesario
                stream_actual.link_camara = camara_config.get("link_camara")
                
                # Reiniciar stream
                stream_actual.running = True
                stream_actual.disabled = False
                stream_actual.reconnect_attempts = 0
                stream_actual.alert_sent = False
                
                # Reiniciar segmenter con nueva configuración
                stream_actual.segmenter = FFmpegSegmenter(
                    camera_id, 
                    camara_config.get("link_camara"), 
                    seg_seconds=BATCH_INTERVAL
                )
                stream_actual.segmenter.start()
                stream_actual.preview_source = stream_actual.segmenter.preview_url
                
                # Intentar reconexión
                stream_actual.reconnect_camera()
                
                logger.info(f"Cámara {camera_id}: Stream activado/reiniciado")
        
        else:
            # 🆕 CREAR NUEVO stream si no existe
            if nuevo_estado not in [False, "false", "inactiva", 0]:
                logger.info(f"Cámara {camera_id}: Creando nuevo stream...")
                
                # Verificar que tenga link_camara
                if not camara_config.get("link_camara"):
                    logger.error(f"Cámara {camera_id}: No tiene link_camara configurado")
                    return
                
                # Crear nuevo VideoStream
                video_streams[camera_id] = VideoStream(camera_id, camara_config.get("link_camara"))
                video_streams[camera_id].start()
                
                logger.info(f"Cámara {camera_id}: Nuevo stream creado e iniciado")
            
    except Exception as e:
        logger.error(f"Error manejando stream para cámara {camera_id}: {str(e)}")

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=FLASK_PORT, threaded=True)