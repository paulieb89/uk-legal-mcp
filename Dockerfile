FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
RUN pip install --no-cache-dir -e .

COPY src/ src/

EXPOSE 8000

CMD ["python", "-m", "src.gateway"]
