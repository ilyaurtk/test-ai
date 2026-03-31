# IT Courses Platform - Полная документация по интеграции с Proxmox VE

## Обзор

Система преподавания IT-курсов с полноценной интеграцией Proxmox VE для предоставления изолированных рабочих мест студентам.

## Архитектура

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Студент       │────▶│  Flask App       │────▶│  Proxmox VE     │
│   (Браузер)     │◀────│  (WebSocket)     │◀────│  (LXC Контейнеры)│
└─────────────────┘     └──────────────────┘     └─────────────────┘
                              │
                              ▼
                       ┌──────────────────┐
                       │  SQLite Database │
                       └──────────────────┘
```

## Компоненты системы

### 1. Backend (Flask + Socket.IO)
- **app.py** - основное приложение
- WebSocket поддержка через Flask-SocketIO
- REST API для управления курсами и пользователями
- Интеграция с Proxmox VE API

### 2. База данных (SQLite)
Таблицы:
- `users` - пользователи (студенты и администраторы)
- `courses` - курсы с привязкой к контейнерам
- `containers` - информация о LXC контейнерах в Proxmox
- `user_progress` - прогресс студентов
- `terminal_sessions` - активные сессии терминалов

### 3. Proxmox VE Integration
- Аутентификация через ticket-based API
- Запуск/остановка LXC контейнеров
- Выполнение команд через exec API
- WebSocket терминал через termproxy

## Настройка Proxmox VE

### Шаг 1: Создание пользователя и роли

На сервере Proxmox VE выполните скрипт настройки:

```bash
# Скопируйте скрипт на сервер Proxmox
scp setup_proxmox.sh root@<proxmox_ip>:/root/

# Подключитесь к серверу Proxmox
ssh root@<proxmox_ip>

# Запустите скрипт
chmod +x /root/setup_proxmox.sh
./setup_proxmox.sh
```

Или вручную:

```bash
# Создать роль с необходимыми правами
pveum role add ITCourses -privs "VM.Allocate VM.Audit VM.Config.Disk VM.Config.Network VM.Monitor VM.PowerMgmt SDN.Use"

# Создать пользователя
pveum user add itcourses@pve --password <your_password>

# Назначить права
pveum aclmod / -user itcourses@pve -role ITCourses
```

### Шаг 2: Подготовка LXC контейнеров

Создайте шаблоны контейнеров для разных курсов:

```bash
# Пример создания контейнера для курса Python
pct create 100 local:vztmpl/debian-11-standard_11.6-1_amd64.tar.gz \
    --hostname python-course \
    --memory 512 \
    --swap 256 \
    --disk local-lvm:8G \
    --net0 name=eth0,bridge=vmbr0,type=veth \
    --ostype debian \
    --rootfs local-lvm:8G,size=8G \
    --features nesting=1,mount=fuse \
    --unprivileged 1

# Установите необходимое ПО в контейнер
pct enter 100
apt update && apt install -y python3 python3-pip git vim nano htop
# Настройте среду под конкретный курс
exit

# Превратите контейнер в шаблон
pct template 100
```

### Шаг 3: Настройка приложения

Скопируйте файл конфигурации и отредактируйте:

```bash
cp proxmox_config.example /etc/proxmox_ve_config.env
nano /etc/proxmox_ve_config.env
```

Укажите ваши параметры:
```
PVE_HOST=192.168.1.100
PVE_PORT=8006
PVE_USER=itcourses@pve
PVE_PASSWORD=your_secure_password
PVE_NODE=pve
PVE_VERIFY_SSL=false
```

## Запуск приложения

### Вариант 1: С переменными окружения

```bash
export PVE_HOST=192.168.1.100
export PVE_PORT=8006
export PVE_USER=itcourses@pve
export PVE_PASSWORD=your_secure_password
export PVE_NODE=pve
export PVE_VERIFY_SSL=false

cd /workspace
python app.py
```

### Вариант 2: Через systemd сервис

Создайте файл `/etc/systemd/system/it-courses.service`:

```ini
[Unit]
Description=IT Courses Platform
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/workspace
Environment="PVE_HOST=192.168.1.100"
Environment="PVE_PORT=8006"
Environment="PVE_USER=itcourses@pve"
Environment="PVE_PASSWORD=your_secure_password"
Environment="PVE_NODE=pve"
Environment="PVE_VERIFY_SSL=false"
ExecStart=/usr/local/bin/python app.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Запустите сервис:
```bash
systemctl daemon-reload
systemctl enable it-courses
systemctl start it-courses
systemctl status it-courses
```

## Использование

### Для администраторов

1. Войдите как admin/admin123
2. Создайте контейнер в панели администратора:
   - Укажите имя контейнера
   - VM ID контейнера в Proxmox
   - Ноду Proxmox
