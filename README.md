# IT Course Platform

Веб-приложение для преподавания курсов по информационным технологиям с интеграцией PVE (Proxmox VE) контейнеров.

## Возможности

### Для студентов:
- Просмотр доступных курсов
- Выполнение заданий с отслеживанием прогресса
- Запрос рабочего места (терминал с PVE контейнером)
- Отслеживание собственного прогресса

### Для администраторов:
- Создание и редактирование курсов (название, описание, контент, изображения)
- Управление PVE контейнерами для разных курсов
- Мониторинг прогресса всех пользователей
- Панель статистики

## Технические требования

- Python 3.8+
- Flask
- SQLite (или другая БД для production)
- Debian Linux
- Proxmox VE (опционально, для реальной интеграции контейнеров)

## Установка

### 1. Установка зависимостей

```bash
pip install flask werkzeug
```

### 2. Запуск приложения

```bash
python app.py
```

Приложение будет доступно по адресу: http://localhost:5000

### 3. Первый вход

По умолчанию создается администратор:
- **Логин:** admin
- **Пароль:** admin123

**Важно:** Смените пароль после первого входа!

## Структура проекта

```
/workspace
├── app.py                      # Основное приложение Flask
├── it_courses.db               # База данных SQLite (создается автоматически)
├── static/
│   ├── css/
│   │   └── style.css          # Стили в техническом зелёном стиле
│   └── uploads/               # Загруженные изображения курсов
└── templates/
    ├── base.html              # Базовый шаблон
    ├── index.html             # Главная страница
    ├── login.html             # Вход
    ├── register.html          # Регистрация
    ├── course.html            # Страница курса
    ├── terminal.html          # Терминал
    └── admin/                 # Админ-панель
        ├── dashboard.html
        ├── courses.html
        ├── create_course.html
        ├── edit_course.html
        ├── containers.html
        ├── create_container.html
        ├── users.html
        └── user_progress.html
```

## База данных

Приложение использует SQLite с следующими таблицами:

- **users** - Пользователи (студенты и администраторы)
- **courses** - Курсы с заданиями
- **containers** - PVE контейнеры (рабочие окружения)
- **user_progress** - Прогресс пользователей по курсам
- **terminal_sessions** - Активные сессии терминала

## Интеграция с Proxmox VE

Для полноценной работы с PVE контейнерами необходимо:

1. Установить библиотеку proxmoxer:
   ```bash
   pip install proxmoxer requests
   ```

2. Настроить подключение к PVE API в `app.py`:
   ```python
   from proxmoxer import Proxmox
   
   proxmox = Proxmox(
       host='pve.example.com',
       user='admin@pam',
       password='your_password',
       verify_ssl=False
   )
   ```

3. Обновить функцию `request_terminal()` для создания реальных контейнеров

## Production развертывание

Для production рекомендуется:

1. Использовать PostgreSQL вместо SQLite
2. Настроить Gunicorn или uWSGI
3. Использовать Nginx как reverse proxy
4. Настроить HTTPS
5. Изменить SECRET_KEY на случайную строку
6. Настроить реальную интеграцию с PVE API

```bash
# Пример запуска с Gunicorn
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

## Лицензия

MIT License
