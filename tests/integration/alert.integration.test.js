import request from 'supertest';
import { app } from '../../app.js';
import redisClient, { connectRedis } from '../../config/redis.js';
import pool from '../../config/db.js';
import jwt from 'jsonwebtoken';

// Función auxiliar para generar un token JWT válido
function generarToken() {
  return jwt.sign(
    { id: 1, usuario: 'admin', rol: 1 },
    process.env.JWT_SECRET || 'test_secret'
  );
}

beforeAll(async () => {
  await connectRedis();
});

describe('POST /api/alertas/nueva-alerta (integración)', () => {

  it('debe crear una alerta correctamente', async () => {
    const payload = {
      id_camara: 1,
      mensaje: 'Test integración',
      hora_suceso: new Date().toISOString(),
      tipo: 99,
      score_confianza: 0.92
    };

    const res = await request(app)
      .post('/api/alertas/nueva-alerta')
      .send(payload);

    expect(res.statusCode).toBe(201);
    expect(res.body).toHaveProperty('id');
    expect(res.body.mensaje).toBe('Test integración');
    expect(res.body).toHaveProperty('hora_suceso');
  });

  it('debe fallar si faltan datos', async () => {
    const res = await request(app)
      .post('/api/alertas/nueva-alerta')
      .send({});

    expect(res.statusCode).toBe(500);
  });

  it('debe retornar 401 al acceder a alertas sin token', async () => {
    const res = await request(app)
      .get('/api/alertas');

    expect(res.statusCode).toBe(401);
    expect(res.body).toHaveProperty('mensaje', 'Operación no autorizada');
  });

  it('debe retornar la alerta creada en /ultimas después de crearla', async () => {
    const payload = {
      id_camara: 1,
      mensaje: 'Test Redis persistencia',
      hora_suceso: new Date().toISOString(),
      tipo: 1,
      score_confianza: 0.88
    };

    await request(app)
      .post('/api/alertas/nueva-alerta')
      .send(payload);

    const res = await request(app)
      .get('/api/alertas/ultimas')
      .set('Cookie', `token=${generarToken()}`);

    expect(res.statusCode).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
    const encontrada = res.body.find(a => a.mensaje === 'Test Redis persistencia');
    expect(encontrada).toBeDefined();
  });

  it('debe marcar una alerta como vista en Postgres y Redis', async () => {
    const payload = {
      id_camara: 1,
      mensaje: 'Test marcar vista',
      hora_suceso: new Date().toISOString(),
      tipo: 1,
      score_confianza: 0.75,
      estado: 0
    };

    const crearRes = await request(app)
      .post('/api/alertas/nueva-alerta')
      .send(payload);

    const alertaId = crearRes.body.id;

    const marcarRes = await request(app)
      .post(`/api/alertas/marcar-vista/${alertaId}`)
      .set('Cookie', `token=${generarToken()}`)
      .send({ estado: 1 });

    expect(marcarRes.statusCode).toBe(200);
    expect(marcarRes.body).toHaveProperty('ok', true);

    const enRedis = await redisClient.get(`alerta:${alertaId}`);
    const alertaRedis = JSON.parse(enRedis);
    expect(alertaRedis.estado).toBe(1);
  });
});

afterAll(async () => {
  await pool.end();
  await redisClient.quit();
});