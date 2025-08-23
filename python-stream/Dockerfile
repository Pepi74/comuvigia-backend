#FROM python:3.10-slim

#WORKDIR /app

# Instala libGL para opencv-python
#RUN apt-get update && apt-get install -y ffmpeg

#COPY camera_stream.py .
#COPY requirements.txt .

#RUN pip install --no-cache-dir -r requirements.txt

#EXPOSE 5000

#CMD ["python", "camera_stream.py"]

FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y ffmpeg

COPY multi_camera_stream.py .
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalar dependencias para OpenCV
#RUN apt-get update && apt-get install -y \
#    libglib2.0-0 \
#    libsm6 \
#    libxext6 \
#    libxrender-dev \
#    && rm -rf /var/lib/apt/lists/*

EXPOSE 5000

CMD ["python", "multi_camera_stream.py"]
