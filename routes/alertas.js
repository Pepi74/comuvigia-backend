// Endpoints para alertas
import { Router } from 'express'
import pool from '../config/db.js'
import { createClient } from 'redis';
import { io } from '../app.js';
import { verificarToken } from '../middlewares/auth.js';
import { verificarRol } from '../middlewares/roles.js';

const router = Router()

// Conexión a Redis
const redisClient = createClient(
  {
    url: 'redis://redis:6379',
  }
);
await redisClient.connect();

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
    const { id_camara, mensaje, hora_suceso, tipo, score_confianza, descripcion_suceso, frames,fps } = alerta;

    // 1. Primero insertar la alerta en la BD
    let result;
    if (descripcion_suceso) {
      result = await pool.query(
        `INSERT INTO alertas (id_camara, mensaje, hora_suceso, tipo, score_confianza, descripcion_suceso) VALUES ($1, $2, $3, $4, $5, $6) RETURNING *`,
        [id_camara, mensaje, hora_suceso, tipo, score_confianza, descripcion_suceso]
      );
    } else {
      result = await pool.query(
        `INSERT INTO alertas (id_camara, mensaje, hora_suceso, tipo, score_confianza) VALUES ($1, $2, $3, $4, $5) RETURNING *`,
        [id_camara, mensaje, hora_suceso, tipo, score_confianza]
      );
    }

    const nuevaAlerta = result.rows[0];
    console.log(typeof(frames))
    // 2. Si hay frames, guardarlos en S3 y obtener el key
    if (frames && frames.length > 0) {
      console.log(id_camara)
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
        console.log(response)
        const s3Result = await response.json();

        if (s3Result.success) {
          // 3. Actualizar la alerta con la información de S3
          await pool.query(
            `UPDATE alertas SET clip = $1 WHERE id = $2`,
            [s3Result.s3_info.key, nuevaAlerta.id]
          );

          // Actualizar el objeto de alerta con la info de S3
          nuevaAlerta.s3_key = s3Result.s3_info.key;
          nuevaAlerta.s3_bucket = s3Result.s3_info.bucket;
          nuevaAlerta.frames_count = s3Result.s3_info.frames_count;
          nuevaAlerta.s3_url = s3Result.s3_info.s3_url;
        }
      } catch (s3Error) {
        console.error('Error guardando frames en S3:', s3Error);
        // No guardar la alerta completa si hay error con los frames
        res.status(500).json({ error: s3Error });
      }
    } else{console.log("jaime")}

    // 4. Guardar en Redis y emitir WebSocket
    await redisClient.lPush('alertas', JSON.stringify(nuevaAlerta));
    await redisClient.set(`alerta:${nuevaAlerta.id}`, JSON.stringify(nuevaAlerta));
    await redisClient.sAdd('alertas_no_vistas', nuevaAlerta.id.toString());
    await redisClient.lTrim('alertas', 0, 99);

    io.emit('nueva-alerta', nuevaAlerta);

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
    const result_act = await pool.query( 
      `UPDATE alertas SET estado = $1 WHERE id = $2  
         RETURNING *`,
        [
          0, 
          id_camara
        ]
    );
    const cambioEstado = result_act.rows[0];

    // 3. Guardar en Redis y emitir WebSocket
    await redisClient.lPush('alertas', JSON.stringify(nuevaAlerta));
    await redisClient.set(`alerta:${nuevaAlerta.id}`, JSON.stringify(nuevaAlerta));
    await redisClient.sAdd('alertas_no_vistas', nuevaAlerta.id.toString());
    await redisClient.lTrim('alertas', 0, 99);

    // Emitir evento WebSocket
    io.emit('nueva-alerta', nuevaAlerta);

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
  const alertas = ultimas.map(JSON.parse);
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
    await redisClient.lPush('alertas', nuevasAlertas.map(JSON.stringify));
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

