// Endpoint de prueba
import { Router } from 'express'
const router = Router()
import dotenv from 'dotenv';
import { config } from 'dotenv';

dotenv.config();

console.log(process.env.IA_URL);

router.get('/', (_, res) => {
  res.send('¡Hola mundo desde el backend!')
})

router.post('/casos_prueba', async (req, res) => {
  const { delito } = req.query;
  
  // Validación de entrada
  if (!delito || typeof delito !== 'string' || delito.trim() === '') {
    return res.status(400).json({ 
      error: 'El parámetro "delito" es requerido y debe ser una cadena no vacía' 
    });
  }

  const trimmedDelito = delito.trim();
  const timeout = 30000; // 30 segundos de timeout
  const maxRetries = 3;
  let retryCount = 0;

  const makeRequest = async () => {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);
    console.log(process.env.IA_URL +'/api/casos_prueba')

    try {
      const response = await fetch(process.env.IA_URL+'/api/casos_prueba', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ delito: trimmedDelito }),
        signal: controller.signal
      });
      
      clearTimeout(timeoutId);

      // Verificar si la respuesta es OK (status 200-299)
      if (!response.ok) {
        throw new Error(`Error HTTP: ${response.status} ${response.statusText}`);
      }

      // Parsear la respuesta JSON
      const result = await response.json();

      // Validar estructura de respuesta
      if (!result) {
        throw new Error('Respuesta vacía del servidor de IA');
      }

      if (result.success) {
        return res.status(200).json(result);
      } else {
        return res.status(200).json({
          success: false,
          message: result.message || 'La solicitud no fue exitosa',
          data: result
        });
      }

    } catch (error) {
      clearTimeout(timeoutId);
      
      // Clasificación de errores
      if (error.name === 'AbortError') {
        throw new Error('Timeout al conectar con el servicio de IA');
      } else if (error.name === 'TypeError' && error.message.includes('fetch')) {
        throw new Error('Error de red o conexión al servicio de IA');
      } else if (error.message.includes('HTTP')) {
        throw error; // Ya tiene un mensaje descriptivo
      } else {
        throw new Error(`Error inesperado: ${error.message}`);
      }
    }
  };

  // Intentar con retry mechanism
  while (retryCount < maxRetries) {
    try {
      await makeRequest();
      return; // Salir si la solicitud fue exitosa
    } catch (error) {
      retryCount++;
      
      if (retryCount === maxRetries) {
        // Último intento falló
        console.error('Error final al conectarse al servicio de IA después de', maxRetries, 'intentos:', error);
        
        return res.status(503).json({ 
          error: 'Servicio de IA no disponible',
          message: error.message,
          retries: maxRetries
        });
      }
      
      // Esperar antes de reintentar (exponential backoff)
      const delay = Math.pow(2, retryCount) * 1000;
      console.warn(`Intento ${retryCount} fallido. Reintentando en ${delay}ms...`);
      await new Promise(resolve => setTimeout(resolve, delay));
    }
  }
});

export default router