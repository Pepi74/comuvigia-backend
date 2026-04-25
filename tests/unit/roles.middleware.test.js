import { verificarRol } from '../../middlewares/roles.js';
import { jest } from '@jest/globals';

describe('verificarRol', () => {
    const mockRes = {
        status: jest.fn().mockReturnThis(),
        json: jest.fn()
    };
    const mockNext = jest.fn();

    beforeEach(() => jest.clearAllMocks());

    it('retorna 403 si el rol del usuario no está permitido', () => {
        const mockReq = { user: { rol: 1 } };

        verificarRol([2])(mockReq, mockRes, mockNext);

        expect(mockRes.status).toHaveBeenCalledWith(403);
        expect(mockRes.json).toHaveBeenCalledWith({ 
            mensaje: 'Acceso denegado: no tienes permisos suficientes' 
        });
        expect(mockNext).not.toHaveBeenCalled();
    });

    it('llama a next() si el rol del usuario está permitido', () => {
        const mockReq = { user: { rol: 2 } };

        verificarRol([1, 2])(mockReq, mockRes, mockNext);

        expect(mockNext).toHaveBeenCalled();
        expect(mockRes.status).not.toHaveBeenCalled();
    });
});