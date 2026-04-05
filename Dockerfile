FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --upgrade pip && \
    python -m pip install .

EXPOSE 8000

ENTRYPOINT ["a2a"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000", "--workspace", "/data"]
