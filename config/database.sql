CREATE TABLE camaras (
    id SERIAL PRIMARY KEY, -- Puede ser definida como id_camara
    nombre TEXT NOT NULL,
    posicion DOUBLE PRECISION[] NOT NULL, -- Arreglo con valores latitud y longitud.
    direccion TEXT NOT NULL,
    estado_camara BOOLEAN NOT NULL DEFAULT TRUE,
    ultima_conexion TIMESTAMP NOT NULL,
    link_camara TEXT DEFAULT '' -- Opcional
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

CREATE TABLE sectores (
    id SERIAL PRIMARY KEY,
    nombre_sector VARCHAR(100) ,
    descripcion TEXT NOT NULL
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

INSERT INTO camaras (nombre, posicion, direccion, estado_camara, ultima_conexion, link_camara, id_sector)
VALUES
    ('Cámara Plaza', '{-33.52, -70.603}', 'Avenida 123', TRUE, '2024-06-09 19:30:00', 'http://localhost:5000/video_feed', 3),
    ('Cámara Sur', '{-33.525, -70.6}', 'Avenida 123', FALSE, '2024-06-09 19:30:00', '', 3),
    ('Cámara Centro', '{-33.511, -70.59}', 'Avenida 123', FALSE, '2024-06-09 19:30:00', '', 2);

INSERT INTO sectores (nombre_sector, descripcion)
VALUES
    ('Sector Norte', 'Sector norte de la comuna.'),
    ('Sector Sur', 'Sector sur de la comuna.'),
    ('Sector Centro', 'Sector centro de la comuna.');