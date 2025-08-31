CREATE TABLE sectores (
    id SERIAL PRIMARY KEY,
    nombre_sector VARCHAR(100),
    descripcion TEXT NOT NULL
);

CREATE TABLE camaras (
    id SERIAL PRIMARY KEY, -- Puede ser definida como id_camara
    nombre TEXT NOT NULL,
    posicion DOUBLE PRECISION[] NOT NULL, -- Arreglo con valores latitud y longitud.
    direccion TEXT NOT NULL,
    estado_camara BOOLEAN NOT NULL DEFAULT TRUE,
    ultima_conexion TIMESTAMP NOT NULL,
    link_camara TEXT DEFAULT '', -- Opcional
    id_sector SMALLINT REFERENCES sectores(id) -- FK a id de tabla sectores
);

CREATE TABLE alertas (
    id SERIAL PRIMARY KEY, -- Puede ser definida como id_alerta
    id_camara INTEGER NOT NULL REFERENCES camaras(id), -- FK a id de tabla camaras
    mensaje TEXT NOT NULL,
    hora_suceso TIMESTAMP NOT NULL,
    tipo SMALLINT NOT NULL DEFAULT 0, -- Categorización de tipo de alerta, 0: "No especificado", 1: "Merodeo", 2: "Portonazo"...*se puede ir agregando mas si es necesario*
    score_confianza NUMERIC NOT NULL,
    clip VARCHAR(100), -- Opcional, referencia a la ubicación del clip o video perteneciente a otra base de datos
    descripcion_suceso TEXT, -- Opcional
    estado SMALLINT NOT NULL DEFAULT 0 -- Estado de alerta, 0: "En Observación", 1: "Confirmada", 2: "Falso Positivo"
);

CREATE TABLE tipos_alerta (
    id SERIAL PRIMARY KEY, -- 0: "No especificado", 1: "Merodeo", 2: "Portonazo"...*se puede ir agregando mas si es necesario*
    nombre_tipo VARCHAR(100) ,
    descripcion TEXT NOT NULL
);

-- Vista de tabla camara con el total de alertas de cada camara
CREATE OR REPLACE VIEW camaras_con_alertas AS
SELECT
    c.id,
    c.nombre,
    c.posicion,
    c.direccion,
    c.estado_camara,
    c.ultima_conexion,
    c.link_camara,
    COUNT(a.id) AS total_alertas
FROM
    camaras c
LEFT JOIN
    alertas a ON c.id = a.id_camara
GROUP BY
    c.id, c.nombre, c.posicion, c.direccion, c.estado_camara, c.ultima_conexion, c.link_camara;

-- Función para generar reporte por período flexible
CREATE OR REPLACE FUNCTION reporte_alertas_por_periodo(
    fecha_inicio TIMESTAMP,
    fecha_fin TIMESTAMP,
    agrupacion TEXT DEFAULT 'day'
)
RETURNS TABLE (
    periodo TIMESTAMP,
    id_sector INTEGER,  -- Cambiado de SMALLINT a INTEGER
    nombre_sector VARCHAR(100),
    total_alertas BIGINT,
    camaras_activas BIGINT,
    confianza_promedio NUMERIC,
    alertas_confirmadas BIGINT,
    falsos_positivos BIGINT,
    merodeos BIGINT,
    portonazos BIGINT,
    asaltos_hogar BIGINT,  -- Agregado para tipo 3
    no_especificados BIGINT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        DATE_TRUNC(agrupacion, a.hora_suceso) as periodo,
        s.id as id_sector,  -- s.id es INTEGER (SERIAL)
        s.nombre_sector,
        COUNT(a.id) as total_alertas,
        COUNT(DISTINCT c.id) as camaras_activas,
        ROUND(AVG(a.score_confianza), 2) as confianza_promedio,
        SUM(CASE WHEN a.estado = 1 THEN 1 ELSE 0 END) as alertas_confirmadas,
        SUM(CASE WHEN a.estado = 2 THEN 1 ELSE 0 END) as falsos_positivos,
        SUM(CASE WHEN a.tipo = 1 THEN 1 ELSE 0 END) as merodeos,
        SUM(CASE WHEN a.tipo = 2 THEN 1 ELSE 0 END) as portonazos,
        SUM(CASE WHEN a.tipo = 3 THEN 1 ELSE 0 END) as asaltos_hogar,
        SUM(CASE WHEN a.tipo = 0 THEN 1 ELSE 0 END) as no_especificados
    FROM alertas a
    INNER JOIN camaras c ON a.id_camara = c.id
    INNER JOIN sectores s ON c.id_sector = s.id
    WHERE a.hora_suceso BETWEEN fecha_inicio AND fecha_fin
    GROUP BY DATE_TRUNC(agrupacion, a.hora_suceso), s.id, s.nombre_sector
    ORDER BY periodo DESC, total_alertas DESC;
END;
$$;

INSERT INTO sectores (nombre_sector, descripcion)
VALUES
    ('Sector Norte', 'Sector norte de la comuna.'),
    ('Sector Sur', 'Sector sur de la comuna.'),
    ('Sector Centro', 'Sector centro de la comuna.');

INSERT INTO camaras (nombre, posicion, direccion, estado_camara, ultima_conexion, link_camara, id_sector)
VALUES
    ('Cámara Plaza', '{-33.52, -70.603}', 'Avenida 123', TRUE, '2024-06-09 19:30:00', 'http://localhost:5000/video_feed', 3),
    ('Cámara Sur', '{-33.525, -70.6}', 'Avenida 123', FALSE, '2024-06-09 19:30:00', '', 3),
    ('Cámara Centro', '{-33.511, -70.59}', 'Avenida 123', FALSE, '2024-06-09 19:30:00', '', 2);