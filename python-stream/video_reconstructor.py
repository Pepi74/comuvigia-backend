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
# Constante de fallback si no viene en metadata
DEFAULT_FPS = 12  # ajusta a tu realidad si sabes el valor típico

video_bp = Blueprint('video', __name__)

class VideoReconstructor:
    def __init__(self, s3_client):
        self.s3_client = s3_client
        self.temp_dir = tempfile.gettempdir()
    
    def reconstruct_video(self, camera_id, start_time, end_time, output_format="mp4"):
        batches = self._find_batches_in_range(camera_id, start_time, end_time)
        if not batches:
            return None, "No se encontraron batches en el rango especificado"

        all_frames, fps = self._download_and_extract_batches(batches)
        if not all_frames:
            return None, "No se pudieron extraer frames"

        video_path = self._create_video(all_frames, camera_id, output_format, fps=fps)
        return video_path, None
    '''def reconstruct_video(self, camera_id, start_time, end_time, output_format="mp4"):
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
        
        return video_path, None'''
    
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
        """Descargar y extraer frames de batches; devuelve (frames, fps)"""
        all_frames = []
        fps = None

        for batch in batches:
            try:
                response = self.s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=batch['key'])
                tar_bytes = BytesIO(response['Body'].read())

                with tarfile.open(fileobj=tar_bytes, mode='r:gz') as tar:
                    # Metadata
                    meta_member = tar.extractfile('metadata.json')
                    metadata = json.load(meta_member) if meta_member else {}

                    # 1) intenta leer fps directamente
                    batch_fps = (
                        metadata.get('fps') or
                        metadata.get('frame_rate') or
                        metadata.get('frameRate')
                    )

                    # 2) si no hay fps, intenta derivarlo
                    if not batch_fps:
                        frame_count = metadata.get('frame_count') or metadata.get('frames') or metadata.get('num_frames')
                        duration_sec = metadata.get('duration_seconds') or metadata.get('duration')
                        if frame_count and duration_sec and duration_sec > 0:
                            batch_fps = float(frame_count) / float(duration_sec)

                    # 3) primer fps que encontremos será el que usaremos (asumiendo homogéneo)
                    if not fps and batch_fps:
                        fps = float(batch_fps)

                    # Frames
                    frame_files = [m for m in tar.getmembers() if m.name.startswith('frame_')]
                    frame_files.sort(key=lambda x: x.name)

                    for frame_file in frame_files:
                        frame_data = tar.extractfile(frame_file).read()
                        frame = cv2.imdecode(np.frombuffer(frame_data, np.uint8), cv2.IMREAD_COLOR)
                        if frame is not None:
                            all_frames.append(frame)

            except Exception as e:
                logger.error(f"Error procesando batch {batch['key']}: {str(e)}")
                continue

        # fps fallback si no se pudo determinar
        if not fps:
            fps = DEFAULT_FPS

        return all_frames, fps
    '''def _download_and_extract_batches(self, batches):
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
        
        return all_frames'''
    

    def _create_video(self, frames, camera_id, output_format, fps):
        if not frames:
            return None
        height, width = frames[0].shape[:2]
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        temp_video_path = os.path.join(self.temp_dir, f"{camera_id}_{timestamp}.{output_format}")

        if output_format == "mp4":
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        elif output_format == "avi":
            fourcc = cv2.VideoWriter_fourcc(*'XVID')
        else:
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')

        out = cv2.VideoWriter(temp_video_path, fourcc, float(fps), (width, height))
        for frame in frames:
            out.write(frame)
        out.release()
        return temp_video_path
    '''def _create_video(self, frames, camera_id, output_format):
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
        return temp_video_path'''
    
    def reconstruct_clip(self, key, output_format="mp4"):
        clip = self._find_clip(key)
        if not clip:
            return None, "No se encontro clip"

        all_frames, fps = self._download_and_extract_clip(clip)
        if not all_frames:
            return None, "No se pudieron extraer frames"

        # usa _create_video con fps
        video_path = self._create_video(all_frames, camera_id="clip", output_format=output_format, fps=fps)
        return video_path, None

    def reconstruct_clip_play(self, key, output_format="mp4"):
        clip = self._find_clip(key)
        if not clip:
            return None, "No se encontro clip"

        all_frames, fps = self._download_and_extract_clip(clip)
        if not all_frames:
            return None, "No se pudieron extraer frames"

        camera_id = key.split('/')[1] if len(key.split('/')) > 1 else "unknown"
        # usa _create_video (o _create_clip_video si quieres redimensionar), pero con fps real:
        video_path = self._create_clip_video(all_frames, camera_id, output_format, fps=fps)
        return video_path, None

    '''def reconstruct_clip(self, key, output_format="mp4"):
        """Reconstruir video desde clip de S3"""
        # 1. Encontrar batches en el rango de tiempo
        clip = self._find_clip(key)
        
        if not clip:
            return None, "No se encontro clip"
        
        # 2. Descargar y extraer batches
        all_frames = self._download_and_extract_clip(clip)
        
        if not all_frames:
            return None, "No se pudieron extraer frames"
        
        # 3. Crear video
        video_path = self._create_video(all_frames, output_format)
        
        return video_path, None
    
    def reconstruct_clip_play(self, key, output_format="mp4"):
        """Reconstruir video desde clip de S3"""
        # 1. Encontrar clip
        clip = self._find_clip(key)
        
        if not clip:
            return None, "No se encontro clip"
        
        # 2. Descargar y extraer clip
        all_frames = self._download_and_extract_clip(clip)
        
        if not all_frames:
            return None, "No se pudieron extraer frames"
        
        # 3. Crear video - CORREGIDO: usar _create_video en lugar de _create_clip_video
        camera_id = key.split('/')[1] if len(key.split('/')) > 1 else "unknown"
        video_path = self._create_clip_video(all_frames, camera_id, output_format)
        
        return video_path, None'''

    def _find_clip(self, key):
        """Encontrar clip en S3 dado su ubicacion"""
        try:
            response = self.s3_client.head_object(
                Bucket=S3_BUCKET_NAME,
                Key=key
            )
            filename = key.split('/')[-1]
            if filename.endswith('.tar.gz'):
                try:
                    clip_time_str = filename.split('_')[1] + '_' + filename.split('_')[2].split('.')[0]
                    clip_time = datetime.strptime(clip_time_str, '%Y%m%d_%H%M%S')
                except Exception:
                    clip_time = None

                return {
                    'key': key,
                    'time': clip_time,
                    'size': response['ContentLength']
                }
            else:
                logger.warning(f"El archivo {filename} no es un .tar.gz válido")
                return None
        
        except Exception as e:
            logger.error(f"Error buscando clip: {str(e)}")
            return None
    
    def _download_and_extract_clip(self, clip):
        """Descargar y extraer frames de un solo clip; devuelve (frames, fps)"""
        all_frames = []
        fps = None
        try:
            response = self.s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=clip['key'])
            tar_bytes = BytesIO(response['Body'].read())

            with tarfile.open(fileobj=tar_bytes, mode='r:gz') as tar:
                meta_member = tar.extractfile('metadata.json')
                metadata = json.load(meta_member) if meta_member else {}

                fps = (
                    metadata.get('fps') or
                    metadata.get('frame_rate') or
                    metadata.get('frameRate')
                )
                if not fps:
                    frame_count = metadata.get('frame_count') or metadata.get('frames') or metadata.get('num_frames')
                    duration_sec = metadata.get('duration_seconds') or metadata.get('duration')
                    if frame_count and duration_sec and duration_sec > 0:
                        fps = float(frame_count) / float(duration_sec)

                frame_files = [m for m in tar.getmembers() if m.name.startswith('frame_')]
                frame_files.sort(key=lambda x: x.name)
                for frame_file in frame_files:
                    frame_data = tar.extractfile(frame_file).read()
                    frame = cv2.imdecode(np.frombuffer(frame_data, np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None:
                        all_frames.append(frame)
        except Exception as e:
            logger.error(f"Error procesando clip {clip['key']}: {str(e)}")
            return [], None

        if not fps:
            fps = DEFAULT_FPS
        return all_frames, fps
    '''def _download_and_extract_clip(self, clip):
        """Descargar y extraer frames de clip"""
        all_frames = []
        
        try:
            # Descargar clip
            response = self.s3_client.get_object(
                Bucket=S3_BUCKET_NAME,
                Key=clip['key']
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
            logger.error(f"Error procesando clip {clip['key']}: {str(e)}")
            all_frames = []
        
        return all_frames'''
    
    def _create_clip_video(self, frames, camera_id, output_format, fps):
        if not frames:
            return None
        try:
            temp_dir = tempfile.mkdtemp()
            frame_paths = []
            target_width, target_height = 640, 360

            for i, frame in enumerate(frames):
                frame_resized = self._resize_frame(frame, target_width, target_height)
                fp = os.path.join(temp_dir, f"frame_{i:06d}.jpg")
                cv2.imwrite(fp, frame_resized, [cv2.IMWRITE_JPEG_QUALITY, 95])
                frame_paths.append(fp)

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            temp_video_path = os.path.join(self.temp_dir, f"{camera_id}_{timestamp}.{output_format}")

            import subprocess
            cmd = [
                'ffmpeg', '-y',
                '-r', str(float(fps)),              # <--- usa fps real
                '-i', os.path.join(temp_dir, 'frame_%06d.jpg'),
                '-c:v', 'libx264',
                '-pix_fmt', 'yuv420p',
                '-preset', 'fast',
                '-crf', '23',
                '-movflags', '+faststart',
                temp_video_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            for p in frame_paths:
                try: os.remove(p)
                except: pass
            try: os.rmdir(temp_dir)
            except: pass

            if result.returncode == 0 and os.path.exists(temp_video_path):
                return temp_video_path
            else:
                logger.error(f"ffmpeg failed: {result.stderr}")
                return None
        except Exception as e:
            logger.error(f"Error en método alternativo: {str(e)}")
            return None
    '''def _create_clip_video(self, frames, camera_id, output_format):
        """Método alternativo para crear video con relación de aspecto 640x380"""
        if not frames:
            return None
        
        try:
            # Crear directorio temporal para frames
            temp_dir = tempfile.mkdtemp()
            frame_paths = []
            
            # Dimensiones objetivo
            target_width = 640
            target_height = 360
            
            logger.info(f"Redimensionando frames a {target_width}x{target_height}")
            
            # Guardar cada frame redimensionado
            for i, frame in enumerate(frames):
                # Redimensionar manteniendo relación de aspecto y recortando
                frame_resized = self._resize_frame(frame, target_width, target_height)
                
                frame_path = os.path.join(temp_dir, f"frame_{i:06d}.jpg")
                cv2.imwrite(frame_path, frame_resized, [cv2.IMWRITE_JPEG_QUALITY, 95])
                frame_paths.append(frame_path)
            
            # Crear video con ffmpeg
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            temp_video_path = os.path.join(self.temp_dir, f"{camera_id}_{timestamp}.{output_format}")
            
            import subprocess
            cmd = [
                'ffmpeg', '-y', 
                '-r', str(MAX_FPS),
                '-i', os.path.join(temp_dir, 'frame_%06d.jpg'),
                '-c:v', 'libx264',
                '-pix_fmt', 'yuv420p',
                '-preset', 'fast',
                '-crf', '23',
                '-movflags', '+faststart',
                '-vf', f'scale={target_width}:{target_height}:force_original_aspect_ratio=disable',  # Forzar dimensiones exactas
                temp_video_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            # Limpiar archivos temporales
            for frame_path in frame_paths:
                try:
                    os.remove(frame_path)
                except:
                    pass
            try:
                os.rmdir(temp_dir)
            except:
                pass
            
            if result.returncode == 0 and os.path.exists(temp_video_path):
                logger.info(f"Video creado con dimensiones {target_width}x{target_height}")
                return temp_video_path
            else:
                logger.error(f"ffmpeg failed: {result.stderr}")
                return None
                
        except Exception as e:
            logger.error(f"Error en método alternativo: {str(e)}")
            return None'''

    def _resize_frame(self, frame, target_width, target_height):
        """Redimensionar frame recortando para llenar el frame"""
        height, width = frame.shape[:2]
        
        # Calcular escala para llenar el frame
        scale_x = target_width / width
        scale_y = target_height / height
        scale = max(scale_x, scale_y)  # Usar max para llenar el frame
        
        new_width = int(width * scale)
        new_height = int(height * scale)
        
        # Redimensionar
        resized = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_LANCZOS4)
        
        # Recortar al centro para obtener dimensiones exactas
        start_x = max(0, (new_width - target_width) // 2)
        start_y = max(0, (new_height - target_height) // 2)
        
        cropped = resized[start_y:start_y+target_height, start_x:start_x+target_width]
        
        return cropped

    

# Inicializar reconstructor
S3_ENDPOINT = "http://minio:9000"
S3_ACCESS_KEY = os.environ["S3_ACCESS_KEY"]
S3_SECRET_KEY = os.environ["S3_SECRET_KEY"]
S3_BUCKET_NAME = os.environ["S3_BUCKET_NAME"]
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

    
@video_bp.route('/videos/batches/<camera_id>')
def list_available_batches(camera_id):
    """Listar videos disponibles para una cámara con filtros de fecha y paginación"""
    try:
        # Obtener parámetros de la query string
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        
        # Validar parámetros de paginación
        if page < 1:
            return jsonify({"error": "El número de página debe ser al menos 1"}), 400
        if per_page < 1 or per_page > 100:
            return jsonify({"error": "El tamaño de página debe estar entre 1 y 100"}), 400
        
        # Si no se proporcionan fechas, usar últimos 7 días por defecto
        if not start_date_str or not end_date_str:
            end_time = datetime.now()
            start_time = end_time - timedelta(days=7)
        else:
            # Parsear las fechas proporcionadas
            try:
                start_time = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                end_time = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            except ValueError:
                return jsonify({"error": "Formato de fecha inválido. Use formato ISO (YYYY-MM-DDTHH:MM:SS)"}), 400
        
        # Validar que la fecha de inicio no sea mayor que la fecha de fin
        if start_time > end_time:
            return jsonify({"error": "La fecha de inicio no puede ser mayor que la fecha de fin"}), 400
        
        # Limitar el rango de búsqueda a un máximo de 7 días por seguridad/rendimiento
        max_days = 7
        if (end_time - start_time).days > max_days:
            return jsonify({"error": f"El rango de búsqueda no puede exceder {max_days} días"}), 400
        
        # Obtener todos los batches en el rango
        all_batches = video_reconstructor._find_batches_in_range(camera_id, start_time, end_time)
        
        # Aplicar paginación
        total = len(all_batches)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_batches = all_batches[start_idx:end_idx]
        
        return jsonify({
            "camera_id": camera_id,
            "available_batches": [{
                "time": batch['time'].isoformat(),
                "size_mb": round(batch['size'] / (1024 * 1024), 2),
                "key": batch['key']
            } for batch in paginated_batches],
            "time_range": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat()
            },
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": (total + per_page - 1) // per_page  # Cálculo de total de páginas
            }
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    

# video_reconstructor.py (parte adicional)
@video_bp.route('/video/list/<camera_id>')
def list_virtual_videos(camera_id):
    """Listar videos virtuales basados en batches disponibles"""
    try:
        # Obtener parámetros de la query string
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        video_duration_min = request.args.get('duration_min', 5, type=int)
        
        # Validar parámetros
        if page < 1:
            return jsonify({"error": "El número de página debe ser al menos 1"}), 400
        if per_page < 1 or per_page > 100:
            return jsonify({"error": "El tamaño de página debe estar entre 1 y 100"}), 400
        if video_duration_min < 1 or video_duration_min > 60:
            return jsonify({"error": "La duración debe estar entre 1 y 60 minutos"}), 400
        
        # Parsear fechas
        if not start_date_str or not end_date_str:
            end_time = datetime.now()
            start_time = end_time - timedelta(days=7)
        else:
            try:
                start_time = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                end_time = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            except ValueError:
                return jsonify({"error": "Formato de fecha inválido. Use formato ISO"}), 400
        
        # Validaciones de fecha
        if start_time > end_time:
            return jsonify({"error": "La fecha de inicio no puede ser mayor que la fecha de fin"}), 400
        
        max_days = 7
        if (end_time - start_time).days > max_days:
            return jsonify({"error": f"El rango de búsqueda no puede exceder {max_days} días"}), 400
        
        # Obtener batches disponibles en el rango usando tu método existente
        batches = video_reconstructor._find_batches_in_range(camera_id, start_time, end_time)
        logger.info(f"Se encontraron {len(batches)} batches para la cámara {camera_id} en el rango especificado")
        # Agrupar batches en segmentos de video virtuales
        virtual_videos = create_virtual_videos_from_batches(batches, video_duration_min * 60)
        logger.info(f"Se encontraron {len(virtual_videos)} videos virtuales para la cámara {camera_id}")
        # Aplicar paginación
        total = len(virtual_videos)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_videos = virtual_videos[start_idx:end_idx]
        
        return jsonify({
            "camera_id": camera_id,
            "videos": paginated_videos,
            "time_range": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat()
            },
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": (total + per_page - 1) // per_page
            }
        })
        
    except Exception as e:
        logger.error(f"Error en list_virtual_videos: {str(e)}")
        return jsonify({"error": str(e)}), 400


def create_virtual_videos_from_batches(batches, target_duration_seconds):
    """Agrupar batches en segmentos de video virtuales usando tu estructura de batches"""
    if not batches:
        return []
    
    # Ordenar batches por tiempo (ya viene ordenado de tu método)
    virtual_videos = []
    
    # Si no hay batches, retornar vacío
    if not batches:
        return virtual_videos
    
    current_video_start = batches[0]['time']
    current_video_batches = []
    current_duration = 0
    
    for batch in batches:
        batch_time = batch['time']
        
        # Estimar duración del batch (1MB ≈ 10 segundos de video)
        batch_duration = estimate_batch_duration(batch)
        
        # Si agregar este batch excede la duración objetivo, crear un nuevo video virtual
        if current_duration + batch_duration > target_duration_seconds and current_video_batches:
            virtual_videos.append(create_virtual_video_entry(
                current_video_start, 
                current_video_batches,
                current_duration
            ))
            
            # Reiniciar para el próximo video
            current_video_start = batch_time
            current_video_batches = [batch]
            current_duration = batch_duration
        else:
            current_video_batches.append(batch)
            current_duration += batch_duration
    
    # Agregar el último video
    if current_video_batches:
        virtual_videos.append(create_virtual_video_entry(
            current_video_start, 
            current_video_batches,
            current_duration
        ))
    
    return virtual_videos


def estimate_batch_duration(batch):
    """Estimar la duración de un batch basado en su tamaño"""
    # Asumir 1 MB ≈ 10 segundos de video (ajusta según tu codec)
    size_mb = batch['size'] / (1024 * 1024)
    return min(max(size_mb * 10, 5), 300)  # Entre 5 y 300 segundos


def create_virtual_video_entry(start_time, batches, duration_seconds):
    """Crear entrada de video virtual"""
    total_size = sum(batch['size'] for batch in batches)
    end_time = start_time + timedelta(seconds=duration_seconds)
    
    # Obtener el último batch para el tiempo final preciso
    if batches:
        last_batch_time = batches[-1]['time']
        # Ajustar end_time basado en el último batch + su duración estimada
        end_time = last_batch_time + timedelta(seconds=estimate_batch_duration(batches[-1]))
    
    return {
        "id": f"virtual_{start_time.strftime('%Y%m%d_%H%M%S')}_{int(duration_seconds)}",
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": duration_seconds,
        "size_mb": round(total_size / (1024 * 1024), 2),
        "batch_count": len(batches),
        "batches": [batch['key'] for batch in batches],
        "type": "virtual"
    }


# Endpoint para generar thumbnail bajo demanda
@video_bp.route('/video/thumbnail/<camera_id>')
def generate_thumbnail_on_demand(camera_id):
    """Generar thumbnail bajo demanda desde el primer frame de un batch"""
    try:
        time_str = request.args.get('time')
        if not time_str:
            return jsonify({"error": "Parámetro 'time' requerido"}), 400
        
        target_time = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
        
        # Buscar batch más cercano al tiempo solicitado
        batch = find_closest_batch(camera_id, target_time)
        if not batch:
            return jsonify({"error": "No se encontraron batches para el tiempo solicitado"}), 404
        
        # Generar thumbnail desde el batch
        thumbnail_path = extract_thumbnail_from_batch(batch['key'])
        
        if not thumbnail_path:
            return jsonify({"error": "No se pudo generar el thumbnail"}), 500
        
        return send_file(
            thumbnail_path,
            mimetype='image/jpeg',
            as_attachment=False,
            download_name=f"thumbnail_{camera_id}_{target_time.strftime('%Y%m%d_%H%M%S')}.jpg"
        )
        
    except Exception as e:
        logger.error(f"Error generando thumbnail: {str(e)}")
        return jsonify({"error": str(e)}), 400


def find_closest_batch(camera_id, target_time):
    """Encontrar el batch más cercano al tiempo especificado usando tu método existente"""
    # Buscar batches en un rango de ±2 minutos
    start_range = target_time - timedelta(minutes=2)
    end_range = target_time + timedelta(minutes=2)
    
    batches = video_reconstructor._find_batches_in_range(camera_id, start_range, end_range)
    if not batches:
        return None
    
    # Encontrar el batch más cercano al tiempo objetivo
    closest_batch = min(batches, key=lambda x: abs(x['time'] - target_time))
    return closest_batch


def extract_thumbnail_from_batch(batch_key):
    """Extraer thumbnail desde un batch comprimido"""
    try:
        # Descargar batch
        response = s3_client.get_object(
            Bucket=S3_BUCKET_NAME,
            Key=batch_key
        )
        
        # Extraer tar.gz
        tar_bytes = BytesIO(response['Body'].read())
        
        with tarfile.open(fileobj=tar_bytes, mode='r:gz') as tar:
            # Buscar el primer frame
            frame_files = [m for m in tar.getmembers() if m.name.startswith('frame_')]
            if not frame_files:
                return None
                
            frame_files.sort(key=lambda x: x.name)
            first_frame = frame_files[0]
            
            # Extraer el primer frame
            frame_data = tar.extractfile(first_frame).read()
            frame = cv2.imdecode(np.frombuffer(frame_data, np.uint8), cv2.IMREAD_COLOR)
            
            if frame is None:
                return None
            
            # Crear archivo temporal para el thumbnail
            temp_thumb_path = os.path.join(tempfile.gettempdir(), f"thumb_{os.path.basename(batch_key)}.jpg")
            
            # Redimensionar si es muy grande (max 320x180)
            height, width = frame.shape[:2]
            if width > 320 or height > 180:
                scale = min(320/width, 180/height)
                new_width = int(width * scale)
                new_height = int(height * scale)
                frame = cv2.resize(frame, (new_width, new_height))
            
            # Guardar como JPEG
            cv2.imwrite(temp_thumb_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            
            return temp_thumb_path
            
    except Exception as e:
        logger.error(f"Error extrayendo thumbnail: {str(e)}")
        return None

# Agrega este endpoint a tu API Flask
@video_bp.route('/video/download/<camera_id>', methods=['GET'])
def download_video(camera_id):
    """Descargar video para un rango de tiempo específico"""
    try:
        # Obtener parámetros de la query string
        start_time_str = request.args.get('start_time')
        end_time_str = request.args.get('end_time')
        output_format = request.args.get('format', 'mp4')
        
        if not start_time_str or not end_time_str:
            return jsonify({"error": "Se requieren start_time y end_time"}), 400
        
        # Parsear fechas
        start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
        end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
        
        # Validar que el rango sea razonable (max 1 hora)
        max_duration = timedelta(hours=1)
        if (end_time - start_time) > max_duration:
            return jsonify({
                "error": f"El rango no puede exceder {max_duration.total_seconds() / 60} minutos"
            }), 400
        
        # Reconstruir el video
        video_path, error = video_reconstructor.reconstruct_video(
            camera_id, start_time, end_time, output_format
        )
        
        if error:
            return jsonify({"error": error}), 404        
        # Enviar el archivo para descarga
        return send_file(
            video_path,
            as_attachment=True,
            download_name=f"{camera_id}_{start_time.strftime('%Y%m%d_%H%M')}_{end_time.strftime('%H%M')}.{output_format}",
            mimetype=f"video/{output_format}"
        )
        
    except Exception as e:
        logger.error(f"Error descargando video: {str(e)}")
        return jsonify({"error": str(e)}), 500
    
@video_bp.route('/video/download', methods=['POST'])  # Cambiado a POST
def download_clip():
    """Descargar clip enviando key"""
    try:
        data = request.get_json()
        if not data or 'key' not in data:
            return jsonify({"error": "Se requiere parámetro 'key' en el body JSON"}), 400
            
        key = data['key']
        output_format = request.args.get('format', 'mp4')  # Opcional: mantener format como query param
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Reconstruir el video
        video_path, error_msg = video_reconstructor.reconstruct_clip_play(key, output_format)
        
        if error_msg:
            return jsonify({"error": error_msg}), 404        
        # Enviar el archivo para descarga
        return send_file(
            video_path,
            as_attachment=True,
            download_name=f"clip_{timestamp}.{output_format}",
            mimetype=f"video/{output_format}"
        )
        
    except Exception as e:
        logger.error(f"Error descargando video: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@video_bp.route('/video/play', methods=['GET'])
def play_clip():
    """Reproducir clip directamente en navegador (para tag video)"""
    try:
        # Obtener parámetros de la query string
        key = request.args.get('key')
        output_format = request.args.get('format', 'mp4')
        
        if not key:
            return jsonify({"error": "Se requiere parámetro 'key'"}), 400
        
        # Reconstruir el video
        video_path, error = video_reconstructor.reconstruct_clip_play(key, output_format)
        
        if error:
            return jsonify({"error": error}), 404
        
        # Enviar el archivo para reproducción (no como descarga)
        return send_file(
            video_path,
            as_attachment=False,
            download_name=f"clip.{output_format}",
            mimetype=f"video/{output_format}"
        )
        
    except Exception as e:
        logger.error(f"Error reproduciendo clip: {str(e)}")
        return jsonify({"error": str(e)}), 500
    