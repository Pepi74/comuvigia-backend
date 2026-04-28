# video_reconstructor.py
import boto3
import cv2
import numpy as np
import tempfile
import os
import re
import json
import tarfile
import logging
from io import BytesIO
from datetime import datetime, timedelta, timezone
from flask import Blueprint, send_file, jsonify, request
from botocore.client import Config
from functools import lru_cache
import zipfile
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

_MKV_TS_RE = re.compile(r'(\d{8})_(\d{6})') # p.ej. 20250921_015047
MAX_FPS = 30
DEFAULT_FPS = 12
BATCHES_PREFIX = "batches"
CLIPS_PREFIX = "clips"
DEFAULT_MKV_SECONDS = 5 * 60
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
        all_frames, fps = self._download_and_extract_batches(batches)
        if not all_frames:
            return None, "No se pudieron extraer frames"
        # 3. Crear video
        video_path = self._create_video(all_frames, camera_id, output_format, fps=fps)
        return video_path, None
    
    def _find_batches_in_range(self, camera_id, start_time, end_time):
        """Encontrar batches en S3 considerando zona horaria"""
        batches = []
        
        # Convertir a UTC si es necesario (asumiendo que los batches están en UTC)
        utc_start = start_time.astimezone(timezone.utc) if start_time.tzinfo else start_time
        utc_end = end_time.astimezone(timezone.utc) if end_time.tzinfo else end_time
        
        # Buscar en un rango más amplio para cubrir diferencias horarias
        search_start = utc_start - timedelta(hours=4)  # -4 horas para cobertura
        search_end = utc_end + timedelta(hours=4)      # +4 horas para cobertura
        
        current_time = search_start
        while current_time <= search_end:
            prefix = f"batches/{camera_id}/{current_time.strftime('%Y/%m/%d/%H')}/"
            
            try:
                response = self.s3_client.list_objects_v2(
                    Bucket=S3_BUCKET_NAME,
                    Prefix=prefix
                )
                
                if 'Contents' in response:
                    for obj in response['Contents']:
                        filename = obj['Key'].split('/')[-1]
                        if filename.endswith('.tar.gz'):
                            try:
                                # Extraer timestamp del nombre (asumiendo formato: 1_20250921_015047.tar.gz)
                                time_str = filename.split('_')[1] + '_' + filename.split('_')[2].split('.')[0]
                                batch_time = datetime.strptime(time_str, '%Y%m%d_%H%M%S')
                                
                                # Asumir que el batch está en UTC
                                batch_time_utc = batch_time.replace(tzinfo=timezone.utc)
                                
                                # Convertir a hora local para comparación
                                batch_time_local = batch_time_utc.astimezone()
                                
                                if start_time <= batch_time_local <= end_time:
                                    batches.append({
                                        'key': obj['Key'],
                                        'time': batch_time_local,  # Guardar en hora local
                                        'size': obj['Size'],
                                        'utc_time': batch_time_utc  # Guardar también UTC
                                    })
                                    
                            except Exception as e:
                                logger.warning(f"Error parsing filename {filename}: {str(e)}")
                                continue
            
            except Exception as e:
                logger.error(f"Error buscando batches: {str(e)}")
            
            current_time += timedelta(hours=1)
        
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
                    logger.info(f"Batch {batch['key']} metadata fps: {batch_fps}")
                    # 2) si no hay fps, intenta derivarlo
                    if not batch_fps:
                        logger.info(f"Batch {batch['key']} no tiene fps en metadata, intentando derivar")
                        frame_count = metadata.get('frame_count') or metadata.get('frames') or metadata.get('num_frames')
                        duration_sec = metadata.get('duration_seconds') or metadata.get('duration')
                        if frame_count and duration_sec and duration_sec > 0:
                            batch_fps = float(frame_count) / float(duration_sec)

                    # 3) primer fps que encontremos será el que usaremos (asumiendo homogéneo)
                    if not fps and batch_fps:
                        logger.info(f"Usando fps {batch_fps} del batch {batch['key']}")
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

    def _create_video(self, frames, camera_id, output_format, fps):
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
        
        out = cv2.VideoWriter(temp_video_path, fourcc, float(fps), (width, height))
        
        # Escribir frames
        for frame in frames:
            out.write(frame)
        
        out.release()
        return temp_video_path
    
    def reconstruct_clip(self, key, output_format="mp4"):
        """Reconstruir video desde clip de S3"""
        # 1. Encontrar batches en el rango de tiempo
        clip = self._find_clip(key)
        
        if not clip:
            return None, "No se encontro clip"
        
        # 2. Descargar y extraer batches
        all_frames, fps = self._download_and_extract_clip(clip)
        
        if not all_frames:
            return None, "No se pudieron extraer frames"
        
        # 3. Crear video
        video_path = self._create_video(all_frames, camera_id="clip", output_format=output_format, fps=fps)
        
        return video_path, None

    def reconstruct_clip_play(self, key, output_format="mp4"):
        """Reconstruir video desde clip de S3"""
        # 1. Encontrar clip
        clip = self._find_clip(key)
        
        if not clip:
            return None, "No se encontro clip"
        
        # 2. Descargar y extraer clip
        all_frames, fps = self._download_and_extract_clip(clip)
        
        if not all_frames:
            return None, "No se pudieron extraer frames"
        
        # 3. Crear video - CORREGIDO: usar _create_video en lugar de _create_clip_video
        camera_id = key.split('/')[1] if len(key.split('/')) > 1 else "unknown"
        video_path = self._create_clip_video(all_frames, camera_id, output_format, fps=fps)
        
        return video_path, None

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

    def _list_mkv_in_range(self, camera_id, start_time, end_time):
        """
        Lista grabaciones MKV bajo batches/<camera_id>/YYYY/MM/DD/HH/*.mkv
        Devuelve: [{'key','time','size','utc_time','kind': 'mkv'}, ...]
        """
        # Normaliza rango a aware local y deriva UTC para búsqueda
        start_time = _ensure_local_aware(start_time)
        end_time   = _ensure_local_aware(end_time)

        utc_start = start_time.astimezone(timezone.utc)
        utc_end   = end_time.astimezone(timezone.utc)

        items = []
        ts_regex = re.compile(r'(\d{8})_(\d{6})')  # p.ej. 20250921_015047

        # margen para desfaces horarios
        search_start = utc_start - timedelta(hours=4)
        search_end   = utc_end + timedelta(hours=4)

        current_time = search_start
        while current_time <= search_end:
            prefix = f"{BATCHES_PREFIX}/{camera_id}/{current_time.strftime('%Y/%m/%d/%H')}/"
            continuation = None

            while True:
                kwargs = {
                    "Bucket": S3_BUCKET_NAME,
                    "Prefix": prefix,
                }
                if continuation:
                    kwargs["ContinuationToken"] = continuation

                try:
                    resp = self.s3_client.list_objects_v2(**kwargs)
                except Exception as e:
                    logger.error(f"Error listando MKV en {prefix}: {e}")
                    break

                for obj in resp.get("Contents", []):
                    key = obj["Key"]
                    name = key.split("/")[-1]
                    if not name.lower().endswith(".mkv"):
                        continue

                    # intenta parsear timestamp del filename
                    m = ts_regex.search(name)
                    if m:
                        ymd, hms = m.groups()
                        # asume timestamp en UTC en el nombre
                        batch_time_naive = datetime.strptime(f"{ymd}_{hms}", "%Y%m%d_%H%M%S")
                        batch_time_utc = batch_time_naive.replace(tzinfo=timezone.utc)
                        batch_time_local = batch_time_utc.astimezone()

                        if start_time <= batch_time_local <= end_time:
                            items.append({
                                "key": key,
                                "time": batch_time_local,
                                "size": obj["Size"],
                                "utc_time": batch_time_utc,
                                "kind": "mkv",
                            })
                    else:
                        # no se pudo parsear: incluirlo sin hora (opcional)
                        items.append({
                            "key": key,
                            "time": None,
                            "size": obj["Size"],
                            "utc_time": None,
                            "kind": "mkv",
                        })

                if resp.get("IsTruncated"):
                    continuation = resp.get("NextContinuationToken")
                else:
                    break

            current_time += timedelta(hours=1)

        items.sort(key=lambda x: x["time"] or datetime.min.replace(tzinfo=timezone.utc))
        return items

    def _list_mkv_by_day_range(self, camera_id, start_time, end_time):
        """
        Lista TODOS los .mkv bajo batches/<camera_id>/<YYYY>/<MM>/<DD>/***
        que caen dentro del rango [start_time, end_time].
        - Recorre por día (incluye subcarpetas).
        - Intenta filtrar por timestamp del filename (UTC->local).
          Si no puede parsear el timestamp, igual los incluye (time=None).
        Devuelve: [{'key','time','size','kind': 'mkv'}...], ordenados por 'time'.
        """
        start_time = _ensure_local_aware(start_time)
        end_time   = _ensure_local_aware(end_time)

        items = []

        # Recorre días (inclusive)
        day_cursor = start_time.date()
        end_day    = end_time.date()

        while day_cursor <= end_day:
            day_prefix = f"{BATCHES_PREFIX}/{camera_id}/{day_cursor.strftime('%Y/%m/%d')}/"
            continuation = None

            while True:
                kwargs = {"Bucket": S3_BUCKET_NAME, "Prefix": day_prefix}
                if continuation:
                    kwargs["ContinuationToken"] = continuation

                try:
                    resp = self.s3_client.list_objects_v2(**kwargs)
                except Exception as e:
                    logger.error(f"Error listando MKV en {day_prefix}: {e}")
                    break

                for obj in resp.get("Contents", []):
                    key = obj["Key"]
                    name = key.split("/")[-1].lower()
                    if not name.endswith(".mkv"):
                        continue

                    # Intenta parsear timestamp del filename
                    parsed_time_local = None
                    m = _MKV_TS_RE.search(name)
                    if m:
                        ymd, hms = m.groups()
                        try:
                            naive = datetime.strptime(f"{ymd}_{hms}", "%Y%m%d_%H%M%S")
                            # Interpretamos timestamp como UTC en el nombre
                            t_utc = naive.replace(tzinfo=timezone.utc)
                            parsed_time_local = t_utc.astimezone()
                        except Exception:
                            parsed_time_local = None

                    # Si tenemos hora parseada, filtra por rango exacto
                    if parsed_time_local:
                        if not (start_time <= parsed_time_local <= end_time):
                            continue  # fuera del rango exacto

                    # Si no logramos parsear, igual lo incluimos (porque está bajo el día del rango)
                    items.append({
                        "key": key,
                        "time": parsed_time_local,   # puede ser None
                        "size": obj.get("Size", 0),
                        "kind": "mkv",
                    })

                if resp.get("IsTruncated"):
                    continuation = resp.get("NextContinuationToken")
                else:
                    break

            # siguiente día
            day_cursor = day_cursor + timedelta(days=1)

        # Ordenar: primero por time (None al final), luego por key
        def _sort_key(x):
            return (x["time"] is None, x["time"] or datetime.min.replace(tzinfo=timezone.utc), x["key"])

        items.sort(key=_sort_key)
        return items

