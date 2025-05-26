from flask import Flask, render_template, Response, jsonify, request
import psutil
import threading
import time
import os
import json
import subprocess
import re
from datetime import datetime
from functools import wraps

app = Flask(__name__)

ENCODER_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../encoderData'))
STREAM_PIDFILE = "/tmp/webcam-ffmpeg.pid"
SETTINGS_FILE = os.path.join(ENCODER_DATA_DIR, 'settings.json')

def load_settings():
    settings = {
        "stream_url": "",
        "framerate": 5,
        "crf": 30,
        "resolution": "1280x720",
        "vbitrate": 1000,
        "abitrate": "128k",
        "ar": 44100,
        "upload_url": "",
        "gop": 30
    }
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            settings.update(json.load(f))
    settings['streaming'] = is_streaming()
    return settings

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f)

def get_temperature():
    # Try to get CPU temperature (Linux only, fallback to N/A)
    try:
        for zone in os.listdir('/sys/class/thermal'):
            if zone.startswith('thermal_zone'):
                with open(f'/sys/class/thermal/{zone}/temp') as f:
                    temp = int(f.read()) / 1000.0
                    return f"{temp:.1f}°C"
    except Exception:
        return "N/A"

def is_streaming():
    """Return True if streaming is currently active (broadcast)."""
    if os.path.exists(STREAM_PIDFILE):
        try:
            with open(STREAM_PIDFILE) as f:
                content = f.read().strip()
                if ':' in content:
                    pid, running_stream = content.split(':', 1)
                    pid = int(pid)
                    running_stream = running_stream.strip()
                else:
                    pid = int(content)
                    running_stream = None
            if os.path.exists(f"/proc/{pid}") and running_stream and running_stream == 'broadcast':
                return True
        except Exception:
            pass
    return False

def get_app_version():
    # Find the latest mtime among all .py, .html, .png files in the project
    exts = {'.py', '.html', '.png'}
    latest_mtime = 0
    latest_file = ''
    for root, dirs, files in os.walk(os.path.dirname(__file__)):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in exts:
                path = os.path.join(root, f)
                mtime = os.path.getmtime(path)
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    latest_file = path
    if latest_mtime:
        mtime_dt = datetime.fromtimestamp(latest_mtime)
        return mtime_dt.strftime('%Y-%m-%d %H:%M')
    return ''

@app.route('/')
def home():
    streaming = is_streaming()

    # Gather recording files info
    recording_files = []
    recording_path = os.path.join(ENCODER_DATA_DIR, 'recordings', 'broadcast')
    if os.path.isdir(recording_path):
        files = [f for f in os.listdir(recording_path) if os.path.isfile(os.path.join(recording_path, f))]
        files.sort(key=lambda f: os.path.getmtime(os.path.join(recording_path, f)), reverse=True)
        for f in files:
            file_path = os.path.join(recording_path, f)
            file_size = os.path.getsize(file_path)
            if re.match(r'^\d+\.mp4$', f):
                recording_files.append({
                    'path': file_path,
                    'size': file_size,
                    'active': True,
                    'name': f
                })
            else:
                recording_files.append({
                    'path': file_path,
                    'size': file_size,
                    'active': False
                })

    if request.args.get('active_only') == '1':
        active_files = [
            {
                'name': f['name'],
                'size': f['size'],
                'size_fmt': f"{f['size'] // 1024} KB" if f['size'] < 1024*1024 else f"{f['size'] / (1024*1024):.1f} MB"
            }
            for f in recording_files if f.get('active')
        ]
        return jsonify({'files': active_files})

    return render_template('index.html', active_tab='home', streaming=streaming, recording_files=recording_files, app_version=get_app_version())

