import request from 'supertest';
import { app } from '../../app.js';
import pool from '../../config/db.js';
import redisClient, { connectRedis } from '../../config/redis.js';

beforeAll(async () => {
  await connectRedis();
});

describe('Integración — / (index)', () => {

  it('GET / — retorna mensaje de bienvenida', async () => {
    const res = await request(app).get('/');

    expect(res.statusCode).toBe(200);
    expect(res.text).toContain('Hola');
  });

  it('POST /casos_prueba — retorna 400 si faltan parámetros', async () => {
    const res = await request(app)
      .post('/casos_prueba')
      .send({});

    expect(res.statusCode).toBe(400);
    expect(res.body).toHaveProperty('error');
  });

  it('POST /casos_prueba — retorna 503 si IA no está disponible', async () => {
    const res = await request(app)
      .post('/casos_prueba')
      .send({ id: 1, link_camara: 'rtsp://test' });

    expect(res.statusCode).toBe(503);
    expect(res.body).toHaveProperty('error', 'Servicio de IA no disponible');
  }, 120000);

});

afterAll(async () => {
  await pool.end();
  await redisClient.quit();
});