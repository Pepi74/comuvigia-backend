import { Pool, types } from 'pg'
import dotenv from 'dotenv'

dotenv.config()

types.setTypeParser(1700, val => Number.parseFloat(val))

if (!process.env.DB_USER || !process.env.DB_HOST || !process.env.DB_NAME || !process.env.DB_PASSWORD || !process.env.DB_PORT) {
  throw new Error('Faltan variables de entorno para conexión a la base de datos')
}

const pool = new Pool({
  user: process.env.DB_USER,
  host: process.env.DB_HOST,
  database: process.env.DB_NAME,
  password: process.env.DB_PASSWORD,
  port: process.env.DB_PORT,
})

export default pool