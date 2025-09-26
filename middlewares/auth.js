import jwt from 'jsonwebtoken'

// Middleware usado en endpoints para agregar verificacion de JWT a rutas
export function verificarToken(req, res, next) {
  const token = req.headers.authorization?.split(' ')[1]
  if (!token) return res.status(401).json({ mensaje: 'Operación no autorizada' }) // Cambiar mensaje si es necesario

  try {
    const decoded = jwt.verify(token, process.env.JWT_SECRET)
    req.user = decoded
    next()
  } catch (err) {
    return res.status(403).json({ mensaje: 'Token inválido' }) // Cambiar mensaje si es necesario
  }
}