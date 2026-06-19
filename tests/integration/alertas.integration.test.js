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

let alertaId;

beforeAll(async () => {
  await connectRedis();

  // Crear alerta base para usar en los tests
  const res = await request(app)
    .post('/api/alertas/nueva-alerta')
    .send({
      id_camara: 1,
      mensaje: 'Alerta base para tests',
      hora_suceso: new Date().toISOString(),
      tipo: 1,
      score_confianza: 0.90,
      estado: 0
    });

  alertaId = res.body.id;
});

describe('Integración — /api/alertas (complemento)', () => {

  it('GET / — retorna lista de alertas con token válido', async () => {
    const res = await request(app)
      .get('/api/alertas')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('GET /no-vistas — retorna alertas no vistas con token válido', async () => {
    const res = await request(app)
      .get('/api/alertas/no-vistas')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('GET /no-vistas — retorna 401 sin token', async () => {
    const res = await request(app)
      .get('/api/alertas/no-vistas');

    expect(res.statusCode).toBe(401);
  });

  it('POST /marcar-todas-vistas — marca todas las alertas como vistas', async () => {
    const res = await request(app)
      .post('/api/alertas/marcar-todas-vistas')
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({ estado: 1 });

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('ok', true);
  });

  it('POST /cambiar-estado/:id — cambia estado de alerta', async () => {
    const res = await request(app)
      .post(`/api/alertas/cambiar-estado/${alertaId}`)
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({ estado: 2 });

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('ok', true);
  });

  it('PUT /editar-descripcion/:id — edita descripción de alerta', async () => {
    const res = await request(app)
      .put(`/api/alertas/editar-descripcion/${alertaId}`)
      .send({ descripcion_suceso: 'Descripción actualizada en test' });

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('ok', true);
  });

  it('GET /camara/:id_camara — retorna alertas de una cámara', async () => {
    const res = await request(app)
      .get('/api/alertas/camara/1')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('GET /historial-filtro — retorna alertas filtradas por query', async () => {
    const res = await request(app)
      .get('/api/alertas/historial-filtro')
      .set('Cookie', `token=${tokenAdmin()}`)
      .query({ q: 'test', fields: 'mensaje' });

    expect(res.statusCode).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('GET /estadisticas-camara — retorna 400 sin id_camara', async () => {
    const res = await request(app)
      .get('/api/alertas/estadisticas-camara')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(400);
    expect(res.body).toHaveProperty('error');
  });

  it('GET /estadisticas-camara — retorna estadísticas con id_camara válido', async () => {
    const res = await request(app)
      .get('/api/alertas/estadisticas-camara')
      .set('Cookie', `token=${tokenAdmin()}`)
      .query({ id_camara: 1, dias: 7 });

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('success', true);
  });

  it('GET /estadisticas-totales — retorna estadísticas generales', async () => {
    const res = await request(app)
      .get('/api/alertas/estadisticas-totales')
      .set('Cookie', `token=${tokenAdmin()}`)
      .query({ dias: 7 });

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('success', true);
  });

  // Agregar estos casos al describe de alertas.integration.test.js

  it('POST /cam-reconnection-failure/nueva-alerta — crea alerta de reconexión', async () => {
    const res = await request(app)
      .post('/api/alertas/cam-reconnection-failure/nueva-alerta')
      .send({
        camera_id: 1,
        message: 'Fallo de reconexión test',
        alert_type: 4,
        timestamp: new Date().toISOString(),
        reconnect_attempts: 3,
        max_attempts: 3,
        last_attempt_time: new Date().toISOString(),
        stream_url: 'rtsp://test',
        status: 'failed'
      });

    expect(res.statusCode).toBe(201);
    expect(res.body).toHaveProperty('success', true);
  });

  it('POST /cam-reconnection-failure/nueva-alerta — retorna 400 si faltan campos', async () => {
    const res = await request(app)
      .post('/api/alertas/cam-reconnection-failure/nueva-alerta')
      .send({});

    expect(res.statusCode).toBe(400);
    expect(res.body).toHaveProperty('error');
  });

  it('GET /historial-filtro — retorna array vacío sin keywords', async () => {
    const res = await request(app)
      .get('/api/alertas/historial-filtro')
      .set('Cookie', `token=${tokenAdmin()}`)
      .query({ q: '', fields: 'mensaje' });

    expect(res.statusCode).toBe(200);
    expect(res.body).toEqual([]);
  });

  it('POST /marcar-todas-vistas — retorna 401 sin token', async () => {
    const res = await request(app)
      .post('/api/alertas/marcar-todas-vistas')
      .send({ estado: 1 });

    expect(res.statusCode).toBe(401);
  });

  it('GET /estadisticas-camara — retorna datos con fecha_inicio y fecha_fin', async () => {
    const res = await request(app)
      .get('/api/alertas/estadisticas-camara')
      .set('Cookie', `token=${tokenAdmin()}`)
      .query({
        id_camara: 1,
        fecha_inicio: '2024-01-01',
        fecha_fin: '2025-12-31',
        group: 'day'
      });

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('success', true);
  });

  it('GET /estadisticas-totales — retorna datos con fecha_inicio y fecha_fin', async () => {
    const res = await request(app)
      .get('/api/alertas/estadisticas-totales')
      .set('Cookie', `token=${tokenAdmin()}`)
      .query({
        fecha_inicio: '2024-01-01',
        fecha_fin: '2025-12-31',
        group: 'month'
      });

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('success', true);
  });

  it('DELETE /eliminar-alerta/:id — elimina alerta creada', async () => {
    const res = await request(app)
      .delete(`/api/alertas/eliminar-alerta/${alertaId}`)
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('ok', true);
  });

});

afterAll(async () => {
  await pool.end();
  await redisClient.quit();
});