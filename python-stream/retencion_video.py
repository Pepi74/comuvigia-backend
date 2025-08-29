import boto3
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class RetentionManager:
    def __init__(self, s3_client, bucket_name):
        self.s3_client = s3_client
        self.bucket_name = bucket_name
        self.retention_days = 7
    
    def cleanup_old_batches(self):
        """Eliminar batches más antiguos que 7 días"""
        try:
            cutoff_date = datetime.now() - timedelta(days=self.retention_days)
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
            
            logger.info(f"Retention cleanup: Deleted {deleted_count} old batches")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error in retention cleanup: {str(e)}")
            return 0

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