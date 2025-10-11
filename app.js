import express, { json, urlencoded } from 'express'
import cors from 'cors'
import dotenv from 'dotenv'
import { createServer } from 'http'
import { Server } from 'socket.io'
import indexRoutes from './routes/index.js'
import camarasRoutes from './routes/camaras.js'
import alertasRoutes from './routes/alertas.js'
import tranmisionRoutes from './routes/transmision.js'
import authRoutes from './routes/auth.js'
import userRoutes from './routes/usuarios.js'
import sectoresRoutes from './routes/sectores.js';
import reglasRoutes from './routes/reglas.js'
import informeRoutes from './routes/informe.js';
import cookieParser from 'cookie-parser'

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
export { io, controlCamera, updateCameraStatus };

app.use(cors({
  origin: process.env.FRONTEND_URL,
  credentials: true
}))
app.use(cookieParser())

app.use(json({ limit: '50mb' }))
app.use(urlencoded({ limit: '50mb', extended: true }))
app.use('/', indexRoutes)
app.use('/api/camaras', camarasRoutes)
app.use('/api/alertas', alertasRoutes)
app.use('/api/transmision', tranmisionRoutes)
app.use('/api/auth', authRoutes)
app.use('/api/usuarios', userRoutes)
app.use('/api/sectores', sectoresRoutes)
app.use('/api/reglas', reglasRoutes)
app.use('/api/informe', informeRoutes)

// WebSocket: manejar conexiones entrantes
io.on('connection', (socket) => {
  console.log('Cliente conectado vía Socket.IO:', socket.id);

  // Escuchar eventos del servicio de streaming
  socket.on('streaming_status', (data) => {
    console.log('Estado de streaming recibido:', data);
    // Aquí puedes procesar el estado de las cámaras
  });

  socket.on('camera_status', (data) => {
    console.log('Estado de cámara recibido:', data);
    io.emit('estado-camara', {
      cameraId: data.camera_id,
      estado: data.status === 'active',
      ultima_conexion: new Date().toISOString(),
      desdeStreaming: true
    });
  });

  // Función para controlar cámaras desde el backend
  socket.on('control_camera', (data) => {
    const { camera_id, action, params } = data;
    console.log(`Controlando cámara ${camera_id}: ${action}`);
    
    io.emit('camera_control', {
      camera_id: camera_id,
      action: action,
      params: params
    });
  });

  socket.on('disconnect', () => {
    console.log('Cliente desconectado:', socket.id);
  });
});

// Función para enviar comandos a cámaras específicas
function controlCamera(cameraId, action, params = {}) {
  io.emit('camera_control', {
    camera_id: cameraId,
    action: action,
    params: params,
    timestamp: Date.now()
  });
}

// Función para actualizar estado de cámara
function updateCameraStatus(cameraId, status, config = null) {
  io.emit('camera_status_update', {
    camera_id: cameraId,
    status: status,
    config: config,
    timestamp: Date.now()
  });
}

const PORT = process.env.PORT
httpServer.listen(PORT, () => {
  console.log(`Servidor escuchando en puerto ${PORT}`);
});
