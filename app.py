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

STREAM_PIDFILE = "/tmp/webcam-ffmpeg.pid"
SETTINGS_FILE = 'settings.json'

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    # Default settings
    return {
        "stream_url": "",
        "framerate": 5,
        "crf": 30,
        "resolution": "1280x720",
        "vbitrate": 1000,
        "abitrate": "128k",
        "ar": 44100,
        "upload_url": ""
    }

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

@app.route('/')
def home():
    streaming = is_streaming()

    # Gather recording files info
    recording_files = []
    recording_path = os.path.join('recordings', 'broadcast')
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

    return render_template('index.html', active_tab='home', streaming=streaming, recording_files=recording_files)

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
        settings['stream_url'] = data.get('stream_url', '')
        settings['framerate'] = int(data.get('framerate', 5))
        settings['crf'] = int(data.get('crf', 30))
        settings['resolution'] = data.get('resolution', '1280x720')
        settings['vbitrate'] = int(data.get('vbitrate', 1000))
        settings['abitrate'] = data.get('abitrate', '128k')
        settings['ar'] = int(data.get('ar', 44100))
        settings['upload_url'] = data.get('upload_url', '')
        save_settings(settings)
        return '', 204
    else:
        return jsonify(load_settings())

@app.route('/settings-page')
def settings_page():
    return render_template('settings.html', active_tab='settings', settings=load_settings())

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
    return render_template('camera_viewer.html', active_tab='camera', streaming=streaming)

@app.route('/istreaming')
def istreaming():
    return jsonify({'streaming': is_streaming()})

# --- Basic HTTP Auth ---
import json

def get_auth_creds():
    auth_file = 'auth.json'
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
        # Allow static files without auth
        if hasattr(request, 'path') and request.path.startswith('/static/'):
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# Apply the requires_auth decorator to all routes except static
for rule in list(app.url_map.iter_rules()):
    if rule.endpoint != 'static':
        view_func = app.view_functions[rule.endpoint]
        app.view_functions[rule.endpoint] = requires_auth(view_func)

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

@app.route('/system-settings')
def system_settings():
    return render_template('system_settings.html', active_tab='system_settings')

@app.route('/system-settings-data')
def system_settings_data():
    # Load auth.json and wifi.json if present
    auth = {}
    wifi = {}
    if os.path.exists('auth.json'):
        with open('auth.json') as f:
            auth = json.load(f)
    if os.path.exists('wifi.json'):
        with open('wifi.json') as f:
            wifi = json.load(f)
    return jsonify({'auth': auth, 'wifi': wifi})

@app.route('/system-settings-auth', methods=['POST'])
def system_settings_auth():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required.'})
    with open('auth.json', 'w') as f:
        json.dump({'username': username, 'password': password}, f)
    return jsonify({'success': True})

