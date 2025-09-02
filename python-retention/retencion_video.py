import boto3
from botocore.client import Config
from datetime import datetime, timedelta, timezone
import logging

logger = logging.getLogger("retencion_video")
logger.setLevel(logging.INFO)

ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')
ch.setFormatter(fmt)
logger.addHandler(ch)

class RetentionManager:
    def __init__(self, s3_client, bucket_name):
        self.s3_client = s3_client
        self.bucket_name = bucket_name
        self.retention_days = 7 # Se define segun sea necesario
    
    def cleanup_old_batches(self):
        """Eliminar batches más antiguos que 7 días"""
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
            deleted_count = 0
            
            # Listar todos los objetos
            paginator = self.s3_client.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix='batches/'):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        if obj['LastModified'] < cutoff_date:
                            self.s3_client.delete_object(
                                Bucket=self.bucket_name,
                                Key=obj['Key']
                            )
                            deleted_count += 1
            
            logger.info(f"Limpieza de retencion: se eliminaron {deleted_count} batches antiguos")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error en limpieza de retencion: {str(e)}")
            return 0

# Si se utiliza en otro script en otra thread
"""
# En tu aplicación principal
from retencion_video import RetentionManager

# Inicializar el retention manager
retention_manager = RetentionManager(s3_client, S3_BUCKET_NAME)

# Programar limpieza diaria
import schedule
import time

def daily_cleanup():
    retention_manager.cleanup_old_batches()

# Programar limpieza cada 24 horas
schedule.every(24).hours.do(daily_cleanup)

# En un hilo separado
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(3600)  # Revisar cada hora

Thread(target=run_scheduler, daemon=True).start()
"""

# Si se utiliza como servicio aparte

if __name__ == "__main__":

    import os
    import time
    import schedule

    # Configuración S3
    S3_ENDPOINT = "http://minio:9000"  # Para MinIO local
    S3_ACCESS_KEY = os.environ["S3_ACCESS_KEY"]
    S3_SECRET_KEY = os.environ["S3_SECRET_KEY"]
    S3_BUCKET_NAME = os.environ["S3_BUCKET_NAME"]
    S3_REGION = "us-east-1"

    # Inicializar cliente S3
    s3_client = boto3.client(
                's3',
                endpoint_url=S3_ENDPOINT,
                aws_access_key_id=S3_ACCESS_KEY,
                aws_secret_access_key=S3_SECRET_KEY,
                config=Config(signature_version='s3v4'),
                region_name=S3_REGION
    )

    bucket_name = S3_BUCKET_NAME
    retention_manager = RetentionManager(s3_client, bucket_name)

    def daily_cleanup():
        logger.info("Ejecutando limpieza diaria de retencion")
        retention_manager.cleanup_old_batches()

    # Programar ejecución diaria, se puede definir segun sea necesario
    schedule.every(24).hours.do(daily_cleanup)

    logger.info("Inicializando servicio de retencion...")

    while True:
        logger.info("Revision de limpieza programada...")
        schedule.run_pending()
        time.sleep(3600) # Revisar cada x tiempo definido en segundos