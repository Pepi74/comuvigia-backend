FROM python:3.10-slim

WORKDIR /app

# Instala libGL para opencv-python
RUN apt-get update && apt-get install -y ffmpeg

COPY camera_stream.py .
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 5000

CMD ["python", "camera_stream.py"]
