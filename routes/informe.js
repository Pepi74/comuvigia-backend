import { Router } from "express";
import pool from "../config/db.js";
import PDFDocument from 'pdfkit';
import { createCanvas } from 'canvas';
import Chart from 'chart.js/auto';
import { verificarToken } from "../middlewares/auth.js";
import { verificarRol } from "../middlewares/roles.js";

const router = Router();

router.get('/generar-pdf', verificarToken, verificarRol([1, 2]), async (req, res) => {
  try {
    const { fechaInicio, fechaFin } = req.query;
    
    // Obtener los datos (reutilizando tu lógica existente)
    const datos = await obtenerDatosParaPDF(fechaInicio, fechaFin);
    
    // Generar el PDF
    const pdfBuffer = await generarPDFCompleto(datos);
    
    // Configurar headers para descarga
    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader('Content-Disposition', `attachment; filename="reporte-estadisticas-${new Date().toISOString().split('T')[0]}.pdf"`);
    res.send(pdfBuffer);
    
  } catch (error) {
    console.error('Error generando PDF:', error);
    res.status(500).json({ 
      success: false, 
      error: 'Error al generar PDF',
      detalle: error.message 
    });
  }
});

// Función auxiliar para obtener datos (similar a tu lógica actual)
async function obtenerDatosParaPDF(fechaInicio, fechaFin) {
  let client;
  try {
    const dias = 7;
    const group = 'hour';

    let startDate, endDate;

    if (fechaInicio && fechaFin) {
      startDate = new Date(fechaInicio);
      endDate = new Date(fechaFin);
    } else {
      endDate = new Date();
      startDate = new Date();
      startDate.setDate(startDate.getDate() - parseInt(dias));
    }

    // ✅ DEFINIR estas variables que faltaban
    const fechaInicioStr = startDate.toISOString().replace('T', ' ').substring(0, 19);
    const fechaFinStr = endDate.toISOString().replace('T', ' ').substring(0, 19);

    let grupo;
    const gruposValidos = ['hour', 'day', 'week', 'month'];
    grupo = gruposValidos.includes(group) ? group : 'day';

    client = await pool.connect();
    
    const query = `
      SELECT r.*, s.coordinates
      FROM reporte_alertas_por_periodo($1, $2, $3) r
      JOIN sectores s ON r.id_sector = s.id
    `;

    const result = await client.query(query, [fechaInicioStr, fechaFinStr, grupo]);

    if (result.rows.length === 0) {
      return {
        periodo: {
          fechaInicio: fechaInicioStr,
          fechaFin: fechaFinStr,
          dias: Math.ceil((endDate - startDate) / (1000 * 60 * 60 * 24))
        },
        estadisticas_totales: {
          total_alertas: 0,
          alertas_confirmadas: 0,
          falsos_positivos: 0,
          merodeos: 0,
          portonazos: 0,
          asaltos_hogar: 0,
          no_especificados: 0,
          tasa_confianza: 0
        },
        sectores: []
      };
    }

    // ✅ COPIAR TODA LA LÓGICA DE PROCESAMIENTO de tu endpoint /sacar-data
    // Calcular totales
    const totales = {
      total_alertas: 0,
      alertas_confirmadas: 0,
      falsos_positivos: 0,
      merodeos: 0,
      portonazos: 0,
      asaltos_hogar: 0,
      no_especificados: 0
    };

    // Agrupar por sector
    const sectores = {};
    
    result.rows.forEach(row => {
      // Totales generales
      totales.total_alertas += parseInt(row.total_alertas) || 0;
      totales.alertas_confirmadas += parseInt(row.alertas_confirmadas) || 0;
      totales.falsos_positivos += parseInt(row.falsos_positivos) || 0;
      totales.merodeos += parseInt(row.merodeos) || 0;
      totales.portonazos += parseInt(row.portonazos) || 0;
      totales.no_especificados += parseInt(row.no_especificados) || 0;
      totales.asaltos_hogar += parseInt(row.asaltos_hogar) || 0;

      // Por sector
      const sectorId = row.id_sector;
      if (!sectores[sectorId]) {
        sectores[sectorId] = {
          id_sector: sectorId,
          nombre_sector: row.nombre_sector,
          total_alertas: 0,
          alertas_confirmadas: 0,
          falsos_positivos: 0,
          merodeos: 0,
          portonazos: 0,
          asaltos_hogar: 0,
          no_especificados: 0,
          coordinates: row.coordinates || null
        };
      }
      sectores[sectorId].coordinates = row.coordinates || null;
      sectores[sectorId].total_alertas += parseInt(row.total_alertas) || 0;
      sectores[sectorId].alertas_confirmadas += parseInt(row.alertas_confirmadas) || 0;
      sectores[sectorId].falsos_positivos += parseInt(row.falsos_positivos) || 0;
      sectores[sectorId].merodeos += parseInt(row.merodeos) || 0;
      sectores[sectorId].portonazos += parseInt(row.portonazos) || 0;
      sectores[sectorId].asaltos_hogar += parseInt(row.asaltos_hogar) || 0;
      sectores[sectorId].no_especificados += parseInt(row.no_especificados) || 0;
    });

    // Calcular tasas (igual que en tu endpoint original)
    const tasaConfianza = totales.total_alertas > 0 
      ? Math.round((totales.alertas_confirmadas / totales.total_alertas) * 100)
      : 0;
    
    const tasaPrecision = totales.total_alertas > 0 
      ? Math.round((totales.alertas_confirmadas / (totales.total_alertas - totales.falsos_positivos)) * 100)
      : 0;

    const tasaFalsosPositivos = totales.total_alertas > 0 
      ? Math.round((totales.falsos_positivos / totales.total_alertas) * 100)
      : 0;

    const scoreCalidad = totales.total_alertas > 0 
      ? Math.round((
          (totales.alertas_confirmadas * 2) -
          totales.falsos_positivos
        ) / (totales.total_alertas * 2) * 100)
      : 0;

    // Agregar las tasas calculadas a totales
    totales.tasa_confianza = tasaConfianza;
    totales.tasa_precision = tasaPrecision;
    totales.tasa_error = tasaFalsosPositivos;
    totales.score_calidad = scoreCalidad;

    // ✅ Convertir sectores a arreglo y ordenarlos por total_alertas
    const listaSectores = Object.values(sectores).sort((a, b) => b.total_alertas - a.total_alertas);

    // ✅ Obtener los sectores más críticos (por ejemplo, top 3)
    const sectoresCriticos = listaSectores.slice(0, 5);

    // Inicializar horarios
    const horarios = {
      madrugada: { merodeos: 0, portonazos: 0, asaltos_hogar: 0, falsos_positivos: 0, no_especificados: 0 },
      manana: { merodeos: 0, portonazos: 0, asaltos_hogar: 0, falsos_positivos: 0, no_especificados: 0 },
      tarde: { merodeos: 0, portonazos: 0, asaltos_hogar: 0, falsos_positivos: 0, no_especificados: 0 },
      noche: { merodeos: 0, portonazos: 0, asaltos_hogar: 0, falsos_positivos: 0, no_especificados: 0 }
    };

    // Recorrer los registros y acumular por franja horaria
    result.rows.forEach(row => {
      const hora = new Date(row.periodo).getHours();
      let franja;
      if (hora >= 0 && hora < 6) franja = 'madrugada';
      else if (hora >= 6 && hora < 12) franja = 'manana';
      else if (hora >= 12 && hora < 18) franja = 'tarde';
      else franja = 'noche';

      horarios[franja].merodeos += parseInt(row.merodeos) || 0;
      horarios[franja].portonazos += parseInt(row.portonazos) || 0;
      horarios[franja].asaltos_hogar += parseInt(row.asaltos_hogar) || 0;
      horarios[franja].falsos_positivos += parseInt(row.falsos_positivos) || 0;
      horarios[franja].no_especificados += parseInt(row.no_especificados) || 0;
    });

    return {
      periodo: {
        fechaInicio: fechaInicioStr,
        fechaFin: fechaFinStr,
        dias: Math.ceil((endDate - startDate) / (1000 * 60 * 60 * 24))
      },
      estadisticas_totales: totales,
      sectores: Object.values(sectores),
      sectores_criticos: sectoresCriticos,
      horarios_alertas: horarios,
      registros_crudos: result.rows // para análisis temporal
    };
    
  } catch (error) {
    console.error('Error en obtenerDatosParaPDF:', error);
    throw error;
  } finally {
    if (client) client.release();
  }
}

