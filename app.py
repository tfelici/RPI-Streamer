# Monkey patch for gevent compatibility (must be at the very top)
try:
    from gevent import monkey
    monkey.patch_all()
except ImportError:
    print("Warning: gevent not installed. Install with 'pip install gevent' for better async performance.")
    pass

from flask import Flask, render_template, Response, jsonify, request
import psutil
import threading
import time
import os
import json
import subprocess
import re
import uuid
import requests
from datetime import datetime
from functools import wraps
from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor
from utils import list_audio_inputs, list_video_inputs, find_usb_storage, get_ups_status, move_file_to_usb, copy_settings_and_executables_to_usb

# Use pymediainfo for fast video duration extraction
try:
    from pymediainfo import MediaInfo
except ImportError:
    MediaInfo = None

app = Flask(__name__)

# Global dictionary to track upload progress and allow cancellation
upload_progress = {}
upload_threads = {}
# SSE clients tracking for upload progress
upload_sse_clients = {}

@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

STREAMER_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'streamerData'))
STREAM_PIDFILE = "/tmp/relay-ffmpeg-webcam.pid"
SETTINGS_FILE = os.path.join(STREAMER_DATA_DIR, 'settings.json')

def load_settings():
    settings = {
        "stream_url": "",
        "framerate": 30,
        "crf": '',
        "resolution": "1360x768",
        "vbitrate": 384,
        "abitrate": "128k",
        "ar": 16000,
        "upload_url": "",
        "volume": 100,
        "gop": 30,
        "dynamicBitrate": True
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                settings.update(json.load(f))
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Could not parse settings.json: {e}")
            # Keep default settings
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

def get_power_draw():
    """
    Try to get total power draw in watts (W) or microwatts (uW).
    Returns a string, or 'N/A' if not available.
    """
    power_paths = [
        '/sys/class/power_supply/rpi_battery/power_now',
        '/sys/class/power_supply/battery/power_now',
        '/sys/class/power_supply/axp20x-battery/power_now',
        '/sys/class/power_supply/usb/power_now',
    ]
    for path in power_paths:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    val = int(f.read().strip())
                    if val > 10000:
                        return f"{val/1_000_000:.2f} W"
                    else:
                        return f"{val} uW"
            except Exception:
                continue
    # Try using vcgencmd if available (returns voltage and current)
    try:
        result = subprocess.run(['vcgencmd', 'measure_volts'], capture_output=True, text=True)
        volts = None
        if result.returncode == 0:
            m = re.search(r'volt=([\d.]+)V', result.stdout)
            if m:
                volts = float(m.group(1))
        result = subprocess.run(['vcgencmd', 'measure_current'], capture_output=True, text=True)
        amps = None
        if result.returncode == 0:
            m = re.search(r'current=([\d.]+)A', result.stdout)
            if m:
                amps = float(m.group(1))
        if volts is not None and amps is not None:
            watts = volts * amps
            return f"{watts:.2f} W"
        elif volts is not None:
            return f"{volts:.2f} V"
    except Exception:
        pass
    return "N/A"

def is_streaming():
    """Return True if streaming is currently active."""
    if os.path.exists(STREAM_PIDFILE):
        try:
            with open(STREAM_PIDFILE) as f:
                pid = int(f.read().strip())
            # Check if the process is still running
            return is_pid_running(pid)
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

def is_pid_running(pid):
    """
    Check if a process with the given PID is actually running using psutil.
    Returns False for non-existent, zombie, or dead processes.
    """
    try:
        process = psutil.Process(pid)
        # Check if process is running (not zombie/dead)
        status = process.status()
        return status not in [psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False

def get_active_recording_info():
    """Return (pid, file_path) if an active recording is in progress, else (None, None)"""
    ACTIVE_PIDFILE = "/tmp/relay-ffmpeg-record-webcam.pid"
    if os.path.exists(ACTIVE_PIDFILE):
        try:
            with open(ACTIVE_PIDFILE, 'r') as f:
                line = f.read().strip()
                if line:
                    pid_str, file_path = line.split(':', 1)
                    pid = int(pid_str)
                    if is_pid_running(pid):
                        return pid, file_path
        except Exception:
            pass
    return None, None

def add_files_from_path(recording_files, path, source_label="", location="Local"):
    """Helper function to add files from a given path. Appends to the passed-in recording_files list."""
    active_pid, active_file = get_active_recording_info()
    if os.path.isdir(path):
        files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
        files.sort(key=lambda f: os.path.getmtime(os.path.join(path, f)), reverse=True)
        for f in files:
            file_path = os.path.join(path, f)
            file_size = os.path.getsize(file_path)
            display_name = f"{source_label}{f}" if source_label else f
            is_active = (active_file is not None and os.path.abspath(file_path) == os.path.abspath(active_file) and is_pid_running(active_pid))
            # Add duration if file is not active
            if is_active:
                duration = None
            else:
                duration = get_video_duration_mediainfo(file_path)
            # Extract timestamp from filename if possible
            m = re.match(r'^(\d+)\.mp4$', f)
            timestamp = int(m.group(1)) if m else None
            recording_files.append({
                'path': file_path,
                'size': file_size,
                'active': is_active,
                'name': display_name,
                'location': location,
                'duration': duration,
                'timestamp': timestamp
            })

@app.route('/')
def home():
    streaming = is_streaming()

    # Gather recording files info
    recording_files = []
    local_recording_path = os.path.join(STREAMER_DATA_DIR, 'recordings', 'webcam')
    add_files_from_path(recording_files, local_recording_path, "", "Local")
    # Add files from USB storage if available
    usb_mount_point = find_usb_storage()
    if usb_mount_point:
        usb_recording_path = os.path.join(usb_mount_point, 'streamerData', 'recordings', 'webcam')
        add_files_from_path(recording_files, usb_recording_path, "[USB] ", "USB")

    return render_template('index.html', active_tab='home', streaming=streaming, recording_files=recording_files, app_version=get_app_version())

@app.route('/stats')
def stats():
    def event_stream():
        while True:
            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory().percent
            temp = get_temperature()
            power = get_power_draw()
            connection = get_connection_info()
            
            # Format connection info for JSON
            import json
            connection_json = json.dumps(connection).replace('"', '\\"')
            
            data = f"data: {{\"cpu\": {cpu}, \"mem\": {mem}, \"temp\": \"{temp}\", \"power\": \"{power}\", \"connection\": \"{connection_json}\"}}\n\n"
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
            crf_val = data['crf']
            if crf_val is None or crf_val == "":
                settings['crf'] = ""  # or None, as your ffmpeg logic expects
            else:
                settings['crf'] = int(crf_val)
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
        if 'audio_input' in data:
            settings['audio_input'] = data['audio_input']
        if 'video_input' in data:
            settings['video_input'] = data['video_input']
        if 'volume' in data:
            settings['volume'] = int(data['volume'])
        if 'dynamicBitrate' in data:
            # Accept both boolean and string values from the form
            val = data['dynamicBitrate']
            if isinstance(val, bool):
                settings['dynamicBitrate'] = val
            elif isinstance(val, str):
                settings['dynamicBitrate'] = val.lower() == 'true'
        save_settings(settings)
        return '', 204
    else:
        return jsonify(load_settings())

@app.route('/settings-page')
def settings_page():
    audio_inputs = list_audio_inputs()
    video_inputs = list_video_inputs()
    return render_template(
        'settings.html',
        active_tab='settings',
        settings=load_settings(),
        app_version=get_app_version(),
        audio_inputs=audio_inputs,
        video_inputs=video_inputs
    )

@app.route('/audio-inputs') 
def audio_inputs():
    return jsonify(list_audio_inputs())

@app.route('/video-inputs')
def video_inputs():
    return jsonify(list_video_inputs())

@app.route('/video-resolutions')
def video_resolutions():
    """
    Returns a list of supported video resolutions (width x height) for /dev/video0,
    filtered to only those at or below 1920x1080. Uses v4l2-ctl --try-fmt-video to check validity and ensures the Width/Height in the output match the requested values.
    """
    try:
        output = subprocess.check_output([
            'v4l2-ctl', '--list-formats-ext', '-d', '/dev/video0'
        ], text=True, stderr=subprocess.DEVNULL)
        # Find all lines like: 'Size: Discrete 1920x1080'
        resolutions = set(re.findall(r'Size: Discrete (\d+)x(\d+)', output))
        valid_res_list = []
        for w, h in resolutions:
            if int(w) <= 1920 and int(h) <= 1080:
                try:
                    try_fmt_cmd = [
                        'v4l2-ctl', '--device=/dev/video0', f'--try-fmt-video=width={w},height={h},pixelformat=MJPG'
                    ]
                    result = subprocess.run(try_fmt_cmd, capture_output=True, text=True, timeout=2)
                    if result.returncode == 0:
                        # Look for 'Width/Height      : WxH' in the output
                        m = re.search(r'Width/Height\s*:\s*(\d+)/(\d+)', result.stdout)
                        if m and m.group(1) == w and m.group(2) == h:
                            valid_res_list.append(f'{w}x{h}')
                except Exception:
                    continue
        valid_res_list.sort(key=lambda s: (-int(s.split('x')[0]), -int(s.split('x')[1])))
        return jsonify(valid_res_list)
    except Exception as e:
        return jsonify([])

@app.route('/stream-control', methods=['POST'])
def stream_control():
    data = request.get_json()
    action = data.get('action')
    if action == 'start':
        # Check if already streaming
        if is_streaming():
            return jsonify({'status': 'Streaming is already active.'})
        settings = load_settings()
        stream_url = settings.get('stream_url', '').strip()
        if not stream_url:
            return jsonify({'error': 'Remote Streaming URL is not set. Please configure it in Settings.'}), 400
        #start relay-ffmpeg.py asynchronously
        subprocess.Popen(['python', 'relay-ffmpeg.py', 'webcam'])
        #also start relay-ffmpeg-record.py asynchronously
        subprocess.Popen(['python', 'relay-ffmpeg-record.py', 'webcam'])
        return jsonify({'status': 'started'})
    elif action == 'stop':
        if is_streaming():
            #stop both relay-ffmpeg.py and relay-ffmpeg-record.py processes
            print("Stopping stream...")
            try:
                with open(STREAM_PIDFILE, 'r') as f:
                    pid = int(f.read().strip())
                print(f"Stopping stream with PID {pid}")
                if is_pid_running(pid):
                    os.kill(pid, 15)  # SIGTERM
                #also stop the recording process if it exists
                active_pid, _ = get_active_recording_info()
                if active_pid and is_pid_running(active_pid):
                    print(f"Stopping recording with PID {active_pid}")
                    os.kill(active_pid, 15)  # SIGTERM
                # loop until both processes are no longer running
                while active_pid and is_pid_running(active_pid) or pid and is_pid_running(pid):
                    #print status
                    print(f"Waiting for stream and recording to stop... (stream PID: {pid}={is_pid_running(pid)}, recording PID: {active_pid}={is_pid_running(active_pid)})")
                    time.sleep(0.5)  # Give it a moment to terminate
                print("Stream and recording stopped successfully.")
            except Exception as e:
                print(f"Error stopping stream: {e}")
        else:
            print("No active stream to stop.")
        return jsonify({'status': 'stopped'})
    else:
        return jsonify({'error': 'Invalid action'}), 400

@app.route('/upload-recording', methods=['POST'])
def upload_recording():
    from werkzeug.utils import secure_filename
    
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
    # Generate unique upload ID for progress tracking
    upload_id = str(uuid.uuid4())
    
    # Store upload progress globally
    upload_progress[upload_id] = {
        'progress': 0,
        'status': 'starting',
        'error': None,
        'result': None,
        'cancelled': False
    }
    
    def upload_file_async():
        try:
            upload_progress[upload_id]['status'] = 'uploading'
            
            # Get file size for progress calculation
            file_size = os.path.getsize(file_path)
            
            def progress_callback(monitor):
                if upload_progress[upload_id]['cancelled']:
                    # Cancel the upload by raising an exception
                    raise Exception("Upload cancelled by user")
                
                progress = min(100, int((monitor.bytes_read / file_size) * 100))
                upload_progress[upload_id]['progress'] = progress
                
                # Notify all SSE clients about the progress
                for client_id, client_data in upload_sse_clients.items():
                    if client_data['upload_id'] == upload_id:
                        try:
                            # Send progress update to SSE client
                            client_data['queue'].put({'progress': progress})
                        except Exception:
                            pass  # Ignore errors in notifying clients
            
            # Use MultipartEncoder for upload with progress monitoring
            with open(file_path, 'rb') as f:
                multipart_data = MultipartEncoder(
                    fields={'video': (secure_filename(os.path.basename(file_path)), f, 'application/octet-stream')}
                )
                
                monitor = MultipartEncoderMonitor(multipart_data, progress_callback)
                
                response = requests.post(
                    upload_url, 
                    data=monitor,
                    headers={'Content-Type': monitor.content_type},
                    timeout=300
                )
                
                if response.status_code == 200:
                    try:
                        result = response.json()
                    except:
                        result = {'success': True, 'message': 'Upload completed', 'error': ''}
                else:
                    result = {'error': f'Upload failed: {response.status_code}'}
                
                upload_progress[upload_id]['status'] = 'completed'
                upload_progress[upload_id]['progress'] = 100
                upload_progress[upload_id]['result'] = result
                
                # If upload succeeded and no error, delete the original file
                if result.get('error') == '':
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass  # Ignore deletion errors
                        
        except Exception as e:
            if upload_progress[upload_id]['cancelled']:
                upload_progress[upload_id]['status'] = 'cancelled'
                upload_progress[upload_id]['error'] = 'Upload cancelled by user'
            else:
                upload_progress[upload_id]['status'] = 'error'
                upload_progress[upload_id]['error'] = f'Upload failed: {e}'
        finally:
            # Clean up thread reference
            if upload_id in upload_threads:
                del upload_threads[upload_id]
      # Start upload in background thread
    thread = threading.Thread(target=upload_file_async)
    thread.daemon = True
    upload_threads[upload_id] = thread
    thread.start()
    
    return jsonify({'upload_id': upload_id, 'status': 'started'})

@app.route('/upload-progress/<upload_id>')
def get_upload_progress(upload_id):
    """Get the current progress of an upload"""
    if upload_id not in upload_progress:
        return jsonify({'error': 'Upload ID not found'}), 404
    
    progress_data = upload_progress[upload_id].copy()
    
    # Clean up completed/error/cancelled uploads after returning status
    if progress_data['status'] in ['completed', 'error', 'cancelled']:
        # Keep the data for a short time to allow frontend to get final status
        pass
    
    return jsonify(progress_data)

@app.route('/cancel-upload/<upload_id>', methods=['POST'])
def cancel_upload(upload_id):
    """Cancel an ongoing upload"""
    if upload_id not in upload_progress:
        return jsonify({'error': 'Upload ID not found'}), 404
    
    # Mark upload as cancelled
    upload_progress[upload_id]['cancelled'] = True
    upload_progress[upload_id]['status'] = 'cancelling'
    
    return jsonify({'status': 'cancelling'})

@app.route('/upload-progress-stream/<upload_id>')
def upload_progress_stream(upload_id):
    """SSE endpoint for real-time upload progress monitoring"""
    def generate():
        # Send initial connection event
        yield f"data: {json.dumps({'type': 'connected', 'upload_id': upload_id})}\n\n"
        
        # Monitor upload progress
        while upload_id in upload_progress:
            progress_data = upload_progress[upload_id].copy()
            
            # Send progress update
            progress_data['type'] = 'progress'
            yield f"data: {json.dumps(progress_data)}\n\n"
            
            # If upload is finished, send final status and close
            if progress_data['status'] in ['completed', 'error', 'cancelled']:
                time.sleep(0.1)  # Small delay to ensure client receives final update
                break
                
            time.sleep(0.2)  # Update every 200ms for real-time feel
        
        # Send close event
        yield f"data: {json.dumps({'type': 'closed', 'upload_id': upload_id})}\n\n"
    
    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Cache-Control'
        }
    )

