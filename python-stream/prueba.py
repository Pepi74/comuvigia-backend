# conexion_automatica.py
import cv2
import time

def conectar_camara():
    # Probar diferentes IPs y URLs
    configuraciones = [
        # IP fija que debería tener
        ("192.168.1.100", "rtsp://root:comuvigia25.@192.168.1.100:554/live.sdp"),
        
        # IP por defecto
        ("192.168.0.168", "rtsp://root:comuvigia25.@192.168.0.168:554/live.sdp"),
        
        # IP APIPA
        ("169.254.11.172", "rtsp://root:comuvigia25.@169.254.11.172:554/live.sdp"),
    ]
    
    for ip, rtsp_url in configuraciones:
        print(f"🔗 Probando: {ip}")
        cap = cv2.VideoCapture(rtsp_url)
        
        if cap.isOpened():
            print(f"✅ Conectado a {ip}")
            return cap, ip
        
        time.sleep(1)
    
    return None, None

# Usar así:
cap, ip_camara = conectar_camara()
if cap:
    print(f"🎥 Transmitiendo desde: {ip_camara}")
    # ... tu código de procesamiento