@app.route('/stats')
def stats():
    def event_stream():
        while True:
            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory().percent
            temp = get_temperature()
            data = f"data: {{\"cpu\": {cpu}, \"mem\": {mem}, \"temp\": \"{temp}\"}}\n\n"
            yield data
            time.sleep(1)
    return Response(event_stream(), mimetype='text/event-stream')

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        data = request.get_json()
        settings = load_settings()
        # Only update if present in request
        if 'stream_url' in data:
            settings['stream_url'] = data['stream_url']
        if 'framerate' in data:
            settings['framerate'] = int(data['framerate'])
        if 'crf' in data:
            settings['crf'] = int(data['crf'])
        if 'gop' in data:
            settings['gop'] = int(data['gop'])
        if 'resolution' in data:
            settings['resolution'] = data['resolution']
        if 'vbitrate' in data:
            settings['vbitrate'] = int(data['vbitrate'])
        if 'abitrate' in data:
            settings['abitrate'] = data['abitrate']
        if 'ar' in data:
            settings['ar'] = int(data['ar'])
        if 'upload_url' in data:
            settings['upload_url'] = data['upload_url']
        save_settings(settings)
        return '', 204
    else:
        return jsonify(load_settings())

@app.route('/settings-page')
def settings_page():
    return render_template('settings.html', active_tab='settings', settings=load_settings(), app_version=get_app_version())

@app.route('/stream-control', methods=['POST'])
def stream_control():
    data = request.get_json()
    action = data.get('action')
    if action == 'start':
        settings = load_settings()
        stream_url = settings.get('stream_url', '').strip()
        if not stream_url:
            return jsonify({'error': 'Remote Streaming URL is not set. Please configure it in Settings.'}), 400
        subprocess.run(['python', 'webcam-ffmpeg.py', 'stop'])
        subprocess.Popen(['python', 'webcam-ffmpeg.py', 'start', 'broadcast'])
        return jsonify({'status': 'started'})
    elif action == 'stop':
        subprocess.run(['python', 'webcam-ffmpeg.py', 'stop'])
        return jsonify({'status': 'stopped'})
    else:
        return jsonify({'error': 'Invalid action'}), 400

@app.route('/upload-recording', methods=['POST'])
def upload_recording():
    from werkzeug.utils import secure_filename
    import requests
    settings = load_settings()
    upload_url = settings.get('upload_url', '').strip()
    if not upload_url:
        return jsonify({'error': 'Upload URL is not set. Please configure it in Settings.'}), 400
    # Ensure command=replacerecordings is present
    if 'command=replacerecordings' not in upload_url:
        if '?' in upload_url:
            upload_url += '&command=replacerecordings'
        else:
            upload_url += '?command=replacerecordings'
    file_path = request.form.get('file_path')
    if not file_path or not os.path.isfile(file_path):
        return jsonify({'error': 'Recording file not found.'}), 400
    with open(file_path, 'rb') as f:
        files = {'video': (secure_filename(os.path.basename(file_path)), f)}
        try:
            resp = requests.post(upload_url, files=files)
            resp.raise_for_status()
            result = resp.json()
        except Exception as e:
            return jsonify({'error': f'Upload failed: {e}'}), 500
    # If upload succeeded and no error, delete the original file
    if result.get('error') == '':
        try:
            os.remove(file_path)
        except Exception:
            pass  # Ignore deletion errors
    return jsonify(result)

@app.route('/camera-viewer')
def camera_viewer():
    streaming = is_streaming()
    return render_template('camera_viewer.html', active_tab='camera', streaming=streaming, app_version=get_app_version())

# --- Basic HTTP Auth ---
import json

def get_auth_creds():
    auth_file = os.path.join(ENCODER_DATA_DIR, 'auth.json')
    if os.path.exists(auth_file):
        with open(auth_file) as f:
            auth = json.load(f)
        return auth.get('username', 'admin'), auth.get('password', '12345')
    return 'admin', '12345'

def check_auth(username, password):
    expected_user, expected_pass = get_auth_creds()
    return username == expected_user and password == expected_pass

def authenticate():
    from flask import Response
    return Response(
        'Authentication required', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'
    })

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # This decorator is no longer used globally
        return f(*args, **kwargs)
    return decorated

@app.before_request
def global_auth():
    # Allow static files and favicon.ico without auth
    if request.path.startswith('/static/') or request.path == '/favicon.ico':
        return
    auth = request.authorization
    if not auth or not check_auth(auth.username, auth.password):
        return authenticate()

# Jinja2 filter to format UNIX timestamp as human-readable date/time
@app.template_filter('datetimeformat')
def datetimeformat_filter(value):
    try:
        return datetime.utcfromtimestamp(int(value)).strftime('%Y-%m-%d %H:%M:%S UTC')
    except Exception:
        return str(value)

