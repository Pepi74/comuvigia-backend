export async function crearAlertaBase({
  alerta,
  pool,
  redisClient,
  io
}) {
  const {
    id_camara,
    mensaje,
    hora_suceso,
    tipo,
    score_confianza,
    descripcion_suceso,
    estado
  } = alerta;

  if (!id_camara || !mensaje || !tipo) {
    throw new Error('Datos de alerta incompletos');
  }

  let result;

  if (estado !== undefined && estado !== null) {
    const status = Number.parseInt(estado);
    if (descripcion_suceso) {
      result = await pool.query(
        `INSERT INTO alertas 
         (id_camara, mensaje, hora_suceso, tipo, score_confianza, descripcion_suceso, estado)
         VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *`,
        [id_camara, mensaje, hora_suceso, tipo, score_confianza, descripcion_suceso, status]
      );
    } else {
      result = await pool.query(
        `INSERT INTO alertas 
         (id_camara, mensaje, hora_suceso, tipo, score_confianza, estado)
         VALUES ($1,$2,$3,$4,$5,$6) RETURNING *`,
        [id_camara, mensaje, hora_suceso, tipo, score_confianza, status]
      );
    }
  } else {
    result = await pool.query(
      `INSERT INTO alertas 
       (id_camara, mensaje, hora_suceso, tipo, score_confianza, descripcion_suceso)
       VALUES ($1,$2,$3,$4,$5,$6) RETURNING *`,
      [id_camara, mensaje, hora_suceso, tipo, score_confianza, descripcion_suceso]
    );
  }

  const nuevaAlerta = result.rows[0];

  // Obtener sector
  const sector = await pool.query(
    'SELECT id_sector FROM camaras WHERE id = $1',
    [id_camara]
  );

  nuevaAlerta.id_sector = sector.rows[0]?.id_sector;

  // Redis + WebSocket
  await redisClient.lPush('alertas', JSON.stringify(nuevaAlerta));
  await redisClient.set(`alerta:${nuevaAlerta.id}`, JSON.stringify(nuevaAlerta));

  if (nuevaAlerta.estado === 0) {
    await redisClient.sAdd('alertas_no_vistas', nuevaAlerta.id.toString());
  }

  await redisClient.lTrim('alertas', 0, 99);
  io.emit('nueva-alerta', nuevaAlerta);

  return nuevaAlerta;
}