// Función para generar gráficos como imágenes
async function generarGraficoComoImagen(tipo, datos, ancho = 1000, alto = 600) {
  const escala = 2; // Doble resolución
  const canvas = createCanvas(ancho * escala, alto * escala);
  const ctx = canvas.getContext('2d');
  ctx.scale(escala, escala);

  const coloresGraficos = {
    primario: ['#3498db', '#2ecc71', '#e74c3c', '#f39c12', '#9b59b6', '#1abc9c'],
    secundario: ['#5dade2', '#58d68d', '#ec7063', '#f8c471', '#af7ac5', '#48c9b0']
  };

  let config;

  switch (tipo) {
    // 📊 Tipos de alertas
    case 'tipos_alertas':
      config = {
        type: 'bar',
        data: {
          labels: [
            'Merodeos',
            'Portonazos',
            'Asaltos Hogar',
            'No Especificados'
          ],
          datasets: [{
            data: [
              datos.estadisticas_totales.merodeos,
              datos.estadisticas_totales.portonazos,
              datos.estadisticas_totales.asaltos_hogar,
              datos.estadisticas_totales.falsos_positivos,
              datos.estadisticas_totales.no_especificados
            ],
            backgroundColor: coloresGraficos.primario,
            borderColor: '#fff',
            borderWidth: 2
          }]
        },
        options: {
          responsive: false,
          plugins: {
            title: {
              display: true,
              text: 'Distribución de Tipos de Alertas',
              font: { size: 28, weight: 'bold' } // 🔹 Más grande
            },
            legend: {
              position: 'bottom',
              labels: {
                font: { size: 18 }, // 🔹 Aumentado
                usePointStyle: true,
                boxWidth: 18,
                padding: 20
              }
            }
          }
        }
      };
      break;

    // 📈 Estadísticas generales
    case 'estadisticas_generales':
      config = {
        type: 'bar',
        data: {
          labels: ['Total', 'Confirmadas', 'Falsos Positivos'],
          datasets: [{
            label: 'Cantidad',
            data: [
              datos.estadisticas_totales.total_alertas,
              datos.estadisticas_totales.alertas_confirmadas,
              datos.estadisticas_totales.falsos_positivos
            ],
            backgroundColor: ['#3498db', '#2ecc71', '#e74c3c'],
            borderRadius: 10
          }]
        },
        options: {
          responsive: false,
          plugins: {
            title: {
              display: true,
              text: 'Estadísticas Generales',
              font: { size: 28, weight: 'bold' }
            },
            legend: { display: false }
          },
          scales: {
            x: {
              ticks: {
                font: { size: 20, weight: 'medium' }, // 🔹 Ejes más grandes
                padding: 10
              },
              grid: { color: 'rgba(0,0,0,0.05)' }
            },
            y: {
              beginAtZero: true,
              ticks: {
                font: { size: 20, weight: 'medium' },
                padding: 10
              },
              grid: { color: 'rgba(0,0,0,0.1)' }
            }
          }
        }
      };
      break;

    // 🏙️ Distribución por sectores
    case 'sectores':
      const sectores = datos.sectores || [];
      const nombres = sectores.map(s => s.nombre_sector);
      const totales = sectores.map(s => s.total_alertas);

      config = {
        type: 'bar',
        data: {
          labels: nombres,
          datasets: [{
            label: 'Total Alertas',
            data: totales,
            backgroundColor: coloresGraficos.primario[0],
            borderRadius: 8
          }]
        },
        options: {
          responsive: false,
          plugins: {
            title: {
              display: true,
              text: 'Alertas por Sector',
              font: { size: 28, weight: 'bold' }
            },
            legend: { display: false }
          },
          scales: {
            x: {
              ticks: {
                font: { size: 16 },
                maxRotation: 45,
                minRotation: 45,
                padding: 8,
                autoSkip: false
              },
              grid: { color: 'rgba(0,0,0,0.05)' }
            },
            y: {
              beginAtZero: true,
              ticks: { font: { size: 16 }, padding: 8 },
              grid: { color: 'rgba(0,0,0,0.1)' }
            }
          }
        }
      };
      break;
    // Alertas por franja horaria
      case 'horarios_alertas':
        const franjas = ['madrugada', 'manana', 'tarde', 'noche'];
        const tipos = ['merodeos', 'portonazos', 'asaltos_hogar', 'no_especificados'];

        const datasets = tipos.map((tipo, i) => ({
          label: tipo.charAt(0).toUpperCase() + tipo.slice(1).replace('_', ' '),
          data: franjas.map(f => datos.horarios_alertas[f][tipo]),
          backgroundColor: coloresGraficos.primario[i % coloresGraficos.primario.length],
          borderRadius: 5
        }));

        config = {
          type: 'bar',
          data: { labels: ['Madrugada', 'Mañana', 'Tarde', 'Noche'], datasets },
          options: {
            responsive: false,
            plugins: {
              title: {
                display: true,
                text: 'Alertas por Franja Horaria y Tipo',
                font: { size: 28, weight: 'bold' }
              },
              legend: {
                position: 'bottom',
                labels: { font: { size: 16 } }
              }
            },
            scales: {
              x: { stacked: true, ticks: { font: { size: 16 } } },
              y: { stacked: true, beginAtZero: true, ticks: { font: { size: 16 } } }
            }
          }
        };
        break;

      // 📈 Tendencia temporal (alertas por día)
      case 'tendencia_temporal':
        // Agrupamos las alertas por día
        const conteoPorDia = {};
        datos.registros_crudos?.forEach(row => {
          const fecha = new Date(row.periodo).toISOString().split('T')[0]; // yyyy-mm-dd
          conteoPorDia[fecha] = (conteoPorDia[fecha] || 0) + (parseInt(row.total_alertas) || 0);
        });

        const fechasOrdenadas = Object.keys(conteoPorDia).sort();
        const valores = fechasOrdenadas.map(f => conteoPorDia[f]);

        config = {
          type: 'line',
          data: {
            labels: fechasOrdenadas.map(f => {
              const fecha = new Date(f);
              return fecha.toLocaleDateString('es-ES', { day: '2-digit', month: 'short' });
            }),
            datasets: [{
              label: 'Alertas diarias',
              data: valores,
              borderColor: '#3498db',
              backgroundColor: 'rgba(52, 152, 219, 0.2)',
              borderWidth: 3,
              pointRadius: 5,
              pointBackgroundColor: '#2c3e50',
              tension: 0.3 // curva suave
            }]
          },
          options: {
            responsive: false,
            plugins: {
              title: {
                display: true,
                text: 'Tendencia Temporal de Alertas por Día',
                font: { size: 28, weight: 'bold' }
              },
              legend: {
                display: false
              }
            },
            scales: {
              x: {
                ticks: {
                  font: { size: 16 },
                  maxRotation: 45,
                  minRotation: 0
                },
                grid: { color: 'rgba(0,0,0,0.05)' }
              },
              y: {
                beginAtZero: true,
                ticks: { font: { size: 16 } },
                grid: { color: 'rgba(0,0,0,0.1)' }
              }
            }
          }
        };
        break;

        // 🗺️ Mapa de calor por cantidad de alertas
        case 'mapa_calor':
          const sectoresMapa = datos.sectores || [];
          const coordenadas = sectoresMapa.map(s => ({
            nombre: s.nombre_sector,
            coords: (() => {
              if (!s.coordinates) return null;
              // Si viene como GeoJSON
              if (s.coordinates.type === 'Polygon')
                return s.coordinates.coordinates[0].map(([lon, lat]) => [lat, lon]);
              if (s.coordinates.type === 'MultiPolygon')
                return s.coordinates.coordinates[0][0].map(([lon, lat]) => [lat, lon]);
              // Si ya es array simple
              return s.coordinates;
            })(), // Asegúrate que tu query o procesamiento incluya esto (ver nota abajo)
            total: s.total_alertas
          }));

          // Normalizar totales (para intensidad)
          const maxAlertas = Math.max(...sectoresMapa.map(s => s.total_alertas), 1);

          // Crear canvas y contexto 2D
          ctx.fillStyle = '#f0f0f0';
          ctx.fillRect(0, 0, ancho, alto);

          ctx.font = '20px Arial';
          ctx.fillStyle = '#2c3e50';
          ctx.fillText('Mapa de Calor por Cantidad de Alertas', 30, 40);

          // Calcular límites de coordenadas
          let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
          coordenadas.forEach(s => {
            if (!s.coords) return;
            s.coords.forEach(([lat, lon]) => {
              if (lat < minLat) minLat = lat;
              if (lat > maxLat) maxLat = lat;
              if (lon < minLon) minLon = lon;
              if (lon > maxLon) maxLon = lon;
            });
          });

          // Margen para evitar bordes
          const margin = 50;
          const latRange = maxLat - minLat;
          const lonRange = maxLon - minLon;

          // Ajuste de escala y offset
          const scale = Math.min(
            (ancho - 2 * margin) / lonRange,
            (alto - 2 * margin) / latRange
          );
          const offsetX = margin - minLon * scale;
          const offsetY = alto - margin + minLat * scale;


          // Dibujar cada sector
          coordenadas.forEach((sector) => {
            if (!sector.coords) return;
            const intensidad = sector.total / maxAlertas; // 0–1
            const color = `rgba(231, 76, 60, ${0.2 + intensidad * 0.8})`; // rojo más intenso según cantidad

            ctx.beginPath();
            sector.coords.forEach(([lat, lon], i) => {
              // Escalar coordenadas a una proyección simple (sin librería GIS)
              const x = lon * scale + offsetX;
              const y = -lat * scale + offsetY;
              if (i === 0) ctx.moveTo(x, y);
              else ctx.lineTo(x, y);
            });
            ctx.closePath();
            ctx.fillStyle = color;
            ctx.fill();
            ctx.strokeStyle = '#34495e';
            ctx.lineWidth = 1;
            ctx.stroke();

            // Etiqueta del sector
            const centroid = sector.coords.reduce(
              (acc, [lat, lon]) => [acc[0] + lat, acc[1] + lon],
              [0, 0]
            ).map(v => v / sector.coords.length);

            const labelX = (centroid[1] + 70.65) * 8000;
            const labelY = (centroid[0] + 33.55) * -8000;
            ctx.fillStyle = '#2c3e50';
            ctx.font = '14px Helvetica';
            ctx.fillText(sector.nombre, labelX - 30, labelY);
          });

          return canvas.toBuffer('image/png', { compressionLevel: 0 });


    default:
      throw new Error(`Tipo de gráfico desconocido: ${tipo}`);
  }

  new Chart(ctx, config);

  // Exportar como imagen PNG nítida sin compresión
  return canvas.toBuffer('image/png', { compressionLevel: 0 });
}




