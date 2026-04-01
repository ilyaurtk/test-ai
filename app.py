import os
import sqlite3
import requests
import urllib.parse
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from functools import wraps
from werkzeug.utils import secure_filename
import uuid
import base64
import urllib3
import asyncio
import websockets
import json
from flask_socketio import SocketIO, emit
from flask import request as flask_request
import subprocess
import threading
import select
import socket
import paramiko
from paramiko import SSHClient, AutoAddPolicy

import time

# Отключаем предупреждения о самоподписанных SSL сертификатах
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Константа задержки между остановкой и удалением контейнера (секунды)
STOP_TIMEOUT = 5

app = Flask(__name__)
app.secret_key = 'it_courses_secret_key_2024'
app.config['UPLOAD_FOLDER'] = '/workspace/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
app.config['SECRET_KEY'] = 'it_courses_secret_key_2024'

# Инициализация SocketIO для WebSocket поддержки
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Proxmox VE Configuration
PVE_HOST = os.getenv('PVE_HOST', '192.168.1.100')
PVE_PORT = int(os.getenv('PVE_PORT', 8006))
PVE_USER = os.getenv('PVE_USER', 'root@pam')
PVE_PASSWORD = os.getenv('PVE_PASSWORD', '')
PVE_NODE = os.getenv('PVE_NODE', 'pve')
PVE_VERIFY_SSL = os.getenv('PVE_VERIFY_SSL', 'false').lower() == 'true'

