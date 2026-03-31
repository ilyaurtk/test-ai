#!/usr/bin/env python3
"""
IT Course Platform - Backend Application
Flask-based web application for teaching IT courses with PVE container integration.
"""

import os
import sqlite3
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, g, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

DATABASE = 'it_courses.db'

def get_db():
    """Get database connection."""
    if not hasattr(g, 'sqlite_db'):
        g.sqlite_db = sqlite3.connect(DATABASE)
        g.sqlite_db.row_factory = sqlite3.Row
    return g.sqlite_db

@app.teardown_appcontext
def close_db(error):
    """Close database connection."""
    if hasattr(g, 'sqlite_db'):
        g.sqlite_db.close()

def init_db():
    """Initialize the database with required tables."""
    db = get_db()
    cursor = db.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'student',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Containers table (PVE container templates)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS containers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            pve_node TEXT NOT NULL,
            pve_container_id TEXT,
            docker_image TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Courses table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            content TEXT,
            container_id INTEGER,
            image_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (container_id) REFERENCES containers(id)
        )
    ''')
    
    # User progress table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            status TEXT DEFAULT 'not_started',
            current_step INTEGER DEFAULT 0,
            completed_steps TEXT DEFAULT '',
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (course_id) REFERENCES courses(id),
            UNIQUE(user_id, course_id)
        )
    ''')
    
    # Terminal sessions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS terminal_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            container_id INTEGER,
            session_token TEXT UNIQUE,
            status TEXT DEFAULT 'inactive',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (course_id) REFERENCES courses(id),
            FOREIGN KEY (container_id) REFERENCES containers(id)
        )
    ''')
    
    db.commit()
    
    # Create default admin user if not exists
    cursor.execute('SELECT * FROM users WHERE username = ?', ('admin',))
    if not cursor.fetchone():
        admin_hash = generate_password_hash('admin123')
        cursor.execute(
            'INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)',
            ('admin', admin_hash, 'admin')
        )
        db.commit()
        print("Default admin user created: username=admin, password=admin123")

def login_required(f):
    """Decorator to require login."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator to require admin role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'error')
            return redirect(url_for('login'))
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],))
        user = cursor.fetchone()
        if not user or user['role'] != 'admin':
            flash('Access denied. Admin privileges required.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# Routes
@app.route('/')
def index():
    """Home page with course list."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT c.*, cont.name as container_name 
        FROM courses c 
        LEFT JOIN containers cont ON c.container_id = cont.id
        ORDER BY c.created_at DESC
    ''')
    courses = cursor.fetchall()
    return render_template('index.html', courses=courses)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login."""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            flash('Login successful!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password.', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """User logout."""
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    """User registration."""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('Username and password are required.', 'error')
            return render_template('register.html')
        
        password_hash = generate_password_hash(password)
        
        try:
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                'INSERT INTO users (username, password_hash) VALUES (?, ?)',
                (username, password_hash)
            )
            db.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username already exists.', 'error')
    
    return render_template('register.html')

@app.route('/course/<int:course_id>')
@login_required
def view_course(course_id):
    """View a specific course with assignment."""
    db = get_db()
    cursor = db.cursor()
    
    # Get course details
    cursor.execute('''
        SELECT c.*, cont.name as container_name, cont.pve_node, cont.docker_image
        FROM courses c 
        LEFT JOIN containers cont ON c.container_id = cont.id
        WHERE c.id = ?
    ''', (course_id,))
    course = cursor.fetchone()
    
    if not course:
        flash('Course not found.', 'error')
        return redirect(url_for('index'))
    
    # Get or create user progress
    cursor.execute('''
        SELECT * FROM user_progress 
        WHERE user_id = ? AND course_id = ?
    ''', (session['user_id'], course_id))
    progress = cursor.fetchone()
    
    if not progress:
        cursor.execute('''
            INSERT INTO user_progress (user_id, course_id, status, started_at)
            VALUES (?, ?, 'in_progress', ?)
        ''', (session['user_id'], course_id, datetime.now()))
        db.commit()
        cursor.execute('''
            SELECT * FROM user_progress 
            WHERE user_id = ? AND course_id = ?
        ''', (session['user_id'], course_id))
        progress = cursor.fetchone()
    
    # Check for active terminal session
    cursor.execute('''
        SELECT * FROM terminal_sessions 
        WHERE user_id = ? AND course_id = ? AND status = 'active'
        ORDER BY created_at DESC LIMIT 1
    ''', (session['user_id'], course_id))
    active_session = cursor.fetchone()
    
    return render_template('course.html', course=course, progress=progress, active_session=active_session)

@app.route('/course/<int:course_id>/update_progress', methods=['POST'])
@login_required
def update_progress(course_id):
    """Update user progress for a course."""
    step = request.form.get('step', type=int)
    completed = request.form.get('completed', 'false') == 'true'
    
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute('''
        SELECT * FROM user_progress 
        WHERE user_id = ? AND course_id = ?
    ''', (session['user_id'], course_id))
    progress = cursor.fetchone()
    
    if progress:
        if completed:
            cursor.execute('''
                UPDATE user_progress 
                SET status = 'completed', completed_at = ?, current_step = ?
                WHERE user_id = ? AND course_id = ?
            ''', (datetime.now(), step, session['user_id'], course_id))
        else:
            cursor.execute('''
                UPDATE user_progress 
                SET current_step = ?, last_accessed = ?
                WHERE user_id = ? AND course_id = ?
            ''', (step, datetime.now(), session['user_id'], course_id))
        db.commit()
    
    return jsonify({'success': True})

@app.route('/course/<int:course_id>/request_terminal', methods=['POST'])
@login_required
def request_terminal(course_id):
    """Request a new terminal session for a course."""
    db = get_db()
    cursor = db.cursor()
    
    # Get course container
    cursor.execute('''
        SELECT c.container_id, c.id as course_id
        FROM courses c 
        WHERE c.id = ?
    ''', (course_id,))
    course = cursor.fetchone()
    
    if not course or not course['container_id']:
        return jsonify({'error': 'No container configured for this course'}), 400
    
    # Generate session token
    import secrets
    session_token = secrets.token_urlsafe(32)
    
    # Create terminal session
    cursor.execute('''
        INSERT INTO terminal_sessions (user_id, course_id, container_id, session_token, status)
        VALUES (?, ?, ?, ?, 'active')
    ''', (session['user_id'], course_id, course['container_id'], session_token))
    db.commit()
    
    # In production, this would integrate with PVE API to start container
    # For now, we just return the session token
    return jsonify({
        'success': True,
        'session_token': session_token,
        'message': 'Terminal session created. Connecting to container...'
    })

@app.route('/course/<int:course_id>/terminal')
@login_required
def terminal_view(course_id):
    """View terminal for a course."""
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute('''
        SELECT c.*, cont.name as container_name
        FROM courses c 
        LEFT JOIN containers cont ON c.container_id = cont.id
        WHERE c.id = ?
    ''', (course_id,))
    course = cursor.fetchone()
    
    if not course:
        flash('Course not found.', 'error')
        return redirect(url_for('index'))
    
    return render_template('terminal.html', course=course)

@app.route('/course/<int:course_id>/close_terminal', methods=['POST'])
@login_required
def close_terminal(course_id):
    """Close terminal session."""
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute('''
        UPDATE terminal_sessions 
        SET status = 'closed', closed_at = ?
        WHERE user_id = ? AND course_id = ? AND status = 'active'
    ''', (datetime.now(), session['user_id'], course_id))
    db.commit()
    
    return jsonify({'success': True})

# Admin routes
@app.route('/admin')
@admin_required
def admin_dashboard():
    """Admin dashboard."""
    db = get_db()
    cursor = db.cursor()
    
    # Get statistics
    cursor.execute('SELECT COUNT(*) as total_users FROM users WHERE role = "student"')
    total_students = cursor.fetchone()['total_users']
    
    cursor.execute('SELECT COUNT(*) as total_courses FROM courses')
    total_courses = cursor.fetchone()['total_courses']
    
    cursor.execute('SELECT COUNT(*) as total_containers FROM containers')
    total_containers = cursor.fetchone()['total_containers']
    
    cursor.execute('''
        SELECT u.username, c.title, up.status, up.last_accessed
        FROM user_progress up
        JOIN users u ON up.user_id = u.id
        JOIN courses c ON up.course_id = c.id
        ORDER BY up.last_accessed DESC
        LIMIT 10
    ''')
    recent_activity = cursor.fetchall()
    
    return render_template('admin/dashboard.html',
                         total_students=total_students,
                         total_courses=total_courses,
                         total_containers=total_containers,
                         recent_activity=recent_activity)

@app.route('/admin/courses')
@admin_required
def admin_courses():
    """Manage courses."""
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute('''
        SELECT c.*, cont.name as container_name
        FROM courses c
        LEFT JOIN containers cont ON c.container_id = cont.id
        ORDER BY c.created_at DESC
    ''')
    courses = cursor.fetchall()
    
    return render_template('admin/courses.html', courses=courses)

@app.route('/admin/courses/create', methods=['GET', 'POST'])
@admin_required
def admin_create_course():
    """Create a new course."""
    db = get_db()
    cursor = db.cursor()
    
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        content = request.form.get('content')
        container_id = request.form.get('container_id', type=int)
        
        image_path = None
        if 'image' in request.files:
            file = request.files['image']
            if file.filename:
                filename = secure_filename(file.filename)
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                image_path = filepath
        
        cursor.execute('''
            INSERT INTO courses (title, description, content, container_id, image_path)
            VALUES (?, ?, ?, ?, ?)
        ''', (title, description, content, container_id, image_path))
        db.commit()
        
        flash('Course created successfully!', 'success')
        return redirect(url_for('admin_courses'))
    
    # Get available containers
    cursor.execute('SELECT id, name FROM containers')
    containers = cursor.fetchall()
    
    return render_template('admin/create_course.html', containers=containers)

@app.route('/admin/courses/<int:course_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_edit_course(course_id):
    """Edit an existing course."""
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute('SELECT * FROM courses WHERE id = ?', (course_id,))
    course = cursor.fetchone()
    
    if not course:
        flash('Course not found.', 'error')
        return redirect(url_for('admin_courses'))
    
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        content = request.form.get('content')
        container_id = request.form.get('container_id', type=int)
        
        image_path = course['image_path']
        if 'image' in request.files:
            file = request.files['image']
            if file.filename:
                filename = secure_filename(file.filename)
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                image_path = filepath
        
        cursor.execute('''
            UPDATE courses 
            SET title = ?, description = ?, content = ?, container_id = ?, image_path = ?
            WHERE id = ?
        ''', (title, description, content, container_id, image_path, course_id))
        db.commit()
        
        flash('Course updated successfully!', 'success')
        return redirect(url_for('admin_courses'))
    
    cursor.execute('SELECT id, name FROM containers')
    containers = cursor.fetchall()
    
    return render_template('admin/edit_course.html', course=course, containers=containers)

@app.route('/admin/courses/<int:course_id>/delete', methods=['POST'])
@admin_required
def admin_delete_course(course_id):
    """Delete a course."""
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute('DELETE FROM courses WHERE id = ?', (course_id,))
    db.commit()
    
    flash('Course deleted successfully!', 'success')
    return redirect(url_for('admin_courses'))

@app.route('/admin/containers')
@admin_required
def admin_containers():
    """Manage PVE containers."""
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute('SELECT * FROM containers ORDER BY created_at DESC')
    containers = cursor.fetchall()
    
    return render_template('admin/containers.html', containers=containers)

@app.route('/admin/containers/create', methods=['GET', 'POST'])
@admin_required
def admin_create_container():
    """Create a new PVE container template."""
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        pve_node = request.form.get('pve_node')
        pve_container_id = request.form.get('pve_container_id')
        docker_image = request.form.get('docker_image')
        
        db = get_db()
        cursor = db.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO containers (name, description, pve_node, pve_container_id, docker_image)
                VALUES (?, ?, ?, ?, ?)
            ''', (name, description, pve_node, pve_container_id, docker_image))
            db.commit()
            
            flash('Container created successfully!', 'success')
            return redirect(url_for('admin_containers'))
        except sqlite3.IntegrityError:
            flash('Container name already exists.', 'error')
    
    return render_template('admin/create_container.html')

@app.route('/admin/containers/<int:container_id>/delete', methods=['POST'])
@admin_required
def admin_delete_container(container_id):
    """Delete a container."""
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute('DELETE FROM containers WHERE id = ?', (container_id,))
    db.commit()
    
    flash('Container deleted successfully!', 'success')
    return redirect(url_for('admin_containers'))

@app.route('/admin/users')
@admin_required
def admin_users():
    """View all users and their progress."""
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute('''
        SELECT u.id, u.username, u.role, u.created_at,
               COUNT(DISTINCT up.course_id) as courses_enrolled,
               SUM(CASE WHEN up.status = 'completed' THEN 1 ELSE 0 END) as courses_completed
        FROM users u
        LEFT JOIN user_progress up ON u.id = up.user_id
        WHERE u.role = 'student'
        GROUP BY u.id
        ORDER BY u.created_at DESC
    ''')
    users = cursor.fetchall()
    
    return render_template('admin/users.html', users=users)

@app.route('/admin/user/<int:user_id>/progress')
@admin_required
def admin_user_progress(user_id):
    """View detailed progress for a specific user."""
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_users'))
    
    cursor.execute('''
        SELECT c.title, up.status, up.current_step, up.started_at, up.completed_at, up.last_accessed
        FROM user_progress up
        JOIN courses c ON up.course_id = c.id
        WHERE up.user_id = ?
        ORDER BY up.last_accessed DESC
    ''', (user_id,))
    progress = cursor.fetchall()
    
    return render_template('admin/user_progress.html', user=user, progress=progress)

if __name__ == '__main__':
    # Initialize database
    with app.app_context():
        init_db()
    
    # Run the application
    app.run(host='0.0.0.0', port=5000, debug=True)
