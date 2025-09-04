// Endpoints para camaras
import { Router } from 'express'
import pool from '../config/db.js'

const router = Router()

router.get('/', async (_, res) => {
  try {
    const result = await pool.query('SELECT * FROM camaras')
        const extraCameras = [
      {
        id: 9991,
        nombre: "Merodeo",
        posicion: [-33.51, -70.603],
        direccion: "Demo - Santiago Centro",
        estado_camara: true,
        ultima_conexion: "2025-08-28T12:00:00Z",
        link_camara: "/loitering.mp4",
        link_camara_externo: ""
      },
      {
        id: 9992,
        nombre: "Asalto a Hogar",
        posicion: [-33.53, -70.603],
        direccion: "Demo - Providencia",
        estado_camara: true,
        ultima_conexion: "2025-08-28T12:00:00Z",
        link_camara: "/burglary.mp4",
        link_camara_externo: ""
      },
      {
        id: 9993,
        nombre: "Portonazo",
        posicion: [-33.52, -70.61],
        direccion: "Demo - Ñuñoa",
        estado_camara: true,
        ultima_conexion: "2025-08-28T12:00:00Z",
        link_camara: "/portonazo.mp4",
        link_camara_externo: ""
      }

      
    ];

    const camerasWithExtras = [...result.rows, ...extraCameras];
    console.log(camerasWithExtras);
    res.json(camerasWithExtras)
  } catch (error) {
    console.error('Error al obtener camaras:', error)
    res.status(500).send('Error en el servidor')
  }
})

router.get('/cantidad-alertas', async (req, res) => {
  try {
    const result = await pool.query('SELECT * FROM camaras_con_alertas');
    // Convierte los BigInt de string a enteros
    const datosConvertidos = result.rows.map(cam => ({
      ...cam,
      total_alertas: Number(cam.total_alertas),
    }));

            const extraCameras = [
      {
        id: 9991,
        nombre: "Merodeo",
        posicion: [-33.51, -70.603],
        direccion: "Demo - Santiago Centro",
        estado_camara: true,
        ultima_conexion: "2025-08-28T12:00:00Z",
        link_camara: "/loitering.mp4",
        link_camara_externo: "",
        total_alertas: 0,
        id_sector: 1,
        zona_interes: "Zona Demo"
      },
      {
        id: 9992,
        nombre: "Asalto a Hogar",
        posicion: [-33.53, -70.603],
        direccion: "Demo - Providencia",
        estado_camara: true,
        ultima_conexion: "2025-08-28T12:00:00Z",
        link_camara: "/burglary.mp4",
        link_camara_externo: "",
        total_alertas: 0,
        id_sector: 1,
        zona_interes: "Zona Demo"
      },
      {
        id: 9993,
        nombre: "Portonazo",
        posicion: [-33.52, -70.61],
        direccion: "Demo - Ñuñoa",
        estado_camara: true,
        ultima_conexion: "2025-08-28T12:00:00Z",
        link_camara: "/portonazo.mp4",
        link_camara_externo: "",
        total_alertas: 0,
        id_sector: 1,
        zona_interes: "Zona Demo"
      }

      
    ];

    const camerasWithExtras = [...datosConvertidos, ...extraCameras];
    console.log(camerasWithExtras);
    res.json(camerasWithExtras);
  } catch (err) {
    console.error('Error al obtener cámaras con alertas:', err);
    res.status(500).json({ error: 'Error al obtener cámaras con alertas' });
  }
})

router.get('/nombre-camaras', async (_, res) => {
  try {
    const result = await pool.query('SELECT id, nombre FROM camaras');
    const cameraMap = {};
    result.rows.forEach(row => {
      cameraMap[row.id] = row.nombre;
    });
    res.json(cameraMap);
  } catch (err) {
    console.error(err);
    res.status(500).send('Error servidor');
  }
})

export default router