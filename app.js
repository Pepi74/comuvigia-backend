import express, { json } from 'express'
import cors from 'cors'
import dotenv from 'dotenv'
import indexRoutes from './routes/index.js'
import camarasRoutes from './routes/camaras.js'
import alertasRoutes from './routes/alertas.js'
import tranmisionRoutes from './routes/transmision.js'

dotenv.config()

const app = express()

app.use(cors())
app.use(json())

app.use('/', indexRoutes)
app.use('/api/camaras', camarasRoutes)
app.use('/api/alertas', alertasRoutes)
app.use('/api/transmision', tranmisionRoutes)

const PORT = process.env.PORT
app.listen(PORT, () => {
  console.log(`Servidor escuchando en puerto ${PORT}`)
})