router.get('/estadisticas-totales', verificarToken, verificarRol([1, 2]), async (req, res) => {
  let client;
  try {
    const { dias = 7, fecha_inicio, fecha_fin, group } = req.query;

    let startDate, endDate;

    if (fecha_inicio && fecha_fin) {
      // Usar fechas proporcionadas
      startDate = new Date(fecha_inicio);
      endDate = new Date(fecha_fin);
    } else {
      // Usar el parámetro de días
      endDate = new Date();
      startDate = new Date();
      startDate.setDate(startDate.getDate() - parseInt(dias));
    }

    // Formatear fechas para PostgreSQL
    const fechaInicioStr = startDate.toISOString().replace('T', ' ').substring(0, 19);
    const fechaFinStr = endDate.toISOString().replace('T', ' ').substring(0, 19);

    let grupo; 
    const gruposValidos = ['day', 'week', 'month'];
    grupo = gruposValidos.includes(group) ? group : 'day';

    client = await pool.connect();
    
    //console.log('Fecha inicio:', fechaInicioStr);
    //console.log('Fecha fin:', fechaFinStr);
    //console.log('Grupo:', grupo);
    
    // Llamar a la función de PostgreSQL
    const query = `
      SELECT * FROM reporte_alertas_por_periodo($1, $2, $3)
    `;

    const result = await client.query(query, [fechaInicioStr, fechaFinStr, grupo]);
    //console.log('resultado query;', result)
    if (result.rows.length === 0) {
      return res.json({
        success: true,
        periodo: {
          fecha_inicio: fechaInicioStr,
          fecha_fin: fechaFinStr,
          dias: Math.ceil((endDate - startDate) / (1000 * 60 * 60 * 24))
        },
        estadisticas_totales: {
          total_alertas: 0,
          alertas_confirmadas: 0,
          falsos_positivos: 0,
          merodeos: 0,
          portonazos: 0,
          asaltos_hogar: 0,
          no_especificados: 0,
          tasa_confianza: 0
        },
        sectores: []
      });
    }

    // Calcular totales
    const totales = {
      total_alertas: 0,
      alertas_confirmadas: 0,
      falsos_positivos: 0,
      merodeos: 0,
      portonazos: 0,
      asaltos_hogar: 0,
      no_especificados: 0
    };

    // Agrupar por sector
    const sectores = {};
    
    result.rows.forEach(row => {
      // Totales generales
      totales.total_alertas += parseInt(row.total_alertas) || 0;
      totales.alertas_confirmadas += parseInt(row.alertas_confirmadas) || 0;
      totales.falsos_positivos += parseInt(row.falsos_positivos) || 0;
      totales.merodeos += parseInt(row.merodeos) || 0;
      totales.portonazos += parseInt(row.portonazos) || 0;
      totales.no_especificados += parseInt(row.no_especificados) || 0;
      totales.asaltos_hogar += parseInt(row.asaltos_hogar) || 0;
      // Por sector
      const sectorId = row.id_sector;
      if (!sectores[sectorId]) {
        sectores[sectorId] = {
          id_sector: sectorId,
          nombre_sector: row.nombre_sector,
          total_alertas: 0,
          alertas_confirmadas: 0,
          falsos_positivos: 0,
          merodeos: 0,
          portonazos: 0,
          no_especificados: 0
        };
      }

      sectores[sectorId].total_alertas += parseInt(row.total_alertas) || 0;
      sectores[sectorId].alertas_confirmadas += parseInt(row.alertas_confirmadas) || 0;
      sectores[sectorId].falsos_positivos += parseInt(row.falsos_positivos) || 0;
      sectores[sectorId].merodeos += parseInt(row.merodeos) || 0;
      sectores[sectorId].portonazos += parseInt(row.portonazos) || 0;
      sectores[sectorId].asaltos_hogar += parseInt(row.asaltos_hogar) || 0;
      sectores[sectorId].no_especificados += parseInt(row.no_especificados) || 0;
    });

    // Calcular tasa de confianza
    const tasaConfianza = totales.total_alertas > 0 
      ? Math.round((totales.alertas_confirmadas / totales.total_alertas) * 100 )
      : 0;
    
    // Porcentaje de alertas que son verdaderas positivas (excluye falsos positivos del total)
    const tasaPrecision = totales.total_alertas > 0 
      ? Math.round((totales.alertas_confirmadas / (totales.total_alertas - totales.falsos_positivos)) * 100)
      : 0;

    // Porcentaje de alertas que fueron falsos positivos
    const tasaFalsosPositivos = totales.total_alertas > 0 
      ? Math.round((totales.falsos_positivos / totales.total_alertas) * 100)
      : 0;

    // Métrica compuesta que penaliza falsos positivos
    const scoreCalidad = totales.total_alertas > 0 
      ? Math.round((
          (totales.alertas_confirmadas * 2) - // Doble peso a confirmadas
          totales.falsos_positivos            // Penalización por falsos positivos
        ) / (totales.total_alertas * 2) * 100) // Normalizado a 100
      : 0;

    res.json({
      success: true,
      periodo: {
        fecha_inicio: fechaInicioStr,
        fecha_fin: fechaFinStr,
        dias: Math.ceil((endDate - startDate) / (1000 * 60 * 60 * 24))
      },
      estadisticas_totales: {
        ...totales,
        tasa_confianza: tasaConfianza,
        tasa_precision: tasaPrecision,
        tasa_error: tasaFalsosPositivos,
        score_calidad: scoreCalidad
      },
      sectores: Object.values(sectores)
    });

  } catch (err) {
    console.error('Error en /estadisticas-totales:', err);
    res.status(500).json({ 
      success: false,
      error: 'Error al obtener estadísticas totales',
      detalle: err.message 
    });
  } finally {
    if (client) {
      client.release();
    }
  }
});


export default router