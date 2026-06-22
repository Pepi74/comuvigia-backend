import request from 'supertest';
import { app } from '../../app.js';
import pool from '../../config/db.js';
import redisClient, { connectRedis } from '../../config/redis.js';

const usuarioAdmin = `test_auth_admin`;
const usuarioOperador = `test_auth_operador`;
const contrasena = 'password123';

let adminId;
let operadorId;

beforeAll(async () => {
  await connectRedis();

  // Crear usuario admin de prueba
  const resAdmin = await request(app)
    .post('/api/usuarios/register')
    .send({ usuario: usuarioAdmin, contrasena, nombre: 'Admin Test', rol: 2 });
  adminId = resAdmin.body.id;

  // Crear usuario operador de prueba
  const resOperador = await request(app)
    .post('/api/usuarios/register')
    .send({ usuario: usuarioOperador, contrasena, nombre: 'Operador Test', rol: 1 });
  operadorId = resOperador.body.id;
});

describe('Integración — /api/auth', () => {

  it('POST /login — credenciales correctas retorna 200 y cookie', async () => {
    const res = await request(app)
      .post('/api/auth/login')
      .send({ usuario: usuarioAdmin, contrasena });

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('usuario', usuarioAdmin);
    expect(res.body).toHaveProperty('rol', 2);
    expect(res.headers['set-cookie']).toBeDefined();
  });

  it('POST /login — usuario no existe retorna 401', async () => {
    const res = await request(app)
      .post('/api/auth/login')
      .send({ usuario: 'usuario_inexistente', contrasena: '123' });

    expect(res.statusCode).toBe(401);
    expect(res.body).toHaveProperty('mensaje', 'Usuario no encontrado');
  });

  it('POST /login — contraseña incorrecta retorna 401', async () => {
    const res = await request(app)
      .post('/api/auth/login')
      .send({ usuario: usuarioAdmin, contrasena: 'contrasena_incorrecta' });

    expect(res.statusCode).toBe(401);
    expect(res.body).toHaveProperty('mensaje', 'Contraseña incorrecta');
  });

  it('POST /logout — cierra sesión correctamente', async () => {
    const res = await request(app)
      .post('/api/auth/logout');

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('mensaje', 'Sesión cerrada');
  });

  it('GET /check — retorna datos con token válido', async () => {
    const loginRes = await request(app)
      .post('/api/auth/login')
      .send({ usuario: usuarioAdmin, contrasena });

    const cookie = loginRes.headers['set-cookie'];

    const res = await request(app)
      .get('/api/auth/check')
      .set('Cookie', cookie);

    expect(res.statusCode).toBe(200);
    expect(res.body).toHaveProperty('usuario', usuarioAdmin);
  });

  it('GET /check — retorna 401 sin token', async () => {
    const res = await request(app)
      .get('/api/auth/check');

    expect(res.statusCode).toBe(401);
    expect(res.body).toHaveProperty('mensaje', 'No hay sesión activa');
  });

});

afterAll(async () => {
  await pool.query('DELETE FROM usuarios WHERE usuario = $1', [usuarioAdmin]);
  await pool.query('DELETE FROM usuarios WHERE usuario = $1', [usuarioOperador]);
  await pool.end();
  await redisClient.quit();
});