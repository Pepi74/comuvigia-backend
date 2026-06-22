"""
Pruebas unitarias y de los endpoints Flask de multi_camera_stream.py

Cubren la lógica testeable sin hardware de cámaras:
  - Funciones puras: sanitize_metadata, actualizar_por_id, decode_* 
  - Endpoints Flask simples vía test client: /cameras, /config, /health,
    /video_feed/<id>/status, /debug/camera/<id>, /save-frames (validación)

La lógica de captura RTSP, FFmpeg y OpenCV en vivo (VideoStream, FFmpegSegmenter,
generate_frames) NO se prueba aquí porque requiere hardware/streams reales; queda
documentada como fuera de alcance de las pruebas unitarias.
"""
import base64
import json
import numpy as np
import pytest

import multi_camera_stream as mcs


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def client():
    """Test client de Flask para probar los endpoints sin levantar el servidor."""
    mcs.app.config["TESTING"] = True
    with mcs.app.test_client() as c:
        yield c


def _jpeg_base64(con_header=False):
    """Genera un JPEG mínimo válido codificado en base64 para los tests decode."""
    import cv2
    img = np.zeros((10, 10, 3), np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    if con_header:
        return "data:image/jpeg;base64," + b64
    return b64


# --------------------------------------------------------------------------
# Funciones puras
# --------------------------------------------------------------------------
class TestSanitizeMetadata:
    def test_normaliza_acentos_a_ascii(self):
        out = mcs.sanitize_metadata({"nombre": "Cámara Ñoño"})
        assert out["nombre"] == "Camara Nono"

    def test_convierte_valores_no_string_a_string(self):
        out = mcs.sanitize_metadata({"id": 5, "activo": True})
        assert out["id"] == "5"
        assert out["activo"] == "True"

    def test_dict_vacio_retorna_dict_vacio(self):
        assert mcs.sanitize_metadata({}) == {}


class TestActualizarPorId:
    def test_actualiza_campo_existente_y_retorna_true(self):
        lista = [{"id": 1, "nombre": "A"}, {"id": 2, "nombre": "B"}]
        resultado = mcs.actualizar_por_id(lista, 2, "nombre", "Nuevo")
        assert resultado is True
        assert lista[1]["nombre"] == "Nuevo"

    def test_retorna_false_si_no_encuentra_id(self):
        lista = [{"id": 1, "nombre": "A"}]
        assert mcs.actualizar_por_id(lista, 99, "nombre", "X") is False

    def test_lista_vacia_retorna_false(self):
        assert mcs.actualizar_por_id([], 1, "campo", "v") is False


class TestDecodeFrames:
    def test_decode_base64_frame_con_header(self):
        frame = mcs.decode_base64_frame(_jpeg_base64(con_header=True))
        assert frame is not None
        assert frame.shape[2] == 3

    def test_decode_base64_frame_sin_header(self):
        frame = mcs.decode_base64_frame(_jpeg_base64(con_header=False))
        assert frame is not None

    def test_decode_base64_frame_invalido_retorna_none(self):
        assert mcs.decode_base64_frame("no-es-base64-valido!!!") is None

    def test_decode_base64_simple_ok(self):
        frame = mcs.decode_base64_simple(_jpeg_base64())
        assert frame is not None

    def test_decode_base64_simple_invalido_retorna_none(self):
        assert mcs.decode_base64_simple("###") is None

    def test_decode_structured_frame_ok(self):
        frame = mcs.decode_structured_frame({"image_data": _jpeg_base64()})
        assert frame is not None

    def test_decode_structured_frame_sin_image_data_retorna_none(self):
        assert mcs.decode_structured_frame({"otro_campo": "x"}) is None


# --------------------------------------------------------------------------
# Endpoints Flask
# --------------------------------------------------------------------------
class TestEndpoints:
    def test_health_check_retorna_200_y_estado(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "healthy"
        assert data["service"] == "multi_camara_stream"
        assert "uptime_seconds" in data

    def test_list_cameras_retorna_lista(self, client):
        resp = client.get("/cameras")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["nombre"] == "Cam Test 1"

    def test_config_get_retorna_camaras(self, client):
        resp = client.get("/config")
        assert resp.status_code == 200

    def test_config_post_retorna_mensaje(self, client):
        resp = client.post("/config", json={})
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "Configuración actualizada"

    def test_video_feed_status_camara_inexistente(self, client):
        resp = client.get("/video_feed/999/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["camera_id"] == 999
        assert data["in_video_streams"] is False
        assert data["stream_active"] is False

    def test_video_feed_status_id_invalido_retorna_500(self, client):
        resp = client.get("/video_feed/abc/status")
        assert resp.status_code == 500
        assert "error" in resp.get_json()

    def test_debug_camera_inexistente_retorna_404(self, client):
        resp = client.get("/debug/camera/999")
        assert resp.status_code == 404
        assert "error" in resp.get_json()

    def test_debug_camera_id_invalido_retorna_500(self, client):
        resp = client.get("/debug/camera/xyz")
        assert resp.status_code == 500

    def test_save_frames_sin_json_retorna_400(self, client):
        # Un body JSON que evalúa como vacío dispara el 400 de "No se proporcionaron datos"
        resp = client.post("/save-frames", json={})
        assert resp.status_code == 400

    def test_save_frames_sin_campos_obligatorios_retorna_400(self, client):
        resp = client.post("/save-frames", json={"camera_id": 1})
        assert resp.status_code == 400

    def test_socketio_status_responde(self, client):
        resp = client.get("/socketio/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "socketio_connected" in data


# --------------------------------------------------------------------------
# SocketIOClientManager (lógica testeable sin red)
# --------------------------------------------------------------------------
class TestSocketIOClientManager:
    def test_get_camera_lock_reutiliza_mismo_lock(self):
        mgr = mcs.SocketIOClientManager()
        lock1 = mgr.get_camera_lock(5)
        lock2 = mgr.get_camera_lock(5)
        assert lock1 is lock2

    def test_get_camera_lock_distinto_por_camara(self):
        mgr = mcs.SocketIOClientManager()
        assert mgr.get_camera_lock(1) is not mgr.get_camera_lock(2)

    def test_send_camera_status_no_hace_nada_si_desconectado(self):
        mgr = mcs.SocketIOClientManager()
        mgr.connected = False
        # No debe lanzar excepción aunque sio no esté conectado
        assert mgr.send_camera_status(1, "active") is None

    def test_handle_update_id_invalido_no_lanza(self):
        mgr = mcs.SocketIOClientManager()
        # camera con id no convertible a int: la función captura y retorna sin error
        mgr._handle_camera_update_from_backend({"action": "update", "camera": {"id": "abc"}})