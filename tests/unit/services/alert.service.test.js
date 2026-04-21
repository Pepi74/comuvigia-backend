import { crearAlertaBase } from "../../../services/alert.service.js";
import { jest } from '@jest/globals';

describe('crearAlertaBase', () => {
  const mockPool = { query: jest.fn() };
  const mockRedis = {
    lPush: jest.fn(),
    set: jest.fn(),
    sAdd: jest.fn(),
    lTrim: jest.fn()
  };
  const mockIo = { emit: jest.fn() };

  beforeEach(() => jest.clearAllMocks());

  it('crea una alerta válida y la publica', async () => {
    mockPool.query
      .mockResolvedValueOnce({ rows: [{ id: 1, estado: 0 }] })
      .mockResolvedValueOnce({ rows: [{ id_sector: 3 }] });

    const alerta = {
      id_camara: 1,
      mensaje: 'Alerta prueba',
      hora_suceso: new Date(),
      tipo: 1,
      score_confianza: 0.9
    };

    const result = await crearAlertaBase({
      alerta,
      pool: mockPool,
      redisClient: mockRedis,
      io: mockIo
    });

    expect(mockIo.emit).toHaveBeenCalledWith('nueva-alerta', result);
    expect(result.id_sector).toBe(3);
  });

  it('lanza error si faltan datos obligatorios', async () => {
    const alertaInvalida = {
        mensaje: 'Sin cámara'
    };

    await expect(
        crearAlertaBase({
            alerta: alertaInvalida,
            pool: mockPool,
            redisClient: mockRedis,
            io: mockIo
            })
        ).rejects.toThrow('Datos de alerta incompletos');
    }); 

    it('no agrega a alertas_no_vistas si la alerta está vista', async () => {
        mockPool.query
            .mockResolvedValueOnce({ rows: [{ id: 2, estado: 1 }] })
            .mockResolvedValueOnce({ rows: [{ id_sector: 1 }] });

        const alerta = {
            id_camara: 1,
            mensaje: 'Alerta vista',
            tipo: 2,
            score_confianza: 0.8,
            estado: 1
        };

        await crearAlertaBase({
            alerta,
            pool: mockPool,
            redisClient: mockRedis,
            io: mockIo
        });

        expect(mockRedis.sAdd).not.toHaveBeenCalled();
    });

    it('propaga el error si falla la base de datos', async () => {
        mockPool.query.mockRejectedValue(new Error('DB error'));

        const alerta = {
            id_camara: 1,
            mensaje: 'Error DB',
            tipo: 1
        };

        await expect(
            crearAlertaBase({
                alerta,
                pool: mockPool,
                redisClient: mockRedis,
                io: mockIo
                })
            ).rejects.toThrow('DB error');
        });

    it('agrega a alertas_no_vistas si estado es 0 explícito', async () => {
        mockPool.query
            .mockResolvedValueOnce({ rows: [{ id: 3, estado: 0 }] })
            .mockResolvedValueOnce({ rows: [{ id_sector: 2 }] });

        const alerta = {
            id_camara: 1,
            mensaje: 'Alerta no vista',
            tipo: 1,
            score_confianza: 0.85,
            estado: 0
        };

        await crearAlertaBase({
            alerta,
            pool: mockPool,
            redisClient: mockRedis,
            io: mockIo
        });

        expect(mockRedis.sAdd).toHaveBeenCalledWith('alertas_no_vistas', '3');
    });

    it('crea alerta correctamente cuando se incluye descripcion_suceso y estado', async () => {
        mockPool.query
            .mockResolvedValueOnce({ rows: [{ id: 4, estado: 0, descripcion_suceso: 'Persona sospechosa' }] })
            .mockResolvedValueOnce({ rows: [{ id_sector: 1 }] });

        const alerta = {
            id_camara: 1,
            mensaje: 'Merodeo detectado',
            hora_suceso: new Date(),
            tipo: 1,
            score_confianza: 0.95,
            descripcion_suceso: 'Persona sospechosa en perimetro',
            estado: 0
        };

        const result = await crearAlertaBase({
            alerta,
            pool: mockPool,
            redisClient: mockRedis,
            io: mockIo
        });

        expect(result.descripcion_suceso).toBe('Persona sospechosa');
        expect(mockPool.query).toHaveBeenCalledTimes(2);
    });

    it('asigna id_sector como undefined si la camara no tiene sector', async () => {
        mockPool.query
            .mockResolvedValueOnce({ rows: [{ id: 5, estado: 0 }] })
            .mockResolvedValueOnce({ rows: [] });

        const alerta = {
            id_camara: 99,
            mensaje: 'Alerta sin sector',
            hora_suceso: new Date(),
            tipo: 1,
            score_confianza: 0.7
        };

        const result = await crearAlertaBase({
            alerta,
            pool: mockPool,
            redisClient: mockRedis,
            io: mockIo
        });

        expect(result.id_sector).toBeUndefined();
    });
});