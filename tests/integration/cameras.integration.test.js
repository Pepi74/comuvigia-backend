import request from 'supertest';
import { app } from '../../app.js';
import pool from '../../config/db.js';
import redisClient from '../../config/redis.js';
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

let camaraId;

describe('Integración — /api/camaras', () => {

  it('GET / — retorna lista de cámaras sin autenticación', async () => {
    const res = await request(app).get('/api/camaras');
    expect(res.statusCode).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('POST / — crea cámara con campos obligatorios', async () => {
    const res = await request(app)
      .post('/api/camaras')
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({
        nombre: `Camara Test ${Date.now()}`,
        posicion: '{-33.4, -70.6}',
        direccion: 'Dirección Test 123',
        estado_camara: false,
        ultima_conexion: new Date().toISOString(),
        id_sector: 1
      });

    expect(res.statusCode).toBe(201);
    expect(res.body).toHaveProperty('id');
    camaraId = res.body.id;
  });

  it('POST / — falla si faltan campos obligatorios', async () => {
    const res = await request(app)
      .post('/api/camaras')
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({ nombre: 'Solo nombre' });

    expect(res.statusCode).toBe(400);
    expect(res.body).toHaveProperty('error');
  });

  it('POST / — retorna 403 con token rol 1', async () => {
    const res = await request(app)
      .post('/api/camaras')
      .set('Cookie', `token=${tokenOperador()}`)
      .send({
        nombre: 'Camara Operador',
        posicion: '{}',
        direccion: 'Dir',
        ultima_conexion: new Date().toISOString()
      });

    expect(res.statusCode).toBe(403);
  });

  it('GET /:id — retorna cámara existente', async () => {
    const res = await request(app)
      .get(`/api/camaras/${camaraId}`)
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('id', camaraId);
  });

  it('GET /:id — retorna 404 si cámara no existe', async () => {
    const res = await request(app)
      .get('/api/camaras/999999')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(404);
    expect(res.body).toHaveProperty('error');
  });

  it('GET /cantidad-alertas — retorna datos con token válido', async () => {
    const res = await request(app)
      .get('/api/camaras/cantidad-alertas')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('GET /cantidad-alertas-fecha — retorna datos con fechas válidas', async () => {
    const res = await request(app)
      .get('/api/camaras/cantidad-alertas-fecha')
      .set('Cookie', `token=${tokenAdmin()}`)
      .query({
        fecha_inicio: '2024-01-01',
        fecha_fin: '2025-12-31'
      });

    expect(res.statusCode).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('GET /cantidad-alertas-fecha — falla sin parámetros de fecha', async () => {
    const res = await request(app)
      .get('/api/camaras/cantidad-alertas-fecha')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(400);
    expect(res.body).toHaveProperty('error');
  });

  it('PATCH /:id — actualiza nombre de cámara', async () => {
    const res = await request(app)
      .patch(`/api/camaras/${camaraId}`)
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({ nombre: 'Camara Actualizada' });

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('nombre', 'Camara Actualizada');
  });

  it('GET /nombre-camaras — retorna mapa de nombres con token válido', async () => {
    const res = await request(app)
      .get('/api/camaras/nombre-camaras')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(200);
    expect(typeof res.body).toBe('object');
  });

  it('PUT /:id — actualiza estado de cámara', async () => {
  const crearRes = await request(app)
    .post('/api/camaras')
    .set('Cookie', `token=${tokenAdmin()}`)
    .send({
      nombre: `Camara PUT Test ${Date.now()}`,
      posicion: '{-33.4, -70.6}',
      direccion: 'Dirección PUT Test',
      ultima_conexion: new Date().toISOString(),
      estado_camara: false
    });

    const idParaPut = crearRes.body.id;

    const res = await request(app)
        .put(`/api/camaras/${idParaPut}`)
        .set('Cookie', `token=${tokenAdmin()}`)
        .send({ estado: true });

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('estado_camara', true);

    await pool.query('DELETE FROM camaras WHERE id = $1', [idParaPut]);
    });

  it('PATCH /:id — retorna 400 si no se envían campos válidos', async () => {
    const res = await request(app)
      .patch('/api/camaras/1')
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({});

    expect(res.statusCode).toBe(400);
    expect(res.body).toHaveProperty('error');
  });

  it('GET /cantidad-alertas — retorna 401 sin token', async () => {
    const res = await request(app)
      .get('/api/camaras/cantidad-alertas');

    expect(res.statusCode).toBe(401);
  });

  it('GET /nombre-camaras — retorna 401 sin token', async () => {
    const res = await request(app)
      .get('/api/camaras/nombre-camaras');

    expect(res.statusCode).toBe(401);
  });

  it('GET /:id — retorna 401 sin token', async () => {
    const res = await request(app)
      .get('/api/camaras/1');

    expect(res.statusCode).toBe(401);
  });

  it('DELETE /:id — retorna 401 sin token', async () => {
    const res = await request(app)
      .delete('/api/camaras/1');

    expect(res.statusCode).toBe(401);
  });

  it('PATCH /:id — retorna 404 si cámara no existe', async () => {
    const res = await request(app)
      .patch('/api/camaras/999999')
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({ nombre: 'No existe' });

    expect(res.statusCode).toBe(404);
    expect(res.body).toHaveProperty('error');
  });

  it('PATCH /:id — actualiza zona_interes como string JSON válido', async () => {
    const res = await request(app)
      .patch(`/api/camaras/${camaraId}`)
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({ zona_interes: '{"x": 100, "y": 200}' });

    expect(res.statusCode).toBe(200);
  });

  it('PATCH /:id — actualiza zona_interes como objeto', async () => {
    const res = await request(app)
      .patch(`/api/camaras/${camaraId}`)
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({ zona_interes: { x: 100, y: 200 } });

    expect(res.statusCode).toBe(200);
  });

  it('PATCH /:id — actualiza zona_interes vacío', async () => {
    const res = await request(app)
      .patch(`/api/camaras/${camaraId}`)
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({ zona_interes: '' });

    expect(res.statusCode).toBe(200);
  });

  it('PATCH /:id — actualiza zona_interes como string JSON inválido', async () => {
    const res = await request(app)
      .patch(`/api/camaras/${camaraId}`)
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({ zona_interes: 'no es json' });

    expect(res.statusCode).toBe(200);
  });

  it('POST / — retorna 400 si id_sector no existe', async () => {
    const res = await request(app)
      .post('/api/camaras')
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({
        nombre: `Camara FK Test ${Date.now()}`,
        posicion: '{-33.4, -70.6}',
        direccion: 'Dir Test',
        ultima_conexion: new Date().toISOString(),
        estado_camara: false,
        id_sector: 9999
      });

    expect(res.statusCode).toBe(400);
    expect(res.body).toHaveProperty('error', 'El sector especificado no existe');
  });

  it('PATCH /:id — retorna 400 si id_sector no existe', async () => {
    const res = await request(app)
      .patch(`/api/camaras/${camaraId}`)
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({ id_sector: 9999 });

    expect(res.statusCode).toBe(400);
    expect(res.body).toHaveProperty('error', 'El sector especificado no existe');
  });

  it('DELETE /:id — elimina cámara creada', async () => {
    const res = await request(app)
      .delete(`/api/camaras/${camaraId}`)
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('message', 'Cámara eliminada correctamente');
  });

  it('PUT /:id — actualiza cámara completa con todos los campos', async () => {
    const crearRes = await request(app)
      .post('/api/camaras')
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({
        nombre: `Camara PUT Completo ${Date.now()}`,
        posicion: '{-33.4, -70.6}',
        direccion: 'Dirección PUT Completo',
        ultima_conexion: new Date().toISOString(),
        estado_camara: false
      });

    const idParaPut = crearRes.body.id;

    const res = await request(app)
      .put(`/api/camaras/${idParaPut}`)
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({
        nombre: 'Camara PUT Completo Actualizada',
        posicion: '{-33.5, -70.7}',
        direccion: 'Dirección Actualizada',
        estado_camara: false,
        ultima_conexion: new Date().toISOString(),
        link_camara: '',
        id_sector: 3,
        zona_interes: null
      });

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('nombre', 'Camara PUT Completo Actualizada');

    await pool.query('DELETE FROM camaras WHERE id = $1', [idParaPut]);
  });

  it('PUT /:id — retorna 404 si cámara no existe en actualización completa', async () => {
    const res = await request(app)
      .put('/api/camaras/999999')
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({
        nombre: 'No existe',
        posicion: '{}',
        direccion: 'Dir',
        estado_camara: false,
        ultima_conexion: new Date().toISOString()
      });

    expect(res.statusCode).toBe(404);
  });

});

afterAll(async () => {
  await pool.end();
  await redisClient.quit();
});