@app.route('/camera-viewer')
def camera_viewer():
    streaming = is_streaming()
    return render_template('camera_viewer.html', active_tab='camera', streaming=streaming, app_version=get_app_version())

# --- Basic HTTP Auth ---
import json

def get_auth_creds():
    auth_file = os.path.join(STREAMER_DATA_DIR, 'auth.json')
    if os.path.exists(auth_file):
        try:
            with open(auth_file) as f:
                auth = json.load(f)
            return auth.get('username', 'admin'), auth.get('password', '12345')
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Could not parse auth.json: {e}")
            return 'admin', '12345'
    return '', ''

def is_auth_enabled():
    """Check if authentication is enabled (password is not empty)"""
    _, password = get_auth_creds()
    return password and password.strip() != ''

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
    
    # Skip authentication if password is empty/blank
    if not is_auth_enabled():
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

# Fast duration extraction using pymediainfo
def get_video_duration_mediainfo(path):
    if MediaInfo is None:
        return None
    try:
        media_info = MediaInfo.parse(path)
        for track in media_info.tracks:
            if track.track_type == 'Video' and track.duration:
                return track.duration / 1000.0  # ms to seconds
        # fallback: try general track
        for track in media_info.tracks:
            if track.track_type == 'General' and track.duration:
                return track.duration / 1000.0
    except Exception:
        pass
    return None

