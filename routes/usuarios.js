import { Router } from 'express'
import dotenv from 'dotenv'
import bcrypt from 'bcrypt'
import pool from '../config/db.js'
import { verificarToken } from '../middlewares/auth.js'
import { verificarRol } from '../middlewares/roles.js'

dotenv.config()

const router = Router()

// Endpoint para registrar un usuario en BD con contraseña hasheada
router.post('/register', async(req, res) => {

    const { usuario, contrasena, nombre } = req.body

    if (!usuario || !contrasena || !nombre) return res.status(400).json({ error: "Faltan campos obligatorios" })
    
    const hash = await bcrypt.hash(contrasena, 10)
    
    try{
        await pool.query(
          'INSERT INTO usuarios (usuario, contrasena, nombre) VALUES ($1, $2, $3)',
          [usuario, hash, nombre]
        )
         res.status(201).json({ message: 'Usuario creado con éxito' })
    }
    catch(err){
        console.error(err)
        res.status(500).json({ error: 'Error al registrar usuario' })
    }
})

// Endpoint para obtener un listado de todos los usuarios
router.get('/usuarios', verificarToken, verificarRol([2]), async (_, res) => {
  try {
    const result = await pool.query('SELECT * FROM usuarios')
    res.json(result.rows)
  } catch (err) {
    console.error(err)
    res.status(500).json({ error: "Error al obtener usuarios" })
  }
})

// Endpoint para obtener un usuario por id
router.get('/usuarios/:id', verificarToken, verificarRol([1, 2]), async (req, res) => {
  const { id } = req.params
  try {
    const result = await pool.query('SELECT * FROM usuarios WHERE id = $1', [id])
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
router.get('/usuarios', verificarToken, verificarRol([1, 2]), async (req, res) => {
  const usuario = req.body.usuario
  try {
    const result = await pool.query('SELECT * FROM usuarios WHERE id = $1', [usuario])
    if (result.rows.length === 0) {
      return res.status(404).json({ error: "Usuario no encontrado" })
    }
    res.json(result.rows[0])
  } catch (err) {
    console.error(err)
    res.status(500).json({ error: "Error al obtener usuario" })
  }
})

// Endpoint para modificar el rol de un usuario por id
router.put('/usuarios/:id', verificarToken, verificarRol([2]), async (req, res) => {
  const { id } = req.params
  const rol = req.body.rol

  try {
    const result = await pool.query(
      'UPDATE usuarios SET rol = $1 WHERE id = $2 RETURNING id, rol',
      [rol, id]
    )
    if (result.rows.length === 0) {
      return res.status(404).json({ error: "Usuario no encontrado" })
    }
    res.json({ message: "Rol de Usuario actualizado", usuario: result.rows[0] })
  } catch (err) {
    console.error(err)
    res.status(500).json({ error: "Error al actualizar usuario" })
  }
})

// Endpoint para eliminar usuario por id
router.delete('/usuarios/:id', verificarToken, verificarRol([2]), async (req, res) => {
  const { id } = req.params
  try {
    const result = await pool.query('DELETE FROM usuarios WHERE id = $1 RETURNING id', [id])
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