function formatearFecha(fechaStr) {
  const fecha = new Date(fechaStr);
  return fecha.toLocaleDateString('es-ES', {
    year: 'numeric',
    month: 'long',
    day: 'numeric'
  });
}

// Función principal para generar PDF
async function generarPDFCompleto(datos) {
  return new Promise((resolve, reject) => {
    try {
      const doc = new PDFDocument({ 
        margin: 40,
        size: 'A4',
        bufferPages: true
      });
      
      const buffers = [];
      doc.on('data', buffers.push.bind(buffers));
      doc.on('end', () => resolve(Buffer.concat(buffers)));

      const colores = {
        primario: '#2c3e50',
        secundario: '#3498db',
        acento: '#e74c3c',
        texto: '#2c3e50',
        fondo: '#f8f9fa',
        borde: '#bdc3c7'
      };

      // HEADER
      doc.rect(0, 0, doc.page.width, 80)
         .fill(colores.primario);
      
      doc.fontSize(20)
         .fillColor('white')
         .font('Helvetica-Bold')
         .text('REPORTE DE SEGURIDAD', 50, 30, { align: 'center' });
      
      doc.fontSize(12)
         .fillColor('#ecf0f1')
         .font('Helvetica')
         .text('Estadísticas de Alertas y Eventos', 50, 55, { align: 'center' });

      // Información del período
      let yPosition = 100;
      doc.fillColor(colores.texto)
         .fontSize(10)
         .text(`Período: ${formatearFecha(datos.periodo.fechaInicio)} - ${formatearFecha(datos.periodo.fechaFin)}`, 50, yPosition);

      yPosition += 20;
      
      // Línea decorativa
      doc.moveTo(50, yPosition)
         .lineTo(doc.page.width - 50, yPosition)
         .strokeColor(colores.secundario)
         .lineWidth(2)
         .stroke();
      
      yPosition += 30;

      // ✅ CONTROLAR ESPACIO DISPONIBLE ANTES DE AGREGAR CONTENIDO
      const espacioMinimoParaNuevaSeccion = 300; // espacio mínimo para nueva sección
      
      // Generar contenido con control de páginas
      // En generarPDFCompleto, cambia esta parte:
      generarContenidoConPaginas(doc, datos, colores, yPosition, espacioMinimoParaNuevaSeccion)
      .then(() => {
        // ✅ AGREGAR FOOTER SOLO AL FINAL
        doc.end();
      })
      .catch(reject);

    } catch (error) {
      reject(error);
    }
  });
}


