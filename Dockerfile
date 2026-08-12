FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ARCHITECTURE_AND_PLAN.md ./
COPY config ./config
COPY src ./src

RUN pip install --no-cache-dir .

ENTRYPOINT ["onpage-seo"]
CMD ["audit", "--help"]