@app.route('/system-settings-wifi', methods=['POST'])
def system_settings_wifi():
    data = request.get_json()
    ssid = data.get('ssid', '').strip()
    password = data.get('password', '').strip()
    if not ssid or not password:
        return jsonify({'success': False, 'error': 'SSID and password required.'})
    # Save to wifi.json for web UI
    with open('wifi.json', 'w') as f:
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
    Check for updates by comparing local files to remote timestamps from gyropilots.org.
    Returns a string summary of files that need updating, or a message if up to date.
    """
    try:
        try:
            import requests
        except ImportError:
            return jsonify({'success': False, 'error': 'Python requests module is not installed. Please install it with "pip install requests".'})
        url = 'https://gyropilots.org/updateservices.php?command=checkforEncoderUpdates'
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            remote_files = resp.json()  # Should be {filename: timestamp}
        except Exception as e:
            return jsonify({'success': False, 'error': f'Could not fetch update info: {e}'})
        if not isinstance(remote_files, dict):
            return jsonify({'success': False, 'error': 'Invalid update info format from server.'})
        update_info = []
        # Only consider .py, .html, .png files from remote
        filtered_remote_files = {fname: ts for fname, ts in remote_files.items() if fname.endswith(('.py', '.html', '.png'))}
        # Check for new or updated files in filtered_remote_files
        for fname, remote_ts in filtered_remote_files.items():
            local_path = os.path.join(os.getcwd(), fname.replace('/', os.sep))
            if not os.path.isfile(local_path):
                update_info.append({
                    'filename': fname,
                    'action': 'new',
                    'detail': f"new file available (remote {remote_ts})"
                })
            else:
                try:
                    local_ts = int(os.path.getmtime(local_path))
                except Exception:
                    local_ts = 0
                if int(remote_ts) > local_ts:
                    update_info.append({
                        'filename': fname,
                        'action': 'update',
                        'detail': f"outdated (local {local_ts}, remote {remote_ts})"
                    })
        # Check for local .py, .html, .png files not in filtered_remote_files (will be deleted)
        for root, dirs, files in os.walk(os.getcwd()):
            for file in files:
                if file.endswith('.py') or file.endswith('.html') or file.endswith('.png'):
                    rel_path = os.path.relpath(os.path.join(root, file), os.getcwd()).replace('\\', '/')
                    if rel_path not in filtered_remote_files and not any(u['filename'] == rel_path for u in update_info):
                        update_info.append({
                            'filename': rel_path,
                            'action': 'delete',
                            'detail': 'file will be deleted'
                        })
        if update_info:
            summary = "Updates are available."
        else:
            summary = "All files are up to date."
        return jsonify({'success': True, 'summary': summary, 'updates': update_info})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Update check failed: {e}'})

@app.route('/system-do-update', methods=['POST'])
def system_do_update():
    """
    Download a .tgz archive of all update files from the remote server and extract it, then delete any local .py, .html, .png files not present in the archive.
    """
    import requests
    import shutil
    import tarfile
    import tempfile
    try:
        # Download the .tgz archive from the remote server
        tgz_url = 'https://gyropilots.org/updateservices.php?command=getEncoderUpdatesPayload'
        with requests.get(tgz_url, timeout=60, stream=True) as r:
            r.raise_for_status()
            with tempfile.NamedTemporaryFile(delete=False, suffix='.tgz') as tmpf:
                shutil.copyfileobj(r.raw, tmpf)
                tgz_path = tmpf.name
        # List files in the archive (normalize paths)
        with tarfile.open(tgz_path, 'r:gz') as tar:
            archive_files = []
            for m in tar.getmembers():
                if m.isfile() and m.name.endswith(('.py', '.html', '.png')):
                    norm_name = m.name.lstrip('./').replace('\\', '/').replace('\\', '/')
                    archive_files.append(norm_name)
            # Extract all files to the current working directory, preserving structure
            tar.extractall(path=os.getcwd())
        # Delete any local .py, .html, .png files not present in the archive
        deleted = []
        for root, dirs, files in os.walk(os.getcwd()):
            for file in files:
                if file.endswith('.py') or file.endswith('.html') or file.endswith('.png'):
                    rel_path = os.path.relpath(os.path.join(root, file), os.getcwd()).replace('\\', '/').lstrip('./')
                    if rel_path not in archive_files:
                        try:
                            os.remove(os.path.join(root, file))
                            deleted.append(f"Deleted: {rel_path}")
                        except Exception as e:
                            deleted.append(f"Failed to delete {rel_path}: {e}")
        # Change ownership of all extracted files and folders to user and group 'admin'
        import pwd, grp
        try:
            uid = pwd.getpwnam('admin').pw_uid
            gid = grp.getgrnam('admin').gr_gid
            for f in archive_files:
                abs_path = os.path.join(os.getcwd(), f)
                if os.path.exists(abs_path):
                    try:
                        os.chown(abs_path, uid, gid)
                    except Exception as e:
                        deleted.append(f"Failed to chown file {f}: {e}")
            # Also chown all parent directories of extracted files
            extracted_dirs = set(os.path.dirname(os.path.join(os.getcwd(), f)) for f in archive_files)
            for d in extracted_dirs:
                if os.path.exists(d):
                    try:
                        os.chown(d, uid, gid)
                    except Exception as e:
                        deleted.append(f"Failed to chown dir {d}: {e}")
        except Exception as e:
            deleted.append(f"Failed to set ownership to admin: {e}")
        os.remove(tgz_path)
        #restart the flask_app systemd service if it exists
        try:
            subprocess.run(['sudo', 'systemctl', 'restart', 'flask_app'], check=True)
        except Exception as e:
            deleted.append(f"Failed to restart flask_app service: {e}")
        #restart the mediamtx systemd service if it exists
        try:
            subprocess.run(['sudo', 'systemctl', 'restart', 'mediamtx'], check=True)
        except Exception as e:
            deleted.append(f"Failed to restart mediamtx service: {e}")
        return jsonify({'success': True, 'results': [f"Extracted: {f}" for f in archive_files] + deleted})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Update failed: {e}'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=True, threaded=True)