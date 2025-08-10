CREATE TABLE alertas (
    id SERIAL PRIMARY KEY, -- Puede ser definida como id_alerta
    id_camara INTEGER NOT NULL REFERENCES camaras(id), -- FK a id de tabla camaras
    mensaje TEXT NOT NULL,
    hora_suceso TIMESTAMP NOT NULL,
    score_confianza NUMERIC NOT NULL,
    id_clip INTEGER, -- Opcional, referencia el id del clip o video perteneciente a otra base de datos
    descripcion_suceso TEXT, -- Opcional
    estado SMALLINT NOT NULL DEFAULT 0 -- Estado de alerta, 0: "En Observación", 1: "Confirmada", 2: "Falso Positivo"
);

CREATE TABLE camaras (
    id SERIAL PRIMARY KEY, -- Puede ser definida como id_camara
    nombre TEXT NOT NULL,
    posicion DOUBLE PRECISION[] NOT NULL, -- Arreglo con valores latitud y longitud.
    direccion TEXT NOT NULL,
    estado_camara BOOLEAN NOT NULL DEFAULT TRUE,
    ultima_conexion TIMESTAMP NOT NULL,
    link_camara TEXT DEFAULT '' -- Opcional
);

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

INSERT INTO camaras (nombre, posicion, direccion, estado_camara, ultima_conexion, link_camara)
VALUES
    ('Cámara Plaza', '{-33.52, -70.603}', 'Avenida 123', TRUE, '2024-06-09 19:30:00', 'http://localhost:5000/video_feed'),
    ('Cámara Sur', '{-33.525, -70.6}', 'Avenida 123', FALSE, '2024-06-09 19:30:00', ''),
    ('Cámara Centro', '{-33.511, -70.59}', 'Avenida 123', FALSE, '2024-06-09 19:30:00', '');