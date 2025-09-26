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
    link_camara_externo TEXT DEFAULT '',
    id_sector SMALLINT REFERENCES sectores(id), -- FK a id de tabla sectores
    zona_interes TEXT DEFAULT ''
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

CREATE TABLE usuarios (
    id SERIAL PRIMARY KEY,
    usuario VARCHAR(50) UNIQUE NOT NULL,
    contrasena TEXT NOT NULL,
    nombre VARCHAR(100) NOT NULL,
    rol SMALLINT NOT NULL DEFAULT 0 -- 0: 'invitado', 1: 'funcionario', 2: 'administrador'
)

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
    c.id_sector,
    c.link_camara_externo,
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

-- Insertar datos en la tabla sectores
INSERT INTO public.sectores (id, nombre_sector, descripcion) VALUES
(1, 'Sector Norte', 'Sector norte de la comuna.'),
(2, 'Sector Sur', 'Sector sur de la comuna.'),
(3, 'Sector Centro', 'Sector centro de la comuna.');

-- Insertar datos en la tabla camaras
INSERT INTO public.camaras ( nombre, posicion, direccion, estado_camara, ultima_conexion, link_camara, id_sector, link_camara_externo) VALUES
(1, 'Cámara Plaza', '{-33.52,-70.603}', 'Doctor Luis Calvo Mackenna 1361', true, '2024-06-09 19:30:00', 'rtsp://192.168.194.154:8554/cam_web', 3, 'http://localhost:5000/video_feed/1'),
(2, 'Cámara Sur', '{-33.525,-70.6}', 'El Blanco 178', true, '2024-06-09 19:30:00', 'rtsp://192.168.194.154:8554/cam_rtsp', 2, 'http://localhost:5000/video_feed/2'),
(3, 'Cámara Centro', '{-33.511,-70.59}', 'Avda Departamental 10450', false, '2024-06-09 19:30:00', 'rtsp://192.168.194.154:8554/cam_rtsp2', 3, 'http://localhost:5000/video_feed/3'),
(4, 'Cámara Merodeo', '{-33.51, -70.603}', 'Nva Uno 6476', true, '2024-06-09 19:30:00', '/loitering.mp4', 1, ''),
(5, 'Camára Asalto a Hogar', '{-33.53, -70.603}', 'Yungay 645', true, '2024-06-09 19:30:00', '/burglary.mp4', 1, ''),
(6, 'Cámara Portonazo', '{-33.52, -70.61}', 'Atahualpa 6892', true, '2024-06-09 19:30:00', '/portonazo.mp4', 2, '');

