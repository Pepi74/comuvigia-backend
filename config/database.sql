CREATE TABLE sectores (
    id SERIAL PRIMARY KEY,
    nombre_sector VARCHAR(100),
    descripcion TEXT NOT NULL,
    coordinates JSONB
);

CREATE TABLE camaras (
    id SERIAL PRIMARY KEY,
    nombre TEXT NOT NULL,
    posicion DOUBLE PRECISION[] NOT NULL,
    direccion TEXT NOT NULL,
    estado_camara BOOLEAN NOT NULL DEFAULT TRUE,
    ultima_conexion TIMESTAMP NOT NULL,
    link_camara TEXT DEFAULT '',
    link_camara_externo TEXT DEFAULT '',
    id_sector SMALLINT REFERENCES sectores(id),
    zona_interes JSONB
);

CREATE  TABLE reglas(
    id SERIAL PRIMARY KEY,
    riesgo VARCHAR(10),
    tipoAlerta VARCHAR(50), -- SEPARADAS POR COMA
    horaInicio TIME,
    horaFin TIME,
    score INT,
    sector VARCHAR(100)
);

CREATE TABLE alertas (
    id SERIAL PRIMARY KEY,
    id_camara INTEGER NOT NULL REFERENCES camaras(id),
    mensaje TEXT NOT NULL,
    hora_suceso TIMESTAMP NOT NULL,
    tipo SMALLINT NOT NULL DEFAULT 0,
    score_confianza NUMERIC NOT NULL,
    clip VARCHAR(100),
    descripcion_suceso TEXT,
    estado SMALLINT NOT NULL DEFAULT 0,
    reconnect_attempts INTEGER DEFAULT 0,
    max_reconnect_attempts INTEGER DEFAULT 3,
    last_attempt_time TIMESTAMP,
    sector INT
);

CREATE TABLE usuarios (
    id SERIAL PRIMARY KEY,
    usuario VARCHAR(50) UNIQUE NOT NULL,
    contrasena TEXT NOT NULL,
    nombre VARCHAR(100) NOT NULL,
    rol SMALLINT NOT NULL DEFAULT 0
);

CREATE TABLE tipos_alerta (
    id SERIAL PRIMARY KEY,
    nombre_tipo VARCHAR(100),
    descripcion TEXT NOT NULL
);

-- Agrega el campo sector a las alertas
CREATE OR REPLACE FUNCTION sincronizar_sector_alerta()
RETURNS TRIGGER AS $$
BEGIN
    -- Solo actúa si se inserta o cambia el id_camara
    IF (TG_OP = 'INSERT') OR (TG_OP = 'UPDATE' AND NEW.id_camara IS DISTINCT FROM OLD.id_camara) THEN
        -- Obtener el id_sector de la cámara relacionada
        SELECT id_sector
        INTO NEW.sector
        FROM camaras
        WHERE id = NEW.id_camara;
        -- En caso de que no exista la cámara, puedes decidir qué hacer:
        IF NEW.sector IS NULL THEN
            RAISE NOTICE 'No se encontró cámara con id %, no se actualizó sector.', NEW.id_camara;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trigger_sincronizar_sector ON alertas;
