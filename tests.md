## Testing

Se implementaron:

- Tests unitarios para servicios de alertas
- Tests de integración para endpoints REST

Tecnologías utilizadas:
- Jest
- Supertest

Se manejaron variables de entorno específicas para testing, evitando dependencias de Docker mediante overrides locales.

También se separó la inicialización del servidor para permitir pruebas sin levantar servicios HTTP.