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
