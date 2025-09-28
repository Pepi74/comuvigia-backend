import { Router } from 'express'
import jwt from 'jsonwebtoken'
import dotenv from 'dotenv'
import bcrypt from 'bcrypt'
import pool from '../config/db.js'

dotenv.config()

const router = Router()

// Ruta de login, verifica si existe usuario en BD y asigna un token JWT
router.post('/login', async (req, res) => {
  const { usuario, contrasena } = req.body

  try {
    const result = await pool.query(
      'SELECT * FROM usuarios WHERE usuario = $1',
      [usuario]
    )

    const user = result.rows[0]

    if (!user) {
      return res.status(401).json({ mensaje: 'Usuario no encontrado' })
    }

    const passwordOk = await bcrypt.compare(contrasena, user.contrasena)

    if (!passwordOk) {
      return res.status(401).json({ mensaje: 'Contraseña incorrecta' })
    }

    const token = jwt.sign(
      { id: user.id, usuario: user.usuario, rol: user.rol },
      process.env.JWT_SECRET,
      { expiresIn: '2h' } // Modificar si es necesario
    )

    res.json({ token, usuario: user.usuario, rol: user.rol, nombre: user.nombre })
  } catch (error) {
    console.error('Error al autenticar usuario:', error)
    res.status(500).send('Error del servidor')
  }
})

export default router