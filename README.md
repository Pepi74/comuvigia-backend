# ComuVigIA Backend

Backend de la plataforma ComuVigIA desarrollado en NodeJS.

## Instalación

Utilizar el siguiente comando en una terminal

```
npm install
```

## Variables de entorno

Realizar una copia del archivo `.env.example` y renombrarlo a `.env`, luego rellenar los campos necesarios de puerto y credenciales de base de datos Postgres.

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


### Ejecución de pruebas unitarias y de integración
```bash
npm test
```

### Ejecución con reporte de cobertura
Genera el reporte de cobertura en `coverage/lcov-report/index.html` 
y el archivo `coverage/lcov.info` para integración con SonarQube:

```bash
npm test -- --coverage --coverageReporters=lcov
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

### Pruebas de carga y estrés (JMeter)
Requiere Apache JMeter 5.6+.

> Nota: ajustar la ruta de los archivos .jmx según la ubicación de instalación de JMeter y los planes de prueba.

Ejecutar lo siguiente para cada plan de prueba:

```bash
jmeter -n -t smoke-test.jmx -l resultados/smoke-test.jtl -e -o resultados/smoke-test
```

```bash
jmeter -n -t carga-normal.jmx -l resultados/carga-normal.jtl -e -o resultados/carga-normal
```

```bash
jmeter -n -t estres-progresivo.jmx -l resultados/estres-progresivo.jtl -e -o resultados/estres-progresivo
```

```bash
jmeter -n -t frames-ia.jmx -l resultados/frames-ia.jtl -e -o resultados/frames-ia
```

```bash
jmeter -n -t pico-subito.jmx -l resultados/pico-subito.jtl -e -o resultados/pico-subito
```

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
```bash
sonar-scanner \
  -Dsonar.projectKey=comuvigia-backend \
  -Dsonar.sources=. \
  -Dsonar.host.url=http://localhost:9010 \
  -Dsonar.token=TU_TOKEN \
  -Dsonar.exclusions=node_modules/**,coverage/**,tests/** \
  -Dsonar.javascript.lcov.reportPaths=coverage/lcov.info
```

> Nota: generar primero el reporte de cobertura con `npm test -- --coverage --coverageReporters=lcov` antes de ejecutar el scanner para incluir la cobertura real y reemplazar TU_TOKEN con el token generado en SonarQube para el proyecto comuvigia-backend.