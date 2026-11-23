FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt
COPY bot/ bot/
COPY main.py cloud_run.py ./
RUN mkdir data && chown nobody data
USER nobody
ENTRYPOINT ["python", "cloud_run.py"]
CMD []