// ✅ FUNCIÓN MEJORADA PARA CONTROLAR ESPACIO ENTRE SECCIONES
async function generarContenidoConPaginas(doc, datos, colores, yStart, espacioMinimo) {
  let yPosition = yStart;

  // Evita agregar páginas innecesarias
  async function verificarEspacio(alturaNecesaria) {
    const limiteInferior = doc.page.height - 100;
    if (yPosition + alturaNecesaria > limiteInferior) {
      doc.addPage();
      yPosition = 80;
    }
  }

  // 🔹 Sección 1: Análisis Visual
  doc.fontSize(16)
     .fillColor(colores.primario)
     .font('Helvetica-Bold')
     .text('ANÁLISIS TIPO DE DELITO', 50, yPosition);
  yPosition += 25;
  // 🟢 Gráfico 1: Tipos de alertas (ocupa casi media página)
  await verificarEspacio(350);
  const grafico1 = await generarGraficoComoImagen('tipos_alertas', datos, 900, 450);
  doc.image(grafico1, 50, yPosition, { fit: [500, 300], align: 'center' });
  yPosition += 330;
  
  // 🟢 Gráfico 2: Estadísticas generales (debajo del anterior)
  await verificarEspacio(350);
    doc.fontSize(16)
     .fillColor(colores.primario)
     .font('Helvetica-Bold')
     .text('ESTADÍSTICAS DE LA IA', 50, yPosition);
  yPosition += 25;
  const grafico2 = await generarGraficoComoImagen('estadisticas_generales', datos, 900, 450);
  doc.image(grafico2, 50, yPosition, { fit: [500, 300], align: 'center' });
  yPosition += 330;


  // 🔹 Sección 2: Distribución por Sectores
  if (datos.sectores.length > 0) {
    await verificarEspacio(300);

    doc.fontSize(14)
       .fillColor(colores.primario)
       .text('DISTRIBUCIÓN POR SECTORES', 50, yPosition);
    yPosition += 25;

    const grafico3 = await generarGraficoComoImagen('sectores', datos, 900, 450);
    doc.image(grafico3, 50, yPosition, { fit: [500, 300], align: 'center' });
    yPosition += 320;
  }

    // 🔹 Sección 2.5: Mapa de calor por cantidad de alertas
  if (datos.sectores.length > 0) {
    await verificarEspacio(350);
    doc.fontSize(14)
       .fillColor(colores.primario)
       .text('MAPA DE CALOR DE ALERTAS POR SECTOR', 50, yPosition);
    yPosition += 25;

    const graficoMapa = await generarGraficoComoImagen('mapa_calor', datos, 900, 600);
    doc.image(graficoMapa, 50, yPosition, { fit: [500, 400], align: 'center' });
    yPosition += 420;
  }

  // 🔹 Sección 3: Horarios de mayor incidencia
  if (datos.horarios_alertas) {
    await verificarEspacio(350);
    doc.fontSize(14)
      .fillColor(colores.primario)
      .text('HORARIOS DE MAYOR INCIDENCIA', 50, yPosition);
    yPosition += 25;

    const grafico4 = await generarGraficoComoImagen('horarios_alertas', datos, 900, 450);
    doc.image(grafico4, 50, yPosition, { fit: [500, 300], align: 'center' });
    yPosition += 320;
  }

  // 🔹 Sección 4: Tendencia temporal
  await verificarEspacio(350);
  doc.fontSize(14)
    .fillColor(colores.primario)
    .text('TENDENCIA TEMPORAL (ALERTAS POR DÍA)', 50, yPosition);
  yPosition += 25;

  const grafico5 = await generarGraficoComoImagen('tendencia_temporal', datos, 900, 450);
  doc.image(grafico5, 50, yPosition, { fit: [500, 300], align: 'center' });
  yPosition += 330;

  // 🔹 Sección 5: Resumen estadístico
  await verificarEspacio(350);
  agregarTablaResumen(doc, datos, colores, yPosition);
}


