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

let reglaId;

beforeAll(async () => {
  await connectRedis();
});

describe('Integración — /api/reglas', () => {

  it('GET /obtener — retorna reglas con token válido', async () => {
    const res = await request(app)
      .get('/api/reglas/obtener')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('GET /obtener — retorna 401 sin token', async () => {
    const res = await request(app)
      .get('/api/reglas/obtener');

    expect(res.statusCode).toBe(401);
  });

  it('GET /sectores — retorna sectores con token válido', async () => {
    const res = await request(app)
      .get('/api/reglas/sectores')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('GET /sectores — retorna 401 sin token', async () => {
    const res = await request(app)
      .get('/api/reglas/sectores');

    expect(res.statusCode).toBe(401);
  });

  it('POST /insertar — crea regla con campos completos', async () => {
    const res = await request(app)
      .post('/api/reglas/insertar')
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({
        riesgo: 'Alto',
        tipoAlerta: ['Intrusión', 'Movimiento'],
        horaInicio: '08:00',
        horaFin: '18:00',
        score: 85,
        sector: 'Sector Test'
      });

    expect(res.statusCode).toBe(201);
    expect(res.body).toHaveProperty('id');

    reglaId = res.body.id;
  });

  it('POST /insertar — falla si faltan campos obligatorios', async () => {
    const res = await request(app)
      .post('/api/reglas/insertar')
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({ riesgo: 'Bajo' });

    expect(res.statusCode).toBe(400);
    expect(res.body).toHaveProperty('error');
  });

  it('POST /actualizar — actualiza regla existente', async () => {
    const res = await request(app)
      .post('/api/reglas/actualizar')
      .set('Cookie', `token=${tokenAdmin()}`)
      .send([
        {
          id: reglaId,
          riesgo: 'Medio',
          tipoAlerta: ['Movimiento'],
          horaInicio: '09:00',
          horaFin: '17:00',
          score: 75,
          sector: 'Sector Actualizado'
        }
      ]);

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('mensaje', 'Reglas actualizadas correctamente');
  });

  it('POST /actualizar — retorna 401 sin token', async () => {
    const res = await request(app)
    .post('/api/reglas/actualizar')
    .send([]);

    expect(res.statusCode).toBe(401);
  });

  it('POST /insertar — retorna 401 sin token', async () => {
    const res = await request(app)
    .post('/api/reglas/insertar')
    .send({});

    expect(res.statusCode).toBe(401);
  });

});

afterAll(async () => {
  if (reglaId) {
    await pool.query('DELETE FROM reglas WHERE id = $1', [reglaId]);
  }
  await pool.end();
  await redisClient.quit();
});