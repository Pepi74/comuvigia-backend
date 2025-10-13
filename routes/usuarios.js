import { Router } from 'express'
import dotenv from 'dotenv'
import bcrypt from 'bcrypt'
import pool from '../config/db.js'
import { verificarToken } from '../middlewares/auth.js'
import { verificarRol } from '../middlewares/roles.js'

dotenv.config()

const router = Router()

// Endpoint para registrar un usuario en BD con contraseña hasheada
router.post('/register', verificarToken, verificarRol([2]), async(req, res) => {

    const { usuario, contrasena, nombre, rol } = req.body

    if (!usuario || !contrasena || !nombre) return res.status(400).json({ error: "Faltan campos obligatorios" })
    
    const hash = await bcrypt.hash(contrasena, 10)
    
    try{
        const result = await pool.query(
          'INSERT INTO usuarios (usuario, contrasena, nombre, rol) VALUES ($1, $2, $3, $4) RETURNING id, usuario, nombre, rol',
          [usuario, hash, nombre, rol]
        )
        res.status(201).json(result.rows[0])
    }
    catch(err){
        console.error(err)
        res.status(500).json({ error: 'Error al registrar usuario' })
    }
})

// Endpoint para obtener un listado de todos los usuarios
router.get('/', verificarToken, verificarRol([2]), async (_, res) => {
  try {
    const result = await pool.query('SELECT id, usuario, nombre, rol FROM usuarios')
    res.json(result.rows)
  } catch (err) {
    console.error(err)
    res.status(500).json({ error: "Error al obtener usuarios" })
  }
})

// Endpoint para obtener un usuario por id
router.get('/:id', verificarToken, verificarRol([1, 2]), async (req, res) => {
  const { id } = req.params
  try {
    const result = await pool.query('SELECT id, usuario, nombre, rol FROM usuarios WHERE id = $1', [id])
    if (result.rows.length === 0) {
      return res.status(404).json({ error: "Usuario no encontrado" })
    }
    res.json(result.rows[0])
  } catch (err) {
    console.error(err)
    res.status(500).json({ error: "Error al obtener usuario" })
  }
})

// Endpoint para obtener un usuario por campo usuario(ya que es unico)
router.get('/usuario', verificarToken, verificarRol([1, 2]), async (req, res) => {
  const usuario = req.body.usuario
  try {
    const result = await pool.query('SELECT id, usuario, nombre, rol FROM usuarios WHERE usuario = $1', [usuario])
    if (result.rows.length === 0) {
      return res.status(404).json({ error: "Usuario no encontrado" })
    }
    res.json(result.rows[0])
  } catch (err) {
    console.error(err)
    res.status(500).json({ error: "Error al obtener usuario" })
  }
})

// Endpoint para modificar los campos de un usuario por id
router.put('/:id', verificarToken, verificarRol([2]), async (req, res) => {
  const { id } = req.params
  const { usuario, contrasena, nombre, rol } = req.body

  try {
    const fields = []
    const values = []
    let index = 1

    if (usuario && usuario.trim() !== '') {
      fields.push(`usuario = $${index++}`)
      values.push(usuario)
    }
    if (contrasena && contrasena.trim() !== '') {
      const hash = await bcrypt.hash(contrasena, 10)
      fields.push(`contrasena = $${index++}`)
      values.push(hash)
    }
    if (nombre && nombre.trim() !== '') {
      fields.push(`nombre = $${index++}`)
      values.push(nombre)
    }
    if (rol) {
      fields.push(`rol = $${index++}`)
      values.push(rol)
    }

    if (fields.length === 0) {
      return res.status(400).json({ error: "No se proporcionaron campos para actualizar" })
    }

    values.push(id)
    
    const query = `
      UPDATE usuarios
      SET ${fields.join(', ')}
      WHERE id = $${index}
      RETURNING id, usuario, nombre, rol
    `

    const result = await pool.query(query, values)
    if (result.rows.length === 0) {
      return res.status(404).json({ error: "Usuario no encontrado" })
    }
    res.json(result.rows[0])
  } catch (err) {
    console.error(err)
    res.status(500).json({ error: "Error al actualizar usuario" })
  }
})

// Endpoint para eliminar usuario por id
router.delete('/:id', verificarToken, verificarRol([2]), async (req, res) => {
  const { id } = req.params
  try {
    const result = await pool.query('DELETE FROM usuarios WHERE id = $1 RETURNING id, usuario', [id])
    if (result.rows.length === 0) {
      return res.status(404).json({ error: "Usuario no encontrado" })
    }
    res.json({ message: "Usuario eliminado" })
  } catch (err) {
    console.error(err)
    res.status(500).json({ error: "Error al eliminar usuario" })
  }
})

export default router