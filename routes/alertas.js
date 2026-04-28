// Endpoints para alertas
import { Router } from 'express'
import pool from '../config/db.js'
import redisClient from '../config/redis.js';
import { io } from '../app.js';
import { verificarToken } from '../middlewares/auth.js';
import { verificarRol } from '../middlewares/roles.js';
import { crearAlertaBase } from '../services/alert.service.js';

const router = Router()

router.get('/', verificarToken, verificarRol([1, 2]), async (_, res) => {
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
// Por ahora sera sin verificacion, preguntar y definir bien como la IA enviara nuevas alertas antes de colocar middlewares y si es necesario tenerlos implementados
// TODO: Implementar middleware para IA
router.post('/nueva-alerta', async (req, res) => {
  try {
    const alerta = req.body;
    const { id_camara, mensaje, hora_suceso, tipo, score_confianza, descripcion_suceso, estado, frames, fps } = alerta;

    const nuevaAlerta = await crearAlertaBase({
      alerta,
      pool,
      redisClient,
      io
    });

    if (frames && frames.length > 0) {
      //console.log(id_camara)
      try {
        const metadata = {
          alert_id: nuevaAlerta.id,
          event_type: tipo,
          confidence: score_confianza,
          description: descripcion_suceso || mensaje
        };

        // Llamar a la API de Python para guardar frames
        const response = await fetch('http://python-stream:5000/save-frames', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            camera_id: id_camara,
            frames: frames,
            metadata: metadata,
            fps: fps
          })
        });
        //console.log(response)
        const s3Result = await response.json();

        if (s3Result.success) {
          // 3. Actualizar la alerta con la información de S3
          await pool.query(
            `UPDATE alertas SET clip = $1 WHERE id = $2`,
            [s3Result.s3_info.key, nuevaAlerta.id]
          );

          // Actualizar el objeto de alerta con la info de S3
          nuevaAlerta.clip = s3Result.s3_info.key;
          /* nuevaAlerta.s3_key = s3Result.s3_info.key;
          nuevaAlerta.s3_bucket = s3Result.s3_info.bucket;
          nuevaAlerta.frames_count = s3Result.s3_info.frames_count;
          nuevaAlerta.s3_url = s3Result.s3_info.s3_url; */
        }
      } catch (s3Error) {
        console.error('Error guardando frames en S3:', s3Error);
        // No guardar la alerta completa si hay error con los frames
        res.status(500).json({ error: s3Error });
      }
    } //else{console.log("jaime")}

    res.status(201).json(nuevaAlerta);

  } catch (error) {
    console.error('Error al procesar la alerta:', error);
    res.status(500).json({ error: 'Error interno del servidor' });
  }
});

