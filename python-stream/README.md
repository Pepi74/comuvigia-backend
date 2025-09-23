# ComuVigIA Stream Camara

### 1. (Recomendado) Crear y seleccionar entorno virtual
```
python -m venv stream-env
stream-env\Scripts\activate
```
### 2. Ejecutar instalación (requirements.txt):
```
pip install -r requirements.txt
```

### 3. Ejecución
```
python camera_stream.py
```




## Ejecución de setup para cámaras.

- Conectar cámaras
- Para la RTSP solo hay que obtener ip y luego conectarse (*Falta ver como usar el router*)
- Para la cámara web: 
    - Abrir OBS &rarr; Ajustes &rarr; Emisión
    - Crear transmisión personalizada
    - Abrir MediaMTX agregando en mediamtx.yml los *paths* de las cámaras

