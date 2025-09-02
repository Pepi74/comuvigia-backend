import express, { json } from 'express'
import cors from 'cors'
import dotenv from 'dotenv'
import { createServer } from 'http'
import { Server } from 'socket.io'
import indexRoutes from './routes/index.js'
import camarasRoutes from './routes/camaras.js'
import alertasRoutes from './routes/alertas.js'
import tranmisionRoutes from './routes/transmision.js'

dotenv.config()

const app = express()
const httpServer = createServer(app); // Servidor HTTP base
const io = new Server(httpServer, {
  cors: {
    //origin: `${process.env.FRONTEND_URL}`,
    origin: '*',
    credentials: true
  },
});

// Exportamos `io` para usarlo en otras partes
export { io };

app.use(cors())
app.use(json())

app.use('/', indexRoutes)
app.use('/api/camaras', camarasRoutes)
app.use('/api/alertas', alertasRoutes)
app.use('/api/transmision', tranmisionRoutes)

// WebSocket: manejar conexiones entrantes
io.on('connection', (socket) => {
  //console.log('Cliente conectado vía WebSocket');

  socket.on('disconnect', () => {
    //console.log('Cliente desconectado');
  });
});

const PORT = process.env.PORT
httpServer.listen(PORT, () => {
  console.log(`Servidor escuchando en puerto ${PORT}`);
});
