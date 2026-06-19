import request from 'supertest';
import { app } from '../../app.js';
import pool from '../../config/db.js';
import redisClient, { connectRedis } from '../../config/redis.js';
import jwt from 'jsonwebtoken';

function tokenAdmin() {
  return jwt.sign(
    { id: 1, usuario: 'admin', rol: 2 },
    process.env.JWT_SECRET || 'test_secret'
  );
}

beforeAll(async () => {
  await connectRedis();
});

describe('Integración — /api/informe', () => {

  it('GET /generar-pdf — genera PDF con fechas válidas', async () => {
    const res = await request(app)
      .get('/api/informe/generar-pdf')
      .set('Cookie', `token=${tokenAdmin()}`)
      .query({ fechaInicio: '2024-01-01', fechaFin: '2025-12-31' });

    expect(res.statusCode).toBe(200);
    expect(res.headers['content-type']).toContain('application/pdf');
  }, 30000);

  it('GET /generar-pdf — genera PDF sin fechas usando últimos 7 días', async () => {
    const res = await request(app)
      .get('/api/informe/generar-pdf')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(200);
    expect(res.headers['content-type']).toContain('application/pdf');
  }, 30000);

  it('GET /generar-pdf — retorna 401 sin token', async () => {
    const res = await request(app)
      .get('/api/informe/generar-pdf');

    expect(res.statusCode).toBe(401);
  });

});

afterAll(async () => {
  await pool.end();
  await redisClient.quit();
});