function agregarTablaResumen(doc, datos, colores, startY) {
  // Título de sección
  doc.fontSize(16)
     .fillColor(colores.primario)
     .font('Helvetica-Bold')
     .text('RESUMEN ESTADÍSTICO', 50, startY);

  const tablaX = 50;
  const tablaY = startY + 30;
  const tablaWidth = 300; // ancho de la tabla principal
  const tablaAlturaFila = 25;

  // Encabezado de tabla
  doc.rect(tablaX, tablaY, tablaWidth, tablaAlturaFila)
     .fill(colores.secundario);
  
  doc.fontSize(12)
     .fillColor('white')
     .font('Helvetica-Bold')
     .text('MÉTRICA', tablaX + 10, tablaY + 8)
     .text('VALOR', tablaX + tablaWidth - 90, tablaY + 8, { width: 80, align: 'right' });

  // Métricas sin "Sectores Críticos"
  const metrics = [
    { label: 'Total de Alertas', value: datos.estadisticas_totales.total_alertas, tipo: 'numero' },
    { label: 'Alertas Confirmadas', value: datos.estadisticas_totales.alertas_confirmadas, tipo: 'numero' },
    { label: 'Falsos Positivos', value: datos.estadisticas_totales.falsos_positivos, tipo: 'numero' },
    { label: 'Merodeos Detectados', value: datos.estadisticas_totales.merodeos, tipo: 'numero' },
    { label: 'Portonazos', value: datos.estadisticas_totales.portonazos, tipo: 'numero' },
    { label: 'Asaltos a Hogar', value: datos.estadisticas_totales.asaltos_hogar, tipo: 'numero' },
    { label: 'Tasa de Confianza', value: `${datos.estadisticas_totales.tasa_confianza}%`, tipo: 'porcentaje' },
    { label: 'Score de Calidad', value: `${datos.estadisticas_totales.score_calidad}%`, tipo: 'porcentaje' }
  ];

  let y = tablaY + tablaAlturaFila;

  metrics.forEach((metric, index) => {
    if (y + tablaAlturaFila > doc.page.height - 50) {
      doc.addPage();
      y = 50;
    }

    // Fondo alternado
    if (index % 2 === 0) {
      doc.rect(tablaX, y, tablaWidth, tablaAlturaFila).fill(colores.fondo);
    }

    // Texto métrica
    doc.fontSize(10)
       .fillColor(colores.texto)
       .font('Helvetica')
       .text(metric.label, tablaX + 10, y + 8);

    // Color valor
    let colorValor = colores.texto;
    if (metric.tipo === 'porcentaje') {
      const valorNum = parseInt(metric.value);
      colorValor = valorNum < 50 ? colores.acento : valorNum < 80 ? '#f39c12' : '#27ae60';
    }

    doc.fillColor(colorValor)
       .font(metric.tipo === 'porcentaje' ? 'Helvetica-Bold' : 'Helvetica')
       .text(metric.value.toString(), tablaX + tablaWidth - 90, y + 8, { width: 80, align: 'right' });

    y += tablaAlturaFila;
  });

  // Sectores críticos a la derecha de la tabla
  const listaX = tablaX + tablaWidth + 20; // espacio a la derecha
  const listaY = tablaY;
  const sectoresCriticosTexto = datos.sectores_criticos
    .map(s => `${s.nombre_sector} (${s.total_alertas})`);

  doc.fontSize(12)
     .fillColor(colores.primario)
     .font('Helvetica-Bold')
     .text('SECTORES CRÍTICOS', listaX, listaY);

  let yLista = listaY + 20;
  doc.fontSize(10).fillColor(colores.texto).font('Helvetica');

  sectoresCriticosTexto.forEach((s) => {
    doc.text(`• ${s}`, listaX, yLista);
    yLista += 15;
  });
}


export default router;
