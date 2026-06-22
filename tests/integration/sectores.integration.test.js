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

function tokenOperador() {
  return jwt.sign(
    { id: 2, usuario: 'operador', rol: 1 },
    process.env.JWT_SECRET || 'test_secret'
  );
}

let sectorId;

beforeAll(async () => {
  await connectRedis();
});

describe('Integración — /api/sectores', () => {

  it('GET / — retorna sectores con fechas válidas', async () => {
    const res = await request(app)
      .get('/api/sectores')
      .set('Cookie', `token=${tokenAdmin()}`)
      .query({ fecha_inicio: '2024-01-01', fecha_fin: '2025-12-31' });

    expect(res.statusCode).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('GET / — retorna 400 sin fechas', async () => {
    const res = await request(app)
      .get('/api/sectores')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(400);
    expect(res.body).toHaveProperty('error');
  });

  it('GET / — retorna 401 sin token', async () => {
    const res = await request(app)
      .get('/api/sectores')
      .query({ fecha_inicio: '2024-01-01', fecha_fin: '2025-12-31' });

    expect(res.statusCode).toBe(401);
  });

  it('GET /alltime — retorna todos los sectores con token válido', async () => {
    const res = await request(app)
      .get('/api/sectores/alltime')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('GET /alltime — retorna 401 sin token', async () => {
    const res = await request(app)
      .get('/api/sectores/alltime');

    expect(res.statusCode).toBe(401);
  });

  it('POST / — crea sector con datos válidos', async () => {
    const res = await request(app)
      .post('/api/sectores')
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({
        nombre: `Sector Test ${Date.now()}`,
        descripcion: 'Sector creado en test',
        coordinates: { lat: -33.4, lng: -70.6 }
      });

    expect(res.statusCode).toBe(201);
    expect(res.body).toHaveProperty('id');

    sectorId = res.body.id;
  });

  it('POST / — retorna 401 sin token', async () => {
    const res = await request(app)
      .post('/api/sectores')
      .send({
        nombre: 'Sector Sin Auth',
        descripcion: 'Test',
        coordinates: {}
      });

    expect(res.statusCode).toBe(401);
  });

  it('POST / — retorna 403 con token rol 1', async () => {
    const res = await request(app)
      .post('/api/sectores')
      .set('Cookie', `token=${tokenOperador()}`)
      .send({
        nombre: 'Sector Operador',
        descripcion: 'Test',
        coordinates: {}
      });

    expect(res.statusCode).toBe(403);
  });

  it('GET /alltime — retorna 403 con token rol 1', async () => {
    const res = await request(app)
      .get('/api/sectores/alltime')
      .set('Cookie', `token=${tokenOperador()}`);

    expect(res.statusCode).toBe(200);
  });

});

afterAll(async () => {
  if (sectorId) {
    await pool.query('DELETE FROM sectores WHERE id = $1', [sectorId]);
  }
  await pool.end();
  await redisClient.quit();
});