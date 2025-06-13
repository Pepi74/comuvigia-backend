// Endpoints para alertas
import { Router } from 'express'
import pool from '../config/db.js'

const router = Router()

router.get('/', async (_, res) => {
  try {
    const result = await pool.query('SELECT * FROM alertas')
    res.json(result.rows)
  } catch (error) {
    console.error('Error al obtener alertas:', error)
    res.status(500).send('Error en el servidor')
  }
})

export default router