import request from 'supertest';
import { app } from '../../app.js';
import pool from '../../config/db.js';
import jwt from 'jsonwebtoken';
import redisClient, { connectRedis } from '../../config/redis.js';

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

let usuarioCreadoId;
let usuarioCreadoNombre;

beforeAll(async () => {
  await connectRedis();
});

describe('Integración — /api/usuarios', () => {

  it('POST /register — crea usuario con campos completos', async () => {
    const res = await request(app)
      .post('/api/usuarios/register')
      .send({
        usuario: `test_user_${Date.now()}`,
        contrasena: 'password123',
        nombre: 'Usuario Test',
        rol: 1
      });

    expect(res.statusCode).toBe(201);
    expect(res.body).toHaveProperty('id');
    expect(res.body).toHaveProperty('usuario');
    expect(res.body).toHaveProperty('nombre', 'Usuario Test');

    usuarioCreadoId = res.body.id;
    usuarioCreadoNombre = res.body.usuario;
  });

  it('POST /register — falla si faltan campos obligatorios', async () => {
    const res = await request(app)
      .post('/api/usuarios/register')
      .send({ usuario: 'soloUsuario' });

    expect(res.statusCode).toBe(400);
    expect(res.body).toHaveProperty('error');
  });

  it('GET / — retorna lista de usuarios con token rol 2', async () => {
    const res = await request(app)
      .get('/api/usuarios')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('GET / — retorna 401 sin token', async () => {
    const res = await request(app)
      .get('/api/usuarios');

    expect(res.statusCode).toBe(401);
  });

  it('GET / — retorna 403 con token rol 1', async () => {
    const res = await request(app)
      .get('/api/usuarios')
      .set('Cookie', `token=${tokenOperador()}`);

    expect(res.statusCode).toBe(403);
  });

  it('GET /:id — retorna usuario existente', async () => {
    const res = await request(app)
      .get(`/api/usuarios/${usuarioCreadoId}`)
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('id', usuarioCreadoId);
  });

  it('GET /:id — retorna 404 si usuario no existe', async () => {
    const res = await request(app)
      .get('/api/usuarios/999999')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(404);
    expect(res.body).toHaveProperty('error');
  });

  it('PUT /:id — actualiza nombre del usuario', async () => {
    const res = await request(app)
      .put(`/api/usuarios/${usuarioCreadoId}`)
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({ nombre: 'Nombre Actualizado' });

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('nombre', 'Nombre Actualizado');
  });

  it('PUT /:id — falla si no se envían campos', async () => {
    const res = await request(app)
      .put(`/api/usuarios/${usuarioCreadoId}`)
      .set('Cookie', `token=${tokenAdmin()}`)
      .send({});

    expect(res.statusCode).toBe(400);
    expect(res.body).toHaveProperty('error');
  });

  it('PUT /:id — retorna 404 si usuario no existe', async () => {
    const res = await request(app)
        .put('/api/usuarios/999999')
        .set('Cookie', `token=${tokenAdmin()}`)
        .send({ nombre: 'No existe' });

    expect(res.statusCode).toBe(404);
    expect(res.body).toHaveProperty('error');
  });
  
  it('DELETE /:id — elimina el usuario creado', async () => {
    const res = await request(app)
      .delete(`/api/usuarios/${usuarioCreadoId}`)
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('message', 'Usuario eliminado');
  });

  it('DELETE /:id — retorna 404 si usuario no existe', async () => {
    const res = await request(app)
      .delete('/api/usuarios/999999')
      .set('Cookie', `token=${tokenAdmin()}`);

    expect(res.statusCode).toBe(404);
    expect(res.body).toHaveProperty('error');
  });

});

afterAll(async () => {
  await pool.end();
  await redisClient.quit();
});