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

export default router