def _ensure_local_aware(dt: datetime) -> datetime:
    """Devuelve dt con tz local; si viene naive, asume hora local."""
    local_tz = datetime.now().astimezone().tzinfo
    if dt.tzinfo is None:
        return dt.replace(tzinfo=local_tz)
    return dt.astimezone(local_tz)

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
        start_time = _ensure_local_aware(start_time)
        end_time   = _ensure_local_aware(end_time)
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
    """Listar videos disponibles para una cámara con filtros de fecha y paginación"""
    try:
        kind = request.args.get('kind', 'tar').lower()
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
            #end_time = datetime.now()
            #start_time = end_time - timedelta(days=7)
            end_time = datetime.now().astimezone()
            start_time = end_time - timedelta(days=7)
            start_time = _ensure_local_aware(start_time)
            end_time = _ensure_local_aware(end_time)
        else:
            # Parsear las fechas proporcionadas
            try:
                start_time = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                end_time = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                start_time = _ensure_local_aware(start_time)
                end_time   = _ensure_local_aware(end_time)
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
        if kind == 'mkv':
            all_batches = video_reconstructor._list_mkv_in_range(camera_id, start_time, end_time)
        else:
            all_batches = video_reconstructor._find_batches_in_range(camera_id, start_time, end_time)

        # Aplicar paginación
        total = len(all_batches)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_batches = all_batches[start_idx:end_idx]
        
        return jsonify({
            "camera_id": camera_id,
            "kind": kind,
            "available_batches": [{
                "time": (batch['time'].isoformat() if batch.get('time') else None),
                "size_mb": round(batch['size'] / (1024 * 1024), 2),
                "key": batch['key'],
                "type": batch.get('kind', 'tar')
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
    
@video_bp.route('/video/list/<camera_id>')
def list_virtual_videos(camera_id):
    """Listar videos (MKV o TAR) filtrados por rango de fecha y rango horario"""
    try:
        # Parámetro para definir la fuente (por compatibilidad)
        source = request.args.get('source', 'tar').lower()

        # Parámetros de la query
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        video_duration_min = request.args.get('duration_min', 5, type=int)
        time_range = request.args.get('time_range', 'all').lower()

        # Validaciones básicas
        if page < 1:
            return jsonify({"error": "El número de página debe ser al menos 1"}), 400
        if per_page < 1 or per_page > 100:
            return jsonify({"error": "El tamaño de página debe estar entre 1 y 100"}), 400
        if video_duration_min < 1 or video_duration_min > 60:
            return jsonify({"error": "La duración debe estar entre 1 y 60 minutos"}), 400

        # Parsear fechas
        if not start_date_str or not end_date_str:
            end_time = datetime.now().astimezone()
            start_time = end_time - timedelta(days=7)
        else:
            try:
                start_time = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                end_time   = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            except ValueError:
                return jsonify({"error": "Formato de fecha inválido. Use formato ISO"}), 400

        start_time = _ensure_local_aware(start_time)
        end_time   = _ensure_local_aware(end_time)

        # Validaciones de fecha
        if start_time > end_time:
            return jsonify({"error": "La fecha de inicio no puede ser mayor que la fecha de fin"}), 400

        max_days = 7
        if (end_time - start_time).days > max_days:
            return jsonify({"error": f"El rango de búsqueda no puede exceder {max_days} días"}), 400

        # --- Definición de rangos horarios ---
        ranges = {
            'morning': (6, 12),         # Mañana
            'afternoon': (12, 18),      # Tarde
            'night': (18, 23),          # Noche
            'earlymorning': (23, 6),    # Madrugada
        }

        # ----------------------------
        # Fuente MKV
        # ----------------------------
        if source == 'mkv':
            mkv_items = video_reconstructor._list_mkv_in_range(camera_id, start_time, end_time)
            logger.info(f"Se encontraron {len(mkv_items)} MKV para la cámara {camera_id}")

            # --- Filtro horario (HdU 12) ---
            if time_range != 'all' and time_range in ranges:
                start_h, end_h = ranges[time_range]
                filtered_items = []

                for item in mkv_items:
                    try:
                        ts = item.get('time') or item.get('timestamp') or item.get('start_time')
                        if not ts:
                            continue
                        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        hour = dt.hour

                        # Normal y cruzado (madrugada)
                        if start_h < end_h:
                            if start_h <= hour < end_h:
                                filtered_items.append(item)
                        else:
                            if hour >= start_h or hour < end_h:
                                filtered_items.append(item)

                    except Exception as ex:
                        logger.warning(f"No se pudo procesar item: {ex}")

                mkv_items = filtered_items
                logger.info(f"Rango horario '{time_range}': {len(mkv_items)} resultados")

            # Paginación
            total = len(mkv_items)
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            page_items = mkv_items[start_idx:end_idx]

            return jsonify({
                "camera_id": camera_id,
                "source": source,
                "videos": page_items,
                "time_range": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat(),
                    "filter": time_range
                },
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "pages": (total + per_page - 1) // per_page
                }
            })

        # ----------------------------
        # Fuente TAR (virtual)
        # ----------------------------
        else:
            batches = video_reconstructor._find_batches_in_range(camera_id, start_time, end_time)
            logger.info(f"Se encontraron {len(batches)} batches para la cámara {camera_id}")

            virtual_videos = create_virtual_videos_from_batches(batches, video_duration_min * 60)

            # Filtro horario también disponible para TAR
            if time_range != 'all' and time_range in ranges:
                start_h, end_h = ranges[time_range]
                filtered = []
                for v in virtual_videos:
                    ts = v.get('time') or v.get('timestamp') or v.get('start_time')
                    if not ts:
                        continue
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    h = dt.hour
                    if start_h < end_h:
                        if start_h <= h < end_h:
                            filtered.append(v)
                    else:
                        if h >= start_h or h < end_h:
                            filtered.append(v)
                virtual_videos = filtered
                logger.info(f"Rango horario '{time_range}': {len(virtual_videos)} resultados")

            # Paginación
            total = len(virtual_videos)
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            paginated_videos = virtual_videos[start_idx:end_idx]

            return jsonify({
                "camera_id": camera_id,
                "source": source,
                "videos": paginated_videos,
                "time_range": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat(),
                    "filter": time_range
                },
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "pages": (total + per_page - 1) // per_page
                }
            })

    except Exception as e:
        logger.error(f"Error en list_virtual_videos: {str(e)}", exc_info=True)
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