CREATE TRIGGER trigger_sincronizar_sector
BEFORE INSERT OR UPDATE
ON alertas
FOR EACH ROW
EXECUTE FUNCTION sincronizar_sector_alerta();


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
    total_alertas_camaras_caidas BIGINT,
    camaras_activas BIGINT,
    confianza_promedio NUMERIC,
    alertas_confirmadas BIGINT,
    falsos_positivos BIGINT,
    merodeos BIGINT,
    portonazos BIGINT,
    asaltos_hogar BIGINT,  -- Agregado para tipo 3
    camaras_caidas BIGINT,  -- Agregado para tipo 4
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
        SUM(CASE WHEN a.tipo IN (1, 2, 3) THEN 1 ELSE 0 END) as total_alertas,  -- Solo seguridad
        SUM(CASE WHEN a.tipo = 4 THEN 1 ELSE 0 END) as total_alertas_camaras_caidas,  -- Solo cámaras caídas
        COUNT(DISTINCT c.id) as camaras_activas,
        ROUND(AVG(a.score_confianza), 2) as confianza_promedio,
        -- Solo alertas confirmadas de tipo 1, 2 y 3
        SUM(CASE WHEN a.estado = 1 AND a.tipo IN (1, 2, 3) THEN 1 ELSE 0 END) as alertas_confirmadas,
        -- Solo falsos positivos de tipo 1, 2 y 3
        SUM(CASE WHEN a.estado = 2 AND a.tipo IN (1, 2, 3) THEN 1 ELSE 0 END) as falsos_positivos,
        SUM(CASE WHEN a.tipo = 1 THEN 1 ELSE 0 END) as merodeos,
        SUM(CASE WHEN a.tipo = 2 THEN 1 ELSE 0 END) as portonazos,
        SUM(CASE WHEN a.tipo = 3 THEN 1 ELSE 0 END) as asaltos_hogar,
        SUM(CASE WHEN a.tipo = 4 THEN 1 ELSE 0 END) as camaras_caidas,
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
INSERT INTO public.sectores (id, nombre_sector, descripcion, coordinates) VALUES
(1, 'Sector 1', 'Descripción del Sector 1.', '[
    [-33.5104284, -70.6115323],
    [-33.5261571, -70.6097246],
    [-33.5259878, -70.6066996],
    [-33.5286804, -70.6029879],
    [-33.5281327, -70.5877862],
    [-33.5227191, -70.5899227],
    [-33.5224615, -70.5789122],
    [-33.5190845, -70.5810952],
    [-33.5106144, -70.5888933],
    [-33.5098522, -70.5906195],
    [-33.5084694, -70.6132972],
    [-33.5104284, -70.6115323]
]'::jsonb),
(2, 'Sector 2', 'Descripción del Sector 2.', '[
    [-33.5168933, -70.5583213],
    [-33.5160482, -70.5591322],
    [-33.5113533, -70.5605823],
    [-33.5112102, -70.5644447],
    [-33.5117594, -70.5660124],
    [-33.5101368, -70.5876869],
    [-33.5106144, -70.5888933],
    [-33.5190845, -70.5810952],
    [-33.5224615, -70.5789122],
    [-33.5272522, -70.5768461],
    [-33.5262708, -70.5733565],
    [-33.5273361, -70.5728563],
    [-33.5273322, -70.5687867],
    [-33.525825,  -70.5561342],
    [-33.5240734, -70.5565387],
    [-33.5215346, -70.5567689],
    [-33.518604,  -70.5582591],
    [-33.5168933, -70.5583213]
]'::jsonb),
(3, 'Sector 3', 'Descripción del Sector 3.', '[
    [-33.5149213, -70.540625],
    [-33.5124759, -70.5418328],
    [-33.5123944, -70.542096],
    [-33.5113533, -70.5605823],
    [-33.5160482, -70.5591322],
    [-33.5168933, -70.5583213],
    [-33.518604,   -70.5582591],
    [-33.5215346, -70.5567689],
    [-33.5240734, -70.5565387],
    [-33.525825,   -70.5561342],
    [-33.5264163, -70.5559086],
    [-33.5296737, -70.5555532],
    [-33.5290519, -70.5508387],
    [-33.5296461, -70.5391721],
    [-33.5269008, -70.5389861],
    [-33.5261294, -70.5442467],
    [-33.5205091, -70.5433256],
    [-33.5149213, -70.540625]
]'::jsonb),
(4, 'Sector 4', 'Descripción del Sector 4.', '[
    [-33.5124759, -70.5418328],
    [-33.5149213, -70.540625],
    [-33.5205091, -70.5433256],
    [-33.5261294, -70.5442467],
    [-33.526239,   -70.5434064],
    [-33.5269008, -70.5389861],
    [-33.528779,   -70.5221758],
    [-33.5283141, -70.5202383],
    [-33.5263643, -70.5198275],
    [-33.527536,   -70.5097087],
    [-33.5250392, -70.5101058],
    [-33.5236275, -70.5112543],
    [-33.5227359, -70.5231639],
    [-33.5210245, -70.5252674],
    [-33.5191674, -70.5228857],
    [-33.5181315, -70.5232829],
    [-33.5156815, -70.5211211],
    [-33.504771,   -70.5194609],
    [-33.5061539, -70.522093],
    [-33.5064597, -70.5224971],
    [-33.5080551, -70.5242278],
    [-33.5083029, -70.5246315],
    [-33.508251,   -70.5264316],
    [-33.5084262, -70.5277517],
    [-33.508705,   -70.5288254],
    [-33.5090658, -70.5298141],
    [-33.5092061, -70.5305042],
    [-33.5094508, -70.531498],
    [-33.5097077, -70.5321124],
    [-33.5108387, -70.5357338],
    [-33.5112317, -70.5371503],
    [-33.5114636, -70.5377515],
    [-33.5120873, -70.5389331],
    [-33.5124759, -70.5418328]
]'::jsonb),
(5, 'Sector 5', 'Descripción del Sector 5.', '[
    [-33.5286804, -70.6029879],
    [-33.5259878, -70.6066996],
    [-33.5261571, -70.6097246],
    [-33.537821,  -70.6102302],
    [-33.5391824, -70.6035255],
    [-33.5395758, -70.5911068],
    [-33.5354619, -70.5927646],
    [-33.5360619, -70.584835],
    [-33.5281327, -70.5877862],
    [-33.5286804, -70.6029879]
]'::jsonb),
(6, 'Sector 6', 'Descripción del Sector 6.', '[
    [-33.5227191, -70.5899227],
    [-33.5281327, -70.5877862],
    [-33.5360619, -70.584835],
    [-33.5355977, -70.5735912],
    [-33.5397292, -70.5718317],
    [-33.5399347, -70.561167],
    [-33.5392658, -70.5565162],
    [-33.5361257, -70.5570285],
    [-33.5357757, -70.5546226],
    [-33.5323483, -70.5550141],
    [-33.5296737, -70.5555532],
    [-33.5264163, -70.5559086],
    [-33.525825,  -70.5561342],
    [-33.5273322, -70.5687867],
    [-33.5273361, -70.5728563],
    [-33.5262708, -70.5733565],
    [-33.5272522, -70.5768461],
    [-33.5224615, -70.5789122],
    [-33.5227191, -70.5899227]
]'::jsonb),
(7, 'Sector 7', 'Descripción del Sector 7.', '[
    [-33.537821,  -70.6102302],
    [-33.5466471, -70.6105586],
    [-33.5467651, -70.603617],
    [-33.5470424, -70.5877006],
    [-33.5472878, -70.579111],
    [-33.5453066, -70.5793846],
    [-33.5395899, -70.5834124],
    [-33.5360619, -70.584835],
    [-33.5354619, -70.5927646],
    [-33.5395758, -70.5911068],
    [-33.5391824, -70.6035255],
    [-33.537821,  -70.6102302]
]'::jsonb),
(8, 'Sector 8', 'Descripción del Sector 8.', '[
    [-33.5360619, -70.584835],
    [-33.5395899, -70.5834124],
    [-33.5453066, -70.5793846],
    [-33.5472878, -70.579111],
    [-33.5474251, -70.5684186],
    [-33.5478621, -70.5541638],
    [-33.5469326, -70.5532432],
    [-33.5459313, -70.5510354],
    [-33.5410673, -70.5527997],
    [-33.5400669, -70.5528953],
    [-33.5357757, -70.5546226],
    [-33.5361257, -70.5570285],
    [-33.5392658, -70.5565162],
    [-33.5393934, -70.5578948],
    [-33.5399347, -70.561167],
    [-33.5397292, -70.5718317],
    [-33.5355977, -70.5735912],
    [-33.5360619, -70.584835]
]'::jsonb),
(9, 'Sector 9', 'Descripción del Sector 9.', '[
    [-33.5296461, -70.5391721],
    [-33.5290519, -70.5508387],
    [-33.5296715, -70.5554942],
    [-33.5302351, -70.555422],
    [-33.5323483, -70.5550141],
    [-33.5357757, -70.5546226],
    [-33.5400669, -70.5528953],
    [-33.5410673, -70.5527997],
    [-33.5459313, -70.5510354],
    [-33.5469326, -70.5532432],
    [-33.5478621, -70.5541638],
    [-33.5478965, -70.5500213],
    [-33.5489902, -70.5488478],
    [-33.5503418, -70.548261],
    [-33.5508566, -70.5471523],
    [-33.5519009, -70.5468554],
    [-33.5527091, -70.5458915],
    [-33.5506859, -70.5436714],
    [-33.5500218, -70.5439405],
    [-33.5501804, -70.5425788],
    [-33.5487652, -70.5413029],
    [-33.54561,   -70.5399817],
    [-33.5385843, -70.5351942],
    [-33.5351145, -70.5349367],
    [-33.5331518, -70.5371831],
    [-33.5329997, -70.5394652],
    [-33.5296461, -70.5391721]
]'::jsonb),
(10, 'Sector 10', 'Descripción del Sector 10.', '[
    [-33.5700324, -70.6114668],
    [-33.5673316, -70.5992203],
    [-33.567772,   -70.5980597],
    [-33.5661706, -70.5981643],
    [-33.5654275, -70.598462],
    [-33.5638696, -70.5985426],
    [-33.5634001, -70.5993044],
    [-33.5613884, -70.6004846],
    [-33.5577227, -70.6010426],
    [-33.5467651, -70.603617],
    [-33.5464833, -70.6148595],
    [-33.5618511, -70.6104441],
    [-33.5621679, -70.6125278],
    [-33.5700324, -70.6114668]
]'::jsonb),
(11, 'Sector 11', 'Descripción del Sector 11.', '[
    [-33.5467651, -70.603617],
    [-33.5577227, -70.6010426],
    [-33.5613884, -70.6004846],
    [-33.5634001, -70.5993044],
    [-33.5638696, -70.5985426],
    [-33.5654275, -70.598462],
    [-33.5661706, -70.5981643],
    [-33.567772,   -70.5980597],
    [-33.5698966, -70.5922174],
    [-33.5688231, -70.583927],
    [-33.565511,   -70.5845921],
    [-33.5470424, -70.5877006],
    [-33.5467651, -70.603617]
]'::jsonb),
(12, 'Sector 12', 'Descripción del Sector 12.', '[
    [-33.5470424, -70.5877006],
    [-33.565511,   -70.5845921],
    [-33.5608249, -70.557276],
    [-33.5474251, -70.5684186],
    [-33.5472878, -70.579111],
    [-33.5470424, -70.5877006]
]'::jsonb);

