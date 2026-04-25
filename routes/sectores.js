import { Router } from 'express';
import pool from '../config/db.js';
import { verificarToken } from '../middlewares/auth.js';
import { verificarRol } from '../middlewares/roles.js';

const router = Router();

// GET sectores con conteo de alertas en un rango de fechas
// Si no les funciona borren las verificaciones
router.get('/', verificarToken, verificarRol([1, 2]), async (req, res) => {
  try {
    const { fecha_inicio, fecha_fin } = req.query;

    if (!fecha_inicio || !fecha_fin) {
      return res.status(400).json({ error: "Debe proporcionar fecha_inicio y fecha_fin" });
    }

    console.log("🔍 Fechas recibidas en API:", { fecha_inicio, fecha_fin });

    const result = await pool.query(
      `SELECT 
         s.id,
         s.nombre_sector,
         s.descripcion,
         s.coordinates,
         COUNT(a.id) AS total_alertas
       FROM sectores s
       LEFT JOIN camaras c ON c.id_sector = s.id
       LEFT JOIN alertas a ON a.id_camara = c.id 
         AND a.hora_suceso >= $1::timestamp  -- Condición en el JOIN, no en WHERE
         AND a.hora_suceso <= $2::timestamp
       GROUP BY s.id, s.nombre_sector, s.descripcion, s.coordinates
       ORDER BY s.id;`,
      [fecha_inicio, fecha_fin]
    );

    console.log(`✅ Encontrados ${result.rows.length} sectores (incluyendo sin alertas)`);
    res.json(result.rows);
  } catch (error) {
    console.error("❌ Error al obtener sectores por rango:", error);
    res.status(500).json({ error: "Error al obtener sectores por rango de fechas" });
  }
});

// GET sectores con todas las alertas existentes
router.get('/alltime', verificarToken, verificarRol([1, 2]), async (_, res) => {
  try {
    const result = await pool.query(`
      SELECT s.id, s.nombre_sector, s.descripcion, s.coordinates,
             COALESCE(SUM(ca.total_alertas), 0) AS total_alertas
      FROM sectores s
      LEFT JOIN camaras_con_alertas ca ON ca.id_sector = s.id
      GROUP BY s.id, s.nombre_sector, s.descripcion, s.coordinates
      ORDER BY s.id
    `);
    res.json(result.rows);
  } catch (error) {
    console.error('Error al obtener sectores:', error);
    res.status(500).json({ error: 'Error al obtener sectores' });
  }
});

// Crear un nuevo sector
router.post('/', verificarToken, verificarRol([2]), async (req, res) => {
  try {
    const { nombre, descripcion, coordinates } = req.body;
    const result = await pool.query(
      `INSERT INTO sectores (nombre_sector, descripcion, coordinates)
       VALUES ($1, $2, $3)
       RETURNING *`,
      [nombre, descripcion, JSON.stringify(coordinates)]
    );
    res.status(201).json(result.rows[0]);
  } catch (error) {
    console.error('Error al crear sector:', error);
    res.status(500).json({ error: 'Error al crear sector' });
  }
});

export default router;
