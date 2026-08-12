FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY config ./config
COPY src ./src
COPY dashboard ./dashboard

RUN pip install --no-cache-dir .

ENV ONPAGE_SEO_THRESHOLDS_PATH=/app/config/thresholds.yaml

ENTRYPOINT ["onpage-seo"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