-- Insertar datos en la tabla camaras
INSERT INTO public.camaras ( nombre, posicion, direccion, estado_camara, ultima_conexion, link_camara, id_sector, link_camara_externo) VALUES
('Cámara Plaza', '{-33.5125, -70.60686}', 'Doctor Luis Calvo Mackenna 1361', true, '2024-06-09 19:30:00', 'rtsp://192.168.194.154:8554/cam_web', 1, 'http://localhost:5000/video_feed/1'),
('Cámara Sur', '{-33.51794, -70.59475}', 'El Blanco 178', true, '2024-06-09 19:30:00', 'rtsp://192.168.194.154:8554/cam_rtsp', 1, 'http://localhost:5000/video_feed/2'),
('Cámara Centro', '{-33.51989, -70.57076}', 'Avda Departamental 10450', false, '2024-06-09 19:30:00', 'rtsp://192.168.194.154:8554/cam_rtsp2', 2, 'http://localhost:5000/video_feed/3'),
('Cámara Merodeo', '{-33.53235, -70.60089}', 'Nva Uno 6476', true, '2024-06-09 19:30:00', '/loitering.mp4', 5, ''),
('Camára Asalto a Hogar', '{-33.5277, -70.57909}', 'Yungay 645', true, '2024-06-09 19:30:00', '/burglary.mp4', 6, ''),
('Cámara Portonazo', '{-33.53987, -70.57537}', 'Atahualpa 6892', true, '2024-06-09 19:30:00', '/portonazo.mp4', 8, ''),
('Cámara 7', '{-33.5442, -70.59787}', 'Calle Falsa 123', true, '2024-06-09 19:30:00', '/loitering.mp4', 7, ''),
('Cámara 8', '{-33.5233, -70.54786}', 'Calle Verdadera 456', true, '2024-06-09 19:30:00', '/loitering.mp4', 3, ''),
('Cámara 9', '{-33.51472, -70.53653}', 'Avenida Siempre Viva 789', true, '2024-06-09 19:30:00', '/loitering.mp4', 4, ''),
('Cámara 10', '{-33.53375, -70.55198}', 'Boulevard Central 101', true, '2024-06-09 19:30:00', '/burglary.mp4', 9, ''),
('Cámara 11', '{-33.55601, -70.60608}', 'Plaza Mayor 202', true, '2024-06-09 19:30:00', '', 10, '/burglary.mp4'),
('Cámara 12', '{-33.55551, -70.59466}', 'Callejón del Gato 303', true, '2024-06-09 19:30:00', '/burglary.mp4', 11, ''),
('Cámara 13', '{-33.55522, -70.57363}', 'Callejón del Gato 303', true, '2024-06-09 19:30:00', '/portonazo.mp4', 12, '');

