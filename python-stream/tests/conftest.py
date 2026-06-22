"""
Configuración de pytest para las pruebas del servicio python-stream.

Este archivo se ejecuta ANTES de recolectar e importar los módulos bajo prueba.
Su trabajo es neutralizar todos los efectos secundarios que multi_camera_stream.py
y video_reconstructor.py ejecutan durante el import (a nivel de módulo):

  1. Lectura de variables de entorno (S3_*, FRONTEND_URL).
  2. Llamada HTTP real al backend para obtener las cámaras (requests.request).
  3. Creación del directorio /logs y escritura de archivos de log.
  4. Arranque de hilos en segundo plano (socketio_manager.start()), que de otro
     modo intentan conectarse al backend para siempre y cuelgan la suite.

Para (4) se parchea threading.Thread.start SOLO durante el import del módulo,
de modo que ningún hilo llegue a arrancar. Una vez importado el módulo, se
restaura el comportamiento normal.
"""
import os
import sys
import json
import threading
from unittest.mock import MagicMock, patch

# --- 1. Variables de entorno requeridas en el import ---
os.environ.setdefault("S3_ACCESS_KEY", "test-access")
os.environ.setdefault("S3_SECRET_KEY", "test-secret")
os.environ.setdefault("S3_BUCKET_NAME", "test-bucket")
os.environ.setdefault("FRONTEND_URL", "http://localhost:8100")
os.environ.setdefault("NODE_ENV", "test")

# --- 2. Hacer importable el directorio padre (python-stream/) ---
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# --- 3. Cámaras falsas que reemplazan la respuesta del backend ---
_FAKE_CAMERAS = [
    {"id": 1, "estado_camara": 0, "nombre": "Cam Test 1",
     "posicion": [0, 0], "direccion": "Calle Falsa 123",
     "link_camara": "rtsp://fake/stream1",
     "link_camara_externo": "rtsp://fake/externo1"},
    {"id": 2, "estado_camara": 0, "nombre": "Cam Test 2",
     "posicion": [1, 1], "direccion": "Avenida Siempreviva 742",
     "link_camara": "rtsp://fake/stream2",
     "link_camara_externo": "rtsp://fake/externo2"},
]
_fake_response = MagicMock()
_fake_response.text = json.dumps(_FAKE_CAMERAS)
_fake_response.status_code = 200

# --- 4. Importar el módulo bajo prueba con todos los efectos neutralizados ---
_real_thread_start = threading.Thread.start


def _noop(self, *args, **kwargs):
    """Reemplazo de Thread.start que no hace nada (evita hilos colgados)."""
    return None


with patch("requests.request", return_value=_fake_response), \
     patch("requests.get", return_value=_fake_response), \
     patch("requests.put", return_value=_fake_response), \
     patch("os.makedirs", return_value=None), \
     patch("logging.handlers.RotatingFileHandler", lambda *a, **k: __import__("logging").NullHandler()), \
     patch("boto3.client", MagicMock()), \
     patch("pika.BlockingConnection", MagicMock()), \
     patch("pika.ConnectionParameters", MagicMock()), \
     patch("pika.PlainCredentials", MagicMock()), \
     patch.object(threading.Thread, "start", _noop):
    # El import ocurre aquí dentro: sin red (HTTP, RabbitMQ, S3), sin logs a disco,
    # y sin que ningún hilo en segundo plano arranque.
    import multi_camera_stream  # noqa: E402,F401
    import video_reconstructor  # noqa: E402,F401

# A partir de aquí Thread.start vuelve a su comportamiento normal (lo restaura el
# context manager 'with'), por si algún test quisiera usar hilos reales.