# --- Thumbnail: endpoint con soporte MKV | TAR, y source=auto/tar/mkv ---
@video_bp.route('/video/thumbnail/<camera_id>')
def generate_thumbnail_on_demand(camera_id):
    """
    Generar thumbnail.
    Params:
      - source=tar|mkv  (default: tar)
      - key=...         (solo mkv; si viene, se usa directo)
      - time=ISO8601    (obligatorio en tar; opcional en mkv si no hay key)
    """
    try:
        source = (request.args.get('source') or 'tar').lower()
        key = request.args.get('key')
        time_str = request.args.get('time')

        if source not in ('tar', 'mkv'):
            return jsonify({"error": "source inválido (tar|mkv)"}), 400

        # ---- MKV: thumbnail desde el archivo MKV ----
        if source == 'mkv':
            if key:
                thumb = extract_thumbnail_from_key(key)  # maneja mkv
                if not thumb:
                    return jsonify({"error": "No se pudo generar thumbnail desde el MKV"}), 500
                return send_file(thumb, mimetype='image/jpeg', as_attachment=False,
                                 download_name=f"thumb_{os.path.basename(key)}.jpg")

            # Si no hay key, necesitas 'time' para ubicar el MKV que cubre ese instante
            if not time_str:
                return jsonify({"error": "Para source=mkv debes pasar ?key=... o ?time=..."}), 400

            target_time = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
            target_time = _ensure_local_aware(target_time)

            match = _find_mkv_covering_time(camera_id, target_time, chunk_seconds=DEFAULT_MKV_SECONDS)
            if not match:
                return jsonify({"error": "No se encontró un MKV que cubra ese tiempo"}), 404

            thumb = extract_thumbnail_from_key(match['key'])
            if not thumb:
                return jsonify({"error": "No se pudo generar thumbnail desde el MKV"}), 500

            return send_file(thumb, mimetype='image/jpeg', as_attachment=False,
                             download_name=f"thumb_{os.path.basename(match['key'])}.jpg")

        # ---- TAR: thumbnail desde el primer frame del batch más cercano ----
        else:
            if not time_str:
                return jsonify({"error": "Parámetro 'time' requerido para source=tar"}), 400

            target_time = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
            target_time = _ensure_local_aware(target_time)

            # Buscar batch .tar.gz más cercano ±2 min
            batch = find_closest_batch(camera_id, target_time, source='tar')
            if not batch:
                return jsonify({"error": "No se encontraron batches para el tiempo solicitado"}), 404

            # Generar thumbnail desde el TAR
            thumbnail_path = extract_thumbnail_from_key(batch['key'])  # ahora maneja tar y mkv
            if not thumbnail_path:
                return jsonify({"error": "No se pudo generar el thumbnail"}), 500

            return send_file(
                thumbnail_path,
                mimetype='image/jpeg',
                as_attachment=False,
                download_name=f"thumbnail_{camera_id}_{target_time.strftime('%Y%m%d_%H%M%S')}.jpg"
            )

    except Exception as e:
        logger.error(f"Error generando thumbnail: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 400

def _find_mkv_covering_time(camera_id: str, target_time: datetime, chunk_seconds: int = DEFAULT_MKV_SECONDS):
    """
    Devuelve el MKV cuyo intervalo [start, start+chunk) cubre target_time.
    Busca en la hora anterior, actual y siguiente para cubrir bordes.
    """
    target_time = _ensure_local_aware(target_time)

    def _hour_prefix(dt: datetime) -> str:
        # Si tus carpetas son UTC, usa dt.astimezone(timezone.utc). Si son locales, usa dt directo.
        dt_utc = dt.astimezone(timezone.utc)
        return f"{BATCHES_PREFIX}/{camera_id}/{dt_utc.strftime('%Y/%m/%d/%H')}/"

    hours = [target_time - timedelta(hours=1), target_time, target_time + timedelta(hours=1)]
    candidates = []
    for h in hours:
        prefix = _hour_prefix(h)
        continuation = None
        while True:
            kwargs = {"Bucket": S3_BUCKET_NAME, "Prefix": prefix}
            if continuation:
                kwargs["ContinuationToken"] = continuation
            try:
                resp = s3_client.list_objects_v2(**kwargs)
            except Exception as e:
                logger.error(f"Error listando MKV en {prefix}: {e}")
                break

            for obj in resp.get("Contents", []):
                key = obj["Key"]
                name = key.split("/")[-1].lower()
                if not name.endswith(".mkv"):
                    continue
                m = _MKV_TS_RE.search(name)
                if not m:
                    continue
                ymd, hms = m.groups()
                try:
                    start_naive = datetime.strptime(f"{ymd}_{hms}", "%Y%m%d_%H%M%S")
                    start_utc = start_naive.replace(tzinfo=timezone.utc)
                    start_local = start_utc.astimezone()
                    end_local = start_local + timedelta(seconds=chunk_seconds)
                    candidates.append({"key": key, "time": start_local, "end_time": end_local, "size": obj.get("Size", 0)})
                except Exception:
                    continue

            if resp.get("IsTruncated"):
                continuation = resp.get("NextContinuationToken")
            else:
                break

    for c in sorted(candidates, key=lambda x: x["time"]):
        if c["time"] <= target_time < c["end_time"]:
            return {"key": c["key"], "time": c["time"], "size": c["size"]}

    # Fallback: más cercano ±2 min
    if candidates:
        candidates.sort(key=lambda x: abs(x["time"] - target_time))
        if abs(candidates[0]["time"] - target_time) <= timedelta(minutes=2):
            best = candidates[0]
            return {"key": best["key"], "time": best["time"], "size": best["size"]}

    return None

def find_closest_batch(camera_id, target_time, source='auto'):
    """
    Encontrar el batch/segmento más cercano a target_time.
    - source='tar': busca solo .tar.gz (frames)
    - source='mkv': busca solo .mkv (grabaciones 5min)
    - source='auto': combina ambos y elige el más cercano
    Usa ventana ±2 minutos.
    """
    try:
        target_time = _ensure_local_aware(target_time)
        start_range = target_time - timedelta(minutes=2)
        end_range   = target_time + timedelta(minutes=2)

        candidates = []

        if source in ('auto', 'tar'):
            tar_batches = video_reconstructor._find_batches_in_range(camera_id, start_range, end_range) or []
            # Normaliza estructura
            for b in tar_batches:
                if b.get('time'):
                    candidates.append({
                        'key': b['key'],
                        'time': _ensure_local_aware(b['time']),
                        'size': b.get('size', 0),
                        'type': 'tar'
                    })

        if source in ('auto', 'mkv'):
            mkv_items = video_reconstructor._list_mkv_in_range(camera_id, start_range, end_range) or []
            for m in mkv_items:
                if m.get('time'):
                    candidates.append({
                        'key': m['key'],
                        'time': _ensure_local_aware(m['time']),
                        'size': m.get('size', 0),
                        'type': 'mkv'
                    })

        if not candidates:
            return None

        closest = min(candidates, key=lambda x: abs(x['time'] - target_time))
        return closest

    except Exception as e:
        logger.error(f"Error en find_closest_batch: {e}", exc_info=True)
        return None

def extract_thumbnail_from_key(obj_key):
    """
    Extrae un thumbnail (JPEG) a partir de un objeto en S3:
      - Si es .tar.gz (frames): abre el tar y usa el primer frame_*.jpg/png
      - Si es .mkv: descarga temporalmente y usa ffmpeg para snapshot
    Devuelve ruta al archivo JPEG temporal o None.
    """
    try:
        fname = obj_key.split('/')[-1].lower()
        if fname.endswith('.tar.gz'):
            return _extract_thumb_from_tar(obj_key)
        elif fname.endswith('.mkv'):
            return _extract_thumb_from_mkv(obj_key)
        else:
            logger.warning(f"Formato no soportado para thumbnail: {obj_key}")
            return None
    except Exception as e:
        logger.error(f"Error extrayendo thumbnail para {obj_key}: {e}", exc_info=True)
        return None

def _extract_thumb_from_tar(batch_key):
    """Thumbnail desde .tar.gz (primer frame_)"""
    try:
        resp = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=batch_key, ExpectedBucketOwner=os.getenv('AWS_ACCOUNT_ID', ''))
        tar_bytes = BytesIO(resp['Body'].read())

        with tarfile.open(fileobj=tar_bytes, mode='r:gz') as tar:
            frame_members = [m for m in tar.getmembers() if m.name.startswith('frame_')]
            if not frame_members:
                return None
            frame_members.sort(key=lambda x: x.name)
            first = frame_members[0]

            data = tar.extractfile(first).read()
            frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                return None

            # resize suave (máx 320x180)
            h, w = frame.shape[:2]
            if w > 320 or h > 180:
                scale = min(320 / w, 180 / h)
                frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

            out_path = os.path.join(tempfile.gettempdir(), f"thumb_{os.path.basename(batch_key)}.jpg")
            cv2.imwrite(out_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return out_path

    except Exception as e:
        logger.error(f"Error extrayendo thumbnail TAR {batch_key}: {e}", exc_info=True)
        return None

def _extract_thumb_from_mkv(mkv_key):
    """Thumbnail desde .mkv usando ffmpeg (toma un frame al ~1s)."""
    try:
        # Descarga temporal del MKV
        tmp_dir = tempfile.gettempdir()
        base = os.path.basename(mkv_key)
        mkv_path = os.path.join(tmp_dir, f"tmp_{base}")
        jpg_path = os.path.join(tmp_dir, f"thumb_{os.path.splitext(base)[0]}.jpg")

        # Si ya existe de antes, intenta reutilizar (opc.)
        if not os.path.exists(mkv_path):
            with open(mkv_path, 'wb') as f:
                obj = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=mkv_key, ExpectedBucketOwner=os.getenv('AWS_ACCOUNT_ID', ''))
                for chunk in obj['Body'].iter_chunks(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

        # Ejecuta ffmpeg para snapshot
        # -ss 1: ir a 1s (evita primer frame vacío)
        import subprocess
        cmd = [
            'ffmpeg', '-y',
            '-ss', '1',
            '-i', mkv_path,
            '-frames:v', '1',
            '-q:v', '3',
            jpg_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            logger.error(f"ffmpeg thumbnail mkv error: {res.stderr}")
            return None

        return jpg_path

    except Exception as e:
        logger.error(f"Error extrayendo thumbnail MKV {mkv_key}: {e}", exc_info=True)
        return None
    
# --- Descarga de video: soporta tar (reconstrucción) y mkv (descarga directa) ---
@video_bp.route('/video/download/<camera_id>', methods=['GET'])
def download_video(camera_id):
    """
    Descargar video para un rango de tiempo.
    Query:
      - start_time, end_time (ISO)
      - format=mp4 (solo para 'tar')
      - source=tar|mkv  (default: tar)
    Comportamiento:
      - source=tar: usa reconstrucción por frames (como antes).
      - source=mkv: busca MKV en el rango; si hay 1, lo descarga tal cual.
                    si hay 0 → 404, si hay >1 → 400 (pide acotar rango).
    """
    try:
        source = (request.args.get('source') or 'tar').lower()
        if source not in ('tar', 'mkv'):
            return jsonify({"error": "Parámetro 'source' inválido (tar|mkv)"}), 400

        start_time_str = request.args.get('start_time')
        end_time_str   = request.args.get('end_time')
        output_format  = request.args.get('format', 'mp4')

        if not start_time_str or not end_time_str:
            return jsonify({"error": "Se requieren start_time y end_time"}), 400

        # Parseo + normalización
        start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
        end_time   = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
        start_time = _ensure_local_aware(start_time)
        end_time   = _ensure_local_aware(end_time)

        # Validación de rango
        if start_time > end_time:
            return jsonify({"error": "start_time no puede ser mayor que end_time"}), 400

        # Limite: 1 hora para tar (por performance). Para MKV, dejamos igual (pero resolvemos 1 archivo).
        max_duration = timedelta(hours=1)
        if source == 'tar' and (end_time - start_time) > max_duration:
            return jsonify({"error": f"El rango no puede exceder {int(max_duration.total_seconds()/60)} minutos en source=tar"}), 400

        if source == 'mkv':
            explicit_key = request.args.get('key')
            if not explicit_key:
                return jsonify({
                    "error": "Para source=mkv debes pasar ?key=... (Primero lista con /video/list/<camera_id>?source=mkv para obtener el key)"
                }), 400

            tmp_path = _download_s3_object_to_temp(explicit_key)
            if not tmp_path:
                return jsonify({"error": "No se pudo descargar el MKV"}), 500

            return send_file(
                tmp_path,
                as_attachment=True,
                download_name=os.path.basename(explicit_key),
                mimetype='video/x-matroska'
            )


        else:
            # source = tar → reconstrucción tradicional por frames
            video_path, error = video_reconstructor.reconstruct_video(camera_id, start_time, end_time, output_format)
            if error:
                return jsonify({"error": error}), 404

            return send_file(
                video_path,
                as_attachment=True,
                download_name=f"{camera_id}_{start_time.strftime('%Y%m%d_%H%M')}_{end_time.strftime('%H%M')}.{output_format}",
                mimetype=f"video/{output_format}"
            )

    except Exception as e:
        logger.error(f"Error descargando video: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

def _download_s3_object_to_temp(key: str) -> str | None:
    """Descarga un objeto S3 a /tmp y retorna su ruta, o None si falla."""
    try:
        base = os.path.basename(key)
        out = os.path.join(tempfile.gettempdir(), f"dl_{base}")
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
        with open(out, 'wb') as f:
            obj = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=key, ExpectedBucketOwner=os.getenv('AWS_ACCOUNT_ID', ''))
            for chunk in obj['Body'].iter_chunks(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        return out
    except Exception as e:
        logger.error(f"Error descargando objeto S3 {key}: {e}", exc_info=True)
        return None
      
@video_bp.route('/video/download', methods=['POST'])
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
    
@video_bp.route('/video/download_evidence', methods=['POST'])
def download_evidence():
    """Descargar evidencia enviando datos de alerta y camara"""
    try:
        data = request.get_json()
        if not data or 'key' not in data:
            return jsonify({"error": "Se requiere parámetro 'key' en el body JSON"}), 400
            
        key = data['key']
        nombre_camara = data['nombre_camara']
        descripcion = data['descripcion']
        hora_suceso = data['hora_suceso']
        hora_suceso_fmt = datetime.fromisoformat(hora_suceso.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M:%S")
        ubicacion = data['ubicacion']
        output_format = request.args.get('format', 'mp4')  # Opcional: mantener format como query param
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Reconstruir el video
        video_path, error_msg = video_reconstructor.reconstruct_clip_play(key, output_format)
        
        if error_msg:
            return jsonify({"error": error_msg}), 404    

        with tempfile.TemporaryDirectory() as tmpdir:
            info_path = os.path.join(tmpdir, "info.txt")
            zip_path = os.path.join(tmpdir, f"clip_{timestamp}.zip")
        
            with open(info_path, "w", encoding="utf-8") as f:
                    f.write(
                        f"Descripción: {descripcion}\n"
                        f"Fecha del suceso: {hora_suceso_fmt}\n"
                        f"Ubicación: {ubicacion}\n"
                        f"Cámara: {nombre_camara}\n"
                    )

            with zipfile.ZipFile(zip_path, "w") as zipf:
                    zipf.write(video_path, arcname=f"clip.{output_format}")
                    zipf.write(info_path, arcname="info.txt")
      
            # Enviar el archivo para descarga
            return send_file(
                    zip_path,
                    as_attachment=True,
                    download_name=f"clip_{timestamp}.zip",
                    mimetype="application/octet-stream"
                )
        
    except Exception as e:
        logger.error(f"Error descargando video: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    