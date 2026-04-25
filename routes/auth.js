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
      { id: user.id, usuario: user.usuario, rol: user.rol, nombre: user.nombre },
      process.env.JWT_SECRET,
      { expiresIn: '2h' } // Modificar si es necesario
    )

    const isProd = process.env.NODE_ENV === 'production'

    res.cookie("token", token, {
      httpOnly: true,
      secure: isProd,
      sameSite: isProd ? "none" : "lax",
      maxAge: 2 * 60 * 60 * 1000 // 2h
    })

    res.json({ usuario: user.usuario, rol: user.rol, nombre: user.nombre })
  } catch (error) {
    console.error('Error al autenticar usuario:', error)
    res.status(500).send('Error del servidor')
  }
})

router.post("/logout", (_, res) => {
  res.clearCookie('token', {
    httpOnly: true,
    secure: true,
    sameSite: 'none',
  })
  res.json({ mensaje: "Sesión cerrada" })
})

router.get('/check', (req, res) => {
  const token = req.cookies?.token
  if (!token) {
    return res.status(401).json({ mensaje: 'No hay sesión activa' })
  }

  try {
    const decoded = jwt.verify(token, process.env.JWT_SECRET)
    res.json({ usuario: decoded.usuario, rol: decoded.rol, nombre: decoded.nombre })
  } catch (err) {
    return res.status(403).json({ mensaje: 'Token inválido o expirado' })
  }
})

export default router