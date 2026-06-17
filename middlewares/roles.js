// Midddleware de verificacion de rol de usuarios, recibe la lista rolesPermitidos, ejemplo: [0, 1]
export function verificarRol(rolesPermitidos) {
  return (req, res, next) => {
    const rolUsuario = req.user?.rol
    if (!rolesPermitidos.includes(rolUsuario)) {
      return res.status(403).json({ mensaje: 'Acceso denegado: no tienes permisos suficientes' }) // Cambiar mensaje si es necesario
    }
    next()
  }
}