router.post('/cam-reconnection-failure/nueva-alerta', async (req, res) => {
  try {
    const alerta = req.body;
    const { 
      camera_id, 
      alert_type, 
      message, 
      timestamp, 
      reconnect_attempts, 
      max_attempts, 
      last_attempt_time, 
      stream_url,
      status 
    } = alerta;

    // Validar campos requeridos
    if (!camera_id || !message) {
      return res.status(400).json({ error: 'Campos camera_id y message son requeridos' });
    }

    // Mapear campos a la estructura existente de tu tabla
    const id_camara = camera_id;
    const mensaje = message;
    const hora_suceso = last_attempt_time || timestamp || new Date().toISOString();
    const tipo = alert_type || 4;
    const score_confianza = 1.0; // Máxima confianza para este tipo de alerta
    const descripcion_suceso = `Fallo de reconexión después de ${reconnect_attempts}/${max_attempts} intentos.`;

    // 1. Insertar la alerta en la BD con los campos específicos de reconexión
    let result;
    if (descripcion_suceso) {
      result = await pool.query(
        `INSERT INTO alertas 
         (id_camara, mensaje, hora_suceso, tipo, score_confianza, descripcion_suceso, reconnect_attempts, max_reconnect_attempts, last_attempt_time, estado) 
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) 
         RETURNING *`,
        [
          id_camara, 
          mensaje, 
          hora_suceso, 
          tipo, 
          score_confianza, 
          descripcion_suceso,
          reconnect_attempts,
          max_attempts,
          last_attempt_time,
          0
        ]
      );
    } else {
      result = await pool.query(
        `INSERT INTO alertas 
         (id_camara, mensaje, hora_suceso, tipo, score_confianza, reconnect_attempts, max_reconnect_attempts, last_attempt_time) 
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8) 
         RETURNING *`,
        [
          id_camara, 
          mensaje, 
          hora_suceso, 
          tipo, 
          score_confianza,
          reconnect_attempts,
          max_attempts,
          last_attempt_time
        ]
      );
    }

    const nuevaAlerta = result.rows[0];

    // 2. Actualizar estado
    try {
      // Ejemplo: si tienes una tabla 'camaras'
      await pool.query(
        `UPDATE camaras SET estado_camara = $1 WHERE id = $2`,
        [0, id_camara]
      );
    } catch (updateError) {
      console.warn('No se pudo actualizar estado de cámara:', updateError.message);
      // Continuar aunque falle esta parte
    }

    // 3. Guardar en Redis y emitir WebSocket
    await redisClient.lPush('alertas', JSON.stringify(nuevaAlerta));
    await redisClient.set(`alerta:${nuevaAlerta.id}`, JSON.stringify(nuevaAlerta));
    await redisClient.sAdd('alertas_no_vistas', nuevaAlerta.id.toString());
    await redisClient.lTrim('alertas', 0, 99);

    // Emitir eventos WebSocket
    io.emit('nueva-alerta', nuevaAlerta);
    const cam = { cameraId: id_camara, estado: false};
    io.emit('estado-camara', cam)

    // Log del evento
    console.log(`Alerta de reconexión fallida registrada para cámara ${camera_id}: ${reconnect_attempts}/${max_attempts} intentos`);

    res.status(201).json({
      success: true,
      message: 'Alerta de fallo de reconexión registrada exitosamente',
      alerta: nuevaAlerta
    });

  } catch (error) {
    console.error('Error al procesar la alerta de reconexión:', error);
    res.status(500).json({ 
      error: 'Error interno del servidor',
      details: error.message 
    });
  }
});
  
// --- Enviar alertas no vistas ---
router.get('/no-vistas', verificarToken, verificarRol([1, 2]), async (_, res) => {
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

// --- Enviar últimas 100 alertas ---
router.get('/ultimas', verificarToken, verificarRol([1, 2]), async (_, res) => {
  const ultimas = await redisClient.lRange('alertas', 0, 99);
  const alertas = ultimas.map(item => JSON.parse(item));
  res.json(alertas);
});

// --- Marcar alertas como vistas ---
router.post('/marcar-vista/:id', verificarToken, verificarRol([1, 2]), async (req, res) => {
  const id = req.params.id;
  const estado = req.body.estado; // 1 -> "Confirmada", 2 -> "Falso Positivo"

  await pool.query('UPDATE alertas SET estado=$1 WHERE id=$2', [estado, id]);
  await redisClient.sRem('alertas_no_vistas', id);

  const result = await pool.query('SELECT * FROM alertas WHERE id=$1', [id]);
  const alertaActualizada = result.rows[0];
  await redisClient.set(`alerta:${id}`, JSON.stringify(alertaActualizada));
  const lista = await redisClient.lRange('alertas', 0, -1);
  for (let i = 0; i < lista.length; i++) {
    const alerta = JSON.parse(lista[i]);
    if (Number(alerta.id) === Number(id)) {
      await redisClient.lSet('alertas', i, JSON.stringify(alertaActualizada));
      break;
    }
  }

  res.json({ ok: true });
});

// --- Marcar todas las alertas como vistas ---
router.post('/marcar-todas-vistas', verificarToken, verificarRol([1, 2]), async (req, res) => {
  try {
    const estado = req.body.estado || 1; // Por defecto marcarlas como "Confirmadas"

    // 1. Actualizar en Postgres - marcar todas las alertas no vistas como vistas
    await pool.query(
      'UPDATE alertas SET estado = $1 WHERE estado = 0',
      [estado]
    );

    // 2. Limpiar el conjunto de alertas no vistas en Redis
    await redisClient.del('alertas_no_vistas');

    // 3. Actualizar todas las alertas en Redis
    const todasAlertas = await pool.query('SELECT * FROM alertas ORDER BY hora_suceso DESC LIMIT 100');
    
    // Actualizar la lista de últimas alertas
    await redisClient.del('alertas');
    if (todasAlertas.rows.length > 0) {
      await redisClient.lPush('alertas', todasAlertas.rows.map(alert => JSON.stringify(alert)));
      await redisClient.lTrim('alertas', 0, 99);
    }

    // 4. Actualizar cada alerta individual en Redis
    const multi = redisClient.multi();
    todasAlertas.rows.forEach(alert => {
      multi.set(`alerta:${alert.id}`, JSON.stringify(alert));
    });
    await multi.exec();

    res.json({ 
      ok: true, 
      message: `Todas las alertas han sido marcadas como vistas`,
      alertas_actualizadas: todasAlertas.rows.length
    });

  } catch (error) {
    console.error('Error al marcar todas las alertas como vistas:', error);
    res.status(500).json({ error: 'Error interno del servidor' });
  }
});

// Modificar estado de alerta
router.post('/cambiar-estado/:id', verificarToken, verificarRol([1, 2]), async (req, res) => {
  const id = req.params.id;
  const estado = req.body.estado; // 1 -> "Confirmada", 2 -> "Falso Positivo"
  await pool.query('UPDATE alertas SET estado=$1 WHERE id=$2', [estado, id]);

  const result = await pool.query('SELECT * FROM alertas WHERE id=$1', [id]);
  const alertaActualizada = result.rows[0];
  await redisClient.set(`alerta:${id}`, JSON.stringify(alertaActualizada));
  const lista = await redisClient.lRange('alertas', 0, -1);
  for (let i = 0; i < lista.length; i++) {
    const alerta = JSON.parse(lista[i]);
    if (Number(alerta.id) === Number(id)) {
      await redisClient.lSet('alertas', i, JSON.stringify(alertaActualizada));
      break;
    }
  }

  res.json({ ok: true });
})

// Mismo caso de nueva alerta, preguntar y definir bien como lo va a hacer la IA con los nuevos cambios antes de verificar con middlewares
// TODO: Separar en dos endpoints, otro para la IA (editar-descripcion-ia)
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
    await redisClient.lPush('alertas', nuevasAlertas.map(item => JSON.stringify(item)));
    await redisClient.lTrim('alertas', 0, 99);

    io.emit("nueva-descripcion", alertaActualizada);

    res.json({ ok: true, alerta: alertaActualizada });
  } catch (error) {
    console.error('Error actualizando descripción:', error);
    res.status(500).json({ error: 'Error actualizando la descripción' });
  }
});