# Jinja2 filter to format seconds as H:MM:SS or MM:SS
@app.template_filter('durationformat')
def durationformat_filter(value):
    try:
        seconds = float(value)
        seconds = int(round(seconds))
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if h > 0:
            return f"{h}:{m:02}:{s:02}"
        else:
            return f"{m}:{s:02}"
    except Exception:
        return str(value)

@app.template_filter('parse_recording_filename')
def parse_recording_filename(value):
    filename = os.path.basename(value)
    m = re.search(r'(\d+)d([\d.]+)\.mp4$', filename)
    if m:
        return {'timestamp': int(m.group(1)), 'duration': float(m.group(2))}
    return None

def get_auth_and_wifi():
    auth = {}
    wifi = {}
    auth_path = os.path.join(ENCODER_DATA_DIR, 'auth.json')
    wifi_path = os.path.join(ENCODER_DATA_DIR, 'wifi.json')
    if os.path.exists(auth_path):
        with open(auth_path) as f:
            auth = json.load(f)
    if os.path.exists(wifi_path):
        with open(wifi_path) as f:
            wifi = json.load(f)
    return auth, wifi

@app.route('/system-settings')
def system_settings():
    # Pass the current auth and wifi settings to the template
    auth, wifi = get_auth_and_wifi()
    return render_template('system_settings.html', active_tab='system_settings', auth=auth, wifi=wifi, app_version=get_app_version())

@app.route('/system-settings-data')
def system_settings_data():
    auth, wifi = get_auth_and_wifi()
    return jsonify({'auth': auth, 'wifi': wifi})

@app.route('/system-settings-auth', methods=['POST'])
def system_settings_auth():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required.'})
    auth_path = os.path.join(ENCODER_DATA_DIR, 'auth.json')
    with open(auth_path, 'w') as f:
        json.dump({'username': username, 'password': password}, f)
    return jsonify({'success': True})

@app.route('/system-settings-wifi', methods=['POST'])
def system_settings_wifi():
    data = request.get_json()
    ssid = data.get('ssid', '').strip()
    password = data.get('password', '').strip()
    if not ssid or not password:
        return jsonify({'success': False, 'error': 'SSID and password required.'})
    wifi_path = os.path.join(ENCODER_DATA_DIR, 'wifi.json')
    # Save to wifi.json for web UI
    with open(wifi_path, 'w') as f:
        json.dump({'ssid': ssid, 'password': password}, f)
    # Write to wpa_supplicant.conf
    wpa_conf = f'''
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=US

network={{
    ssid="{ssid}"
    psk="{password}"
    key_mgmt=WPA-PSK
}}
'''
    try:
        with open('/etc/wpa_supplicant/wpa_supplicant.conf', 'w') as f:
            f.write(wpa_conf)
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to write wpa_supplicant.conf: {e}'})
    # Try to reload WiFi settings
    try:
        subprocess.run(['sudo', 'wpa_cli', '-i', 'wlan0', 'reconfigure'], check=True)
    except Exception:
        # If wpa_cli fails, try restarting dhcpcd or networking
        try:
            subprocess.run(['sudo', 'systemctl', 'restart', 'dhcpcd'], check=True)
        except Exception:
            pass  # Ignore if restart fails
    return jsonify({'success': True})

@app.route('/system-settings-reboot', methods=['POST'])
def system_settings_reboot():
    try:
        subprocess.Popen(['sudo', 'reboot'])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/system-check-update', methods=['POST'])
