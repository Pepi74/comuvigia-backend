// Endpoints para alertas
import { Router } from 'express'
import pool from '../config/db.js'
import { createClient } from 'redis';
import { io } from '../app.js';

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

  const { id_camara, mensaje, hora_suceso, score_confianza, descripcion_suceso } = alerta;
  let result;

  if (descripcion_suceso) {
    result = await pool.query(
      `INSERT INTO alertas (id_camara, mensaje, hora_suceso, score_confianza, descripcion_suceso) VALUES ($1, $2, $3, $4, $5) RETURNING *`,
      [id_camara, mensaje, hora_suceso, score_confianza, descripcion_suceso]
    );
  } else {
    result = await pool.query(
      `INSERT INTO alertas (id_camara, mensaje, hora_suceso, score_confianza) VALUES ($1, $2, $3, $4) RETURNING *`,
      [id_camara, mensaje, hora_suceso, score_confianza]
    );
  }

  const nuevaAlerta = result.rows[0];

  // Guardar en Redis
  await redisClient.lPush('alertas', JSON.stringify(nuevaAlerta));
  await redisClient.set(`alerta:${nuevaAlerta.id}`, JSON.stringify(nuevaAlerta));
  await redisClient.sAdd('alertas_no_vistas', nuevaAlerta.id.toString());
  await redisClient.lTrim('alertas', 0, 99);

  // Emitir vía WebSocket
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

router.put('/editar-descripcion/:id', async (req, res) => {
  const id = req.params.id;
  const { descripcion_suceso } = req.body;

  try {
    // 1. Actualizar en Postgres
    await pool.query(
      'UPDATE alertas SET descripcion_suceso = $1 WHERE id = $2',
      [descripcion_suceso, id]
    );

    // 2. Obtener la alerta actualizada
    const result = await pool.query('SELECT * FROM alertas WHERE id = $1', [id]);
    const alertaActualizada = result.rows[0];

    // 3. Actualizar en Redis (si la tienes cacheada)
    await redisClient.set(`alerta:${id}`, JSON.stringify(alertaActualizada));

    // Si la tienes en la lista `alertas`, reemplaza esa entrada (opcional)
    const alertas = await redisClient.lRange('alertas', 0, -1);
    const nuevasAlertas = alertas.map(a => {
      const parsed = JSON.parse(a);
      return parsed.id === alertaActualizada.id ? alertaActualizada : parsed;
    });
    await redisClient.del('alertas');
    await redisClient.lPush('alertas', nuevasAlertas.map(JSON.stringify));
    await redisClient.lTrim('alertas', 0, 99);

    res.json({ ok: true, alerta: alertaActualizada });
  } catch (error) {
    console.error('Error actualizando descripción:', error);
    res.status(500).json({ error: 'Error actualizando la descripción' });
  }
});

export default router