router.delete('/eliminar-alerta/:id', verificarToken, verificarRol([2]), async (req, res) => {
  const id = req.params.id;
  try {
    // Eliminar en Postgres
    await pool.query('DELETE FROM alertas WHERE id = $1', [id]);

    // Eliminar en Redis
    await redisClient.del(`alerta:${id}`);
    await redisClient.sRem('alertas_no_vistas', id);

    // Eliminar de la lista de últimas 100 alertas
    const lista = await redisClient.lRange('alertas', 0, -1);
    const nuevaLista = lista.filter(a => {
      const parsed = JSON.parse(a);
      return Number(parsed.id) !== Number(id);
    });

    // Sobrescribir lista (respetando límite de 100)
    if (nuevaLista.length > 0) {
      await redisClient.del('alertas');
      await redisClient.lPush('alertas', nuevaLista);
      await redisClient.lTrim('alertas', 0, 99);
    }

    res.json({ ok: true, message: `Alerta ${id} eliminada correctamente` });
  } catch (error) {
    console.error(error)
    res.status(500).json({ error: "Error eliminando alerta" });
  }
})

// Obtener alertas por id de camara
router.get('/camara/:id_camara', verificarToken, verificarRol([1, 2]), async (req, res) => {
  const id_camara = req.params.id_camara
  try {
    const result = await pool.query('SELECT * FROM alertas WHERE id_camara = $1 ORDER BY hora_suceso DESC', [id_camara])
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

router.get('/historial-filtro',verificarToken, verificarRol([1, 2]), async (req,res) => {
  try {
    const {
      q = '',
      camaraId,
      fields = 'id,descripcion_suceso,mensaje',
      mode = 'or',
    } = req.query;

    const keywords = q.trim().toLowerCase();
    const words = keywords.split(/\s+/).filter(Boolean);
    const fieldList = fields.split(',').map(f => f.trim()).filter(Boolean);

    if (words.length === 0 || fieldList.length === 0) {
      return res.json([]);
    }

    const params = [];
    const conditions = [];

    // 🔢 contador para los placeholders $1, $2, ...
    let paramIndex = 1;

    for (const field of fieldList) {
      const wordConditions = words.map(word => {
        params.push(`%${word}%`);
        return `LOWER(CAST(${field} AS TEXT)) LIKE $${paramIndex++}`;

      });
      conditions.push(`(${wordConditions.join(mode === 'or' ? ' OR ' : ' AND ')})`);
    }

    let sql = `SELECT * FROM alertas WHERE ${conditions.join(' OR ')}`;

    if (camaraId) {
      params.push(camaraId);
      sql += ` AND id_camara = $${paramIndex++}`;
    }

    console.log('SQL generado:', sql);
    console.log('Params:', params);

    const { rows } = await pool.query(sql, params);
    res.json(rows);
  } catch (err) {
    console.error('Error en /buscar:', err);
    res.status(500).json({ error: 'Error al buscar alertas' });
  }
});

router.get('/estadisticas-camara',verificarToken, verificarRol([1, 2]), async (req, res) => {
  let client;
  try {
    const { dias = 7, fecha_inicio, fecha_fin, group, id_camara } = req.query;

    if (!id_camara) {
      return res.status(400).json({ success: false, error: 'Debe especificar id_camara' });
    }

    let startDate, endDate;

    if (fecha_inicio && fecha_fin) {
      startDate = new Date(fecha_inicio);
      endDate = new Date(fecha_fin);
    } else {
      endDate = new Date();
      startDate = new Date();
      startDate.setDate(endDate.getDate() - Number.parseInt(dias));
    }

    const fechaInicioStr = startDate.toISOString().replace('T', ' ').substring(0, 19);
    const fechaFinStr = endDate.toISOString().replace('T', ' ').substring(0, 19);

    const gruposValidos = ['day', 'week', 'month'];
    const grupo = gruposValidos.includes(group) ? group : 'day';

    client = await pool.connect();

    const query = `SELECT * FROM reporte_alertas_por_camara($1, $2, $3, $4)`;
    const result = await client.query(query, [fechaInicioStr, fechaFinStr, grupo, Number.parseInt(id_camara)]);

    if (result.rows.length === 0) {
      return res.json({
        success: true,
        periodo: {
          fecha_inicio: fechaInicioStr,
          fecha_fin: fechaFinStr,
          dias: Math.ceil((endDate - startDate) / (1000 * 60 * 60 * 24)),
        },
        estadisticas_totales: {
          total_alertas: 0,
          alertas_confirmadas: 0,
          falsos_positivos: 0,
          merodeos: 0,
          portonazos: 0,
          asaltos_hogar: 0,
          no_especificados: 0,
          confianza_promedio: 0,
        },
        periodos: []
      });
    }

    // Calcular totales generales
    const totales = {
      total_alertas: 0,
      alertas_confirmadas: 0,
      falsos_positivos: 0,
      merodeos: 0,
      portonazos: 0,
      asaltos_hogar: 0,
      no_especificados: 0,
      confianza_promedio: 0,
    };

    result.rows.forEach(row => {
      totales.total_alertas += Number.parseInt(row.total_alertas) || 0;
      totales.alertas_confirmadas += Number.parseInt(row.alertas_confirmadas) || 0;
      totales.falsos_positivos += Number.parseInt(row.falsos_positivos) || 0;
      totales.merodeos += Number.parseInt(row.merodeos) || 0;
      totales.portonazos += Number.parseInt(row.portonazos) || 0;
      totales.asaltos_hogar += Number.parseInt(row.asaltos_hogar) || 0;
      totales.no_especificados += Number.parseInt(row.no_especificados) || 0;
      totales.confianza_promedio += Number.parseFloat(row.confianza_promedio) || 0;
    });

    // Promedio de confianza
    totales.confianza_promedio = result.rows.length > 0 
      ? Math.round((totales.confianza_promedio / result.rows.length) * 100) / 100
      : 0;

    res.json({
      success: true,
      periodo: {
        fecha_inicio: fechaInicioStr,
        fecha_fin: fechaFinStr,
        dias: Math.ceil((endDate - startDate) / (1000 * 60 * 60 * 24)),
      },
      estadisticas_totales: totales,
      periodos: result.rows  // listo para graficar por periodo
    });

  } catch (err) {
    console.error('Error en /estadisticas-camara:', err);
    res.status(500).json({
      success: false,
      error: 'Error al obtener estadísticas de la cámara',
      detalle: err.message
    });
  } finally {
    if (client) client.release();
  }
});

router.get('/estadisticas-totales', verificarToken, verificarRol([1, 2]), async (req, res) => {
  let client;
  try {
    const { dias = 7, fecha_inicio, fecha_fin, group } = req.query;

    let startDate, endDate;

    if (fecha_inicio && fecha_fin) {
      startDate = new Date(fecha_inicio + 'T00:00:00');
      endDate = new Date(fecha_fin + 'T23:59:59');
    } else {
      endDate = new Date();
      startDate = new Date();
      startDate.setDate(startDate.getDate() - Number.parseInt(dias));
    }

    // ✅ formato compatible con PostgreSQL TIMESTAMP
    const fechaInicioStr = startDate.toISOString().replace('T', ' ').substring(0, 19);
    const fechaFinStr = endDate.toISOString().replace('T', ' ').substring(0, 19);

    const gruposValidos = ['day', 'week', 'month'];
    const grupo = gruposValidos.includes(group) ? group : 'day';

    client = await pool.connect();

    // === 1️⃣ Estadísticas totales ===
    const result = await client.query(
      `SELECT * FROM reporte_alertas_por_periodo($1, $2, $3)`,
      [fechaInicioStr, fechaFinStr, grupo]
    );

    if (result.rows.length === 0) {
      return res.json({
        success: true,
        periodo: { fecha_inicio: fechaInicioStr, fecha_fin: fechaFinStr },
        estadisticas_totales: {},
        sectores: [],
        horarios: []
      });
    }

    // === 2️⃣ Agregación general y sectores ===
    const totales = {
      total_alertas: 0,
      merodeos: 0,
      portonazos: 0,
      asaltos_hogar: 0,
      falsos_positivos: 0,
      alertas_confirmadas: 0,
    };

    const sectores = {};

    result.rows.forEach(row => {
      totales.total_alertas += Number(row.total_alertas) || 0;
      totales.merodeos += Number(row.merodeos) || 0;
      totales.portonazos += Number(row.portonazos) || 0;
      totales.asaltos_hogar += Number(row.asaltos_hogar) || 0;
      totales.falsos_positivos += Number(row.falsos_positivos) || 0;
      totales.alertas_confirmadas += Number(row.alertas_confirmadas) || 0;

      const idSector = row.id_sector;
      if (!sectores[idSector]) {
        sectores[idSector] = {
          id_sector: idSector,
          nombre_sector: row.nombre_sector,
          total_alertas: 0,
          merodeos: 0,
          portonazos: 0,
          asaltos_hogar: 0,
          alertas_confirmadas: 0,
        };
      }

      sectores[idSector].total_alertas += Number(row.total_alertas) || 0;
      sectores[idSector].merodeos += Number(row.merodeos) || 0;
      sectores[idSector].portonazos += Number(row.portonazos) || 0;
      sectores[idSector].asaltos_hogar += Number(row.asaltos_hogar) || 0;
      sectores[idSector].alertas_confirmadas += Number(row.alertas_confirmadas) || 0;
    });

    // === 3️⃣ Distribución horaria (sin truncamiento) ===
    const resultHorarios = await client.query(
      `
      SELECT
        EXTRACT(HOUR FROM a.hora_suceso) AS hora,
        SUM(CASE WHEN a.tipo = 1 THEN 1 ELSE 0 END) AS merodeos,
        SUM(CASE WHEN a.tipo = 2 THEN 1 ELSE 0 END) AS portonazos,
        SUM(CASE WHEN a.tipo = 3 THEN 1 ELSE 0 END) AS asaltos_hogar
      FROM alertas a
      WHERE a.hora_suceso BETWEEN $1::timestamp AND $2::timestamp
      GROUP BY hora
      ORDER BY hora;
      `,
      [fechaInicioStr, fechaFinStr]
    );

    const horarios = resultHorarios.rows.map(r => ({
      hora: Number(r.hora),
      merodeos: Number(r.merodeos) || 0,
      portonazos: Number(r.portonazos) || 0,
      asaltos_hogar: Number(r.asaltos_hogar) || 0
    }));

    // === 4️⃣ Devolver todo junto ===
    res.json({
      success: true,
      periodo: {
        fecha_inicio: fechaInicioStr,
        fecha_fin: fechaFinStr,
        dias: Math.ceil((endDate - startDate) / (1000 * 60 * 60 * 24))
      },
      estadisticas_totales: totales,
      sectores: Object.values(sectores),
      horarios
    });

  } catch (err) {
    console.error('Error en /estadisticas-totales:', err);
    res.status(500).json({
      success: false,
      error: 'Error al obtener estadísticas totales',
      detalle: err.message
    });
  } finally {
    if (client) client.release();
  }
});

export default router