def system_check_update():
    """
    Check for any differences between local and remote tracked files, ignoring timestamps.
    Returns updates=True if any file content differs, is missing, or is extra locally.
    """
    import subprocess, os
    try:
        # Fetch latest info from remote
        fetch_result = subprocess.run(['git', 'fetch', 'origin'], capture_output=True, text=True)
        if fetch_result.returncode != 0:
            return jsonify({'success': False, 'error': fetch_result.stderr.strip()})
        # Compare local and remote tracked files (ignoring timestamps)
        diff_result = subprocess.run(['git', 'diff', '--name-status', 'origin/main'], capture_output=True, text=True)
        if diff_result.returncode != 0:
            return jsonify({'success': False, 'error': diff_result.stderr.strip()})
        diff_output = diff_result.stdout.strip()
        # Also check for missing files (tracked in remote but missing locally)
        ls_remote = subprocess.run(['git', 'ls-tree', '-r', '--name-only', 'origin/main'], capture_output=True, text=True)
        if ls_remote.returncode != 0:
            return jsonify({'success': False, 'error': ls_remote.stderr.strip()})
        missing_files = []
        for filename in ls_remote.stdout.strip().split('\n'):
            if filename and not os.path.exists(filename):
                missing_files.append(filename)
        # Also check for extra local files tracked by git but not in remote (deleted from remote)
        ls_local = subprocess.run(['git', 'ls-files'], capture_output=True, text=True)
        if ls_local.returncode != 0:
            return jsonify({'success': False, 'error': ls_local.stderr.strip()})
        local_tracked = set(ls_local.stdout.strip().split('\n'))
        remote_tracked = set(ls_remote.stdout.strip().split('\n'))
        extra_local = [f for f in local_tracked if f not in remote_tracked]
        details = diff_output
        if missing_files:
            details += ('\n' if details else '') + 'Missing files: ' + ', '.join(missing_files)
        if extra_local:
            details += ('\n' if details else '') + 'Extra local files: ' + ', '.join(extra_local)
        if diff_output or missing_files or extra_local:
            summary = 'Updates are available from the GitHub repository.'
            return jsonify({'success': True, 'summary': summary, 'updates': True, 'details': details})
        else:
            summary = 'Your code is up to date.'
            return jsonify({'success': True, 'summary': summary, 'updates': False})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Update check failed: {e}'})

@app.route('/system-do-update', methods=['POST'])
def system_do_update():
    """
    Force update the codebase to match the remote GitHub repository (overwriting local changes, restoring missing files, removing extra tracked files), fix permissions, and restart services.
    """
    import subprocess, os
    results = []
    try:
        # Fetch latest changes
        fetch = subprocess.run(['git', 'fetch', 'origin'], capture_output=True, text=True)
        results.append('git fetch: ' + fetch.stdout.strip() + fetch.stderr.strip())
        if fetch.returncode != 0:
            return jsonify({'success': False, 'error': fetch.stderr.strip(), 'results': results})
        # Hard reset to remote/main (restores missing/tracked files, removes local changes, removes extra tracked files)
        reset = subprocess.run(['git', 'reset', '--hard', 'origin/main'], capture_output=True, text=True)
        results.append('git reset: ' + reset.stdout.strip() + reset.stderr.strip())
        if reset.returncode != 0:
            return jsonify({'success': False, 'error': reset.stderr.strip(), 'results': results})
        # Remove extra local files tracked by git but not in remote (deleted from remote)
        clean = subprocess.run(['git', 'clean', '-fd'], capture_output=True, text=True)
        results.append('git clean: ' + clean.stdout.strip() + clean.stderr.strip())
        # Change ownership of all files to admin:admin
        chown = subprocess.run(['sudo', 'chown', '-R', 'admin:admin', '.'], capture_output=True, text=True)
        results.append('chown: ' + chown.stdout.strip() + chown.stderr.strip())
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return jsonify({'success': False, 'error': str(e), 'traceback': tb, 'results': results})

@app.route('/system-restart-services', methods=['POST'])
def restart_services():
    """
    Restart flask_app and mediamtx services, returning a results array with status messages.
    """
    import subprocess
    results = []
    # Restart flask_app service
    try:
        subprocess.run(['sudo', 'systemctl', 'restart', 'flask_app'], check=True)
        results.append('flask_app service restarted.')
    except Exception as e:
        results.append(f"Failed to restart flask_app: {e}")
    # Restart mediamtx service
    try:
        subprocess.run(['sudo', 'systemctl', 'restart', 'mediamtx'], check=True)
        results.append('mediamtx service restarted.')
    except Exception as e:
        results.append(f"Failed to restart mediamtx: {e}")
    return jsonify({'success': True, 'results': results})

@app.route('/delete-recording', methods=['POST'])
def delete_recording():
    data = request.get_json()
    file_path = data.get('file_path')
    if not file_path or not os.path.isfile(file_path):
        return jsonify({'error': 'Recording file not found.'}), 400
    try:
        os.remove(file_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': f'Failed to delete: {e}'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=True, threaded=True)