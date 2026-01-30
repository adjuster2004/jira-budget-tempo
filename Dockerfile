# Используем официальный легкий образ Python
FROM python:3.9-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Копируем файл зависимостей и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY app.py .

# Открываем порт, который использует Streamlit
EXPOSE 8501

# Команда для проверки здоровья контейнера (опционально)
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# Запускаем приложение
ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
