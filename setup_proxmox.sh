#!/bin/bash
# Скрипт настройки Proxmox VE для IT Courses Platform
# Запускайте на сервере Proxmox VE

set -e

echo "=== Настройка Proxmox VE для IT Courses Platform ==="

# Проверка, что скрипт запущен от root
if [ "$EUID" -ne 0 ]; then 
    echo "Пожалуйста, запустите скрипт от root (sudo ./setup_proxmox.sh)"
    exit 1
fi

# Создание роли для IT Courses
echo "[1/3] Создание роли ITCourses..."
pveum role add ITCourses -privs "VM.Allocate VM.Audit VM.Config.Disk VM.Config.Network VM.Config.Options VM.PowerMgmt Sys.Audit Sys.Console SDN.Use" 2>/dev/null || echo "Роль ITCourses уже существует"

# Создание пользователя itcourses
echo "[2/3] Создание пользователя itcourses@pve..."
read -sp "Введите пароль для пользователя itcourses@pve: " PVE_PASSWORD
echo ""
pveum user add itcourses@pve --password "$PVE_PASSWORD" 2>/dev/null || echo "Пользователь itcourses@pve уже существует"

# Назначение прав пользователю
echo "[3/3] Назначение прав пользователю itcourses@pve..."
pveum aclmod / -user itcourses@pve -role ITCourses

echo ""
echo "=== Настройка завершена! ==="
echo ""
echo "Используйте следующие параметры для подключения:"
echo "  PVE_USER=itcourses@pve"
echo "  PVE_PASSWORD=<указанный_пароль>"
echo "  PVE_NODE=pve (или имя вашей ноды)"
echo ""
echo "Пример запуска приложения:"
echo "  export PVE_HOST=<ip_адрес_proxmox>"
echo "  export PVE_PORT=8006"
echo "  export PVE_USER=itcourses@pve"
echo "  export PVE_PASSWORD=<пароль>"
echo "  export PVE_NODE=pve"
echo "  export PVE_VERIFY_SSL=false"
echo "  python app.py"
echo ""
