# Jira Budget Monitor - статистика затрат по задачам

Запускается в docker без прав sudo

Доступ к странице по http://0.0.0.0:8501/


## 🛠️ Последовательность
- **Склонировать репозиторий**
```bash
git clone https://git.astralinux.ru/users/sesmirnov/repos/jira-budget-tempo/
cd jira-budget-tempo
```

- **Собираем образ**
```bash
docker build -t jira-monitor .
```

- **Запускаем контейнер**
```bash
docker run -d \
  --name jira-app \
  --restart unless-stopped \
  -p 8501:8501 \
  -v $(pwd)/config.json:/app/config.json \
  jira-monitor
```

- **Открываем в браузере**
http://0.0.0.0:8501/

- **Заполняем поля**
Jira Domain
Токен / Cookie

Токен можно создать в Профиль - Персональные токены

- **При изменении значения Ключ проекта**
Обязательно нажать Сохранить настройки

Работает сортировка, фильтрация и выгрузка в csv.
Можно выбрать светлую или темную темы.
