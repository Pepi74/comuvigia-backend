// Endpoints para camaras
import { Router } from 'express'
import pool from '../config/db.js'
import { verificarToken } from '../middlewares/auth.js'
import { verificarRol } from '../middlewares/roles.js'

const router = Router()

//---------- CRUD CÁMARAS ----------

// GET - Obtener todas las cámaras
router.get('/', verificarToken, async (_, res) => {
  try {
    const result = await pool.query('SELECT * FROM camaras ORDER BY id')
    res.json(result.rows)
  } catch (error) {
    console.error('Error al obtener camaras:', error)
    res.status(500).send('Error en el servidor')
  }
})

router.get('/cantidad-alertas', verificarToken, async (_, res) => {
  try {
    const result = await pool.query('SELECT * FROM camaras_con_alertas');
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

router.get('/nombre-camaras', verificarToken, async (_, res) => {
  try {
    const result = await pool.query('SELECT id, nombre FROM camaras');
    const cameraMap = {};
    result.rows.forEach(row => {
      cameraMap[row.id] = row.nombre;
    });
    res.json(cameraMap);
  } catch (err) {
    console.error(err);
    res.status(500).send('Error servidor');
  }
})

// GET - Obtener cámara
router.get('/:id', async (req, res) => {
  try {
    const { id } = req.params
    const result = await pool.query('SELECT * FROM camaras WHERE id = $1', [id])
    
    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Cámara no encontrada' })
    }
    
    res.json(result.rows[0])
  } catch (error) {
    console.error('Error al obtener cámara:', error)
    res.status(500).send('Error en el servidor')
  }
})

// POST - Crear nueva cámara
router.post('/', async (req, res) => {
  try {
    const {
      nombre,
      posicion,
      direccion,
      estado_camara = true,
      ultima_conexion,
      link_camara = '',
      link_camara_externo = '',
      id_sector,
      zona_interes = ''
    } = req.body

    // Validaciones básicas
    if (!nombre || !posicion || !direccion || !ultima_conexion) {
      return res.status(400).json({ 
        error: 'Los campos nombre, posicion, direccion y ultima_conexion son obligatorios' 
      })
    }

    const query = `
      INSERT INTO camaras (
        nombre, posicion, direccion, estado_camara, ultima_conexion,
        link_camara, link_camara_externo, id_sector, zona_interes
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
      RETURNING *
    `

    const values = [
      nombre,
      posicion,
      direccion,
      estado_camara,
      ultima_conexion,
      link_camara,
      link_camara_externo,
      id_sector,
      zona_interes
    ]

    const result = await pool.query(query, values)
    res.status(201).json(result.rows[0])
  } catch (error) {
    console.error('Error al crear cámara:', error)
    
    // Manejo de errores de FK
    if (error.code === '23503') { // Foreign key violation
      return res.status(400).json({ error: 'El sector especificado no existe' })
    }
    
    res.status(500).send('Error en el servidor')
  }
})

// PUT - Actualizar cámara
router.put('/:id', async (req, res) => {
  try {
    const { id } = req.params
    const {
      nombre,
      posicion,
      direccion,
      estado_camara,
      ultima_conexion,
      link_camara,
      link_camara_externo,
      id_sector,
      zona_interes
    } = req.body

    // Verificar si la cámara existe
    const checkResult = await pool.query('SELECT id FROM camaras WHERE id = $1', [id])
    if (checkResult.rows.length === 0) {
      return res.status(404).json({ error: 'Cámara no encontrada' })
    }

    const query = `
      UPDATE camaras SET
        nombre = $1,
        posicion = $2,
        direccion = $3,
        estado_camara = $4,
        ultima_conexion = $5,
        link_camara = $6,
        link_camara_externo = $7,
        id_sector = $8,
        zona_interes = $9
      WHERE id = $10
      RETURNING *
    `

    const values = [
      nombre,
      posicion,
      direccion,
      estado_camara,
      ultima_conexion,
      link_camara,
      link_camara_externo,
      id_sector,
      zona_interes,
      id
    ]

    const result = await pool.query(query, values)
    res.json(result.rows[0])
  } catch (error) {
    console.error('Error al actualizar cámara:', error)
    
    if (error.code === '23503') {
      return res.status(400).json({ error: 'El sector especificado no existe' })
    }
    
    res.status(500).send('Error en el servidor')
  }
})

// PATCH - Actualizar parcialmente una cámara
router.patch('/:id', async (req, res) => {
  try {
    const { id } = req.params
    const updates = req.body

    // Verificar si la cámara existe
    const checkResult = await pool.query('SELECT id FROM camaras WHERE id = $1', [id])
    if (checkResult.rows.length === 0) {
      return res.status(404).json({ error: 'Cámara no encontrada' })
    }

    // Construir dinámicamente la consulta UPDATE
    const allowedFields = [
      'nombre', 'posicion', 'direccion', 'estado_camara', 
      'ultima_conexion', 'link_camara', 'link_camara_externo', 
      'id_sector', 'zona_interes'
    ]

    const setClauses = []
    const values = []
    let paramCount = 1

    for (const [field, value] of Object.entries(updates)) {
      if (allowedFields.includes(field)) {
        setClauses.push(`${field} = $${paramCount}`)
        values.push(value)
        paramCount++
      }
    }

    if (setClauses.length === 0) {
      return res.status(400).json({ error: 'No se proporcionaron campos válidos para actualizar' })
    }

    values.push(id)
    const query = `
      UPDATE camaras 
      SET ${setClauses.join(', ')}
      WHERE id = $${paramCount}
      RETURNING *
    `

    const result = await pool.query(query, values)
    res.json(result.rows[0])
  } catch (error) {
    console.error('Error al actualizar cámara:', error)
    
    if (error.code === '23503') {
      return res.status(400).json({ error: 'El sector especificado no existe' })
    }
    
    res.status(500).send('Error en el servidor')
  }
})

// DELETE - Eliminar cámara
router.delete('/:id', async (req, res) => {
  try {
    const { id } = req.params

    const result = await pool.query('DELETE FROM camaras WHERE id = $1 RETURNING *', [id])
    
    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Cámara no encontrada' })
    }

    res.json({ message: 'Cámara eliminada correctamente', cámara: result.rows[0] })
  } catch (error) {
    console.error('Error al eliminar cámara:', error)
    
    // Manejo de errores de integridad referencial
    if (error.code === '23503') {
      return res.status(400).json({ 
        error: 'No se puede eliminar la cámara porque tiene registros relacionados' 
      })
    }
    
    res.status(500).send('Error en el servidor')
  }
})

export default router