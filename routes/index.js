// Endpoint de prueba
import { Router } from 'express'

const router = Router()

router.get('/', (_, res) => {
  res.send('¡Hola mundo desde el backend!')
})

export default router