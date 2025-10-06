import { Router } from 'express'
import dotenv from 'dotenv'
import pool from '../config/db.js'
import { verificarToken } from '../middlewares/auth.js';
import { verificarRol } from '../middlewares/roles.js';

dotenv.config()

const router = Router()

// Endpoint para registrar un usuario en BD con contraseña hasheada
router.get('/obtener', verificarToken, verificarRol([2]), async(req, res) => {
  try {
    const result = await pool.query('SELECT * FROM reglas');
    const reglas = result.rows.map(regla => ({
        id: Number(regla.id), 
        riesgo: regla.riesgo,
        tipoAlerta: regla.tipoalerta ? regla.tipoalerta.split(',').map(t => t.trim()) : [],
        horaInicio: regla.horainicio,
        horaFin: regla.horafin,
        score: Number(regla.score),
        sector: regla.sector
    }));
    res.json(reglas);
  } catch (err) {
    console.error('Error al obtener cámaras con alertas:', err);
    res.status(500).json({ error: 'Error al obtener cámaras con alertas' });
  }
  }
);

router.post('/actualizar', verificarToken, verificarRol([2]), async (req, res) => {
  try {
    const reglas = req.body; // arreglo de objetos RulesType con id incluido

    const client = await pool.connect();
    try {
      await client.query('BEGIN');

      for (const regla of reglas) {
        const { id, riesgo, tipoAlerta, horaInicio, horaFin, score, sector } = regla;
        await client.query(
          `UPDATE reglas
           SET riesgo = $1,
               tipoAlerta = $2,
               horaInicio = $3,
               horaFin = $4,
               score = $5,
               sector = $6
           WHERE id = $7`,
          [riesgo, tipoAlerta.join(','), horaInicio, horaFin, score, sector, id]
        );
      }

      await client.query('COMMIT');
      res.json({ mensaje: 'Reglas actualizadas correctamente' });
    } catch (err) {
      await client.query('ROLLBACK');
      throw err;
    } finally {
      client.release();
    }
  } catch (err) {
    console.error('Error al actualizar reglas:', err);
    res.status(500).json({ error: 'Error al actualizar reglas' });
  }
});

router.post('/insertar', verificarToken, verificarRol([2]), async (req, res) => {
  try {
    const { riesgo, tipoAlerta, horaInicio, horaFin, score, sector } = req.body;

    if (!riesgo || !horaInicio || !horaFin || score == null) {
      return res.status(400).json({ error: 'Faltan campos obligatorios' });
    }

    const result = await pool.query(
      `INSERT INTO reglas (riesgo, tipoAlerta, horaInicio, horaFin, score, sector)
       VALUES ($1, $2, $3, $4, $5, $6)
       RETURNING id`,
      [
        riesgo,
        Array.isArray(tipoAlerta) ? tipoAlerta.join(',') : tipoAlerta || '',
        horaInicio,
        horaFin,
        score,
        sector || ''
      ]
    );
    res.status(201).json({ id: result.rows[0].id });
  } catch (err) {
    console.error('Error al insertar regla:', err);
    res.status(500).json({ error: 'Error al insertar regla' });
  }
});