-- Insertar datos en la tabla alertas
INSERT INTO public.alertas (id, id_camara, mensaje, hora_suceso, score_confianza, descripcion_suceso, estado, tipo, clip) VALUES
(2, 2, 'Merodeo detectado en Calle 1', '2025-06-18 16:39:00', 0.85, 'Persona merodeando en la casa 645', 1, 1, NULL),
(3, 1, 'Merodeo', '2025-08-10 21:41:34.128106', 0.9279417991638184, 'a woman is seen in this surveillance image', 1, 1, NULL),
(4, 1, 'Merodeo', '2025-08-10 21:41:49.365153', 0.9386122822761536, 'a man is seen in this surveillance image', 1, 1, NULL),
(5, 1, 'Merodeo', '2025-08-10 21:50:05.289628', 0.9279417991638184, 'a woman is seen in this surveillance image', 1, 1, NULL),
(6, 1, 'Merodeo', '2025-08-10 21:50:21.728442', 0.9386122822761536, 'a man is seen in this surveillance image', 1, 1, NULL),
(7, 1, 'Merodeo', '2025-08-10 21:50:31.309801', 0.9300715923309326, 'a man is seen in this surveillance image', 1, 1, NULL),
(8, 1, 'Merodeo', '2025-08-10 22:05:32.867594', 0.9279417991638184, 'a woman is seen in this surveillance image', 1, 1, NULL),
(9, 1, 'Merodeo', '2025-08-10 22:17:42.564887', 0.9279417991638184, 'a woman is seen in this surveillance image', 1, 1, NULL),
(10, 1, 'Merodeo', '2025-08-10 22:18:46.271335', 0.9329694509506226, 'a woman is seen in this surveillance image', 1, 1, NULL),
(11, 3, 'Merodeo', '2025-08-10 22:25:38.354863', 0.93, 'a woman is seen in this surveillance image', 2, 1, NULL),
(12, 3, 'Merodeo', '2025-08-11 16:17:38.460177', 0.93, '[Alerta merodeo] 2025-08-11 12:17:27 | Activar protocolo: notificar a operador, enfocar cámara, registrar evidencia en enviar patrulla cercana.', 0, 1, NULL),
(13, 3, 'Merodeo', '2025-08-11 16:29:50.976066', 0.93, '[Alerta merodeo] 2025-08-11 12:29:39 | Activar protocolo: notificar a operador, enfocar cámara, registrar evidencia en enviar patrulla cercana.', 0, 1, NULL),
(14, 1, 'Merodeo', '2025-08-11 16:34:37.73551', 0.93, '[Alerta merodeo] 2025-08-11 12:34:27 |  Activar protocolo: notificar a operador, enfocar cámara, registrar evidencia en enviar patrulla cercana.', 0, 1, NULL),
(15, 1, 'Merodeo', '2025-08-11 16:46:24.940547', 0.93, '[Alerta merodeo] 2025-08-11 12:45:56 | Continuar observación pasiva en registro.', 0, 1, NULL),
(16, 1, 'Merodeo', '2025-08-12 21:50:21.635222', 0.93, '[Alerta merodeo] sin datos (timeout del analizador)', 0, 1, NULL),
(17, 1, 'Merodeo', '2025-08-12 23:22:04.24088', 0.93, '[Alerta merodeo] sin datos (timeout del analizador)', 1, 1, NULL),
(18, 1, 'Merodeo', '2025-08-12 23:27:12.847831', 0.93, '[Alerta merodeo] 2025-08-12 19:26:59 | Continuar observación pasiva en registro.', 1, 1, NULL),
(19, 1, 'Merodeo', '2025-08-12 23:38:06.396645', 0.93, '[Descripción] 2025-08-12 19:37:12 | Ropa: blusa blanca en pantalones negros, Cabello: largo en oscuro.', 2, 1, NULL),
(20, 1, 'Merodeo', '2025-08-12 23:44:44.747405', 0.93, 'La persona está vestida con una camiseta blanca, pantalones negros en zapatillas de deporte. No se observan accesorios visibles como gorra, capucha, mochila, guantes o gafas. El cabello', 1, 1, NULL),
(21, 1, 'Merodeo', '2025-08-13 00:21:00.251535', 0.93, 'No se observan detalles específicos sobre la ropa o accesorio. El cabello es oscuro en largo.', 1, 1, NULL),
(22, 2, 'Merodeo', '2025-08-14 04:18:21.907023', 0.93, 'No se observan ropas superiores o accesorio distintivo. El cabello es oscuro en corto. La persona lleva una camiseta blanca, pantalones negros en zapatillas.', 0, 1, NULL),
(23, 2, 'Merodeo', '2025-08-14 04:35:33.279086', 0.93, 'Descripción no disponible (error de procesamiento).', 0, 1, NULL),
(24, 1, 'Portonazo', '2025-08-14 05:20:58.495481', 0.89, '[Alerta merodeo] sin datos (error del analizador)', 1, 2, NULL),
(25, 2, 'Portonazo', '2025-08-14 05:26:41.290484', 0.93, '[Alerta merodeo] sin datos (error del analizador)', 0, 2, NULL),
(26, 1, 'Asalto hogar', '2025-08-14 05:32:57.458472', 0.75, 'La persona lleva una camiseta blanca en pantalones negros. No se observa anomalía.', 1, 3, NULL),
(27, 1, 'Asalto hogar', '2025-08-14 05:34:40.703153', 0.65, 'Niño con camiseta blanca en pantalón negro, lleva una mochila roja. Cabeza negra corta, no se observa barba. Se mueve con normalidad, observando el camino.', 1, 3, NULL);

-- Actualizar las secuencias
SELECT pg_catalog.setval('public.alertas_id_seq', 27, true);
SELECT pg_catalog.setval('public.camaras_id_seq', 6, true);
SELECT pg_catalog.setval('public.sectores_id_seq', 3, true);
SELECT pg_catalog.setval('public.tipos_alerta_id_seq', 1, false);
