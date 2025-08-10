// Endpoints para camaras
import { Router } from 'express'
import pool from '../config/db.js'

const router = Router()

/*
En las camaras, se asume que se tiene creada una base de datos Postgresql en donde existe la tabla definida de la siguiente forma:
CREATE TABLE camaras (
    id SERIAL PRIMARY KEY, -- Puede ser definida como id_camara
    nombre TEXT NOT NULL,
    posicion DOUBLE PRECISION[] NOT NULL, -- Arreglo con valores latitud y longitud.
    direccion TEXT NOT NULL,
    estado_camara BOOLEAN NOT NULL DEFAULT TRUE,
    ultima_conexion TIMESTAMP NOT NULL,
    link_camara TEXT DEFAULT '' -- Opcional
);
*/

router.get('/', async (_, res) => {
  try {
    const result = await pool.query('SELECT * FROM camaras')
    res.json(result.rows)
  } catch (error) {
    console.error('Error al obtener camaras:', error)
    res.status(500).send('Error en el servidor')
  }
})

/* Para este endpoint se asume que se tiene creado una vista en la base de datos de la siguiente forma:
CREATE OR REPLACE VIEW camaras_con_alertas AS
SELECT
    c.id,
    c.nombre,
    c.posicion,
    c.direccion,
    c.estado_camara,
    c.ultima_conexion,
    c.link_camara,
    COUNT(a.id) AS total_alertas
FROM
    camaras c
LEFT JOIN
    alertas a ON c.id = a.id_camara
GROUP BY
    c.id, c.nombre, c.posicion, c.direccion, c.estado_camara, c.ultima_conexion, c.link_camara;
*/
router.get('/cantidad-alertas', async (req, res) => {
  try {
    const result = await pool.query('SELECT * FROM camaras_con_alertas');
    // Convierte los BigInt de string a enteros
    const datosConvertidos = result.rows.map(cam => ({
      ...cam,
      total_alertas: Number(cam.total_alertas),
    }));
    res.json(datosConvertidos);
  } catch (err) {
    console.error('Error al obtener cámaras con alertas:', err);
    res.status(500).json({ error: 'Error al obtener cámaras con alertas' });
  }
})

export default router