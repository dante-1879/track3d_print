# track3d: web UI + headless build API in one container.
#   docker build -t track3d .
#   docker run -p 8765:8765 -v track3d-data:/data -e TRACK3D_API_KEY=change-me \
#              -e TRACK3D_CORS_ORIGINS=https://your-site.example track3d
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY track3d ./track3d

RUN useradd --create-home app && mkdir /data && chown app /data
USER app
VOLUME /data
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8765/api/health', timeout=4)" || exit 1

# Options can also be given as TRACK3D_* environment variables (see API.md).
CMD ["python", "-m", "track3d", "ui", "--host", "0.0.0.0", "--port", "8765", "--workdir", "/data", "--no-browser"]
