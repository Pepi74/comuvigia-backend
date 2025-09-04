#!/bin/bash
# run_queries.sh

# Variables según tu configuración
CONTAINER_NAME="comuvigia-postgres"
DB_NAME="comuvigia-backend"
DB_USER="comuvigia"
DB_PASSWORD="comuvigia123"

# Ejecutar el script SQL directamente
docker exec -i $CONTAINER_NAME psql -U $DB_USER -d $DB_NAME << EOF
$(cat init_data.sql)
EOF

echo "Queries ejecutadas exitosamente!"