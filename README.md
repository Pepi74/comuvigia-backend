# ComuVigIA Backend

Backend de la plataforma ComuVigIA desarrollado en NodeJS.

## Instalación

Utilizar el siguiente comando en una terminal

```
npm install
```

## Variables de entorno

Realizar una copia del archivo `.env.example` y renombrarlo a `.env`, 
luego rellenar los campos con los valores correspondientes al entorno:

```bash
# Backend
PORT=3000 # Ajustar si es necesario

# PostgreSQL
DB_USER=tu_usuario
DB_PASSWORD=tu_contraseña
DB_HOST=postgres
DB_PORT=5432
DB_NAME=comuvigia-backend

# pgAdmin
PGADMIN_DEFAULT_EMAIL=tu_correo
PGADMIN_DEFAULT_PASSWORD=tu_contraseña

# RabbitMQ
RABBIT_HOST=rabbitmq
RABBIT_PORT=5672
RABBITMQ_DEFAULT_USER=tu_usuario
RABBITMQ_DEFAULT_PASS=tu_contraseña

# Redis
REDIS_URL=redis://redis:6379

# MinIO (S3)
S3_ACCESS_KEY=tu_access_key
S3_SECRET_KEY=tu_secret_key
S3_BUCKET_NAME=comuvigia-video-batches

# URLs de servicios
FRONTEND_URL=url_frontend # http://localhost:8100 por defecto
CAMERA_URL=url_backend_flask # http://localhost:5000/ por defecto
IA_URL=url_ia # http://host.docker.internal:4000/ por defecto

# JWT
JWT_SECRET=tu_secreto_seguro

# Entorno
NODE_ENV=test
```

## Ejecución

Para ejecutar el backend en modo producción, utilizar el siguiente comando

```
npm run start
```

Para ejecutar el backend en modo desarrollador, utilizar el siguiente comando

```
npm run dev
```

## Levantar Bases de datos + Backend
```
docker compose up -d --build
```
## Solo Backend
```
docker compose up -d --build backend
```

## Pruebas automatizadas

### Requisitos
- Node.js 18+
- Docker (para Postgres y Redis activos)


### Pruebas unitarias e integración (Jest)

Las pruebas unitarias no requieren infraestructura activa. Las pruebas de integración requieren Postgres y Redis corriendo:

```bash
docker compose up -d postgres redis
```

Ejecutar todas las pruebas:
```bash
npm test
```

Con reporte de cobertura (genera `coverage/lcov-report/index.html`):
```bash
npm test -- --coverage --coverageReporters=lcov
```

Los archivos de prueba se encuentran en:
```
tests/
  unit/
    services/alert.service.test.js     → UT-01 a UT-07
    auth.middleware.test.js            → UT-08 a UT-10
    roles.middleware.test.js           → UT-11 a UT-13
  integration/
    alert.integration.test.js          → IT-01 a IT-05
```

### Pruebas de sistema (Postman + Newman)
Requiere Newman instalado globalmente:
```bash
npm install -g newman newman-reporter-htmlextra
```

Ejecutar colección:
```bash
newman run tests/system/comuvigia-pruebas-sistema.json \
  --env-var "base_url=http://localhost:3000" \
  --reporters cli,htmlextra \
  --reporter-htmlextra-export tests/system/reporte-pruebas-sistema.html
```

El reporte HTML se genera en `tests/system/reporte-pruebas-sistema.html`.

### Pruebas de carga y estrés (JMeter)
Requiere Apache JMeter 5.6+. Los planes de prueba se encuentran en `tests/jmeter/`.

```bash
cd tests/jmeter
```

| Archivo | Escenario | Usuarios |
|--------|-----------|----------|
| `smoke-test.jmx` | E1 — Verificación básica | 5 |
| `carga-normal.jmx` | E2 — Carga normal | 50 |
| `estres-progresivo.jmx` | E3 — Estrés progresivo | 200 |
| `pico-subito.jmx` | E4 — Pico súbito | 300 |
| `frames-ia.jmx` | E5 — Procesamiento IA | 5 cámaras |


Ejecutar cada plan:
```bash
jmeter -n -t smoke-test.jmx -l resultados/smoke-test.jtl -e -o resultados/smoke-test
jmeter -n -t carga-normal.jmx -l resultados/carga-normal.jtl -e -o resultados/carga-normal
jmeter -n -t estres-progresivo.jmx -l resultados/estres-progresivo.jtl -e -o resultados/estres-progresivo
jmeter -n -t pico-subito.jmx -l resultados/pico-subito.jtl -e -o resultados/pico-subito
jmeter -n -t frames-ia.jmx -l resultados/frames-ia.jtl -e -o resultados/frames-ia
```
El dashboard HTML de cada escenario queda en `tests/jmeter/resultados/<escenario>/index.html`.

## Análisis de calidad de código (SonarQube)

### Requisitos
- Docker instalado
- sonar-scanner-cli instalado

### Levantar SonarQube
```bash
docker run -d --name sonarqube -p 9010:9000 sonarqube:community
```
Acceder en `http://localhost:9010` (usuario: admin, contraseña: admin).

### Ejecutar análisis
Generar cobertura primero:
```bash
npm test -- --coverage --coverageReporters=lcov
```

Luego ejecutar el scanner:
```bash
sonar-scanner \
  -Dsonar.projectKey=comuvigia-backend \
  -Dsonar.sources=. \
  -Dsonar.host.url=http://localhost:9010 \
  -Dsonar.token=TU_TOKEN \
  -Dsonar.exclusions=node_modules/**,coverage/**,tests/**,python-stream/tests/**,python-stream/prueba.py,python-stream/camara_stream_antiguo.py,python-stream/camera_stream.py,python-stream/config.py,python-stream/config-3.py \
  -Dsonar.javascript.lcov.reportPaths=coverage/lcov.info \
  -Dsonar.python.coverage.reportPaths=python-stream/coverage.xml \
  -Dsonar.python.version=3.12
```

> Reemplazar `TU_TOKEN` con el token generado en SonarQube para el proyecto `comuvigia-backend`.