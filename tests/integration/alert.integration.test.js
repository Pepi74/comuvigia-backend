import request from 'supertest';
import { app } from '../../app.js';
import redisClient, { connectRedis } from '../../config/redis.js';
import pool from '../../config/db.js';

beforeAll(async () => {
  await connectRedis();
})

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
});

afterAll(async () => {
  await pool.end();
  await redisClient.quit();
});