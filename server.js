import { httpServer } from './app.js';

const PORT = process.env.PORT || 3000;

httpServer.listen(PORT, () => {
  console.log(`Servidor escuchando en puerto ${PORT}`);
});