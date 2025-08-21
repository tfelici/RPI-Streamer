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
import socket
import platform
from datetime import datetime
from functools import wraps
from pathlib import Path
from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor
from utils import list_audio_inputs, list_video_inputs, find_usb_storage, move_file_to_usb, copy_settings_and_executables_to_usb, DEFAULT_SETTINGS, SETTINGS_FILE, STREAMER_DATA_DIR, is_streaming, is_pid_running, STREAM_PIDFILE, is_gps_tracking, get_gps_tracking_status, load_settings, save_settings

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

def get_temperature():
    # Use vcgencmd like in get_system_diagnostics for consistency
    try:
        # Check if vcgencmd is available first
        subprocess.run(['vcgencmd', 'version'], capture_output=True, text=True, timeout=2)
        
        # Get temperature using vcgencmd
        result = subprocess.run(['vcgencmd', 'measure_temp'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            output = result.stdout.strip()
            # Extract temperature from "temp=48.8'C"
            temp_match = re.search(r'temp=([\d.]+)', output)
            if temp_match:
                return f"{temp_match.group(1)}°C"
            else:
                return output
        else:
            return "N/A"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # vcgencmd not available, fallback to "--" like in diagnostics
        return "--"
    except Exception:
        return "N/A"

def get_fan_rpm():
    try:
        sys_devices_path = Path('/sys/devices/platform/cooling_fan')
        fan_input_files = list(sys_devices_path.rglob('fan1_input'))
        if not fan_input_files:
            return "No fan?"
        with open(fan_input_files[0], 'r') as file:
            rpm = file.read().strip()
        return f"{rpm} RPM"
    except FileNotFoundError:
        return "Fan RPM file not found"
    except PermissionError:
        return "Permission denied accessing the fan RPM file"
    except Exception as e:
        return f"Unexpected error: {e}"

def power_consumption_watts():
    """
    Calculate total power consumption in watts by parsing all PMIC voltage and current readings.
    Returns formatted power string like "2.45 W", or fallback values if not available.
    """
    # First try system power supply files
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
    
    try:
        # Check if vcgencmd is available first
        subprocess.run(['vcgencmd', 'version'], capture_output=True, text=True, timeout=2)
        
        # Get all PMIC ADC readings
        output = subprocess.check_output(['vcgencmd', 'pmic_read_adc'], timeout=5).decode("utf-8")
        lines = output.split('\n')
        amperages = {}
        voltages = {}
        
        for line in lines:
            cleaned_line = line.strip()
            if cleaned_line and '=' in cleaned_line:
                try:
                    parts = cleaned_line.split(' ')
                    label, value = parts[0], parts[-1]
                    val = float(value.split('=')[1][:-1])  # Remove unit (V or A)
                    short_label = label[:-2]  # Remove _V or _A suffix
                    if label.endswith('A'):
                        amperages[short_label] = val
                    elif label.endswith('V'):
                        voltages[short_label] = val
                except (ValueError, IndexError):
                    continue
        
        # Calculate total wattage (V * A = W) for matching voltage/current pairs
        wattage = sum(amperages[key] * voltages[key] for key in amperages if key in voltages)
        if wattage > 0:
            return f"{wattage:.2f} W"
        
        # Fallback: Try individual VDD_CORE readings if comprehensive method failed
        voltage = None
        current = None
        
        # Get VDD_CORE voltage
        result = subprocess.run(['vcgencmd', 'pmic_read_adc', 'VDD_CORE_V'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and 'VDD_CORE_V' in result.stdout:
            volt_match = re.search(r'=([\d.]+)V', result.stdout)
            if volt_match:
                voltage = float(volt_match.group(1))
        
        # Get VDD_CORE current
        result = subprocess.run(['vcgencmd', 'pmic_read_adc', 'VDD_CORE_A'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and 'VDD_CORE_A' in result.stdout:
            current_match = re.search(r'=([\d.]+)A', result.stdout)
            if current_match:
                current = float(current_match.group(1))
        
        # Calculate power if both voltage and current are available
        if voltage is not None and current is not None:
            watts = voltage * current
            return f"{watts:.2f} W"
        elif voltage is not None:
            return f"{voltage:.3f} V (core)"
        
        return "0.00 W"
        
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # vcgencmd not available, fallback to "--" like in diagnostics
        return "--"
    except Exception as e:
        print(f"Error calculating power consumption: {e}")
        return "N/A"

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
    gps_tracking = is_gps_tracking()
    gps_status = get_gps_tracking_status()
    settings = load_settings()

    # Gather recording files info
    recording_files = []
    local_recording_path = os.path.join(STREAMER_DATA_DIR, 'recordings', 'webcam')
    add_files_from_path(recording_files, local_recording_path, "", "Local")
    # Add files from USB storage if available
    usb_mount_point = find_usb_storage()
    if usb_mount_point:
        usb_recording_path = os.path.join(usb_mount_point, 'streamerData', 'recordings', 'webcam')
        add_files_from_path(recording_files, usb_recording_path, "[USB] ", "USB")

    return render_template(
        'index.html', 
        active_tab='home', 
        streaming=streaming, 
        gps_tracking=gps_tracking,
        gps_status=gps_status,
        recording_files=recording_files, 
        app_version=get_app_version(),
        settings=settings
    )

@app.route('/stats')
def stats():
    def event_stream():
        client_timeout = 0
        max_timeout = 30  # Stop after 30 seconds of no activity
        
        while client_timeout < max_timeout:
            try:
                cpu = psutil.cpu_percent(interval=1)
                mem = psutil.virtual_memory().percent
                temp = get_temperature()
                power = power_consumption_watts()
                fan_rpm = get_fan_rpm()
                connection = get_connection_info()
                
                # Format connection info for JSON
                import json
                connection_json = json.dumps(connection).replace('"', '\\"')
                
                data = f"data: {{\"cpu\": {cpu}, \"mem\": {mem}, \"temp\": \"{temp}\", \"power\": \"{power}\", \"fan_rpm\": \"{fan_rpm}\", \"connection\": \"{connection_json}\"}}\n\n"
                yield data
                client_timeout = 0  # Reset timeout on successful yield
                time.sleep(1)
            except GeneratorExit:
                # Client disconnected
                print("Stats SSE client disconnected")
                break
            except Exception as e:
                print(f"Stats SSE error: {e}")
                client_timeout += 1
                time.sleep(1)
        print("Stats SSE stream ended")
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
        if 'use_gstreamer' in data:
            # Accept both boolean and string values from the form
            val = data['use_gstreamer']
            if isinstance(val, bool):
                settings['use_gstreamer'] = val
            elif isinstance(val, str):
                settings['use_gstreamer'] = val.lower() == 'true'
        if 'video_stabilization' in data:
            # Accept both boolean and string values from the form
            val = data['video_stabilization']
            if isinstance(val, bool):
                settings['video_stabilization'] = val
            elif isinstance(val, str):
                settings['video_stabilization'] = val.lower() == 'true'
        save_settings(settings)
        return '', 204
    else:
        settings = load_settings()
        settings['streaming'] = is_streaming()
        return jsonify(settings)

@app.route('/stream-settings')
def stream_settings_page():
    audio_inputs = list_audio_inputs()
    video_inputs = list_video_inputs()
    return render_template(
        'stream_settings.html',
        active_tab='stream_settings',
        settings=load_settings(),
        app_version=get_app_version(),
        audio_inputs=audio_inputs,
        video_inputs=video_inputs
    )

@app.route('/flight-settings')
def flight_settings_page():
    return render_template(
        'flight_settings.html',
        active_tab='flight_settings',
        settings=load_settings(),
        app_version=get_app_version()
    )

@app.route('/flight-settings', methods=['POST'])
def flight_settings_save():
    settings = load_settings()
    
    # Update flight settings
    settings['gps_username'] = request.form.get('gps_username', '').strip()
    settings['aircraft_registration'] = request.form.get('aircraft_registration', '').strip()
    settings['gps_stream_link'] = request.form.get('gps_stream_link') == 'on'
    old_gps_start_mode = settings['gps_start_mode']
    settings['gps_start_mode'] = request.form.get('gps_start_mode', 'manual')
    settings['gps_stop_on_power_loss'] = request.form.get('gps_stop_on_power_loss') == 'on'
    
    # Handle power loss timeout with validation
    try:
        power_loss_minutes = int(request.form.get('gps_stop_power_loss_minutes', DEFAULT_SETTINGS['gps_stop_power_loss_minutes']))
        if 1 <= power_loss_minutes <= 60:
            settings['gps_stop_power_loss_minutes'] = power_loss_minutes
        else:
            settings['gps_stop_power_loss_minutes'] = DEFAULT_SETTINGS['gps_stop_power_loss_minutes']  # Default fallback
    except (ValueError, TypeError):
        settings['gps_stop_power_loss_minutes'] = DEFAULT_SETTINGS['gps_stop_power_loss_minutes']  # Default fallback
    
    # Save settings
    save_settings(settings)
    
    # Manage GPS startup service based on the start mode
    new_gps_start_mode = settings['gps_start_mode']
    if old_gps_start_mode != new_gps_start_mode:
        try:
            if new_gps_start_mode in ['boot', 'motion']:
                # Enable and start the GPS startup service
                subprocess.run(['sudo', 'systemctl', 'enable', 'gps-startup.service'], check=False)
                subprocess.run(['sudo', 'systemctl', 'restart', 'gps-startup.service'], check=False)
                print(f"GPS startup service enabled for mode: {new_gps_start_mode}")
            else:
                # Disable and stop the GPS startup service for manual mode
                subprocess.run(['sudo', 'systemctl', 'stop', 'gps-startup.service'], check=False)
                subprocess.run(['sudo', 'systemctl', 'disable', 'gps-startup.service'], check=False)
                print("GPS startup service disabled for manual mode")
        except Exception as e:
            print(f"Warning: Could not manage GPS startup service: {e}")
    
    return render_template(
        'flight_settings.html',
        active_tab='flight_settings',
        settings=settings,
        app_version=get_app_version(),
        message='Flight settings saved successfully!'
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

def start_streaming():
    """Helper function to start streaming. Returns (success, message, status_code)"""
    # Check if already streaming
    if is_streaming():
        return False, 'Streaming is already active.', 200
    
    settings = load_settings()
    stream_url = settings['stream_url'].strip()
    if not stream_url:
        return False, 'Remote Streaming URL is not set. Please configure it in Settings.', 400
    
    # Start relay-ffmpeg.py asynchronously
    subprocess.Popen(['python', 'relay-ffmpeg.py', 'webcam'])
    # Also start relay-ffmpeg-record.py asynchronously
    subprocess.Popen(['python', 'relay-ffmpeg-record.py', 'webcam'])
    
    return True, 'started', 200

def stop_streaming():
    """Helper function to stop streaming. Returns (success, message, status_code)"""
    if is_streaming():
        # Stop both relay-ffmpeg.py and relay-ffmpeg-record.py processes
        print("Stopping stream...")
        try:
            with open(STREAM_PIDFILE, 'r') as f:
                pid = int(f.read().strip())
            print(f"Stopping stream with PID {pid}")
            if is_pid_running(pid):
                os.kill(pid, 15)  # SIGTERM
            # Also stop the recording process if it exists
            active_pid, _ = get_active_recording_info()
            if active_pid and is_pid_running(active_pid):
                print(f"Stopping recording with PID {active_pid}")
                os.kill(active_pid, 15)  # SIGTERM
            # Loop until both processes are no longer running
            while active_pid and is_pid_running(active_pid) or pid and is_pid_running(pid):
                # Print status
                print(f"Waiting for stream and recording to stop... (stream PID: {pid}={is_pid_running(pid)}, recording PID: {active_pid}={is_pid_running(active_pid)})")
                time.sleep(0.5)  # Give it a moment to terminate
            print("Stream and recording stopped successfully.")
        except Exception as e:
            print(f"Error stopping stream: {e}")
    else:
        print("No active stream to stop.")
    
    return True, 'stopped', 200

def start_flight():
    """Helper function to start GPS tracking. Returns (success, message, status_code)"""
    # Check if already tracking
    if is_gps_tracking():
        return False, 'GPS tracking is already active.', 200
    
    # Get username from settings and validate
    settings = load_settings()
    username = settings['gps_username'].strip()
    if not username:
        return False, 'GPS username is not configured. Please set a username for GPS tracking in the flight settings.', 400
    
    # Start GPS tracker in real mode (hardware will be initialized internally)
    try:
        subprocess.Popen(['python', 'gps_tracker.py', username])
    except Exception as e:
        return False, f'Failed to start GPS tracker process: {e}', 500
    
    # If gps_stream_link is enabled, also start video streaming
    if settings['gps_stream_link']:
        print("Auto-starting video streaming with GPS tracking...")
        success, message, status_code = start_streaming()
        if success:
            print("Video streaming started automatically with GPS tracking.")
        else:
            print(f"Warning: Failed to auto-start video streaming: {message}")
    
    return True, 'started', 200

def stop_flight():
    """Helper function to stop GPS tracking. Returns (success, message, status_code)"""
    settings = load_settings()
    
    if is_gps_tracking():
        print("Stopping GPS tracking...")
        try:
            gps_status = get_gps_tracking_status()
            gps_pid = gps_status['pid']
            if gps_pid:
                print(f"Stopping GPS tracking with PID {gps_pid}")
                if is_pid_running(gps_pid):
                    os.kill(gps_pid, 15)  # SIGTERM
                # Wait for the process to stop
                for _ in range(20):  # Wait up to 2 seconds
                    if not is_pid_running(gps_pid):
                        break
                    time.sleep(0.1)
                print("GPS tracking stopped successfully.")
        except Exception as e:
            print(f"Error stopping GPS tracking: {e}")
            
        # If gps_stream_link is enabled, also stop video streaming
        if settings['gps_stream_link']:
            print("Auto-stopping video streaming with GPS tracking...")
            success, message, status_code = stop_streaming()
            if success:
                print("Video streaming stopped automatically with GPS tracking.")
            else:
                print(f"Warning: Failed to auto-stop video streaming: {message}")
    else:
        print("No active GPS tracking to stop.")
    
    return True, 'stopped', 200

@app.route('/stream-control', methods=['POST'])
def stream_control():
    data = request.get_json()
    action = data.get('action')
    
    if action == 'start':
        success, message, status_code = start_streaming()
        if success:
            return jsonify({'status': message})
        else:
            return jsonify({'error': message}), status_code
    elif action == 'stop':
        success, message, status_code = stop_streaming()
        return jsonify({'status': message})
    else:
        return jsonify({'error': 'Invalid action'}), 400

@app.route('/gps-control', methods=['POST'])
def gps_control():
    data = request.get_json()
    action = data.get('action')
    
    if action == 'start':
        success, message, status_code = start_flight()
        if success:
            return jsonify({'status': message})
        else:
            return jsonify({'error': message}), status_code
    elif action == 'stop':
        success, message, status_code = stop_flight()
        return jsonify({'status': message})
    else:
        return jsonify({'error': 'Invalid action'}), 400

@app.route('/gps-status')
def gps_status():
    """Get current GPS tracking status including hardware details"""
    status = get_gps_tracking_status()
    return jsonify(status)

@app.route('/upload-recording', methods=['POST'])
def upload_recording():
    from werkzeug.utils import secure_filename
    
    settings = load_settings()
    upload_url = settings['upload_url'].strip()
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
        
        # Configure WiFi with lower priority - fallback when Ethernet unavailable
        subprocess.run([
            'sudo', 'nmcli', 'connection', 'modify', ssid,
            'connection.autoconnect', 'yes',
            'connection.autoconnect-priority', '5'
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

def get_wifi_mode_status():
    """Get current WiFi mode and hotspot status"""
    settings = load_settings()
    wifi_mode = settings['wifi_mode']
    
    # Check if hostapd is running (indicates hotspot mode)
    try:
        result = subprocess.run(['sudo', 'systemctl', 'is-active', 'hostapd'], 
                              capture_output=True, text=True)
        hostapd_active = result.stdout.strip() == 'active'
    except:
        hostapd_active = False
    
    # Check current IP configuration
    current_ip = None
    try:
        result = subprocess.run(['ip', 'addr', 'show', 'wlan0'], 
                              capture_output=True, text=True)
        for line in result.stdout.split('\n'):
            if 'inet ' in line and not '127.0.0.1' in line:
                current_ip = line.strip().split()[1].split('/')[0]
                break
    except:
        pass
    
    return {
        'mode': wifi_mode,
        'hostapd_active': hostapd_active,
        'current_ip': current_ip,
        'hotspot_ssid': settings['hotspot_ssid'],
        'hotspot_ip': settings['hotspot_ip']
    }

def configure_wifi_hotspot(ssid, password, channel=6, ip_address="192.168.4.1"):
    """Configure WiFi hotspot using hostapd and dnsmasq"""
    try:
        # Install required packages if not present
        subprocess.run(['sudo', 'apt-get', 'update'], check=False)
        subprocess.run(['sudo', 'apt-get', 'install', '-y', 'hostapd', 'dnsmasq'], check=False)
        
        # Stop services
        subprocess.run(['sudo', 'systemctl', 'stop', 'hostapd'], check=False)
        subprocess.run(['sudo', 'systemctl', 'stop', 'dnsmasq'], check=False)
        subprocess.run(['sudo', 'systemctl', 'stop', 'NetworkManager'], check=False)
        
        # Configure hostapd
        hostapd_conf = f"""interface=wlan0
driver=nl80211
ssid={ssid}
hw_mode=g
channel={channel}
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase={password}
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
"""
        
        with open('/tmp/hostapd.conf', 'w') as f:
            f.write(hostapd_conf)
        subprocess.run(['sudo', 'mv', '/tmp/hostapd.conf', '/etc/hostapd/hostapd.conf'], check=True)
        
        # Configure dnsmasq
        dnsmasq_conf = f"""interface=wlan0
dhcp-range={ip_address.rsplit('.', 1)[0]}.10,{ip_address.rsplit('.', 1)[0]}.50,255.255.255.0,24h
"""
        
        with open('/tmp/dnsmasq.conf', 'w') as f:
            f.write(dnsmasq_conf)
        subprocess.run(['sudo', 'mv', '/tmp/dnsmasq.conf', '/etc/dnsmasq.conf'], check=True)
        
        # Configure static IP for wlan0
        subprocess.run(['sudo', 'ip', 'addr', 'flush', 'dev', 'wlan0'], check=False)
        subprocess.run(['sudo', 'ip', 'addr', 'add', f'{ip_address}/24', 'dev', 'wlan0'], check=True)
        subprocess.run(['sudo', 'ip', 'link', 'set', 'wlan0', 'up'], check=True)
        
        # Enable IP forwarding
        subprocess.run(['sudo', 'sysctl', 'net.ipv4.ip_forward=1'], check=True)
        
        # Configure iptables for NAT (if eth0 is available)
        subprocess.run(['sudo', 'iptables', '-t', 'nat', '-A', 'POSTROUTING', '-o', 'eth0', '-j', 'MASQUERADE'], check=False)
        subprocess.run(['sudo', 'iptables', '-A', 'FORWARD', '-i', 'eth0', '-o', 'wlan0', '-m', 'state', '--state', 'RELATED,ESTABLISHED', '-j', 'ACCEPT'], check=False)
        subprocess.run(['sudo', 'iptables', '-A', 'FORWARD', '-i', 'wlan0', '-o', 'eth0', '-j', 'ACCEPT'], check=False)
        
        # Start services
        subprocess.run(['sudo', 'systemctl', 'start', 'hostapd'], check=True)
        subprocess.run(['sudo', 'systemctl', 'start', 'dnsmasq'], check=True)
        
        # Enable services for boot
        subprocess.run(['sudo', 'systemctl', 'enable', 'hostapd'], check=True)
        subprocess.run(['sudo', 'systemctl', 'enable', 'dnsmasq'], check=True)
        
        return True, "Hotspot configured successfully"
        
    except subprocess.CalledProcessError as e:
        return False, f"Failed to configure hotspot: {e}"
    except Exception as e:
        return False, f"Error configuring hotspot: {e}"

def configure_wifi_client():
    """Switch back to WiFi client mode"""
    try:
        # Stop hotspot services
        subprocess.run(['sudo', 'systemctl', 'stop', 'hostapd'], check=False)
        subprocess.run(['sudo', 'systemctl', 'stop', 'dnsmasq'], check=False)
        subprocess.run(['sudo', 'systemctl', 'disable', 'hostapd'], check=False)
        subprocess.run(['sudo', 'systemctl', 'disable', 'dnsmasq'], check=False)
        
        # Clear iptables rules
        subprocess.run(['sudo', 'iptables', '-t', 'nat', '-F'], check=False)
        subprocess.run(['sudo', 'iptables', '-F'], check=False)
        
        # Reset wlan0 interface
        subprocess.run(['sudo', 'ip', 'addr', 'flush', 'dev', 'wlan0'], check=False)
        subprocess.run(['sudo', 'ip', 'link', 'set', 'wlan0', 'down'], check=False)
        
        # Start NetworkManager
        subprocess.run(['sudo', 'systemctl', 'start', 'NetworkManager'], check=True)
        subprocess.run(['sudo', 'systemctl', 'enable', 'NetworkManager'], check=True)
        
        # Wait a moment for NetworkManager to initialize
        time.sleep(3)
        
        # Try to reconnect to saved WiFi networks
        subprocess.run(['sudo', 'nmcli', 'device', 'set', 'wlan0', 'autoconnect', 'yes'], check=False)
        subprocess.run(['sudo', 'nmcli', 'connection', 'up', '--help'], check=False)  # Wake up nmcli
        
        return True, "Switched to client mode successfully"
        
    except subprocess.CalledProcessError as e:
        return False, f"Failed to switch to client mode: {e}"
    except Exception as e:
        return False, f"Error switching to client mode: {e}"

@app.route('/system-settings-wifi-mode', methods=['POST'])
def system_settings_wifi_mode():
    """Handle WiFi mode switching between client and hotspot"""
    data = request.get_json()
    mode = data.get('mode', 'client')
    
    # Load current settings
    settings = load_settings()
    
    if mode == 'hotspot':
        ssid = data.get('hotspot_ssid', settings['hotspot_ssid'])
        password = data.get('hotspot_password', settings['hotspot_password'])
        channel = int(data.get('hotspot_channel', settings['hotspot_channel']))
        ip_address = data.get('hotspot_ip', settings['hotspot_ip'])
        
        if len(password) < 8:
            return jsonify({'success': False, 'error': 'Hotspot password must be at least 8 characters'})
        
        # Update settings
        settings.update({
            'wifi_mode': 'hotspot',
            'hotspot_ssid': ssid,
            'hotspot_password': password,
            'hotspot_channel': channel,
            'hotspot_ip': ip_address
        })
        save_settings(settings)
        
        # Configure hotspot
        success, message = configure_wifi_hotspot(ssid, password, channel, ip_address)
        
        if success:
            return jsonify({'success': True, 'message': f'Hotspot "{ssid}" created successfully'})
        else:
            return jsonify({'success': False, 'error': message})
            
    elif mode == 'client':
        # Update settings
        settings['wifi_mode'] = 'client'
        save_settings(settings)
        
        # Switch to client mode
        success, message = configure_wifi_client()
        
        if success:
            return jsonify({'success': True, 'message': 'Switched to WiFi client mode'})
        else:
            return jsonify({'success': False, 'error': message})
    
    else:
        return jsonify({'success': False, 'error': 'Invalid WiFi mode'})

@app.route('/system-settings-wifi-status')
def system_settings_wifi_status():
    """Get current WiFi mode status"""
    status = get_wifi_mode_status()
    return jsonify(status)

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

@app.route('/ups-monitor-log')
def ups_monitor_log():
    """Serve the UPS monitor log file for viewing."""
    log_file_path = '/var/log/ups-monitor.log'
    
    try:
        # Check if log file exists
        if not os.path.exists(log_file_path):
            return "UPS monitor log file not found.", 404
        
        # Read the log file
        with open(log_file_path, 'r') as f:
            log_content = f.read()
        
        # Return as plain text with monospace font
        return Response(
            log_content, 
            mimetype='text/plain',
            headers={
                'Content-Type': 'text/plain; charset=utf-8',
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0'
            }
        )
    except Exception as e:
        return f"Error reading UPS monitor log: {str(e)}", 500

@app.route('/diagnostics-sse')
def diagnostics_sse():
    """SSE endpoint for system diagnostics updates."""
    import json
    def event_stream():
        last_diagnostics = None
        client_timeout = 0
        max_timeout = 30  # Stop after 30 seconds of no activity
        
        while client_timeout < max_timeout:
            try:
                diagnostics = get_system_diagnostics()
                
                # Only send if changed (to reduce bandwidth)
                if diagnostics != last_diagnostics:
                    yield f"data: {json.dumps(diagnostics)}\n\n"
                    last_diagnostics = diagnostics
                    client_timeout = 0  # Reset timeout on successful yield
                    
                time.sleep(2)  # Update every 2 seconds
            except GeneratorExit:
                # Client disconnected
                print("Diagnostics SSE client disconnected")
                break
            except Exception as e:
                print(f"Diagnostics SSE error: {e}")
                error_data = {'error': str(e)}
                yield f"data: {json.dumps(error_data)}\n\n"
                client_timeout += 1
                time.sleep(5)  # Wait longer on error
        print("Diagnostics SSE stream ended")
    return Response(event_stream(), mimetype='text/event-stream')

def get_system_diagnostics():
    """
    Get comprehensive system diagnostics using vcgencmd.
    Returns a dictionary with all available diagnostic information.
    """
    diagnostics = {}
    
    # Check if vcgencmd is available first
    try:
        subprocess.run(['vcgencmd', 'version'], capture_output=True, text=True, timeout=2)
        vcgencmd_available = True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        vcgencmd_available = False
    
    # List of vcgencmd commands to run
    commands = {
        'temperature': ['measure_temp'],
        'pmic_vdd_core_v': ['pmic_read_adc', 'VDD_CORE_V'],
        'pmic_vdd_core_a': ['pmic_read_adc', 'VDD_CORE_A'],
        'pmic_ext5v_v': ['pmic_read_adc', 'EXT5V_V'],
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
    
    # If vcgencmd is not available, set all values to "--"
    if not vcgencmd_available:
        for key in commands.keys():
            diagnostics[key] = "--"
    else:
        for key, cmd_args in commands.items():
            try:
                result = subprocess.run(['vcgencmd'] + cmd_args, capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    output = result.stdout.strip()
                    
                    # Parse and clean up specific outputs
                    if key == 'temperature':
                        # Extract temperature from "temp=48.8'C"
                        temp_match = re.search(r'temp=([\d.]+)', output)
                        if temp_match:
                            diagnostics[key] = f"{temp_match.group(1)}°C"
                        else:
                            diagnostics[key] = output
                    elif key.startswith('pmic_'):
                        # Parse PMIC values
                        if 'VDD_CORE_V' in output:
                            # Extract voltage from "VDD_CORE_V volt(15)=0.84104930V"
                            volt_match = re.search(r'=([\d.]+)V', output)
                            if volt_match:
                                diagnostics[key] = f"{float(volt_match.group(1)):.3f}V"
                            else:
                                diagnostics[key] = output
                        elif 'VDD_CORE_A' in output:
                            # Extract current from "VDD_CORE_A current(7)=2.35752000A"
                            current_match = re.search(r'=([\d.]+)A', output)
                            if current_match:
                                diagnostics[key] = f"{float(current_match.group(1)):.3f}A"
                            else:
                                diagnostics[key] = output
                        elif 'EXT5V_V' in output:
                            # Extract voltage from "EXT5V_V volt(24)=5.15096000V"
                            volt_match = re.search(r'=([\d.]+)V', output)
                            if volt_match:
                                diagnostics[key] = f"{float(volt_match.group(1)):.3f}V"
                            else:
                                diagnostics[key] = output
                        else:
                            diagnostics[key] = output
                    else:
                        diagnostics[key] = output
                else:
                    diagnostics[key] = f"Error: {result.stderr.strip()}" if result.stderr.strip() else "N/A"
            except subprocess.TimeoutExpired:
                diagnostics[key] = "Timeout"
            except FileNotFoundError:
                diagnostics[key] = "--"
            except Exception as e:
                diagnostics[key] = f"Error: {str(e)}"
    
    # Add UPS status information
    try:
        from x120x import X120X
        with X120X() as ups:
            ups_status = ups.get_status()
            diagnostics['ups_voltage'] = ups_status['voltage']
            diagnostics['ups_capacity'] = ups_status['capacity'] 
            diagnostics['ups_battery_status'] = ups_status['battery_status']
            diagnostics['ups_ac_power'] = ups_status['ac_power_connected']
    except RuntimeError as e:
        # UPS device not found or libraries not available
        diagnostics['ups_voltage'] = None
        diagnostics['ups_capacity'] = None
        diagnostics['ups_battery_status'] = "UPS Not Available"
        diagnostics['ups_ac_power'] = None
    except Exception as e:
        diagnostics['ups_voltage'] = None
        diagnostics['ups_capacity'] = None
        diagnostics['ups_battery_status'] = f"Error: {str(e)}"
        diagnostics['ups_ac_power'] = None
    
    # Add fan RPM information
    try:
        diagnostics['fan_rpm'] = get_fan_rpm()
    except Exception as e:
        diagnostics['fan_rpm'] = f"Error: {str(e)}"
    
    # Add INA219 power monitoring information
    try:
        from INA219 import INA219
        with INA219(addr=0x41) as ina219:
            diagnostics['ina219_bus_voltage'] = f"{ina219.getBusVoltage_V():.3f} V"
            diagnostics['ina219_shunt_voltage'] = f"{ina219.getShuntVoltage_mV():.3f} mV"
            diagnostics['ina219_current'] = f"{ina219.getCurrent_mA():.1f} mA"
            diagnostics['ina219_power'] = f"{ina219.getPower_W():.3f} W"
            
            # Get power status
            power_status = ina219.getPowerStatus()
            if power_status is True:
                diagnostics['ina219_power_source'] = "Plugged In"
            elif power_status is False:
                diagnostics['ina219_power_source'] = "Unplugged"
            else:
                diagnostics['ina219_power_source'] = "Unknown"
                
            # Calculate PSU voltage (bus + shunt)
            psu_voltage = ina219.getBusVoltage_V() + (ina219.getShuntVoltage_mV() / 1000)
            diagnostics['ina219_psu_voltage'] = f"{psu_voltage:.3f} V"
            
            # Calculate battery percentage (based on 3S: 9V empty, 12.6V full)
            bus_voltage = ina219.getBusVoltage_V()
            battery_percent = (bus_voltage - 9) / 3.6 * 100
            battery_percent = max(0, min(100, battery_percent))
            diagnostics['ina219_battery_percent'] = f"{battery_percent:.1f}%"
        
    except RuntimeError as e:
        # INA219 device not found
        diagnostics['ina219_bus_voltage'] = "INA219 Not Available"
        diagnostics['ina219_shunt_voltage'] = "--"
        diagnostics['ina219_current'] = "--"
        diagnostics['ina219_power'] = "--"
        diagnostics['ina219_power_source'] = "--"
        diagnostics['ina219_psu_voltage'] = "--"
        diagnostics['ina219_battery_percent'] = "--"
    except Exception as e:
        diagnostics['ina219_bus_voltage'] = f"Error: {str(e)}"
        diagnostics['ina219_shunt_voltage'] = "N/A"
        diagnostics['ina219_current'] = "N/A"
        diagnostics['ina219_power'] = "N/A"
        diagnostics['ina219_power_source'] = "N/A"
        diagnostics['ina219_psu_voltage'] = "N/A"
        diagnostics['ina219_battery_percent'] = "N/A"
    
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