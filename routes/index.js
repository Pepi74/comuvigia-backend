import { Router } from 'express'

const router = Router()

router.get('/', (req, res) => {
  res.send('¡Hola mundo desde el backend!')
})

export default router