3. Создайте курс:
   - Название и описание
   - Контент задания
   - Изображение курса
   - Привяжите созданный контейнер

### Для студентов

1. Войдите под своим аккаунтом
2. Выберите курс
3. Нажмите "Запросить рабочее место"
4. Откроется терминал с подключением к LXC контейнеру
5. Выполняйте задания
6. Отмечайте выполненные задачи

## API Endpoints

### Proxmox VE API вызовы

| Метод | Endpoint | Описание |
|-------|----------|----------|
| POST | `/api2/json/access/ticket` | Получение тикета аутентификации |
| GET | `/api2/json/nodes/{node}/lxc/{vmid}/status/current` | Статус контейнера |
| POST | `/api2/json/nodes/{node}/lxc/{vmid}/status/start` | Запуск контейнера |
| POST | `/api2/json/nodes/{node}/lxc/{vmid}/status/stop` | Остановка контейнера |
| POST | `/api2/json/nodes/{node}/lxc/{vmid}/termproxy` | Получение билета для терминала |
| POST | `/api2/json/nodes/{node}/lxc/{vmid}/exec` | Выполнение команды |

### WebSocket события (Socket.IO)

| Событие | Направление | Описание |
|---------|-------------|----------|
| `connect` | Client→Server | Подключение клиента |
| `disconnect` | Client→Server | Отключение клиента |
| `terminal_input` | Client→Server | Ввод команды в терминале |
| `terminal_output` | Server→Client | Вывод результата команды |
| `terminal_resize` | Client→Server | Изменение размера терминала |

## Безопасность

### Рекомендации для продакшена

1. **Используйте HTTPS**:
   ```bash
   # Настройте reverse proxy с nginx
   apt install nginx certbot python3-certbot-nginx
   certbot --nginx -d your-domain.com
   ```

2. **Ограничьте права пользователя Proxmox**:
   - Создавайте отдельные контейнеры для каждого курса
   - Используйте unprivileged контейнеры
   - Ограничьте ресурсы (CPU, память, диск)

3. **Изоляция пользователей**:
   - Каждый студент получает отдельную сессию
   - Контейнеры перезапускаются после сессии
   - Логирование всех действий

4. **Регулярное обновление**:
   ```bash
   apt update && apt upgrade -y
   pip install --upgrade flask flask-socketio requests
   ```

## Мониторинг и логи

### Просмотр логов приложения

```bash
tail -f /workspace/server.log
```

### Мониторинг сессий в базе данных

```bash
sqlite3 /workspace/it_courses.db "SELECT * FROM terminal_sessions WHERE status='active';"
```

### Логи Proxmox VE

```bash
# Логи PVE API
journalctl -u pveproxy -f

# Логи контейнера
pct enter <vmid>
journalctl -f
```

## Troubleshooting

### Проблема: Не удаётся подключиться к Proxmox

**Решение:**
1. Проверьте доступность сервера:
   ```bash
   curl -k https://<pve_host>:8006/api2/json/version
   ```
2. Проверьте учётные данные
3. Убедитесь, что фаервол разрешает порт 8006

### Проблема: Терминал не работает

**Решение:**
1. Проверьте, что контейнер запущен:
   ```bash
   pct status <vmid>
   ```
2. Проверьте права пользователя Proxmox
3. Убедитесь, что в контейнере установлен bash

### Проблема: WebSocket не подключается

**Решение:**
1. Проверьте, что Flask-SocketIO установлен
2. Убедитесь, что нет блокировки WebSocket на proxy
3. Проверьте консоль браузера на ошибки

## Структура проекта

```
/workspace/
├── app.py                 # Основное приложение Flask
├── it_courses.db          # База данных SQLite
├── server.log             # Логи приложения
├── requirements.txt       # Зависимости Python
├── proxmox_config.example # Пример конфигурации Proxmox
├── setup_proxmox.sh       # Скрипт настройки Proxmox
├── DEPLOYMENT.md          # Этот файл
├── static/
│   ├── css/
│   │   └── style.css      # Стили (зелёный технический дизайн)
│   └── js/
│       └── terminal.js    # JavaScript для терминала
├── templates/
│   ├── login.html         # Страница входа
│   ├── student_dashboard.html  # Дашборд студента
│   ├── admin_dashboard.html    # Панель администратора
│   ├── course.html        # Страница курса
│   └── terminal.html      # Терминал с WebSocket
└── uploads/               # Загруженные изображения курсов
```

## Зависимости

```
Flask>=2.0.0
Flask-SocketIO>=5.3.0
requests>=2.25.0
urllib3>=1.26.0
python-engineio>=4.0.0
python-socketio>=5.0.0
websockets>=10.0
Werkzeug>=2.0.0
```

## Лицензия

Система разработана для образовательных целей.