-- Insertar datos en la tabla alertas
INSERT INTO public.alertas (id, id_camara, mensaje, hora_suceso, score_confianza, descripcion_suceso, estado, tipo, clip) VALUES
(2, 5, 'Merodeo detectado en Calle 1', '2025-06-18 16:39:00', 0.85, 'Persona merodeando en la casa 645', 1, 1, NULL),
(3, 4, 'Merodeo', '2025-08-10 21:41:34.128106', 0.9279417991638184, 'a woman is seen in this surveillance image', 1, 1, NULL),
(4, 4, 'Merodeo', '2025-08-10 21:41:49.365153', 0.9386122822761536, 'a man is seen in this surveillance image', 1, 1, NULL),
(5, 3, 'Merodeo', '2025-08-10 21:50:05.289628', 0.9279417991638184, 'a woman is seen in this surveillance image', 1, 1, NULL),
(6, 3, 'Merodeo', '2025-08-10 21:50:21.728442', 0.9386122822761536, 'a man is seen in this surveillance image', 1, 1, NULL),
(7, 3, 'Merodeo', '2025-08-10 21:50:31.309801', 0.9300715923309326, 'a man is seen in this surveillance image', 1, 1, NULL),
(8, 1, 'Merodeo', '2025-08-10 22:05:32.867594', 0.9279417991638184, 'a woman is seen in this surveillance image', 1, 1, NULL),
(9, 1, 'Merodeo', '2025-08-10 22:17:42.564887', 0.9279417991638184, 'a woman is seen in this surveillance image', 1, 1, NULL),
(10, 1, 'Merodeo', '2025-08-10 22:18:46.271335', 0.9329694509506226, 'a woman is seen in this surveillance image', 1, 1, NULL),
(11, 3, 'Merodeo', '2025-08-10 22:25:38.354863', 0.93, 'a woman is seen in this surveillance image', 2, 1, NULL),
(12, 3, 'Merodeo', '2025-08-11 16:17:38.460177', 0.93, '[Alerta merodeo] 2025-08-11 12:17:27 | Activar protocolo: notificar a operador, enfocar cámara, registrar evidencia en enviar patrulla cercana.', 0, 1, NULL),
(13, 3, 'Merodeo', '2025-08-11 16:29:50.976066', 0.93, '[Alerta merodeo] 2025-08-11 12:29:39 | Activar protocolo: notificar a operador, enfocar cámara, registrar evidencia en enviar patrulla cercana.', 0, 1, NULL),
(14, 5, 'Merodeo', '2025-08-11 16:34:37.73551', 0.93, '[Alerta merodeo] 2025-08-11 12:34:27 |  Activar protocolo: notificar a operador, enfocar cámara, registrar evidencia en enviar patrulla cercana.', 0, 1, NULL),
(15, 5, 'Merodeo', '2025-08-11 16:46:24.940547', 0.93, '[Alerta merodeo] 2025-08-11 12:45:56 | Continuar observación pasiva en registro.', 0, 1, NULL),
(16, 1, 'Merodeo', '2025-08-12 21:50:21.635222', 0.93, '[Alerta merodeo] sin datos (timeout del analizador)', 0, 1, NULL),
(17, 1, 'Merodeo', '2025-08-12 23:22:04.24088', 0.93, '[Alerta merodeo] sin datos (timeout del analizador)', 1, 1, NULL),
(18, 1, 'Merodeo', '2025-08-12 23:27:12.847831', 0.93, '[Alerta merodeo] 2025-08-12 19:26:59 | Continuar observación pasiva en registro.', 1, 1, NULL),
(19, 5, 'Merodeo', '2025-08-12 23:38:06.396645', 0.93, '[Descripción] 2025-08-12 19:37:12 | Ropa: blusa blanca en pantalones negros, Cabello: largo en oscuro.', 2, 1, NULL),
(20, 5, 'Merodeo', '2025-08-12 23:44:44.747405', 0.93, 'La persona está vestida con una camiseta blanca, pantalones negros en zapatillas de deporte. No se observan accesorios visibles como gorra, capucha, mochila, guantes o gafas. El cabello', 1, 1, NULL),
(21, 3, 'Merodeo', '2025-08-13 00:21:00.251535', 0.93, 'No se observan detalles específicos sobre la ropa o accesorio. El cabello es oscuro en largo.', 1, 1, NULL),
(22, 2, 'Merodeo', '2025-08-14 04:18:21.907023', 0.93, 'No se observan ropas superiores o accesorio distintivo. El cabello es oscuro en corto. La persona lleva una camiseta blanca, pantalones negros en zapatillas.', 0, 1, NULL),
(23, 2, 'Merodeo', '2025-08-14 04:35:33.279086', 0.93, 'Descripción no disponible (error de procesamiento).', 0, 1, NULL),
(24, 3, 'Portonazo', '2025-08-14 05:20:58.495481', 0.89, '[Alerta merodeo] sin datos (error del analizador)', 1, 2, NULL),
(25, 2, 'Portonazo', '2025-08-14 05:26:41.290484', 0.93, '[Alerta merodeo] sin datos (error del analizador)', 0, 2, NULL),
(26, 6, 'Asalto hogar', '2025-08-14 05:32:57.458472', 0.75, 'La persona lleva una camiseta blanca en pantalones negros. No se observa anomalía.', 1, 3, NULL),
(27, 6, 'Asalto hogar', '2025-08-14 05:34:40.703153', 0.65, 'Niño con camiseta blanca en pantalón negro, lleva una mochila roja. Cabeza negra corta, no se observa barba. Se mueve con normalidad, observando el camino.', 1, 3, NULL),
(28, 7, 'Merodeo', '2025-01-05 10:20:15.543210', 0.88, 'Hombre con chaqueta oscura y gorra, mirando constantemente por encima del hombro.', 1, 1, NULL),
(29, 8, 'Merodeo', '2025-01-15 14:45:01.987654', 0.95, 'Mujer con mochila grande, deteniéndose frente a varias casas y tomando fotos.', 0, 1, NULL),
(30, 9, 'Asalto hogar', '2025-02-03 03:05:30.123456', 0.72, 'Vehículo sospechoso (sedán rojo) estacionado sin luces por más de 15 minutos cerca de la entrada.', 1, 3, NULL),
(31, 10, 'Portonazo', '2025-02-28 18:55:40.678901', 0.91, 'Dos individuos en moto esperando en una esquina, uno de ellos con casco oscuro y guantes.', 1, 2, NULL),
(32, 11, 'Merodeo', '2025-03-10 09:10:05.345678', 0.65, 'Persona sin hogar revisando basureros con actitud nerviosa.', 2, 1, NULL),
(33, 12, 'Merodeo', '2025-03-25 22:15:25.890123', 0.82, 'Sujeto vestido completamente de negro, moviéndose lentamente y ocultándose tras árboles.', 0, 1, NULL),
(34, 13, 'Portonazo', '2025-04-01 11:30:50.456789', 0.96, 'Coche con vidrios polarizados dando vueltas en el mismo bloque por tercera vez.', 1, 2, NULL),
(35, 1, 'Asalto hogar', '2025-04-20 01:40:10.012345', 0.78, 'Silueta captada trepando el muro perimetral de una propiedad.', 1, 3, NULL),
(36, 2, 'Merodeo', '2025-05-07 15:50:35.567890', 0.93, 'Adolescente patinando en la calle, pero mirando fijamente hacia las casas.', 0, 1, NULL),
(37, 3, 'Merodeo', '2025-05-19 06:12:00.112233', 0.87, 'Un individuo con abrigo largo a pesar del clima, hablando por un auricular y apuntando.', 1, 1, NULL),
(38, 4, 'Portonazo', '2025-06-05 20:25:45.667788', 0.90, 'Cuatro jóvenes en un automóvil blanco con matrículas cubiertas.', 1, 2, NULL),
(39, 5, 'Asalto hogar', '2025-06-29 02:00:55.998877', 0.70, 'Ruidos fuertes y repetitivos de rotura de vidrios captados por el micrófono de la cámara.', 1, 3, NULL),
(40, 6, 'Merodeo', '2025-07-14 13:40:20.334455', 0.85, 'Vendedor ambulante deteniéndose por un tiempo inusualmente largo en un punto.', 0, 1, NULL),
(41, 7, 'Merodeo', '2025-07-30 04:55:10.776655', 0.98, 'Detección de persona inmóvil en el umbral de una puerta por más de 3 minutos.', 1, 1, NULL),
(42, 8, 'Portonazo', '2025-09-09 19:15:30.221100', 0.68, 'Sospechoso con pasamontañas cerca de un vehículo esperando a entrar a un garaje.', 1, 2, NULL),
(43, 9, 'Merodeo', '2025-09-22 17:05:05.445566', 0.94, 'Mujer mayor con un bolso de mano grande, observando las cerraduras de las puertas.', 0, 1, NULL),
(44, 10, 'Asalto hogar', '2025-10-10 00:35:40.889900', 0.76, 'Se activa sensor de movimiento interno y se ve una sombra en la ventana de la cocina.', 1, 3, NULL),
(45, 11, 'Merodeo', '2025-10-04 21:00:15.112233', 0.89, 'Repartidor de pizza con actitud errática, no entrega pedido y se queda mirando la fachada.', 0, 1, NULL),
(46, 12, 'Portonazo', '2025-10-12 23:45:50.556677', 0.92, 'SUV negro bloqueando la salida de un coche en la calzada.', 1, 2, NULL),
(47, 13, 'Merodeo', '2025-10-15 08:08:08.888888', 0.97, 'Hombre con perro pequeño paseando, pero con movimientos rápidos de la cabeza, inspeccionando techos.', 1, 1, NULL);

-- Actualizar las secuencias
SELECT pg_catalog.setval('public.alertas_id_seq', 47, true);
SELECT pg_catalog.setval('public.camaras_id_seq', 13, true);
SELECT pg_catalog.setval('public.sectores_id_seq', 12, true);
SELECT pg_catalog.setval('public.tipos_alerta_id_seq', 1, false);

INSERT INTO reglas(id, riesgo, tipoAlerta, horaInicio, horaFin, score, sector) VALUES
(1, 'bajo', '1', '23:59', '23:59', 100, ''),
(2, 'medio', '2', '23:59', '23:59', 100, ''),
(3, 'alto', '3', '23:59', '23:59', 100, ''),
(4, 'critico', '', '00:00', '23:59', 90, '');