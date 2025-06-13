// Endpoints para alertas
import { Router } from 'express'
import pool from '../config/db.js'

const router = Router()

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