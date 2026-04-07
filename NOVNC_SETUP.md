# Настройка noVNC для виртуальных машин Proxmox VE

## Обзор

Этот документ описывает интеграцию noVNC для доступа к виртуальным машинам (QEMU VM) в Proxmox VE через веб-интерфейс IT Courses Platform.

## Архитектура

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Студент       │────▶│  Flask App       │────▶│  Proxmox VE     │
│   (Браузер)     │◀────│  (WebSocket)     │◀────│  (QEMU VM)      │
│                 │     │                  │     │  + noVNC        │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

## Как это работает

### Для LXC контейнеров:
- Используется SSH подключение через paramiko
- Терминал работает через xterm.js в браузере
- Команды передаются через WebSocket

### Для QEMU виртуальных машин:
- Используется noVNC консоль Proxmox
- При запросе терминала для VM:
  1. Backend получает VNC proxy ticket через Proxmox API
  2. Генерируется WebSocket URL для подключения
  3. Frontend открывает noVNC консоль в новом окне

## Изменения в коде

### 1. Новая функция `get_vm_vnc_websocket_url()` в `app.py`

Функция получает WebSocket URL для noVNC подключения:

```python
def get_vm_vnc_websocket_url(vm_id, node=None):
    """Получить WebSocket URL для noVNC подключения к QEMU VM"""
    # 1. Получаем vncproxy ticket
    # 2. Получаем vncwebsocket URL
    # 3. Возвращаем полный WebSocket URL с тикетом
```

### 2. Обновленный обработчик `handle_terminal_init()` в `app.py`

Для VM теперь:
- Вызывается `get_vm_vnc_websocket_url()`
- Отправляется событие `novnc_required` с WebSocket URL и данными подключения

### 3. Обновленный JavaScript в `templates/terminal.html`

Обработчик `novnc_required`:
- Получает данные о VM (vm_id, node, host, websocket_url, ticket)
- Открывает noVNC консоль Proxmox в новом окне
- URL формата: `https://{host}:{port}/?console=kvm&novnc=1&vmid={vmid}&node={node}`

## API Endpoints Proxmox VE

### vncproxy (основной endpoint)
```
POST /api2/json/nodes/{node}/qemu/{vmid}/vncproxy
Body: {"websocket": 1}
Response: {"data": {"ticket": "...", "port": 5900, "websocket": "/vncwebsocket?..."}}
```

**Важно:** В новых версиях Proxmox VE (7.x, 8.x) endpoint `vncwebsocket` не требует отдельного POST запроса.
Достаточно получить тикет через `vncproxy` и сформировать WebSocket URL напрямую:

```
wss://{host}:{port}/api2/json/nodes/{node}/qemu/{vmid}/vncwebsocket?port={port}&vncticket={ticket}
```

Это решает ошибку `501 - Method 'POST /nodes/pve/qemu/1000/vncwebsocket' not implemented`.

## Использование

### 1. Создание курса с QEMU VM

В админ-панели при создании курса:
- Выберите тип ресурса: **Virtual Machine (QEMU)**
- Укажите ID шаблона VM в Proxmox
- Включите опцию "noVNC для VM"

### 2. Запрос рабочего места студентом

1. Студент выбирает курс с QEMU VM
2. Нажимает "Запросить рабочее место"
3. Система клонирует VM из шаблона
4. Запускает VM
5. Открывает терминал

### 3. Подключение к консоли VM

1. При инициализации терминала определяется тип ресурса (VM)
2. Backend получает VNC credentials от Proxmox
3. Frontend открывает noVNC консоль в новом окне
4. Студент работает с VM через графическую консоль

## Преимущества noVNC для VM

1. **Графический интерфейс** - полноценный доступ к GUI VM
2. **Безопасность** - используется Proxmox authentication ticket
3. **WebSocket** - современная технология, работает через firewall
4. **Интеграция** - использует встроенный noVNC Proxmox

## Требования к Proxmox VE

1. **Права пользователя**:
   - `VM.Audit` - просмотр статуса VM
   - `VM.PowerMgmt` - запуск/остановка VM
   - `Sys.Console` - доступ к консоли

2. **Настройки VM**:
   - Включен QEMU агент (опционально, для лучшего управления)
   - Настроен сетевой интерфейс
   - Установлена ОС с графическим интерфейсом (для full VM)

## Troubleshooting

### Проблема: Не открывается noVNC консоль

**Решение:**
1. Проверьте, что VM запущена: `pct status <vmid>` или через API
2. Проверьте права пользователя Proxmox
3. Убедитесь, что порт 8006 доступен из сети
4. Проверьте логи приложения: `tail -f /workspace/server.log`

### Проблема: Ошибка аутентификации noVNC

**Решение:**
1. Убедитесь, что vncproxy ticket получен успешно
2. Проверьте время жизни тикета (2 часа по умолчанию)
3. Обновите конфигурацию Proxmox в БД

### Проблема: noVNC показывает черный экран

**Решение:**
1. Дождитесь полной загрузки VM
2. Проверьте, что в VM установлен графический сервер
3. Попробуйте подключиться через прямую ссылку Proxmox

## Ссылки

- [Proxmox VE API Documentation](https://pve.proxmox.com/pve-docs/api-viewer/)
- [noVNC GitHub](https://github.com/novnc/noVNC)
- [Proxmox Forum: noVNC Setup](https://forum.proxmox.com/threads/how-to-set-up-novnc-on-a-web-application.123701/)
- [ProxmoxVE Python Library](https://github.com/zzantares/ProxmoxVE)

## Примеры URL noVNC

### Прямой доступ через Proxmox:
```
https://192.168.1.100:8006/?console=kvm&novnc=1&vmid=100&node=pve&resize=off
```

### Через vncproxy (с тикетом):
```
https://192.168.1.100:8006/vncproxy/?vmid=100&node=pve
```

### WebSocket подключение:
```
wss://192.168.1.100:8006/api2/json/nodes/pve/qemu/100/vncwebsocket?port=5900&vncticket=TICKET
```
