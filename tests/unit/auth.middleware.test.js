import { verificarToken } from '../../middlewares/auth.js';
import jwt from 'jsonwebtoken';
import { jest } from '@jest/globals';

describe('verificarToken', () => {
    const mockRes = {
        status: jest.fn().mockReturnThis(),
        json: jest.fn()
    };
    const mockNext = jest.fn();

    beforeEach(() => jest.clearAllMocks());

    it('retorna 401 si no hay cookie token', () => {
        const mockReq = { cookies: {} };

        verificarToken(mockReq, mockRes, mockNext);

        expect(mockRes.status).toHaveBeenCalledWith(401);
        expect(mockRes.json).toHaveBeenCalledWith({ mensaje: 'Operación no autorizada' });
        expect(mockNext).not.toHaveBeenCalled();
    });

    it('retorna 403 si el token es inválido o expirado', () => {
        const mockReq = { cookies: { token: 'token.invalido.xyz' } };

        verificarToken(mockReq, mockRes, mockNext);

        expect(mockRes.status).toHaveBeenCalledWith(403);
        expect(mockRes.json).toHaveBeenCalledWith({ mensaje: 'Token inválido' });
        expect(mockNext).not.toHaveBeenCalled();
    });

    it('llama a next() y adjunta usuario al req si el token es válido', () => {
        const secret = 'test secret';
        process.env.JWT_SECRET = secret;
        const mockReq = { cookies: { token: jwt.sign(
            { id: 1, usuario: 'admin', rol: 2 },
            secret
        )}};

        verificarToken(mockReq, mockRes, mockNext);

        expect(mockNext).toHaveBeenCalled();
        expect(mockReq.user).toHaveProperty('usuario', 'admin');
        expect(mockReq.user).toHaveProperty('rol', 2);
    });
});