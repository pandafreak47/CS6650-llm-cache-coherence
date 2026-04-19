FROM python:3.11-slim

# git is required by GitClient for cloning and committing
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    gcc g++ cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./src/

EXPOSE 8080

# Run as a package so relative imports work
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
