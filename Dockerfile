FROM python:3.12-alpine

WORKDIR /app

RUN pip install flask requests --break-system-packages

COPY app/index.html /app/index.html
COPY app/entrypoint.sh /app/entrypoint.sh
COPY app/server.py /app/server.py

RUN chmod +x /app/entrypoint.sh

EXPOSE 80

CMD ["python", "/app/server.py"]
