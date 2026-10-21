FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DATA_DIR=/data
WORKDIR /srv
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && groupadd -g 10001 rss && useradd -g rss -u 10001 -M -s /usr/sbin/nologin rss
COPY app ./app
# Fail the image build immediately if an API route needs an undeclared runtime dependency.
# In particular, OPML uploads require python-multipart during FastAPI route registration.
RUN python -c "from app.main import app; assert app.title == 'xsukax RSS Aggregator'"
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "from urllib.request import urlopen;assert urlopen('http://127.0.0.1:8080/healthz',timeout=4).status==200"
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8080","--workers","1","--no-access-log"]
