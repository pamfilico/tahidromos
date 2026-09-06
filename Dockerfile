FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TAHIDROMOS_DATA=/data \
    TAHIDROMOS_CONFIG_DIR=/etc/tahidromos/conf.d

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tahidromos/ ./tahidromos/

RUN mkdir -p /data /etc/tahidromos/conf.d

# smtp, submission, submission-over-TLS, imap, imaps, http
EXPOSE 25 587 465 143 993 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=6 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health').read()"

VOLUME ["/data"]

CMD ["python", "-m", "tahidromos"]