def save_pve_config_db(host, port, user, password, node, verify_ssl):
    """Сохранить конфигурацию Proxmox в базу данных"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pve_config (
            id INTEGER PRIMARY KEY,
            host TEXT,
            port INTEGER,
            user TEXT,
            password TEXT,
            node TEXT,
            verify_ssl INTEGER
        )
    ''')
    cursor.execute('DELETE FROM pve_config')
    cursor.execute('''
        INSERT INTO pve_config (id, host, port, user, password, node, verify_ssl)
        VALUES (1, ?, ?, ?, ?, ?, ?)
    ''', (host, port, user, password, node, 1 if verify_ssl else 0))
    conn.commit()
    conn.close()

def load_pve_config():
    """Загрузить конфигурацию Proxmox из базы данных"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pve_config (
            id INTEGER PRIMARY KEY,
            host TEXT,
            port INTEGER,
            user TEXT,
            password TEXT,
            node TEXT,
            verify_ssl INTEGER
        )
    ''')
    conn.commit()
    cursor.execute('SELECT * FROM pve_config WHERE id = 1')
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'host': row[1],
            'port': row[2],
            'user': row[3],
            'password': row[4],
            'node': row[5],
            'verify_ssl': bool(row[6])
        }
    return None

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db():
    conn = sqlite3.connect('/workspace/it_courses.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'student',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            content TEXT,
            image_path TEXT,
            container_id TEXT,
            template_vm_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_containers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            pve_vm_id INTEGER NOT NULL,
            pve_node TEXT DEFAULT 'pve',
            name TEXT NOT NULL,
            status TEXT DEFAULT 'stopped',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (course_id) REFERENCES courses (id),
            UNIQUE(user_id, course_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS containers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            pve_vm_id INTEGER NOT NULL,
            pve_node TEXT DEFAULT 'pve',
            status TEXT DEFAULT 'stopped',
            is_template INTEGER DEFAULT 0,
            course_id INTEGER,
            FOREIGN KEY (course_id) REFERENCES courses (id)
        )
    ''')
    
    # Миграция: добавляем столбец is_template если он отсутствует
    cursor.execute("PRAGMA table_info(containers)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'is_template' not in columns:
        cursor.execute('ALTER TABLE containers ADD COLUMN is_template INTEGER DEFAULT 0')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            progress_percent INTEGER DEFAULT 0,
            completed_tasks TEXT,
            last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (course_id) REFERENCES courses (id),
            UNIQUE(user_id, course_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS terminal_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            session_token TEXT UNIQUE NOT NULL,
            container_id INTEGER,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP,
            status TEXT DEFAULT 'active',
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (course_id) REFERENCES courses (id),
            FOREIGN KEY (container_id) REFERENCES containers (id)
        )
    ''')
    
    # Create default admin if not exists
    cursor.execute("SELECT * FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
            ('admin', 'admin123', 'admin')
        )
    
    conn.commit()
    conn.close()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Пожалуйста, войдите в систему', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'admin':
            flash('Доступ запрещён', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_pve_ticket():
    """Получить тикет аутентификации от Proxmox VE"""
    # Сначала пробуем загрузить конфигурацию из БД
    config = load_pve_config()
    if config:
        host = config['host']
        port = config['port']
        user = config['user']
        password = config['password']
        verify_ssl = config['verify_ssl']
    else:
        host = PVE_HOST
        port = PVE_PORT
        user = PVE_USER
        password = PVE_PASSWORD
        verify_ssl = PVE_VERIFY_SSL
    
    url = f"https://{host}:{port}/api2/json/access/ticket"
    data = {
        'username': user,
        'password': password
    }
    try:
        response = requests.post(url, data=data, verify=verify_ssl, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if 'data' in result:
                return result['data']['ticket'], result['data']['CSRFPreventionToken']
        else:
            print(f"PVE auth error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"PVE connection error: {e}")
    return None, None

def pve_api_request(method, endpoint, data=None):
    """Выполнить запрос к Proxmox VE API"""
    # Загружаем конфигурацию из БД
    config = load_pve_config()
    if config:
        host = config['host']
        port = config['port']
        verify_ssl = config['verify_ssl']
    else:
        host = PVE_HOST
        port = PVE_PORT
        verify_ssl = PVE_VERIFY_SSL
    
    ticket, csrf_token = get_pve_ticket()
    if not ticket:
        print("PVE: Failed to get authentication ticket")
        return None
    
    url = f"https://{host}:{port}/api2/json/{endpoint}"
    headers = {
        'Cookie': f'PVEAuthCookie={ticket}',
        'CSRFPreventionToken': csrf_token
    }
    
    try:
        if method == 'GET':
            response = requests.get(url, headers=headers, verify=verify_ssl, timeout=10)
        elif method == 'POST':
            response = requests.post(url, headers=headers, json=data, verify=verify_ssl, timeout=10)
        elif method == 'PUT':
            response = requests.put(url, headers=headers, json=data, verify=verify_ssl, timeout=10)
        elif method == 'DELETE':
            response = requests.delete(url, headers=headers, verify=verify_ssl, timeout=10)
        
        if response.status_code in [200, 201]:
            return response.json()
        else:
            print(f"PVE API error ({method} {endpoint}): {response.status_code} - {response.text}")
            # Логируем детальную информацию об ошибке для отладки
            if response.status_code == 400:
                try:
                    error_data = response.json()
                    print(f"PVE API error details: {error_data}")
                except:
                    pass
            return None
    except Exception as e:
        print(f"PVE request error: {e}")
        return None

def get_pve_node():
    """Получить текущий узел Proxmox из конфигурации"""
    config = load_pve_config()
    if config:
        return config['node']
    return PVE_NODE

def clone_container(template_vm_id, new_vm_id, name, node=None):
    """Клонировать LXC контейнер из шаблона в Proxmox"""
    if node is None:
        node = get_pve_node()
    endpoint = f"nodes/{node}/lxc/{template_vm_id}/clone"
    data = {
        'newid': new_vm_id,
        'hostname': name,  # Для LXC используем hostname вместо name
        'full': 1  # Полное клонирование
    }
    result = pve_api_request('POST', endpoint, data)
    if result is None:
        print(f"Clone failed for template {template_vm_id} -> {new_vm_id} ({name})")
    return result is not None

def delete_container(vm_id, node=None):
    """Удалить LXC контейнер в Proxmox"""
    if node is None:
        node = get_pve_node()
    endpoint = f"nodes/{node}/lxc/{vm_id}"
    result = pve_api_request('DELETE', endpoint)
    return result is not None

def start_container(vm_id, node=None):
    """Запустить LXC контейнер в Proxmox"""
    if node is None:
        node = get_pve_node()
    endpoint = f"nodes/{node}/lxc/{vm_id}/status/start"
    result = pve_api_request('POST', endpoint)
    return result is not None

def stop_container(vm_id, node=None):
    """Остановить LXC контейнер в Proxmox"""
    if node is None:
        node = get_pve_node()
    endpoint = f"nodes/{node}/lxc/{vm_id}/status/stop"
    result = pve_api_request('POST', endpoint)
    return result is not None

def get_container_status(vm_id, node=None):
    """Получить статус контейнера"""
    if node is None:
        node = get_pve_node()
    endpoint = f"nodes/{node}/lxc/{vm_id}/status/current"
    result = pve_api_request('GET', endpoint)
    if result and 'data' in result:
        return result['data'].get('status', 'unknown')
    return 'unknown'

def get_container_ip(vm_id, node=None):
    """Получить IP-адрес контейнера из Proxmox через API (включая DHCP) с повторными попытками"""
    if node is None:
        node = get_pve_node()
    
    # Делаем несколько попыток получения IP с интервалом в 2 секунды
    max_attempts = 30
    for attempt in range(max_attempts):
        app.logger.info(f"Attempt {attempt+1}/{max_attempts} to get IP for VM {vm_id}")
        
        # Пробуем получить IP через эндпоинт интерфейсов (работает для DHCP)
        endpoint = f"nodes/{node}/lxc/{vm_id}/interfaces"
        result = pve_api_request('GET', endpoint)
        
        app.logger.info(f"API Response for interfaces: {result}")
        
        if result and 'data' in result:
            interfaces = result['data']
            app.logger.info(f"Got interfaces data: {interfaces}")
            app.logger.info(f"Interfaces type: {type(interfaces)}")
            
            # Обработка разных форматов ответа
            if isinstance(interfaces, list):
                for iface in interfaces:
                    app.logger.info(f"Checking interface: {iface}")
                    # Ищем активные интерфейсы с IPv4 адресами (кроме lo)
                    iface_name = iface.get('name', '')
                    if iface_name == 'lo':
                        continue
                    
                    # Проверяем ip-addresses (список словарей) - основной формат
                    ips = iface.get('ip-addresses', [])
                    app.logger.info(f"Found ip-addresses: {ips}")
                    
                    if isinstance(ips, list):
                        for ip_info in ips:
                            if isinstance(ip_info, dict):
                                ip = ip_info.get('ip-address')
                                ip_type = ip_info.get('ip-address-type', 'inet')
                                app.logger.info(f"Checking IP: {ip}, type: {ip_type}")
                                if ip and ':' not in ip and ip_type == 'inet':  # Только IPv4
                                    app.logger.info(f"SUCCESS: Found valid IPv4 address: {ip}")
                                    return ip
                    
                    # Резервная проверка для ip-address (единственное число)
                    ipaddr = iface.get('ip-address')
                    if ipaddr and ':' not in ipaddr:
                        app.logger.info(f"Found IP address (single): {ipaddr}")
                        return ipaddr
                        
            elif isinstance(interfaces, dict):
                # Если пришел один интерфейс вместо списка
                app.logger.info(f"Single interface received: {interfaces}")
                ips = interfaces.get('ip-addresses', [])
                if isinstance(ips, list):
                    for ip_info in ips:
                        if isinstance(ip_info, dict):
                            ip = ip_info.get('ip-address')
                            if ip and ':' not in ip:
                                app.logger.info(f"Found IP address in single interface: {ip}")
                                return ip
        
        # Если не нашли через interfaces, пробуем конфиг (статический IP)
        endpoint = f"nodes/{node}/lxc/{vm_id}/config"
        config_result = pve_api_request('GET', endpoint)
        
        if config_result and 'data' in config_result:
            config = config_result['data']
            app.logger.info(f"Got config data: {config}")
            for key in config:
                if key.startswith('net'):
                    net_config = config[key]
                    if 'ip=' in net_config:
                        ip_part = net_config.split('ip=')[1].split(',')[0]
                        ip_addr = ip_part.split('/')[0]
                        app.logger.info(f"Found static IP in config: {ip_addr}")
                        return ip_addr
        
        app.logger.warning(f"Attempt {attempt+1} failed to find valid IPv4 address")
        if attempt < max_attempts - 1:
            import time
            time.sleep(2)
    
    app.logger.error(f"Failed to get IP address for VM {vm_id} after {max_attempts} attempts")
    return None

def get_vnc_proxy_url(vm_id, node=None):
    """Получить информацию для SSH подключения к контейнеру"""
    if node is None:
        node = get_pve_node()
    
    # Получаем IP-адрес контейнера
    container_ip = get_container_ip(vm_id, node)
    
    if container_ip:
        # Возвращаем информацию для SSH подключения
        return {
            'host': container_ip,
            'port': 22,
            'username': 'root',
            'password': 'P@ssw0rd'
        }
    
    return None

def get_container_console_ticket(vm_id, node=None):
    """Получить билет для доступа к консоли контейнера через termproxy/xterm.js"""
    if node is None:
        node = get_pve_node()
    
    endpoint = f"nodes/{node}/lxc/{vm_id}/termproxy"
    
    # Пробуем получить ticket с повторными попытками
    import time
    for attempt in range(5):
        try:
            result = pve_api_request('POST', endpoint)
            if result and 'data' in result:
                app.logger.info(f"Successfully got console ticket for VM {vm_id}")
                return result['data']
            else:
                app.logger.warning(f"Attempt {attempt+1} to get console ticket returned no data")
        except Exception as e:
            app.logger.warning(f"Attempt {attempt+1} to get console ticket failed: {e}")
        
        if attempt < 4:
            time.sleep(2)
    
    app.logger.error(f"Failed to get console ticket for VM {vm_id} after all attempts")
    return None

def get_pve_templates(node=None):
    """Получить список шаблонов LXC из Proxmox"""
    if node is None:
        node = get_pve_node()
    endpoint = f"nodes/{node}/lxc"
    result = pve_api_request('GET', endpoint)
    templates = []
    if result and 'data' in result:
        for vm in result['data']:
            if vm.get('template') == 1 or vm.get('template') == True:
                templates.append({
                    'vmid': vm.get('vmid'),
                    'name': vm.get('name', f'VM {vm.get("vmid")}'),
                    'description': vm.get('description', ''),
                    'status': vm.get('status', 'unknown')
                })
    return templates

def get_pve_containers(node=None):
    """Получить список всех LXC контейнеров из Proxmox"""
    if node is None:
        node = get_pve_node()
    endpoint = f"nodes/{node}/lxc"
    result = pve_api_request('GET', endpoint)
    containers = []
    if result and 'data' in result:
        for vm in result['data']:
            containers.append({
                'vmid': vm.get('vmid'),
                'name': vm.get('name', f'VM {vm.get("vmid")}'),
                'status': vm.get('status', 'unknown'),
                'template': vm.get('template', 0),
                'cpu': vm.get('cpu', 0),
                'maxcpu': vm.get('maxcpu', 0),
                'mem': vm.get('mem', 0),
                'maxmem': vm.get('maxmem', 0),
                'disk': vm.get('disk', 0),
                'maxdisk': vm.get('maxdisk', 0)
            })
    return containers

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('student_dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password))
        user = cursor.fetchone()
        conn.close()
        
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            flash('Вход выполнен успешно', 'success')
            return redirect(url_for('index'))
        else:
            flash('Неверное имя пользователя или пароль', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    user_id = session.get('user_id')
    
    # Завершаем все активные сессии пользователя и удаляем контейнеры
    if user_id:
        conn = get_db()
        cursor = conn.cursor()
        
        # Получаем все активные сессии пользователя
        cursor.execute("""
            SELECT ts.id, ts.container_id, cont.pve_vm_id, cont.pve_node
            FROM terminal_sessions ts
            JOIN containers cont ON ts.container_id = cont.id
            WHERE ts.user_id = ? AND ts.status = 'active'
        """, (user_id,))
        active_sessions = cursor.fetchall()
        
        for sess in active_sessions:
            # Останавливаем и удаляем контейнер в Proxmox
            if sess['pve_vm_id']:
                stop_container(sess['pve_vm_id'], sess['pve_node'])
                delete_container(sess['pve_vm_id'], sess['pve_node'])
            
            # Удаляем запись о контейнере из БД
            if sess['container_id']:
                cursor.execute("DELETE FROM containers WHERE id = ?", (sess['container_id'],))
            
            # Обновляем статус сессии
            cursor.execute("""
                UPDATE terminal_sessions 
                SET status = 'closed', ended_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (sess['id'],))
        
        conn.commit()
        conn.close()
    
    session.clear()
    flash('Вы вышли из системы. Все рабочие места удалены.', 'success')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def student_dashboard():
    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT c.*, 
               COALESCE(up.progress_percent, 0) as progress,
               up.last_accessed
        FROM courses c
        LEFT JOIN user_progress up ON c.id = up.course_id AND up.user_id = ?
        ORDER BY c.created_at DESC
    """, (session['user_id'],))
    courses = cursor.fetchall()
    
    cursor.execute("""
        SELECT ts.*, c.title as course_title, cont.name as container_name
        FROM terminal_sessions ts
        JOIN courses c ON ts.course_id = c.id
        LEFT JOIN containers cont ON ts.container_id = cont.id
        WHERE ts.user_id = ? AND ts.status = 'active'
        ORDER BY ts.started_at DESC
    """, (session['user_id'],))
    active_sessions = cursor.fetchall()
    
    conn.close()
    
    return render_template('student_dashboard.html', courses=courses, active_sessions=active_sessions)

@app.route('/course/<int:course_id>')
@login_required
def view_course(course_id):
    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM courses WHERE id = ?", (course_id,))
    course = cursor.fetchone()
    
    if not course:
        flash('Курс не найден', 'error')
        return redirect(url_for('student_dashboard'))
    
    cursor.execute("""
        SELECT * FROM user_progress 
        WHERE user_id = ? AND course_id = ?
    """, (session['user_id'], course_id))
    progress = cursor.fetchone()
    
    cursor.execute("""
        SELECT * FROM terminal_sessions 
        WHERE user_id = ? AND course_id = ? AND status = 'active'
        ORDER BY started_at DESC LIMIT 1
    """, (session['user_id'], course_id))
    active_session = cursor.fetchone()
    
    conn.close()
    
    progress_percent = progress['progress_percent'] if progress else 0
    completed_tasks = progress['completed_tasks'] if progress and progress['completed_tasks'] else ''
    
    return render_template('course.html', 
                         course=course, 
                         progress_percent=progress_percent,
                         completed_tasks=completed_tasks.split(',') if completed_tasks else [],
                         active_session=active_session)

@app.route('/request_terminal/<int:course_id>', methods=['POST'])
@login_required
def request_terminal(course_id):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM courses WHERE id = ?", (course_id,))
    course = cursor.fetchone()
    
    if not course or not course['template_vm_id']:
        flash('Для этого курса не настроено рабочее место', 'error')
        conn.close()
        return redirect(url_for('view_course', course_id=course_id))
    
    # Проверяем, есть ли уже активная сессия у пользователя для этого курса
    cursor.execute("""
        SELECT ts.*, cont.pve_vm_id, cont.pve_node
        FROM terminal_sessions ts
        JOIN containers cont ON ts.container_id = cont.id
        WHERE ts.user_id = ? AND ts.course_id = ? AND ts.status = 'active'
        ORDER BY ts.started_at DESC LIMIT 1
    """, (session['user_id'], course_id))
    existing_session = cursor.fetchone()
    
    if existing_session:
        conn.close()
        flash('У вас уже есть активная сессия для этого курса', 'info')
        return redirect(url_for('terminal', session_token=existing_session['session_token']))
    
    # Получаем шаблон контейнера из Proxmox
    template_vm_id = course['template_vm_id']
    node = get_pve_node()
    
    # Генерируем новый VM ID для клонированного контейнера
    # Получаем список всех контейнеров чтобы найти свободный ID
    all_containers = get_pve_containers(node)
    used_ids = [c['vmid'] for c in all_containers]
    new_vm_id = 1000  # Начальный ID для пользовательских контейнеров
    while new_vm_id in used_ids:
        new_vm_id += 1
    
    # Генерируем уникальное имя контейнера (только lowercase буквы, цифры и дефисы для совместимости с DNS)
    # Формат должен начинаться с буквы и соответствовать RFC 1123
    random_suffix = uuid.uuid4().hex[:8]
    container_name = f"u{session['user_id']}-c{course_id}-{random_suffix}"
    
    # Клонируем шаблон
    app.logger.info(f"Cloning template {template_vm_id} to {new_vm_id}...")
    socketio.emit('progress', {'step': 1, 'total': 4, 'message': 'Клонирование контейнера...'}, room=flask_request.sid)
    if not clone_container(template_vm_id, new_vm_id, container_name, node):
        conn.close()
        flash('Не удалось создать рабочее место. Проверьте подключение к Proxmox.', 'error')
        return redirect(url_for('view_course', course_id=course_id))
    
    # Ждем пока контейнер будет создан и готов к запуску
    import time
    max_wait = 120  # Максимальное время ожидания 120 секунд
    wait_interval = 3  # Интервал проверки 3 секунды
    waited = 0
    
    while waited < max_wait:
        time.sleep(wait_interval)
        waited += wait_interval
        
        # Проверяем статус контейнера
        status = get_container_status(new_vm_id, node)
        
        # Если контейнер существует и не в состоянии создания, пробуем запустить
        if status != 'unknown':
            # Дополнительная задержка для полной готовности файловой системы
            time.sleep(5)
            break
    
    # Пробуем запустить контейнер с повторными попытками
    app.logger.info(f"Starting container {new_vm_id}...")
    socketio.emit('progress', {'step': 2, 'total': 4, 'message': 'Запуск контейнера...'}, room=flask_request.sid)
    start_attempts = 3
    started = False
    for attempt in range(start_attempts):
        if start_container(new_vm_id, node):
            started = True
            break
        time.sleep(2)
    
    if not started:
        conn.close()
        flash('Не удалось запустить рабочее место после нескольких попыток.', 'error')
        return redirect(url_for('view_course', course_id=course_id))
    
    # Увеличенная пауза после запуска для полной инициализации контейнера и консоли
    app.logger.info(f"Container {new_vm_id} started. Waiting for console initialization...")
    socketio.emit('progress', {'step': 3, 'total': 4, 'message': 'Ожидание готовности...'}, room=flask_request.sid)
    time.sleep(15)  # Ждем 15 секунд для полной загрузки служб внутри контейнера
    
    # Получаем IP адрес
    socketio.emit('progress', {'step': 4, 'total': 4, 'message': 'Получение IP адреса...'}, room=flask_request.sid)
    
    # Создаем запись в таблице containers для нового контейнера
    cursor.execute("""
        INSERT INTO containers (name, pve_vm_id, pve_node, status, is_template, course_id)
        VALUES (?, ?, ?, 'running', 0, ?)
    """, (container_name, new_vm_id, node, course_id))
    container_id = cursor.lastrowid
    
    # Создаем сессию терминала
    session_token = str(uuid.uuid4())
    cursor.execute("""
        INSERT INTO terminal_sessions (user_id, course_id, session_token, container_id, status)
        VALUES (?, ?, ?, ?, 'active')
    """, (session['user_id'], course_id, session_token, container_id))
    
    conn.commit()
    
    # Устанавливаем статус сессии как ready
    sid = flask_request.sid
    session_status[sid] = 'ready'
    socketio.emit('progress', {'step': 4, 'total': 4, 'message': 'Готово! Подключение...'}, room=sid)
    
    # Обновляем время последнего доступа
    cursor.execute("""
        INSERT OR REPLACE INTO user_progress (user_id, course_id, last_accessed)
        VALUES (?, ?, CURRENT_TIMESTAMP)
    """, (session['user_id'], course_id))
    conn.commit()
    conn.close()
    
    flash('Рабочее место создано и запущено!', 'success')
    return redirect(url_for('terminal', session_token=session_token))

@app.route('/terminal/<session_token>')
@login_required
def terminal(session_token):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT ts.*, c.title as course_title, cont.pve_vm_id, cont.pve_node
        FROM terminal_sessions ts
        JOIN courses c ON ts.course_id = c.id
        JOIN containers cont ON ts.container_id = cont.id
        WHERE ts.session_token = ? AND ts.user_id = ?
    """, (session_token, session['user_id']))
    
    session_data = cursor.fetchone()
    conn.close()
    
    if not session_data:
        flash('Сессия не найдена', 'error')
        return redirect(url_for('student_dashboard'))
    
    # Получаем URL для VNC прокси сессии через Proxmox API
    vnc_proxy_url = get_vnc_proxy_url(session_data['pve_vm_id'], session_data['pve_node'])
    
    return render_template('terminal.html', 
                         session_data=session_data, 
                         session_token=session_token,
                         PVE_HOST=PVE_HOST, 
                         PVE_PORT=PVE_PORT,
                         vnc_proxy_url=vnc_proxy_url)

@app.route('/api/terminal/<session_token>/exec', methods=['POST'])
@login_required
def terminal_exec(session_token):
    """API endpoint для выполнения команд в терминале через Proxmox"""
    data = request.get_json()
    command = data.get('command', '')
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT ts.*, cont.pve_vm_id, cont.pve_node
        FROM terminal_sessions ts
        JOIN containers cont ON ts.container_id = cont.id
        WHERE ts.session_token = ? AND ts.user_id = ?
    """, (session_token, session['user_id']))
    
    session_data = cursor.fetchone()
    conn.close()
    
    if not session_data:
        return jsonify({'error': 'Session not found'}), 404
    
    # Выполняем команду через Proxmox API
    endpoint = f"nodes/{session_data['pve_node']}/lxc/{session_data['pve_vm_id']}/exec"
    exec_data = {
        'command': command,
        'node': session_data['pve_node']
    }
    result = pve_api_request('POST', endpoint, exec_data)
    
    if result and 'data' in result:
        return jsonify({'output': result['data']})
    else:
        return jsonify({'error': 'Failed to execute command'}), 500

@app.route('/update_progress/<int:course_id>', methods=['POST'])
@login_required
def update_progress(course_id):
    data = request.get_json()
    task_id = data.get('task_id')
    completed = data.get('completed', False)
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT completed_tasks FROM user_progress 
        WHERE user_id = ? AND course_id = ?
    """, (session['user_id'], course_id))
    
    progress = cursor.fetchone()
    
    if progress and progress['completed_tasks']:
        tasks = progress['completed_tasks'].split(',')
    else:
        tasks = []
    
    if completed and task_id not in tasks:
        tasks.append(task_id)
    elif not completed and task_id in tasks:
        tasks.remove(task_id)
    
    progress_percent = len(tasks) * 25  # Предполагаем 4 задачи на курс
    
    cursor.execute("""
        INSERT OR REPLACE INTO user_progress (user_id, course_id, completed_tasks, progress_percent, last_accessed)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (session['user_id'], course_id, ','.join(tasks), min(progress_percent, 100)))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'progress': min(progress_percent, 100)})

# Admin routes
@app.route('/admin')
@admin_required
def admin_dashboard():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
    users = cursor.fetchall()
    
    cursor.execute("SELECT * FROM courses ORDER BY created_at DESC")
    courses = cursor.fetchall()
    
    # Контейнеры теперь загружаются автоматически из Proxmox, 
    # поэтому не загружаем их из БД для отображения
    
    cursor.execute("""
        SELECT up.*, u.username, c.title as course_title
        FROM user_progress up
        JOIN users u ON up.user_id = u.id
        JOIN courses c ON up.course_id = c.id
        ORDER BY up.last_accessed DESC
    """)
    progress_data = cursor.fetchall()
    
    # Загружаем конфигурацию PVE
    pve_config = load_pve_config()
    
    # Автоматически загружаем шаблоны из Proxmox
    pve_templates = get_pve_templates()
    
    conn.close()
    
    return render_template('admin_dashboard.html', 
                         users=users, 
                         courses=courses, 
                         progress_data=progress_data,
                         pve_config=pve_config,
                         pve_templates=pve_templates)

@app.route('/admin/save_pve_config', methods=['POST'])
@admin_required
def save_pve_config():
    host = request.form.get('host', '192.168.1.100')
    port = int(request.form.get('port', 8006))
    user = request.form.get('user', 'root@pam')
    password = request.form.get('password', '')
    node = request.form.get('node', 'pve')
    verify_ssl = request.form.get('verify_ssl', 'false').lower() == 'true'
    
    save_pve_config_db(host, port, user, password, node, verify_ssl)
    flash('Конфигурация Proxmox сохранена', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/test_pve_connection')
@admin_required
def test_pve_connection():
    """Проверить подключение к Proxmox и вернуть список шаблонов"""
    templates = get_pve_templates()
    all_containers = get_pve_containers()
    
    if templates or all_containers:
        return jsonify({
            'success': True,
            'templates': templates,
            'containers': all_containers,
            'message': f'Подключение успешно. Найдено шаблонов: {len(templates)}, контейнеров: {len(all_containers)}'
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Не удалось подключиться к Proxmox или нет данных'
        })

@app.route('/admin/get_pve_templates')
@admin_required
def get_pve_templates_route():
    """API endpoint для получения списка шаблонов"""
    templates = get_pve_templates()
    return jsonify({'templates': templates})

@app.route('/admin/create_user', methods=['POST'])
@admin_required
def create_user():
    username = request.form['username']
    password = request.form['password']
    role = request.form.get('role', 'student')
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
            (username, password, role)
        )
        conn.commit()
        flash(f'Пользователь {username} создан', 'success')
    except sqlite3.IntegrityError:
        flash(f'Пользователь {username} уже существует', 'error')
    
    conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/create_course', methods=['POST'])
@admin_required
def create_course():
    title = request.form['title']
    description = request.form['description']
    content = request.form['content']
    template_vm_id = request.form.get('template_vm_id')
    
    image_path = None
    if 'image' in request.files:
        file = request.files['image']
        if file and file.filename != '' and allowed_file(file.filename):
            filename = secure_filename(f"{uuid.uuid4()}_{file.filename}")
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            image_path = f'/uploads/{filename}'
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO courses (title, description, content, image_path, template_vm_id)
        VALUES (?, ?, ?, ?, ?)
    """, (title, description, content, image_path, template_vm_id if template_vm_id else None))
    
    conn.commit()
    conn.close()
    
    flash(f'Курс "{title}" создан', 'success')
    return redirect(url_for('admin_dashboard'))

# Эти маршруты больше не используются, так как контейнеры загружаются автоматически из Proxmox
# @app.route('/admin/create_container', methods=['POST'])
# @admin_required
# def create_container():
#     name = request.form['name']
#     pve_vm_id = request.form['pve_vm_id']
#     pve_node = request.form.get('pve_node', 'pve')
    
#     conn = get_db()
#     cursor = conn.cursor()
    
#     # Проверяем статус контейнера в Proxmox
#     status = get_container_status(pve_vm_id, pve_node)
    
#     cursor.execute("""
#         INSERT INTO containers (name, pve_vm_id, pve_node, status)
#         VALUES (?, ?, ?, ?)
#     """, (name, pve_vm_id, pve_node, status))
    
#     conn.commit()
#     conn.close()
    
#     flash(f'Контейнер "{name}" добавлен', 'success')
#     return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    if user_id == session['user_id']:
        flash('Нельзя удалить самого себя', 'error')
        return redirect(url_for('admin_dashboard'))
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    flash('Пользователь удалён', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete_course/<int:course_id>', methods=['POST'])
@admin_required
def delete_course(course_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM courses WHERE id = ?", (course_id,))
    conn.commit()
    conn.close()
    
    flash('Курс удалён', 'success')
    return redirect(url_for('admin_dashboard'))

# Этот маршрут больше не используется, так как контейнеры загружаются автоматически из Proxmox
# @app.route('/admin/delete_container/<int:container_id>', methods=['POST'])
# @admin_required
# def delete_container(container_id):
#     conn = get_db()
#     cursor = conn.cursor()
#     cursor.execute("DELETE FROM containers WHERE id = ?", (container_id,))
#     conn.commit()
#     conn.close()
    
#     flash('Контейнер удалён', 'success')
#     return redirect(url_for('admin_dashboard'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# WebSocket обработчики для работы с терминалом Proxmox
# Хранилище активных SSH подключений
ssh_connections = {}

# Словарь для отслеживания статуса сессий (creating, ready, finished)
session_status = {}

@socketio.on('connect')
def handle_connect():
    """Обработка подключения клиента к WebSocket"""
    app.logger.info(f'Клиент подключился: {flask_request.sid}')
    
    # Проверяем, не завершена ли сессия
    sid = flask_request.sid
    if sid in session_status and session_status[sid] == 'finished':
        emit('session_finished', {'message': 'Сессия завершена. Повторный вход невозможен.'})
        return False
    
    emit('connected', {'status': 'ok'})

@socketio.on('disconnect')
def handle_disconnect():
    """Обработка отключения клиента - закрытие SSH соединения и удаление контейнера"""
    app.logger.info(f'Клиент отключился: {flask_request.sid}')
    
    sid = flask_request.sid
    
    # Получаем информацию о сессии для удаления контейнера
    vm_id = None
    node = None
    session_token = None
    
    if sid in ssh_connections:
        ssh_info = ssh_connections[sid]
        vm_id = ssh_info.get('vm_id')
        session_token = ssh_info.get('session_token')
        
        # Закрываем SSH соединение
        try:
            if 'client' in ssh_info and ssh_info['client']:
                ssh_info['client'].close()
            del ssh_connections[sid]
            app.logger.info(f'SSH connection closed for {sid}')
        except Exception as e:
            app.logger.error(f'Error closing SSH connection: {e}')
    
    # Если есть VM ID, останавливаем и удаляем контейнер
    if vm_id:
        try:
            app.logger.info(f'Stopping and deleting container VM {vm_id} on node {node}')
            # Сначала останавливаем контейнер
            app.logger.info(f'Stopping container {vm_id}...')
            stop_container(vm_id, node)
            
            # Ждем пока контейнер полностью остановится
            app.logger.info(f'Waiting {STOP_TIMEOUT} seconds for container to stop...')
            time.sleep(STOP_TIMEOUT)
            
            # Теперь удаляем
            app.logger.info(f'Deleting container {vm_id}...')
            delete_container(vm_id, node)
            app.logger.info(f'Container VM {vm_id} stopped and deleted successfully')
        except Exception as e:
            app.logger.error(f'Error stopping/deleting container VM {vm_id}: {e}')
    
    # Обновляем статус сессии в БД если есть session_token
    if session_token:
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE terminal_sessions 
                SET status = 'closed', ended_at = CURRENT_TIMESTAMP 
                WHERE session_token = ?
            """, (session_token,))
            conn.commit()
            conn.close()
            app.logger.info(f'Terminal session {session_token} marked as closed')
        except Exception as e:
            app.logger.error(f'Error updating terminal session status: {e}')
    
    # Устанавливаем статус сессии в finished чтобы предотвратить повторное подключение
    sid = flask_request.sid
    if sid in session_status:
        session_status[sid] = 'finished'
        socketio.emit('session_finished', {'message': 'Сессия завершена'}, room=sid)
        app.logger.info(f'Session {sid} marked as finished')

@socketio.on('terminal_init')
def handle_terminal_init(data):
    """Инициализация SSH сессии терминала при подключении"""
    session_token = data.get('session_token')
    
    if not session_token:
        emit('terminal_output', {'error': 'Требуется session_token'})
        return
    
    if 'user_id' not in session:
        emit('terminal_output', {'error': 'Требуется авторизация'})
        return
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT ts.*, cont.pve_vm_id, cont.pve_node, cont.status as container_status, c.title as course_title
        FROM terminal_sessions ts
        JOIN containers cont ON ts.container_id = cont.id
        JOIN courses c ON ts.course_id = c.id
        WHERE ts.session_token = ? AND ts.user_id = ?
    """, (session_token, session.get('user_id')))
    
    session_data = cursor.fetchone()
    conn.close()
    
    if not session_data:
        emit('terminal_output', {'error': 'Сессия не найдена'})
        return
    
    # Проверяем статус контейнера
    if session_data['container_status'] != 'running':
        emit('terminal_output', {'error': f'Контейнер не запущен (статус: {session_data["container_status"]})'})
        return
    
    # Получаем информацию для SSH подключения
    ssh_info = get_vnc_proxy_url(session_data['pve_vm_id'], session_data['pve_node'])
    
    if not ssh_info:
        emit('terminal_output', {'error': 'Не удалось получить IP-адрес контейнера'})
        return
    
    try:
        # Создаем SSH подключение
        ssh_client = SSHClient()
        ssh_client.set_missing_host_key_policy(AutoAddPolicy())
        
        app.logger.info(f"Connecting to SSH: {ssh_info['host']}:{ssh_info['port']} as {ssh_info['username']}")
        
        ssh_client.connect(
            hostname=ssh_info['host'],
            port=ssh_info['port'],
            username=ssh_info['username'],
            password=ssh_info['password'],
            timeout=10,
            allow_agent=False,
            look_for_keys=False
        )
        
        # Создаем интерактивную сессию
        channel = ssh_client.invoke_shell(term='xterm-256color')
        channel.settimeout(0)  # Неблокирующий режим
        
        # Сохраняем информацию о подключении
        sid = flask_request.sid
        ssh_connections[sid] = {
            'session_token': session_token,
            'vm_id': session_data['pve_vm_id'],
            'client': ssh_client,
            'channel': channel
        }
        
        emit('terminal_output', {'output': f'\x1b[32m✓ Подключено к контейнеру {ssh_info["host"]} (VM {session_data["pve_vm_id"]})\x1b[0m\r\n'})
        
        # Запускаем фоновый поток для чтения вывода из SSH
        def ssh_reader():
            while True:
                try:
                    if sid not in ssh_connections:
                        break
                    
                    conn_data = ssh_connections.get(sid)
                    if not conn_data or not conn_data.get('channel'):
                        break
                    
                    channel = conn_data['channel']
                    
                    # Проверяем, есть ли данные для чтения
                    if channel.recv_ready():
                        data = channel.recv(4096).decode('utf-8', errors='replace')
                        if data:
                            socketio.emit('terminal_output', {'output': data}, room=sid)
                    
                    # Небольшая пауза чтобы не нагружать CPU
                    import time
                    time.sleep(0.01)
                except Exception as e:
                    app.logger.error(f'SSH reader error: {e}')
                    break
        
        reader_thread = threading.Thread(target=ssh_reader, daemon=True)
        reader_thread.start()
        
    except Exception as e:
        app.logger.error(f'SSH connection error: {e}')
        emit('terminal_output', {'error': f'Ошибка подключения по SSH: {str(e)}'})

@socketio.on('terminal_input')
def handle_terminal_input(data):
    """Обработка ввода команд в терминале через SSH"""
    session_token = data.get('session_token')
    command = data.get('command')
    
    if not session_token or not command:
        return
    
    sid = flask_request.sid
    ssh_info = ssh_connections.get(sid)
    
    if not ssh_info or not ssh_info.get('channel'):
        return
    
    try:
        # Отправляем команду в SSH канал
        channel = ssh_info['channel']
        channel.send(command)
    except Exception as e:
        app.logger.error(f'SSH send error: {e}')
        emit('terminal_output', {'error': f'Ошибка отправки команды: {str(e)}'})

@socketio.on('terminal_resize')
def handle_terminal_resize(data):
    """Обработка изменения размера терминала"""
    session_token = data.get('session_token')
    cols = data.get('cols', 80)
    rows = data.get('rows', 24)
    
    if not session_token:
        return
    
    sid = flask_request.sid
    ssh_info = ssh_connections.get(sid)
    
    if not ssh_info or not ssh_info.get('channel'):
        return
    
    try:
        # Отправляем ANSI escape sequence для изменения размера
        channel = ssh_info['channel']
        resize_cmd = f'\x1b[8;{rows};{cols}t'
        channel.send(resize_cmd)
    except Exception as e:
        app.logger.error(f'SSH resize error: {e}')

if __name__ == '__main__':
    init_db()
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
