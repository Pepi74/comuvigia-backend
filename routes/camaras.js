// Endpoints para camaras
import { Router } from 'express'
import pool from '../config/db.js'
import { verificarToken } from '../middlewares/auth.js'
import { verificarRol } from '../middlewares/roles.js'
import { io, controlCamera, updateCameraStatus, notifyFlaskCameraUpdate } from '../app.js';
import dotenv from 'dotenv';

dotenv.config();

const router = Router()

//---------- CRUD CÁMARAS ----------

// GET - Obtener todas las cámaras
router.get('/', async (_, res) => {
  try {
    const result = await pool.query('SELECT * FROM camaras ORDER BY id')
    res.json(result.rows)
  } catch (error) {
    console.error('Error al obtener camaras:', error)
    res.status(500).send('Error en el servidor')
  }
})

// PUT - Actualizar estado cámara
router.put('/:id', async (req, res) => {
  try {
    const { id } = req.params
    const { estado } = req.body

    // Verificar si la cámara existe
    const checkResult = await pool.query('SELECT id FROM camaras WHERE id = $1', [id])
    if (checkResult.rows.length === 0) {
      return res.status(404).json({ error: 'Cámara no encontrada' })
    }

    const query = `
      UPDATE camaras SET
        estado_camara = $1
      WHERE id = $2
      RETURNING *
    `
    const values = [
      estado,
      id
    ]

    const result = await pool.query(query, values)
    
    io.emit('estado-camara', {
      cameraId: Number.parseInt(id),
      estado: estado,
      ultima_conexion: new Date().toISOString()
    });
    
     // Enviar comando al servicio de streaming
    controlCamera(id, estado ? 'restart' : 'stop');

    res.json(result.rows[0])
  } catch (error) {
    console.error('Error al actualizar cámara:', error)
    
    if (error.code === '23503') {
      return res.status(400).json({ error: 'El sector especificado no existe' })
    }
    
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

router.get('/cantidad-alertas-fecha', verificarToken, async (req, res) => {
  const { fecha_inicio, fecha_fin } = req.query;

  try {
    if (!fecha_inicio || !fecha_fin) {
      return res.status(400).json({ error: 'Debe especificar fecha_inicio y fecha_fin' });
    }

    // Consulta SQL con filtro de fechas
    const query = `
      SELECT 
        c.id,
        c.nombre,
        c.direccion,
        COUNT(a.id) AS total_alertas
      FROM camaras c
      LEFT JOIN alertas a ON a.id_camara = c.id
        AND a.hora_suceso BETWEEN $1 AND $2
      GROUP BY c.id, c.nombre, c.direccion
      ORDER BY total_alertas DESC
    `;

    const result = await pool.query(query, [fecha_inicio, fecha_fin]);

    const datosConvertidos = result.rows.map(cam => ({
      ...cam,
      total_alertas: Number(cam.total_alertas),
    }));

    res.json(datosConvertidos);
  } catch (err) {
    console.error('Error al obtener cámaras por fecha:', err);
    res.status(500).json({ error: 'Error al obtener cámaras por fecha' });
  }
});


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
router.get('/:id', verificarToken, async (req, res) => {
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
router.post('/', verificarToken, verificarRol([2]), async (req, res) => {
  try {
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
      estado_camara || false,
      ultima_conexion || new Date().toISOString(),
      link_camara,
      link_camara_externo,
      id_sector,
      zona_interes ? JSON.stringify(zona_interes) : '{}'
    ]

    const result = await pool.query(query, values)
    const nuevaCamara = result.rows[0]

    // Actualizamos Link cámara externo
    const updateLinkQuery = `
      UPDATE camaras 
      SET link_camara_externo = $1 
      WHERE id = $2
      RETURNING *
    `
    const updatedResult = await pool.query(updateLinkQuery, [
      process.env.CAMERA_URL + '/video_feed/' + nuevaCamara.id,
      nuevaCamara.id
    ])
    nuevaCamara.link_camara_externo = updatedResult.rows[0].link_camara_externo

    // Notificamos python-stream
    notifyFlaskCameraUpdate('create', nuevaCamara)

    res.status(201).json(nuevaCamara)

  } catch (error) {

    console.error('Error al crear cámara:', error)

    // Manejo de errores de FK
    if (error.code === '23503') { // Foreign key violation
      return res.status(400).json({ error: 'El sector especificado no existe' })
    }
    res.status(500).send('Error en el servidor')

  }
})

// PATCH - Actualizar parcialmente una cámara
router.patch('/:id', verificarToken, verificarRol([2]), async (req, res) => {
  try {
    const { id } = req.params
    const updates = req.body

    console.log('📥 Datos recibidos en PATCH:', updates);
     
    // Verificar si la cámara existe
    const checkResult = await pool.query('SELECT * FROM camaras WHERE id = $1', [id])
    if (checkResult.rows.length === 0) {
      return res.status(404).json({ error: 'Cámara no encontrada' })
    }

    const camaraAnterior = checkResult.rows[0]
    console.log('🔍 estado_camara anterior:', camaraAnterior.estado_camara);
    
    // Construir dinámicamente la consulta UPDATE
    const allowedFields = [
      'nombre', 'posicion', 'direccion', 'estado_camara', 
      'ultima_conexion', 'link_camara', 
      'id_sector', 'zona_interes'
    ]

    const setClauses = []
    const values = []
    let paramCount = 1

    for (const [field, value] of Object.entries(updates)) {
      if (allowedFields.includes(field) && value !== undefined) {
        
        if (field === 'zona_interes') {
          setClauses.push(`${field} = $${paramCount}`)
          if (value === '' || value === null || value === undefined) {
            values.push('{}') // JSON vacío válido
          } else if (typeof value === 'string') {
            // Si es string, verificar si ya es JSON válido
            try {
              JSON.parse(value);
              values.push(value);
            } catch {
              // Si no es JSON válido, convertirlo a JSON
              values.push(JSON.stringify(value));
            }
          } else if (typeof value === 'object') {
            // Si es objeto, convertirlo a JSON string
            values.push(JSON.stringify(value));
          } else {
            // Para otros tipos, convertirlo a JSON
            values.push(JSON.stringify({value: value}));
          }
          
        } else {
          setClauses.push(`${field} = $${paramCount}`)
          values.push(value)
        }
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

    console.log('🔍 Query PATCH:', query);
    console.log('🔍 Values:', values);

    const result = await pool.query(query, values)
    const camaraActualizada = result.rows[0]
    
    try {
      notifyFlaskCameraUpdate('update', camaraActualizada)
    } catch (error) {
      console.error('⚠️ Error notificando a Flask:', error);
    }
    
    io.emit('actualizacion-camaras', await obtenerTodasLasCamaras())
    
    if (updates.estado_camara !== undefined) {
      io.emit('estado-camara', {
        cameraId: Number.parseInt(id),
        estado: updates.estado_camara,
        ultima_conexion: new Date().toISOString()
      })
    }

    res.json(camaraActualizada)
  } catch (error) {
    console.error('Error al actualizar cámara (PATCH):', error)
    
    if (error.code === '23502') {
      return res.status(400).json({ error: 'Campo obligatorio no proporcionado' })
    }
    
    if (error.code === '23503') {
      return res.status(400).json({ error: 'El sector especificado no existe' })
    }
    
    if (error.code === '22P02') {
      return res.status(400).json({ error: 'Formato de datos inválido', details: 'El campo zona_interes debe ser un JSON válido' })
    }
    
    res.status(500).json({ 
      error: 'Error en el servidor',
      details: error.message 
    })
  }
})
// Función auxiliar para obtener todas las cámaras
async function obtenerTodasLasCamaras() {
  try {
    const result = await pool.query('SELECT * FROM camaras ORDER BY id')
    return result.rows
  } catch (error) {
    console.error('Error obteniendo cámaras:', error)
    return []
  }
}

// PUT - Actualizar cámara
router.put('/:id', verificarToken, verificarRol([2]), async (req, res) => {
  try {
    const { id } = req.params
    const {
      nombre,
      posicion,
      direccion,
      estado_camara,
      ultima_conexion,
      link_camara,
      id_sector,
      zona_interes
    } = req.body

    // Verificar si la cámara existe
    const checkResult = await pool.query('SELECT * FROM camaras WHERE id = $1', [id])
    if (checkResult.rows.length === 0) {
      return res.status(404).json({ error: 'Cámara no encontrada' })
    }

    const camaraAnterior = checkResult.rows[0]
    console.log('🔍 estado_camara anterior:', camaraAnterior.estado_camara);
    const ultimoIdCamara = camaraAnterior.id + 1;

    const estadoFinal = (estado_camara !== undefined && estado_camara !== null) 
      ? Boolean(estado_camara) 
      : camaraAnterior.estado_camara;

    const query = `
      UPDATE camaras SET
        nombre = $1,
        posicion = $2,
        direccion = $3,
        estado_camara = $4,
        ultima_conexion = $5,
        link_camara = $6,
        id_sector = $7,
        zona_interes = $8
      WHERE id = $9
      RETURNING *
    `

    const values = [
      nombre,
      posicion,
      direccion,
      estadoFinal,
      ultima_conexion || new Date().toISOString(),
      link_camara,
      id_sector,
      zona_interes,
      id
    ]

    const result = await pool.query(query, values)
    const camaraActualizada = result.rows[0]
    
    notifyFlaskCameraUpdate('update', camaraActualizada)
    
    res.json(camaraActualizada)
  } catch (error) {
    console.error('Error al actualizar cámara:', error)
    
    if (error.code === '23503') {
      return res.status(400).json({ error: 'El sector especificado no existe' })
    }
    
    res.status(500).send('Error en el servidor')
  }
})

// DELETE - Eliminar cámara
router.delete('/:id', verificarToken, verificarRol([2]), async (req, res) => {
  try {
    const { id } = req.params

    const camaraResult = await pool.query('SELECT * FROM camaras WHERE id = $1', [id])
    if (camaraResult.rows.length === 0) {
      return res.status(404).json({ error: 'Cámara no encontrada' })
    }

    const camaraAEliminar = camaraResult.rows[0]

    // Eliminamos alertas de la cámara (Luego habría que borrar videos del bucket)
    const result0 = await pool.query('SELECT * FROM alertas WHERE id_camara = $1', [id])
    if (result0.rows.length > 0) {
      const result1 = await pool.query('DELETE FROM alertas WHERE id_camara = $1 RETURNING *', [id])
      if (result1.rows.length === 0) {
        return res.status(404).json({ error: 'Alertas no eliminadas' })
      }
    }

    const result = await pool.query('DELETE FROM camaras WHERE id = $1 RETURNING *', [id])
    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Cámara no encontrada' })
    }

    notifyFlaskCameraUpdate('delete', camaraAEliminar)

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