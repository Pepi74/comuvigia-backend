// Endpoints para alertas
import { Router } from 'express'
import pool from '../config/db.js'
import http from 'http';
import { Server } from 'socket.io';
import cors from 'cors';
import { createClient } from 'redis';


const router = Router()
const server = http.createServer(router);

// WebSocket (con CORS)
const io = new Server(server, {
  cors: { origin: 'http://localhost:8100', methods: ['GET', 'POST'] }
});

const redisClient = createClient({
  url: 'redis://redis:6379',});
await redisClient.connect();


// --- Recibir alerta del servicio IA ---
router.post('/nueva-alerta', async (req, res) => {
  const alerta = req.body;
  // 1. Guardar en Postgres (te devuelve el id real)
  const result = await pool.query(
    'INSERT INTO alertas (id_camara, mensaje, hora_suceso, score_confianza, estado) VALUES ($1, $2, $3, $4, FALSE) RETURNING *',
    [alerta.id_camara, alerta.mensaje, alerta.hora_suceso, alerta.score_confianza]
  );
  const nuevaAlerta = result.rows[0];

  // 2. Guardar serializada en Redis
  await redisClient.lPush('alertas', JSON.stringify(nuevaAlerta));
  await redisClient.set(`alerta:${nuevaAlerta.id}`, JSON.stringify(nuevaAlerta));
  await redisClient.sAdd('alertas_no_vistas', nuevaAlerta.id.toString());

  // 3. Trim lista de alertas (opcional: solo las últimas 100)
  await redisClient.lTrim('alertas', 0, 99);

  // 4. Emitir a los clientes conectados por WS
  io.emit('nueva-alerta', nuevaAlerta);

  res.status(201).json(nuevaAlerta);
});

// --- Enviar alertas no vistas ---
router.get('/no-vistas', async (req, res) => {
  const noVistasIds = await redisClient.sMembers('alertas_no_vistas');
  const multi = redisClient.multi();
  noVistasIds.forEach(id => multi.get(`alerta:${id}`));
  const noVistas = (await multi.exec()).filter(Boolean).map(([err, val]) => JSON.parse(val));
  res.json(noVistas);
});

// --- Enviar últimas alertas ---
router.get('/ultimas', async (req, res) => {
  const ultimas = await redisClient.lRange('alertas', 0, 99);
  const alertas = ultimas.map(JSON.parse);
  res.json(alertas);
});

// --- Marcar alertas como vistas ---
router.post('/api/alertas/marcar-vista/:id', async (req, res) => {
  const id = req.params.id;
  await pool.query('UPDATE alertas SET estado=TRUE WHERE id=$1', [id]);
  await redisClient.sRem('alertas_no_vistas', id);
  res.json({ ok: true });
});


/*
En las alertas, se asume que se tiene creada una base de datos Postgresql en donde existe la tabla definida de la siguiente forma:
CREATE TABLE alertas (
    id SERIAL PRIMARY KEY, -- Puede ser definida como id_alerta
    id_camara INTEGER NOT NULL REFERENCES camaras(id), -- FK a id de tabla camaras
    mensaje TEXT NOT NULL,
    hora_suceso TIMESTAMP NOT NULL,
    score_confianza NUMERIC NOT NULL,
    id_clip INTEGER, -- Opcional, referencia el id del clip o video perteneciente a otra base de datos
    descripcion_suceso TEXT, -- Opcional
    estado BOOLEAN NOT NULL DEFAULT FALSE
);
*/

router.get('/', async (_, res) => {
  try {
    const result = await pool.query('SELECT * FROM alertas')
    const alerts = result.rows
    const cleanAlerts = alerts.map(alert => {
        const cleaned = {}
        for (const key in alert) {
            if (alert[key] !== null) cleaned[key] = alert[key]            
        }
        return cleaned
    })

    res.json(cleanAlerts)
  } catch (error) {
    console.error('Error al obtener alertas:', error)
    res.status(500).send('Error en el servidor')
  }
})

export default router