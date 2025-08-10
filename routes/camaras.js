// Endpoints para camaras
import { Router } from 'express'
import pool from '../config/db.js'

const router = Router()

router.get('/', async (_, res) => {
  try {
    const result = await pool.query('SELECT * FROM camaras')
    res.json(result.rows)
  } catch (error) {
    console.error('Error al obtener camaras:', error)
    res.status(500).send('Error en el servidor')
  }
})

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