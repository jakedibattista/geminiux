# Official Playwright Python image — Chromium + all system deps pre-installed.
# This avoids apt-get failures for font packages that don't exist on newer Debian.
FROM mcr.microsoft.com/playwright/python:v1.52.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