def get_auth_and_wifi():
    auth = {}
    wifi = {}
    auth_path = os.path.join(STREAMER_DATA_DIR, 'auth.json')
    wifi_path = os.path.join(STREAMER_DATA_DIR, 'wifi.json')
    
    if os.path.exists(auth_path):
        try:
            with open(auth_path) as f:
                auth = json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Could not parse auth.json: {e}")
            auth = {}
    
    if os.path.exists(wifi_path):
        try:
            with open(wifi_path) as f:
                wifi = json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Could not parse wifi.json: {e}")
            wifi = {}
    
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
    
    # Username is required, but password can be empty (disables auth)
    if not username:
        return jsonify({'success': False, 'error': 'Username is required.'})
    
    auth_path = os.path.join(STREAMER_DATA_DIR, 'auth.json')
    with open(auth_path, 'w') as f:
        json.dump({'username': username, 'password': password}, f)
    
    if password == '':
        return jsonify({'success': True, 'message': 'Authentication disabled (empty password).'})
    else:
        return jsonify({'success': True, 'message': 'Authentication settings updated.'})

@app.route('/system-settings-wifi', methods=['POST'])
def system_settings_wifi():
    data = request.get_json()
    ssid = data.get('ssid', '').strip()
    password = data.get('password', '').strip()
    if not ssid or not password:
        return jsonify({'success': False, 'error': 'SSID and password required.'})
    wifi_path = os.path.join(STREAMER_DATA_DIR, 'wifi.json')
    # Save to wifi.json for web UI
    with open(wifi_path, 'w') as f:
        json.dump({'ssid': ssid, 'password': password}, f)
    # Use NetworkManager for RPI5 with Bookworm (instead of wpa_supplicant/dhcpcd)
    try:
        # First, remove any existing connection with the same SSID
        subprocess.run(['sudo', 'nmcli', 'connection', 'delete', ssid], check=False)
        
        # Add new WiFi connection with NetworkManager
        cmd = [
            'sudo', 'nmcli', 'device', 'wifi', 'connect', ssid,
            'password', password,
            'name', ssid
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            # If direct connection fails, try creating connection profile first
            create_cmd = [
                'sudo', 'nmcli', 'connection', 'add',
                'type', 'wifi',
                'con-name', ssid,
                'ssid', ssid,
                'wifi-sec.key-mgmt', 'wpa-psk',
                'wifi-sec.psk', password,
                'connection.autoconnect', 'yes'
            ]
            subprocess.run(create_cmd, check=True)
              # Activate the connection
            subprocess.run(['sudo', 'nmcli', 'connection', 'up', ssid], check=True)
        
        # Ensure NetworkManager is enabled for auto-start on boot
        subprocess.run(['sudo', 'systemctl', 'enable', 'NetworkManager'], check=False)
        
        # Configure WiFi with optimized settings for faster connection
        subprocess.run([
            'sudo', 'nmcli', 'connection', 'modify', ssid,
            'connection.autoconnect', 'yes',
            'connection.autoconnect-priority', '5',
            # Optimize WiFi connection timeouts and retry behavior
            'wifi.powersave', '2',  # Disable powersave for faster scanning
            'connection.autoconnect-retries', '0',  # Infinite retries
            'ipv4.dhcp-timeout', '10',  # Faster DHCP timeout (default 45s)
            'ipv6.addr-gen-mode', 'stable-privacy',
            'ipv6.dhcp-timeout', '10'
        ], check=False)
        
        # Set aggressive WiFi scanning for faster detection
        subprocess.run([
            'sudo', 'nmcli', 'connection', 'modify', ssid,
            'wifi.scan-rand-mac-address', 'no',  # Disable MAC randomization for faster scanning
            'connection.wait-device-timeout', '5000'  # 5 second wait for device (default 10s)
        ], check=False)
        
        # Ensure Ethernet connection exists and has higher priority (preferred)
        subprocess.run([
            'sudo', 'nmcli', 'connection', 'modify', 'Wired connection 1',
            'connection.autoconnect', 'yes',
            'connection.autoconnect-priority', '10'
        ], check=False)
        
        # Alternative: Create ethernet connection if it doesn't exist
        subprocess.run([
            'sudo', 'nmcli', 'connection', 'add',
            'type', 'ethernet',
            'con-name', 'Ethernet-Primary',
            'ifname', 'eth0',
            'connection.autoconnect', 'yes',
            'connection.autoconnect-priority', '10'
        ], check=False)
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to configure WiFi with NetworkManager: {e}'})
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
        # Change ownership of all files to the user/group specified by environment variable
        import os
        owner = os.environ.get('OWNER')
        if not owner:
            raise RuntimeError("No user:group parameter provided. Please set OWNER environment variable (e.g., OWNER=pi:pi)")
        chown = subprocess.run(['sudo', 'chown', '-R', owner, '.'], capture_output=True, text=True)
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

def get_connection_info():
    """
    Get current network connection information including IP addresses and connection types.
    Returns a dictionary with connection details.
    """
    connection_info = {
        "ethernet": "Disconnected",
        "wifi": "Disconnected", 
        "ip_addresses": [],
        "active_connections": []
    }
    
    try:
        # Get network interfaces and their addresses
        import socket
        import subprocess
        import platform
        
        # Get all network interfaces with IP addresses
        interfaces = psutil.net_if_addrs()
        for interface_name, addresses in interfaces.items():
            for addr in addresses:
                if addr.family == socket.AF_INET and not addr.address.startswith('127.'):
                    # Skip APIPA addresses (169.254.x.x) unless no other connections exist
                    is_apipa = addr.address.startswith('169.254.')
                    
                    # Determine connection type based on interface name
                    interface_lower = interface_name.lower()
                    if any(keyword in interface_lower for keyword in ['ethernet', 'eth', 'en0', 'eno', 'enp']):
                        conn_type = "ethernet"
                        if not is_apipa:
                            connection_info["ethernet"] = "Connected"
                    elif any(keyword in interface_lower for keyword in ['wi-fi', 'wifi', 'wlan', 'wlp', 'wireless']):
                        conn_type = "wifi"
                        if not is_apipa:
                            connection_info["wifi"] = "Connected"
                    else:
                        conn_type = "other"
                    
                    connection_info["ip_addresses"].append({
                        "interface": interface_name,
                        "ip": addr.address,
                        "type": conn_type
                    })
        
        # Platform-specific connection detection
        try:
            if platform.system() == "Windows":
                # Use ipconfig on Windows to get more detailed connection info
                result = subprocess.run(['ipconfig'], capture_output=True, text=True, timeout=3)
                if result.returncode == 0:
                    lines = result.stdout.split('\n')
                    current_adapter = ""
                    for line in lines:
                        line = line.strip()
                        if "adapter" in line.lower() and ":" in line:
                            current_adapter = line
                        elif "IPv4 Address" in line and current_adapter:
                            # Found an active connection
                            if "ethernet" in current_adapter.lower():
                                connection_info["ethernet"] = "Connected"
                            elif "wi-fi" in current_adapter.lower() or "wireless" in current_adapter.lower():
                                connection_info["wifi"] = "Connected"
            else:
                # Use ip command on Linux/Unix
                result = subprocess.run(['ip', 'route', 'show', 'default'], capture_output=True, text=True, timeout=2)
                if result.returncode == 0:
                    lines = result.stdout.strip().split('\n')
                    for line in lines:
                        if 'dev' in line:
                            parts = line.split()
                            if 'dev' in parts:
                                dev_index = parts.index('dev')
                                if dev_index + 1 < len(parts):
                                    interface = parts[dev_index + 1]
                                    if interface not in connection_info["active_connections"]:
                                        connection_info["active_connections"].append(interface)
                                        
                                        # Determine connection type
                                        if any(keyword in interface.lower() for keyword in ["eth", "en", "eno", "enp"]):
                                            connection_info["ethernet"] = "Connected"
                                        elif any(keyword in interface.lower() for keyword in ["wlan", "wi", "wlp"]):
                                            connection_info["wifi"] = "Connected"
        except Exception:
            pass
              # Try to get WiFi SSID, signal strength, and bitrates if connected
        try:
            if connection_info["wifi"] == "Connected":
                wifi_details = {}
                
                if platform.system() == "Windows":
                    # Try netsh on Windows to get WiFi SSID
                    result = subprocess.run(['netsh', 'wlan', 'show', 'profiles'], capture_output=True, text=True, timeout=3)
                    if result.returncode == 0:
                        # Get currently connected profile
                        interfaces_result = subprocess.run(['netsh', 'wlan', 'show', 'interfaces'], capture_output=True, text=True, timeout=3)
                        if interfaces_result.returncode == 0:
                            for line in interfaces_result.stdout.split('\n'):
                                if 'SSID' in line and ':' in line:
                                    ssid = line.split(':', 1)[1].strip()
                                    if ssid and ssid != '':
                                        wifi_details['ssid'] = ssid
                                        break
                else:
                    # Linux/Unix - Get SSID and detailed WiFi info
                    # Try nmcli first (NetworkManager) for SSID
                    result = subprocess.run(['nmcli', '-t', '-f', 'active,ssid', 'dev', 'wifi'], capture_output=True, text=True, timeout=2)
                    if result.returncode == 0:
                        lines = result.stdout.strip().split('\n')
                        for line in lines:
                            if line.startswith('yes:'):
                                ssid = line.split(':', 1)[1]
                                if ssid:
                                    wifi_details['ssid'] = ssid
                                    break
                    
                    # Get detailed WiFi information using iw command
                    # Try different interface names, including dynamic detection
                    wifi_interfaces = ['wlan0', 'wlp2s0', 'wlp3s0', 'wlo1']
                    
                    # Try to detect available wireless interfaces dynamically
                    try:
                        iw_dev_result = subprocess.run(['iw', 'dev'], capture_output=True, text=True, timeout=2)
                        if iw_dev_result.returncode == 0:
                            # Parse output to find wireless interfaces
                            for line in iw_dev_result.stdout.split('\n'):
                                if 'Interface' in line:
                                    iface_match = re.search(r'Interface\s+(\w+)', line)
                                    if iface_match:
                                        iface = iface_match.group(1)
                                        if iface not in wifi_interfaces:
                                            wifi_interfaces.append(iface)
                    except Exception:
                        pass  # Fall back to default list
                    
                    for iface in wifi_interfaces:
                        try:
                            iw_result = subprocess.run(['iw', 'dev', iface, 'link'], capture_output=True, text=True, timeout=5)
                            if iw_result.returncode == 0 and ('Connected to' in iw_result.stdout or 'SSID:' in iw_result.stdout):
                                lines = iw_result.stdout.split('\n')
                                interface_found = True
                                for line in lines:
                                    line = line.strip()
                                    # Parse signal strength
                                    if 'signal:' in line:
                                        # Extract signal strength (e.g., "signal: -45 dBm")
                                        signal_match = re.search(r'signal:\s*(-?\d+)\s*dBm', line)
                                        if signal_match:
                                            signal_dbm = int(signal_match.group(1))
                                            wifi_details['signal_dbm'] = signal_dbm
                                            # Convert to percentage (rough estimation)
                                            # -30 dBm = 100%, -90 dBm = 0%
                                            signal_percent = max(0, min(100, (signal_dbm + 90) * 100 / 60))
                                            wifi_details['signal_percent'] = int(signal_percent)
                                    # Parse TX bitrate
                                    elif 'tx bitrate:' in line:
                                        # Extract TX bitrate (e.g., "tx bitrate: 72.2 MBit/s")
                                        tx_match = re.search(r'tx bitrate:\s*([\d.]+)\s*MBit/s', line)
                                        if tx_match:
                                            wifi_details['tx_bitrate'] = float(tx_match.group(1))
                                    # Parse RX bitrate
                                    elif 'rx bitrate:' in line:
                                        # Extract RX bitrate (e.g., "rx bitrate: 65.0 MBit/s")
                                        rx_match = re.search(r'rx bitrate:\s*([\d.]+)\s*MBit/s', line)
                                        if rx_match:
                                            wifi_details['rx_bitrate'] = float(rx_match.group(1))
                                break  # Found working interface, stop trying others
                        except Exception as e:
                            # Log the error for debugging but continue trying other interfaces
                            continue
                
                # Update connection_info with detailed WiFi information
                if wifi_details:
                    connection_info['wifi_details'] = wifi_details
                    # Update main wifi status with SSID if available
                    if 'ssid' in wifi_details:
                        wifi_status = f"Connected ({wifi_details['ssid']})"
                        # Add signal strength if available
                        if 'signal_percent' in wifi_details:
                            wifi_status += f" - {wifi_details['signal_percent']}%"
                        connection_info["wifi"] = wifi_status
        except Exception:
            pass
            
    except Exception as e:
        connection_info["error"] = str(e)
    
    return connection_info

#sse entry point to return relay_status_webcam data
@app.route('/relay-status-sse')
def relay_status_sse():
    from flask import Response
    import json, time
    def event_stream():
        last_status = None
        connection_timeout = 0
        while True:
            # Exit if streaming is stopped and we've been disconnected for a while
            if not is_streaming():
                connection_timeout += 1
                if connection_timeout > 3:  # Exit after 3 seconds of no streaming
                    break
            else:
                connection_timeout = 0
                
            try:
                # Read the relay status file
                with open('/tmp/relay_status_webcam.json', 'r') as f:
                    status = f.read().strip()
                
                if status != last_status:
                    yield f"data: {json.dumps({'status': status})}\n\n"
                    last_status = status
                
                time.sleep(1)  # Update every second
            except FileNotFoundError:
                # File doesn't exist yet, send basic status if streaming
                if is_streaming():
                    fallback_status = json.dumps({
                        'bitrate': 'initializing',
                        'network_status': 'starting',
                        'pipeline_status': 'starting',
                        'stream_health': 'initializing'
                    })
                    if fallback_status != last_status:
                        yield f"data: {json.dumps({'status': fallback_status})}\n\n"
                        last_status = fallback_status
                time.sleep(2)  # Check less frequently when file doesn't exist
            except Exception as e:
                print(f"Error reading relay status: {e}")
                time.sleep(2)
                break
        print("Closing SSE stream for relay status")
    return Response(event_stream(), mimetype='text/event-stream')

@app.route('/active-recordings-sse')
def active_recordings_sse():
    import json
    def event_stream():
        last_active_files = None
        usb_mount_point = find_usb_storage()
        while is_streaming() or last_active_files is None:
            # Rebuild recording_files list (same as in home route)
            recording_files = []
            local_recording_path = os.path.join(STREAMER_DATA_DIR, 'recordings', 'webcam')
            add_files_from_path(recording_files, local_recording_path, "", "Local")
            if usb_mount_point:
                usb_recording_path = os.path.join(usb_mount_point, 'streamerData', 'recordings', 'webcam')
                add_files_from_path(recording_files, usb_recording_path, "[USB] ", "USB")
            active_files = [f for f in recording_files if f.get('active')]
            # Only send if changed
            if active_files != last_active_files:
                yield f"data: {json.dumps({'files': active_files})}\n\n"
                last_active_files = active_files
            time.sleep(1)
        print("No active recordings, closing SSE stream")
    return Response(event_stream(), mimetype='text/event-stream')

@app.route('/diagnostics-sse')
def diagnostics_sse():
    """SSE endpoint for system diagnostics updates."""
    import json
    def event_stream():
        last_diagnostics = None
        while True:
            try:
                diagnostics = get_system_diagnostics()
                
                # Only send if changed (to reduce bandwidth)
                if diagnostics != last_diagnostics:
                    yield f"data: {json.dumps(diagnostics)}\n\n"
                    last_diagnostics = diagnostics
                    
                time.sleep(2)  # Update every 2 seconds
            except Exception as e:
                error_data = {'error': str(e)}
                yield f"data: {json.dumps(error_data)}\n\n"
                time.sleep(5)  # Wait longer on error
    return Response(event_stream(), mimetype='text/event-stream')

def get_system_diagnostics():
    """
    Get comprehensive system diagnostics using vcgencmd.
    Returns a dictionary with all available diagnostic information.
    """
    diagnostics = {}
    
    # List of vcgencmd commands to run
    commands = {
        'temperature': ['measure_temp'],
        'voltage_core': ['measure_volts', 'core'],
        'voltage_sdram_c': ['measure_volts', 'sdram_c'],
        'voltage_sdram_i': ['measure_volts', 'sdram_i'],
        'voltage_sdram_p': ['measure_volts', 'sdram_p'],
        'clock_arm': ['measure_clock', 'arm'],
        'clock_core': ['measure_clock', 'core'],
        'clock_h264': ['measure_clock', 'h264'],
        'clock_isp': ['measure_clock', 'isp'],
        'clock_v3d': ['measure_clock', 'v3d'],
        'clock_uart': ['measure_clock', 'uart'],
        'clock_pwm': ['measure_clock', 'pwm'],
        'clock_emmc': ['measure_clock', 'emmc'],
        'clock_pixel': ['measure_clock', 'pixel'],
        'clock_vec': ['measure_clock', 'vec'],
        'clock_hdmi': ['measure_clock', 'hdmi'],
        'clock_dpi': ['measure_clock', 'dpi'],
        'throttled': ['get_throttled'],
        'mem_arm': ['get_mem', 'arm'],
        'mem_gpu': ['get_mem', 'gpu'],
        'codec_h264': ['codec_enabled', 'H264'],
        'codec_mpg2': ['codec_enabled', 'MPG2'],
        'codec_wvc1': ['codec_enabled', 'WVC1'],
        'codec_mpg4': ['codec_enabled', 'MPG4'],
        'codec_mjpg': ['codec_enabled', 'MJPG'],
        'config_int': ['get_config', 'int'],
        'config_str': ['get_config', 'str'],
    }
    
    for key, cmd_args in commands.items():
        try:
            result = subprocess.run(['vcgencmd'] + cmd_args, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                output = result.stdout.strip()
                diagnostics[key] = output
            else:
                diagnostics[key] = f"Error: {result.stderr.strip()}" if result.stderr.strip() else "N/A"
        except subprocess.TimeoutExpired:
            diagnostics[key] = "Timeout"
        except FileNotFoundError:
            diagnostics[key] = "vcgencmd not found"
        except Exception as e:
            diagnostics[key] = f"Error: {str(e)}"
    
    # Add UPS status information
    try:
        ups_status = get_ups_status()
        diagnostics['ups_voltage'] = ups_status['voltage']
        diagnostics['ups_capacity'] = ups_status['capacity'] 
        diagnostics['ups_battery_status'] = ups_status['battery_status']
        diagnostics['ups_ac_power'] = ups_status['ac_power_connected']
    except Exception as e:
        diagnostics['ups_voltage'] = None
        diagnostics['ups_capacity'] = None
        diagnostics['ups_battery_status'] = f"Error: {str(e)}"
        diagnostics['ups_ac_power'] = None
    
    # Parse throttled status for special highlighting
    throttled_raw = diagnostics.get('throttled', '')
    throttled_info = parse_throttled_status(throttled_raw)
    diagnostics['throttled_parsed'] = throttled_info
    
    return diagnostics

def parse_throttled_status(throttled_output):
    """
    Parse the throttled status from vcgencmd get_throttled output.
    Returns a dict with parsed information about undervoltage and throttling.
    """
    info = {
        'raw': throttled_output,
        'has_issues': False,
        'current_issues': [],
        'past_issues': [],
        'hex_value': None
    }
    
    try:
        # Extract hex value from output like "throttled=0x50000"
        match = re.search(r'throttled=0x([0-9a-fA-F]+)', throttled_output)
        if match:
            hex_value = int(match.group(1), 16)
            info['hex_value'] = hex_value
            
            # Bit meanings for throttled status
            bit_meanings = {
                0: 'Under-voltage detected',
                1: 'Arm frequency capped',
                2: 'Currently throttled',
                3: 'Soft temperature limit active',
                16: 'Under-voltage has occurred',
                17: 'Arm frequency capping has occurred', 
                18: 'Throttling has occurred',
                19: 'Soft temperature limit has occurred'
            }
            
            for bit, meaning in bit_meanings.items():
                if hex_value & (1 << bit):
                    info['has_issues'] = True
                    if bit < 16:
                        info['current_issues'].append(meaning)
                    else:
                        info['past_issues'].append(meaning)
                        
    except Exception as e:
        info['parse_error'] = str(e)
    
    return info

@app.route('/move-to-usb', methods=['POST'])
def move_to_usb():
    data = request.get_json()
    file_path = data.get('file_path')
    
    if not file_path or not os.path.isfile(file_path):
        return jsonify({'error': 'Recording file not found.'}), 400
    
    try:
        # Find USB storage
        usb_path = find_usb_storage()
        if not usb_path:
            return jsonify({'error': 'USB storage not found or not accessible. Please ensure a USB drive is connected and properly mounted.'}), 400
        
        # Copy settings and executables to USB if needed
        copy_result = copy_settings_and_executables_to_usb(usb_path)
        settings_copied = copy_result['settings_copied']
        executables_copied = copy_result['executables_copied']
        
        # Move the file to USB
        result = move_file_to_usb(file_path, usb_path)
        
        if result['success']:
            # Always sync the USB drive after moving a recording for data integrity
            print("Syncing recording file to USB drive...")
            try:
                subprocess.run(['sync'], check=True)
                time.sleep(1)  # Give time for filesystem sync
                print("USB sync completed successfully")
            except Exception as e:
                print(f"Warning: USB sync failed: {e}")
            
            # Create detailed message about what was copied
            message_parts = ['Successfully moved to USB drive']
            if settings_copied:
                message_parts.append('settings updated')
            if executables_copied > 0:
                message_parts.append(f'{executables_copied} executable(s) updated')
            
            message = message_parts[0]
            if len(message_parts) > 1:
                message += ' with ' + ' and '.join(message_parts[1:])
            
            return jsonify({
                'success': True,
                'destination': result['destination'],
                'message': message,
                'files_copied': {
                    'settings': settings_copied,
                    'executables': executables_copied
                }
            })
        else:
            return jsonify({'error': result['error']}), 500
            
    except Exception as e:
        return jsonify({'error': f'Failed to move to USB: {e}'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=False, threaded=True)