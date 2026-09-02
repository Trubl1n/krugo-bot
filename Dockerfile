FROM python:3.11-slim

# Обновляем пакеты и устанавливаем системную утилиту ffmpeg, затем очищаем кэш apt
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Копируем файл зависимостей
COPY requirements.txt .

# Устанавливаем python-зависимости без кэширования
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все остальные файлы проекта
COPY . .

# Указываем команду запуска
CMD ["python", "main.py"]
