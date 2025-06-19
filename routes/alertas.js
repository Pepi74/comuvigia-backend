// Endpoints para alertas
import { Router } from 'express'
import pool from '../config/db.js'
import { createClient } from 'redis';
import { io } from '../app.js';

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
    estado SMALLINT NOT NULL DEFAULT 0 -- Estado de alerta, 0: "En Observación", 1: "Confirmada", 2: "Falso Positivo"
);
*/

const router = Router()

// Conexión a Redis
const redisClient = createClient(
  {
    url: 'redis://redis:6379',
  }
);
await redisClient.connect();

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
  
// --- Recibir alerta del servicio IA ---
router.post('/nueva-alerta', async (req, res) => {
  const alerta = req.body;
  // 1. Guardar en Postgres (te devuelve el id real)
  const result = await pool.query(
    'INSERT INTO alertas (id_camara, mensaje, hora_suceso, score_confianza) VALUES ($1, $2, $3, $4) RETURNING *',
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
router.get('/no-vistas', async (_, res) => {
  try {
    const noVistasIds = await redisClient.sMembers('alertas_no_vistas');
    const multi = redisClient.multi();
    noVistasIds.forEach(id => multi.get(`alerta:${id}`));
    const results = await multi.exec();

    const noVistas = results
      .map((val, i) => {
        const id = noVistasIds[i];
        if (!val) {
          console.warn(`Alerta:${id} no existe en Redis`);
          return null;
        }
        try {
          return JSON.parse(val);
        } catch (e) {
          console.error(`JSON malformado en alerta:${id}:`, val);
          return null;
        }
      })
      .filter(Boolean);

    res.json(noVistas);
  } catch (err) {
    console.error('Error general en /no-vistas:', err);
    res.status(500).json({ error: 'Error al recuperar alertas no vistas' });
  }
});

// --- Enviar últimas alertas ---
router.get('/ultimas', async (_, res) => {
  const ultimas = await redisClient.lRange('alertas', 0, 99);
  const alertas = ultimas.map(JSON.parse);
  res.json(alertas);
});

// --- Marcar alertas como vistas ---
// TODO: Actualizar "alertas" de Redis o ver otra forma para utilizar el endpoint /ultimas
router.post('/marcar-vista/:id', async (req, res) => {
  const id = req.params.id;
  const estado = req.body.estado; // 1 -> "Confirmada", 2 -> "Falso Positivo"

  await pool.query('UPDATE alertas SET estado=$1 WHERE id=$2', [estado, id]);
  await redisClient.sRem('alertas_no_vistas', id);

  const result = await pool.query('SELECT * FROM alertas WHERE id=$1', [id]);
  const alertaActualizada = result.rows[0];
  await redisClient.set(`alerta:${id}`, JSON.stringify(alertaActualizada));

  res.json({ ok: true });
});

// Modificar estado de alerta
// TODO: Actualizar "alertas" de Redis o ver otra forma para utilizar el endpoint /ultimas
router.post('/cambiar-estado/:id', async (req, res) => {
  const id = req.params.id;
  const estado = req.body.estado; // 1 -> "Confirmada", 2 -> "Falso Positivo"
  await pool.query('UPDATE alertas SET estado=$1 WHERE id=$2', [estado, id]);

  const result = await pool.query('SELECT * FROM alertas WHERE id=$1', [id]);
  const alertaActualizada = result.rows[0];
  await redisClient.set(`alerta:${id}`, JSON.stringify(alertaActualizada));

  res.json({ ok: true });
})

export default router