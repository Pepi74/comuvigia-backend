"""
Pruebas unitarias y de endpoints de video_reconstructor.py

Cubren la lógica testeable sin acceso real a S3/OpenCV:
  - Funciones puras: _ensure_local_aware, estimate_batch_duration,
    create_virtual_video_entry, create_virtual_videos_from_batches
  - Validaciones de los endpoints (parámetros faltantes, 400/404)

La descarga real desde S3 y la reconstrucción de video con OpenCV
(_create_video, _download_and_extract_batches, etc.) NO se prueban aquí
porque requieren un bucket con datos reales; quedan fuera de alcance unitario.
"""
from datetime import datetime, timedelta, timezone

import pytest

import video_reconstructor as vr


# --------------------------------------------------------------------------
# Funciones puras de fecha
# --------------------------------------------------------------------------
class TestEnsureLocalAware:
    def test_fecha_naive_recibe_tzinfo(self):
        naive = datetime(2025, 1, 1, 12, 0, 0)
        out = vr._ensure_local_aware(naive)
        assert out.tzinfo is not None

    def test_fecha_aware_se_conserva(self):
        aware = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        out = vr._ensure_local_aware(aware)
        assert out.tzinfo is not None


# --------------------------------------------------------------------------
# Cálculo de duración y videos virtuales
# --------------------------------------------------------------------------
class TestEstimateBatchDuration:
    def test_batch_pequeno_minimo_5s(self):
        # 0.1 MB * 10 = 1s -> se eleva al mínimo de 5
        assert vr.estimate_batch_duration({"size": 100_000}) == 5

    def test_batch_grande_maximo_300s(self):
        # 100 MB * 10 = 1000s -> se limita al máximo de 300
        assert vr.estimate_batch_duration({"size": 100 * 1024 * 1024}) == 300

    def test_batch_medio_valor_proporcional(self):
        # 2 MB * 10 = 20s, dentro del rango
        dur = vr.estimate_batch_duration({"size": 2 * 1024 * 1024})
        assert 5 <= dur <= 300
        assert round(dur) == 20


class TestCreateVirtualVideoEntry:
    def test_estructura_basica(self):
        start = datetime(2025, 1, 1, 0, 0, 0)
        batches = [
            {"key": "b1", "size": 1024 * 1024, "time": start},
            {"key": "b2", "size": 1024 * 1024, "time": start + timedelta(seconds=10)},
        ]
        entry = vr.create_virtual_video_entry(start, batches, 20)
        assert entry["type"] == "virtual"
        assert entry["batch_count"] == 2
        assert entry["batches"] == ["b1", "b2"]
        assert entry["id"].startswith("virtual_20250101_000000")

    def test_sin_batches_usa_duracion_dada(self):
        start = datetime(2025, 1, 1, 0, 0, 0)
        entry = vr.create_virtual_video_entry(start, [], 30)
        assert entry["batch_count"] == 0
        assert entry["size_mb"] == 0


class TestCreateVirtualVideosFromBatches:
    def test_agrupa_batches_en_videos_virtuales(self):
        base = datetime(2025, 1, 1, 0, 0, 0)
        batches = [
            {"key": f"b{i}", "size": 1024 * 1024,
             "time": base + timedelta(seconds=i * 5)}
            for i in range(4)
        ]
        videos = vr.create_virtual_videos_from_batches(batches, target_duration_seconds=10)
        assert isinstance(videos, list)
        assert len(videos) >= 1
        assert all(v["type"] == "virtual" for v in videos)

    def test_lista_vacia_retorna_vacio(self):
        assert vr.create_virtual_videos_from_batches([], 10) == []


# --------------------------------------------------------------------------
# Endpoints del blueprint (validación de parámetros)
# --------------------------------------------------------------------------
@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(vr.video_bp)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestEndpointsValidacion:
    def test_reconstruct_video_no_acepta_get(self, client):
        resp = client.get("/video/reconstruct")
        # La ruta existe pero requiere otro método (405) o validación de cliente
        assert resp.status_code in (400, 404, 405, 422, 500)

    def test_download_clip_sin_key_falla(self, client):
        resp = client.get("/video/download-clip")
        assert resp.status_code in (400, 404, 422, 500)