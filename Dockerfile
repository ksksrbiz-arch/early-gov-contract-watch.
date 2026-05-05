FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Default: web dashboard + bot controller (operator controls bot from browser).
# Set CMD to ["python", "main.py"] to run the headless bot loop directly.
EXPOSE 8000
CMD ["python", "web_app.py"]
