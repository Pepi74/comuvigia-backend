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
import traceback
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
        self._camera_locks = {}
        
    def set_video_streams(self, video_streams):
        self.video_streams_ref = video_streams
        
    def start(self):
        """Iniciar el cliente Socket.IO"""
        thread = threading.Thread(target=self._connect_loop, daemon=True)
        thread.start()
        logger.info("Cliente Socket.IO iniciado")
     
    def get_camera_lock(self, camera_id):
        """Obtener lock único por cámara"""
        if camera_id not in self._camera_locks:
            self._camera_locks[camera_id] = threading.RLock()
        return self._camera_locks[camera_id]

    def on_camera_update(self, data):
        """Manejar actualizaciones de cámaras desde Backend Node.js"""
        logger.info(f"📡 Socket.IO: Actualización de cámara recibida - {data}")
        # No se crea un Thread adicional
        self._handle_camera_update_from_backend(data)

    def _handle_camera_update_from_backend(self, data):
        """Manejar actualización de cámara desde Node.js - CON DEBUG Y LOCK"""
        try:
            action = data.get('action')
            camera_data = data.get('camera')
            camera_id = camera_data.get('id')
            
            logger.info(f"📡 Socket.IO: Actualización de cámara recibida - {data}")
            
            # Convertir camera_id a int
            try:
                camera_id_int = int(camera_id)
            except (ValueError, TypeError):
                logger.error(f"❌ Socket.IO: ID de cámara inválido: {camera_id}")
                return
            
            logger.info(f"🔄 Socket.IO: Procesando {action} cámara {camera_id_int}")
            
            # Lock
            camera_lock = self.get_camera_lock(camera_id_int)
            with camera_lock:
                self._process_camera_update_safe(camera_id_int, action, camera_data)
                
        except Exception as e:
            logger.error(f"❌ Socket.IO: Error procesando actualización de cámara: {e}")
            logger.error(f"❌ Stack trace: {traceback.format_exc()}")

    def _process_camera_update_safe(self, camera_id_int, action, camera_data):
        """Procesar actualización de cámara de manera thread-safe"""
        # Buscar la cámara en cameras_data
        camara_encontrada = None
        for camara in cameras_data:
            if camara["id"] == camera_id_int:
                camara_encontrada = camara
                break
        
        # DEBUG
        logger.info(f"🔍 Cámara encontrada en cameras_data: {camara_encontrada is not None}")
        logger.info(f"🔍 Estado anterior: {camara_encontrada.get('estado_camara') if camara_encontrada else 'N/A'}")
        logger.info(f"🔍 Estado nuevo: {camera_data.get('estado_camara')}")
        logger.info(f"🔍 Link cámara: {camera_data.get('link_camara')}")
        logger.info(f"🔍 En video_streams: {camera_id_int in video_streams}")
        
        if action == 'create':
            # AGREGAR a cameras_data y manejar stream
            if camara_encontrada is None:
                cameras_data.append(camera_data)
                camara_encontrada = camera_data
            
            # Iniciar stream si está activa
            if camera_data.get('estado_camara') and camera_data.get('link_camara'):
                logger.info(f"🚀 Iniciando stream para nueva cámara {camera_id_int}")
                manejar_stream_camara(camera_id_int, True, False, camera_data)
            else:
                logger.info(f"⏸️ Cámara {camera_id_int} creada pero inactiva o sin link")
                
        elif action == 'update':
            if camara_encontrada:
                estado_anterior = camara_encontrada.get("estado_camara")
                estado_nuevo = camera_data.get('estado_camara')
                
                logger.info(f"🔄 Actualizando cámara {camera_id_int}: {estado_anterior} -> {estado_nuevo}")
                
                # Actualizar datos en cameras_data
                for key, value in camera_data.items():
                    actualizar_por_id(cameras_data, camera_id_int, key, value)
                
                # Actualizar si cambia de estado
                if estado_anterior != estado_nuevo:
                    logger.info(f"🎛️ Cambio de estado detectado: {estado_anterior} -> {estado_nuevo}")
                    manejar_stream_camara(camera_id_int, estado_nuevo, estado_anterior, camera_data)
                else:
                    logger.info("ℹ️ Sin cambio de estado, omitiendo manejo de stream")
                    
            logger.info(f"✅ Socket.IO: Cámara {camera_id_int} actualizada")
            
        elif action == 'delete':
            # ELIMINAR de cameras_data y detener stream
            if camara_encontrada:
                cameras_data[:] = [cam for cam in cameras_data if cam["id"] != camera_id_int]
            # Detener stream
            if camera_id_int in video_streams:
                logger.info(f"🛑 Deteniendo stream cámara {camera_id_int}")
                detener_camara_simple(camera_id_int)
            logger.info(f"🗑️ Socket.IO: Cámara {camera_id_int} eliminada")
            
        else:
            logger.warning(f"⚠️ Socket.IO: Acción no reconocida: {action}")

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
                sio.on('camera-update', self.on_camera_update)

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
        self.batch_timer = None
        self.batch_interval = BATCH_INTERVAL

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
        elif self.segmenter.proc is None or self.segmenter.proc.poll() is not None:
            # Si existe pero no está corriendo, reiniciar
            self.segmenter.start()
        
        # ✅ OPENCV DIRECTO PARA ANÁLISIS
        self.thread = Thread(target=self.update, daemon=True)
        self.thread.start()
        
        self.start_batch_timer()
        
        logger.info(f"🎬 Stream cámara {self.camera_id} - Timer batch cada {self.batch_interval}s")
        
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
    
    def start_batch_timer(self):
        """Iniciar timer periódico para guardar batches MKV"""
        def batch_timer_task():
            while self.running and not self.disabled:
                try:
                    # Esperar el intervalo configurado
                    time.sleep(self.batch_interval)
                    
                    if not self.running or self.disabled:
                        break
                    
                    # ✅ GUARDAR BATCH CADA 5 MINUTOS
                    logger.info(f"⏰ Cámara {self.camera_id}: Timer batch activado")
                    self.save_batch()
                    
                except Exception as e:
                    logger.error(f"Cámara {self.camera_id}: Error en timer batch: {str(e)}")
                    time.sleep(10)  # Esperar antes de reintentar
        
        # Iniciar thread del timer
        self.batch_timer = Thread(target=batch_timer_task, daemon=True)
        self.batch_timer.start()
        logger.info(f"⏰ Timer batch iniciado para cámara {self.camera_id} - Intervalo: {self.batch_interval}s")

    def update(self):
        """Loop principal con diagnóstico detallado"""
        last_publish_time = 0
        last_batch_check = time.time()
        MAX_PUBLISH_FPS = 15
        BATCH_CHECK_INTERVAL = 60
        
        frame_count = 0
        empty_frame_count = 0
        
        while self.running:
            try:
                if self.disabled:
                    logger.info(f"Cámara {self.camera_id}: Deshabilitada, esperando...")
                    time.sleep(5)
                    continue

                # ✅ DIAGNÓSTICO DE CONEXIÓN
                if not self.is_capture_active():
                    logger.warning(f"Cámara {self.camera_id}: Captura inactiva, reconectando...")
                    success = self.connect_direct_rtsp()
                    if not success:
                        logger.error(f"Cámara {self.camera_id}: Reconexión fallida")
                        time.sleep(2)
                        continue
                    else:
                        logger.info(f"Cámara {self.camera_id}: Reconexión exitosa")
                
                # ✅ CAPTURAR FRAME CON DIAGNÓSTICO
                ret, frame = self.cap.read()
                if not ret:
                    empty_frame_count += 1
                    logger.warning(f"Cámara {self.camera_id}: Frame vacío (#{empty_frame_count})")
                    if empty_frame_count >= 5:
                        logger.error(f"Cámara {self.camera_id}: Demasiados frames vacíos, reconectando...")
                        self.reconnect_direct()
                        empty_frame_count = 0
                    time.sleep(1)
                    continue
                
                # ✅ RESETEAR CONTADOR DE FRAMES VACÍOS
                empty_frame_count = 0
                frame_count += 1
                
                # ✅ VERIFICAR FRAME CAPTURADO
                if frame is None:
                    logger.error(f"Cámara {self.camera_id}: Frame es None después de cap.read()")
                    continue
                    
                if frame.size == 0:
                    logger.error(f"Cámara {self.camera_id}: Frame vacío (size=0)")
                    continue
                
                #logger.info(f"✅ Cámara {self.camera_id}: Frame #{frame_count} capturado - Shape: {frame.shape}")

                # ✅ PROCESAR FRAME
                try:
                    processed_frame = cv2.resize(frame, DEFAULT_OUTPUT_SIZE)
                    #logger.info(f"✅ Cámara {self.camera_id}: Frame procesado - {processed_frame.shape}")
                except Exception as e:
                    logger.error(f"Cámara {self.camera_id}: Error en resize: {str(e)}")
                    continue

                # ✅ ACTUALIZAR BUFFER Y FRAME PRINCIPAL
                with self.buffer_lock:
                    try:
                        # Agregar al buffer
                        self.frames_buffer.append(processed_frame)
                        self.buffer_timestamps.append(time.time())
                        
                        # ✅ ESTABLECER self.frame (CRÍTICO)
                        self.frame = processed_frame
                        
                        # Diagnóstico cada 100 frames
                        if frame_count % 100 == 0:
                            buffer_size = len(self.frames_buffer)
                            logger.info(f"📊 Cámara {self.camera_id}: "
                                    f"Frame #{frame_count}, Buffer: {buffer_size}, "
                                    f"self.frame: {'SET' if self.frame is not None else 'MISSING'}")
                            
                    except Exception as e:
                        logger.error(f"Cámara {self.camera_id}: Error actualizando buffer: {str(e)}")

                # ✅ PUBLICAR A RABBITMQ
                current_time = time.time()
                if current_time - last_publish_time >= 1.0 / MAX_PUBLISH_FPS:
                    try:
                        _, jpeg_buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                        jpeg_bytes = jpeg_buffer.tobytes()
                        publisher.publish_frame(self.camera_id, jpeg_bytes)
                        last_publish_time = current_time
                        logger.debug(f"Cámara {self.camera_id}: Frame publicado a RabbitMQ")
                    except Exception as e:
                        logger.error(f"Cámara {self.camera_id}: Error publicando a RabbitMQ: {str(e)}")

                # ✅ CONTROL DE VELOCIDAD
                elapsed = time.time() - start_time
                target_frame_time = 1.0 / MAX_FPS
                sleep_time = max(0, target_frame_time - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                            
            except Exception as e:
                logger.error(f"Cámara {self.camera_id}: Error en loop principal: {str(e)}")
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
        """Guardar batch coordinando con FFmpeg - VERSIÓN MEJORADA"""
        try:
            if not self.segmenter or not hasattr(self.segmenter, 'out_dir'):
                logger.error(f"Cámara {self.camera_id}: No hay segmenter configurado")
                return
            
            # Verificar que FFmpeg esté corriendo
            if not self.segmenter.proc or self.segmenter.proc.poll() is not None:
                logger.error(f"Cámara {self.camera_id}: FFmpeg no está corriendo, reiniciando...")
                self.segmenter.start()
                time.sleep(5)  # Esperar a que FFmpeg se inicie
                return
            
            # Obtener el archivo más reciente grabado por FFmpeg
            segment_dir = self.segmenter.out_dir
            if not segment_dir.exists():
                logger.error(f"Cámara {self.camera_id}: Directorio de segmentos no existe: {segment_dir}")
                return
            
            # Listar archivos MKV recientes (últimos 10 minutos)
            mkv_files = list(segment_dir.glob("*.mkv"))
            current_time = time.time()
            
            # Filtrar archivos de los últimos 10 minutos
            recent_mkv_files = [f for f in mkv_files if current_time - f.stat().st_mtime < 600]
            
            if not recent_mkv_files:
                logger.warning(f"Cámara {self.camera_id}: No hay archivos MKV recientes (últimos 10min)")
                # Listar todos los archivos para diagnóstico
                if mkv_files:
                    logger.info(f"Cámara {self.camera_id}: Archivos MKV existentes (más antiguos):")
                    for mkv in sorted(mkv_files, key=lambda x: x.stat().st_mtime, reverse=True)[:3]:
                        age = current_time - mkv.stat().st_mtime
                        logger.info(f"  - {mkv.name} ({age:.1f}s old, {mkv.stat().st_size/1024/1024:.2f}MB)")
                return
            
            # Tomar el archivo más reciente que tenga al menos 10 segundos de antigüedad
            valid_files = [f for f in recent_mkv_files if current_time - f.stat().st_mtime > 10]
            
            if not valid_files:
                logger.info(f"Cámara {self.camera_id}: Archivos MKV muy recientes (<10s), esperando...")
                return
                
            latest_mkv = max(valid_files, key=lambda x: x.stat().st_mtime)
            
            # Verificar que el archivo tenga tamaño adecuado (mínimo 1MB para 5 minutos)
            file_size = latest_mkv.stat().st_size
            if file_size < 1024 * 1024:  # Mínimo 1MB
                logger.warning(f"Cámara {self.camera_id}: Archivo MKV muy pequeño: {file_size/1024/1024:.2f} MB")
                return
            
            # Calcular duración basada en timestamp del archivo
            file_mtime = datetime.fromtimestamp(latest_mkv.stat().st_mtime)
            timestamp = datetime.now()
            
            # Intentar calcular duración real desde el nombre del archivo
            try:
                # El formato es %Y%m%d_%H%M%S.mkv
                filename = latest_mkv.stem
                file_dt = datetime.strptime(filename, '%Y%m%d_%H%M%S')
                duration_seconds = (timestamp - file_dt).total_seconds()
                # Ajustar a un valor razonable (entre 30s y 10min)
                duration_seconds = max(30, min(duration_seconds, 600))
            except Exception as e:
                logger.warning(f"Cámara {self.camera_id}: No se pudo calcular duración desde nombre: {e}")
                duration_seconds = BATCH_INTERVAL  # Valor por defecto
            
            # Preparar metadata
            custom_metadata = {
                'start_time': timestamp - timedelta(seconds=duration_seconds),
                'end_time': timestamp,
                'actual_duration': duration_seconds,
                'file_size_bytes': file_size,
                'source': 'ffmpeg_segment',
                'codec': 'h264',
                'container': 'mkv',
                'expected_duration': BATCH_INTERVAL,
                'file_creation_time': file_mtime.isoformat()
            }
            
            # Subir el archivo MKV a S3
            Thread(target=self.upload_mkv_to_s3, 
                args=(latest_mkv, timestamp, custom_metadata), 
                daemon=True).start()
            
            logger.info(f"Cámara {self.camera_id}: Batch MKV enviado a S3 - {latest_mkv.name} "
                    f"({file_size/1024/1024:.2f} MB, {duration_seconds:.1f}s)")
            
        except Exception as e:
            logger.error(f"Cámara {self.camera_id}: Error en save_batch MKV: {str(e)}")
            
    def upload_mkv_to_s3(self, mkv_path, timestamp, custom_metadata):
        """Subir archivo MKV a S3"""
        try:
            # Preparar key para S3
            date_path = timestamp.strftime('%Y/%m/%d/%H')
            batch_id = f"{self.camera_id}_{timestamp.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
            key = f"batches/{self.camera_id}/{date_path}/{batch_id}.mkv"
            
            # Metadata para S3
            metadata = {
                "camera_id": str(self.camera_id),
                "timestamp": timestamp.isoformat(),
                "duration_seconds": BATCH_INTERVAL,
                "codec": "h264",
                "container": "matroska",
                "version": "1.1",
                "batch_type": "continuous_mkv"
            }
            
            # Fusionar metadata
            if custom_metadata:
                for k, v in custom_metadata.items():
                    if isinstance(v, (int, float)):
                        metadata[k] = str(v)  # Convertir números explícitamente
                    elif isinstance(v, (str, bool)):
                        metadata[k] = str(v)
                    elif isinstance(v, datetime):
                        metadata[k] = v.isoformat()
                    elif v is not None:
                        metadata[k] = str(v)
            # Normalizar metadata
            for k, v in metadata.items():
                if not isinstance(v, str):
                    metadata[k] = str(v) if v is not None else ""
            
            # Subir a S3
            self.s3_client.client.upload_file(
                Filename=str(mkv_path),
                Bucket=S3_BUCKET_NAME,
                Key=key,
                ExtraArgs={
                    'ContentType': 'video/x-matroska',
                    'Metadata': metadata
                }
            )
            
            logger.info(f"✅ Cámara {self.camera_id}: MKV subido a S3 - {key}")
            
            # Opcional: eliminar archivo local después de subir
            try:
                mkv_path.unlink()
                logger.debug(f"Cámara {self.camera_id}: Archivo local eliminado - {mkv_path.name}")
            except Exception as e:
                logger.warning(f"Cámara {self.camera_id}: No se pudo eliminar archivo local: {e}")
                
        except Exception as e:
            logger.error(f"Cámara {self.camera_id}: Error subiendo MKV a S3: {str(e)}")

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
                
                #logger.info(f"Cámara {self.camera_id}: JPEG encoded - Size: {len(jpeg.tobytes())} bytes")
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
            # Detener timer primero
            self.running = False

            if self.cap:
                self.cap.release()
                self.cap = None
            
            if self.segmenter:
                self.segmenter.stop()
                
            self.frame = None
            logger.info(f"Cámara {self.camera_id}: Componentes y timer detenidos")
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
    def __init__(self, cam_id, stream_url, out_dir="/tmp/segments", seg_seconds=BATCH_INTERVAL, base_port=12000):
        self.cam_id = cam_id
        self.stream_url = stream_url
        self.out_dir = Path(out_dir) / str(cam_id)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.seg_seconds = seg_seconds
        self.proc = None
        self.port = base_port + int(cam_id)  # puerto único por cámara
         # Configuración mejorada de FFmpeg
        self.ffmpeg_args = [
            "ffmpeg", 
            "-hide_banner", 
            "-nostats", 
            "-loglevel", "error",
            "-rtsp_transport", "tcp",  # Mejor estabilidad
            "-max_delay", "500000",    # Máximo delay para RTSP
            "-i", self.stream_url,
            "-c", "copy",              # Sin re-encoding
            "-f", "segment",
            "-segment_time", str(self.seg_seconds),
            "-segment_format", "matroska",  # Formato MKV explícito
            "-reset_timestamps", "1",
            "-strftime", "1",
            "-segment_atclocktime", "1",    # Segmentos en tiempos exactos
            "-avoid_negative_ts", "make_zero",
            f"{str(self.out_dir)}/%Y%m%d_%H%M%S.mkv"
        ]

    def start(self):
        """Iniciar FFmpeg para grabación con segmentos"""
        try:
            ffmpeg_log = open(f"/logs/ffmpeg_record_{self.cam_id}.log", "ab", buffering=0)
            self.proc = subprocess.Popen(self.ffmpeg_args, stdout=ffmpeg_log, stderr=ffmpeg_log)
            logger.info(f"🎥 FFmpeg grabación iniciada cámara {self.cam_id} (PID: {self.proc.pid})")
            
            # Esperar a que comience a generar archivos
            time.sleep(2)
            
        except Exception as e:
            logger.error(f"❌ Error FFmpeg grabación cámara {self.cam_id}: {e}")

    def get_latest_segment(self):
        """Obtener el segmento más reciente"""
        try:
            mkv_files = list(self.out_dir.glob("*.mkv"))
            return max(mkv_files, key=lambda x: x.stat().st_mtime) if mkv_files else None
        except Exception as e:
            logger.error(f"Error obteniendo segmento cámara {self.cam_id}: {e}")
            return None

    def stop(self):
        """Detener FFmpeg de manera segura"""
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
                logger.info(f"FFmpeg detenido cámara {self.cam_id}")
            except subprocess.TimeoutExpired:
                logger.warning(f"FFmpeg no respondió, forzando terminación cámara {self.cam_id}")
                self.proc.kill()
                self.proc.wait()

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
        logger.warning(f"🎥 Cámara {camera_id_int} no encontrada en video_streams")
        yield error_frame("Camera not found")
        return
        
    stream = video_streams[camera_id_int]
    last_time = time.time()
    frame_count = 0
    while True:
        # Verificacion periodica de que el stream sigue activo
        if camera_id_int not in video_streams or not stream.running or stream.disabled:
            logger.info(f"🎥 Stream cámara {camera_id_int} finalizado")
            yield error_frame("Stream ended")
            break
        frame = stream.get_frame()
        if frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')            
            elapsed = time.time() - last_time
            delay = max(0, (1/MAX_FPS) - elapsed)
            time.sleep(delay)
            last_time = time.time()
            # Log cada 100 frames para debugging
            frame_count += 1
            if frame_count % 100 == 0:
                logger.debug(f"🎥 Cámara {camera_id_int} - Frames enviados: {frame_count}")
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

@app.route('/video_feed/<camera_id>/status')
def video_feed_status(camera_id):
    """Verificar si un stream está activo"""
    try:
        camera_id_int = int(camera_id)
        status = {
            "camera_id": camera_id_int,
            "in_video_streams": camera_id_int in video_streams,
            "stream_active": False,
            "stream_running": False,
            "stream_disabled": False
        }
        
        if camera_id_int in video_streams:
            stream = video_streams[camera_id_int]
            status.update({
                "stream_active": True,
                "stream_running": stream.running,
                "stream_disabled": stream.disabled,
                "has_capture": stream.cap is not None and stream.cap.isOpened() if hasattr(stream, 'cap') else False
            })
        
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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

@app.route('/debug/ffmpeg/<camera_id>')
def debug_ffmpeg(camera_id):
    """Debugging de FFmpeg"""
    try:
        camera_id_int = int(camera_id)
        if camera_id_int not in video_streams:
            return jsonify({"error": f"Cámara {camera_id} no encontrada"}), 404
        
        stream = video_streams[camera_id_int]
        segmenter = stream.segmenter
        
        if not segmenter:
            return jsonify({"error": "Segmenter no inicializado"}), 400
        
        # Información del directorio
        segment_dir = segmenter.out_dir
        mkv_files = list(segment_dir.glob("*.mkv"))
        
        segments_info = []
        for mkv_file in sorted(mkv_files, key=lambda x: x.stat().st_mtime, reverse=True)[:10]:
            stat = mkv_file.stat()
            segments_info.append({
                'name': mkv_file.name,
                'size_mb': round(stat.st_size / (1024 * 1024), 2),
                'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                'age_seconds': round(time.time() - stat.st_mtime, 1),
                'path': str(mkv_file)
            })
        
        # Verificar proceso FFmpeg
        ffmpeg_running = segmenter.proc and segmenter.proc.poll() is None
        ffmpeg_pid = segmenter.proc.pid if segmenter.proc else None
        
        return jsonify({
            "camera_id": camera_id_int,
            "ffmpeg_running": ffmpeg_running,
            "ffmpeg_pid": ffmpeg_pid,
            "segment_dir": str(segment_dir),
            "total_segments": len(mkv_files),
            "segments": segments_info,
            "batch_interval": BATCH_INTERVAL,
            "segment_duration": segmenter.seg_seconds
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/debug/config')
def debug_config():
    """Verificar configuración completa"""
    config_info = {
        "batch_config": {
            "BATCH_DURATION_MIN": BATCH_DURATION_MIN,
            "BATCH_INTERVAL": BATCH_INTERVAL,
            "BATCH_SIZE": BATCH_SIZE,
            "MAX_FPS": MAX_FPS
        },
        "active_cameras": {}
    }
    
    for camera_id, stream in video_streams.items():
        config_info["active_cameras"][camera_id] = {
            "batch_interval": stream.batch_interval,
            "segmenter_seconds": stream.segmenter.seg_seconds if stream.segmenter else None,
            "running": stream.running,
            "disabled": stream.disabled
        }
    
    return jsonify(config_info)

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
    Maneja el inicio/detención del stream según el estado de la cámara - CON DEBUG
    """
    try:
        logger.info(f"🎛️ MANEJAR_STREAM: Cámara {camera_id}, Estado: {estado_anterior} -> {nuevo_estado}")
        
        if estado_anterior == nuevo_estado:
            logger.info(f"ℹ️ Sin cambio de estado, omitiendo")
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
        
        logger.info(f"✅ Thread iniciado para cámara {camera_id}")
            
    except Exception as e:
        logger.error(f"❌ Error manejando cámara {camera_id}: {str(e)}")
        logger.error(f"❌ Stack trace: {traceback.format_exc()}")

def procesar_camara_simple(camera_id, nuevo_estado, camara_config):
    """Procesamiento simple y robusto de cámara - CON DEBUG"""
    try:
        logger.info(f"🔧 PROCESAR_CAMARA: Cámara {camera_id}, Estado: {nuevo_estado}")
        
        if not nuevo_estado:
            logger.info(f"🛑 Deteniendo cámara {camera_id}")
            detener_camara_simple(camera_id)
        else:
            logger.info(f"🚀 Activando cámara {camera_id}")
            activar_camara_simple(camera_id, camara_config)
    except Exception as e:
        logger.error(f"❌ Error procesando cámara {camera_id}: {e}")
        logger.error(f"❌ Stack trace: {traceback.format_exc()}")

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

            # Detener timer de batch
            if hasattr(stream, 'batch_timer') and stream.batch_timer:
                stream.batch_timer = None
            
            # Limpiar buffers
            with stream.buffer_lock:
                stream.frames_buffer.clear()
                stream.buffer_timestamps.clear()
                stream.frame = None
            
            # Remover del diccionario global
            del video_streams[camera_id]
            
            logger.info(f"✅ Cámara {camera_id} detenida y removida de video_stream")
        else:
            logger.info(f"Cámara {camera_id} no encontrada en video_streams") 
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