import { Router } from 'express'
import { spawn } from 'child_process'
import http from 'http';

const router = Router()

// URL RTSP y dimensiones
//const rtspUrl = 'rtsp://admin:FOCGNT@camezviz.duckdns.org:8554/h264/ch1/main/av_stream';

// Endpoint para capturar un frame
/*router.get('/', (req , res) => {
  let responded = false;
  // ffmpeg para capturar 1 frame y devolverlo como JPEG por stdout
  const ffmpeg = spawn('ffmpeg', [
    '-rtsp_transport', 'tcp',
    '-i', rtspUrl,
    '-frames:v', '1',          // Solo un frame
    '-f', 'image2pipe',
    '-vcodec', 'mjpeg',
    '-'
  ]);

  // Buffer para el frame
  let frameData = Buffer.alloc(0);

  ffmpeg.stdout.on('data', (chunk) => {
    frameData = Buffer.concat([frameData, chunk]);
  });

  ffmpeg.on('close', (code) => {
    if (responded) return;
    responded = true;
    if (frameData.length > 0) {
      res.set('Content-Type', 'image/jpeg');
      res.send(frameData);
    } else {
      res.status(500).send('No se pudo capturar la imagen');
    }
  });

  ffmpeg.on('error', (err) => {
    if (responded) return;
    responded = true;
    res.status(500).send('Error ejecutando FFmpeg');
  });

  req.on('close', () => {
    if (responded) return;
    responded = true;
    ffmpeg.kill('SIGTERM');
  });
});*/

router.get('/stream.mjpg', (req, res) => {
  http.get('http://python-stream:5000/video_feed', (pyRes) => {
    res.writeHead(200, {'Content-Type': 'multipart/x-mixed-replace; boundary=frame'});
    pyRes.pipe(res);

    // Por si el cliente cierra la conexión antes
    req.on('close', () => {
      pyRes.destroy();
    });
  }).on('error', (err) => {
    res.status(500).send('Error conectando al streaming Python');
  });
});

export default router