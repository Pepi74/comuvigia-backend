# video_reconstructor.py
import boto3
import cv2
import numpy as np
import tempfile
import os
import json
import tarfile
import logging
from io import BytesIO
from datetime import datetime, timedelta
from flask import Blueprint, send_file, jsonify, request
from botocore.client import Config

# Logs
os.makedirs("/logs", exist_ok=True)
from logging.handlers import RotatingFileHandler

logger = logging.getLogger("video_reconstructor")
logger.setLevel(logging.INFO)

fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')

fh = RotatingFileHandler("/logs/video_reconstructor.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
fh.setFormatter(fmt)
logger.addHandler(fh)

ch = logging.StreamHandler()
ch.setFormatter(fmt)
logger.addHandler(ch)

MAX_FPS = 30

video_bp = Blueprint('video', __name__)

class VideoReconstructor:
    def __init__(self, s3_client):
        self.s3_client = s3_client
        self.temp_dir = tempfile.gettempdir()
    
    def reconstruct_video(self, camera_id, start_time, end_time, output_format="mp4"):
        """Reconstruir video desde batches de S3"""
        # 1. Encontrar batches en el rango de tiempo
        batches = self._find_batches_in_range(camera_id, start_time, end_time)
        
        if not batches:
            return None, "No se encontraron batches en el rango especificado"
        
        # 2. Descargar y extraer batches
        all_frames = self._download_and_extract_batches(batches)
        
        if not all_frames:
            return None, "No se pudieron extraer frames"
        
        # 3. Crear video
        video_path = self._create_video(all_frames, camera_id, output_format)
        
        return video_path, None
    
    def _find_batches_in_range(self, camera_id, start_time, end_time):
        """Encontrar batches en S3 dentro del rango de tiempo"""
        batches = []
        
        # Generar prefijos para buscar (por hora)
        current_time = start_time
        while current_time <= end_time:
            prefix = f"batches/{camera_id}/{current_time.strftime('%Y/%m/%d/%H')}/"
            
            try:
                response = self.s3_client.list_objects_v2(
                    Bucket=S3_BUCKET_NAME,
                    Prefix=prefix
                )
                
                if 'Contents' in response:
                    for obj in response['Contents']:
                        # Extraer timestamp del nombre del archivo
                        filename = obj['Key'].split('/')[-1]
                        if filename.endswith('.tar.gz'):
                            batch_time_str = filename.split('_')[1] + '_' + filename.split('_')[2].split('.')[0]
                            batch_time = datetime.strptime(batch_time_str, '%Y%m%d_%H%M%S')
                            
                            if start_time <= batch_time <= end_time:
                                batches.append({
                                    'key': obj['Key'],
                                    'time': batch_time,
                                    'size': obj['Size']
                                })
            
            except Exception as e:
                logger.error(f"Error buscando batches: {str(e)}")
            
            current_time += timedelta(hours=1)
        
        # Ordenar por tiempo
        batches.sort(key=lambda x: x['time'])
        return batches
    
    def _download_and_extract_batches(self, batches):
        """Descargar y extraer frames de batches"""
        all_frames = []
        
        for batch in batches:
            try:
                # Descargar batch
                response = self.s3_client.get_object(
                    Bucket=S3_BUCKET_NAME,
                    Key=batch['key']
                )
                
                # Extraer tar.gz
                tar_bytes = BytesIO(response['Body'].read())
                
                with tarfile.open(fileobj=tar_bytes, mode='r:gz') as tar:
                    # Leer metadata
                    metadata_file = tar.extractfile('metadata.json')
                    metadata = json.load(metadata_file)
                    
                    # Extraer frames en orden
                    frame_files = [m for m in tar.getmembers() if m.name.startswith('frame_')]
                    frame_files.sort(key=lambda x: x.name)
                    
                    for frame_file in frame_files:
                        frame_data = tar.extractfile(frame_file).read()
                        frame = cv2.imdecode(np.frombuffer(frame_data, np.uint8), cv2.IMREAD_COLOR)
                        all_frames.append(frame)
                        
            except Exception as e:
                logger.error(f"Error procesando batch {batch['key']}: {str(e)}")
                continue
        
        return all_frames
    
    def _create_video(self, frames, camera_id, output_format):
        """Crear video desde frames"""
        if not frames:
            return None
        
        # Determinar resolución del primer frame
        height, width = frames[0].shape[:2]
        
        # Crear nombre de archivo temporal
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        temp_video_path = os.path.join(self.temp_dir, f"{camera_id}_{timestamp}.{output_format}")
        
        # Configurar video writer
        if output_format == "mp4":
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        elif output_format == "avi":
            fourcc = cv2.VideoWriter_fourcc(*'XVID')
        else:
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        
        out = cv2.VideoWriter(temp_video_path, fourcc, MAX_FPS, (width, height))
        
        # Escribir frames
        for frame in frames:
            out.write(frame)
        
        out.release()
        return temp_video_path

# Inicializar reconstructor
S3_ENDPOINT = "http://minio:9000"
S3_ACCESS_KEY = "miniocomuvigia"
S3_SECRET_KEY = "comuvigiaminio123"
S3_BUCKET_NAME = "comuvigia-video-batches"
S3_REGION = "us-east-1"
s3_client = boto3.client(
    's3',
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    config=Config(signature_version='s3v4'),
    region_name=S3_REGION
)
video_reconstructor = VideoReconstructor(s3_client)

# Endpoints de la API
@video_bp.route('/video/reconstruct', methods=['POST'])
def reconstruct_video():
    """Reconstruir video para un rango de tiempo"""
    data = request.json
    
    try:
        camera_id = data['camera_id']
        start_time = datetime.fromisoformat(data['start_time'])
        end_time = datetime.fromisoformat(data['end_time'])
        output_format = data.get('format', 'mp4')
        
        video_path, error = video_reconstructor.reconstruct_video(
            camera_id, start_time, end_time, output_format
        )
        
        if error:
            return jsonify({"error": error}), 404
        
        return send_file(
            video_path,
            as_attachment=True,
            download_name=f"{camera_id}_{start_time.strftime('%Y%m%d_%H%M')}_{end_time.strftime('%H%M')}.{output_format}",
            mimetype=f"video/{output_format}"
        )
        
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@video_bp.route('/video/batches/<camera_id>')
def list_available_batches(camera_id):
    """Listar batches disponibles para una cámara"""
    try:
        # Listar últimos 7 días por defecto
        end_time = datetime.now()
        start_time = end_time - timedelta(days=7)
        
        batches = video_reconstructor._find_batches_in_range(camera_id, start_time, end_time)
        
        return jsonify({
            "camera_id": camera_id,
            "available_batches": [{
                "time": batch['time'].isoformat(),
                "size_mb": round(batch['size'] / (1024 * 1024), 2),
                "key": batch['key']
            } for batch in batches],
            "time_range": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat()
            }
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 400