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
import random
import fcntl
from datetime import datetime
from pathlib import Path
from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor
from utils import list_audio_inputs, list_video_inputs, find_usb_storage, move_file_to_usb, copy_settings_and_executables_to_usb, DEFAULT_SETTINGS, SETTINGS_FILE, STREAMER_DATA_DIR,HEARTBEAT_FILE, is_streaming, is_pid_running, STREAM_PIDFILE, is_gps_tracking, get_gps_tracking_status, load_settings, save_settings, generate_gps_track_id, get_default_hotspot_ssid, get_hardwareid, get_app_version, get_active_recording_info, add_files_from_path, load_wifi_settings, save_wifi_settings, get_wifi_mode_status

# Use pymediainfo for fast video duration extraction - now imported in utils.py

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

def read_stats_file_with_lock(stats_file):
    """
    Read stats file with shared lock for thread safety.
    
    Uses file locking to prevent race conditions between the heartbeat daemon
    (writer) and the web application (reader). Multiple readers can access
    simultaneously with shared locks, but writers get exclusive access.
    
    Args:
        stats_file (str): Path to the stats file to read
        
    Returns:
        dict: Stats data from file
        
    Raises:
        IOError: If file cannot be read
        json.JSONDecodeError: If file contains invalid JSON
    """
    try:
        with open(stats_file, 'r') as f:
            # Acquire shared (read) lock - allows multiple readers
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                # Lock is automatically released when file is closed
                pass
    except (IOError, json.JSONDecodeError) as e:
        raise e

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
    """
    Simple JSON endpoint for system statistics.
    
    This function reads stats data from HEARTBEAT_FILE
    which is written by the heartbeat daemon every 5 seconds.
    
    This centralized approach ensures:
    - Consistency between web interface and heartbeat data
    - No code duplication for stats collection
    - Web server independence from system metrics collection
    """
    try:
        # Read stats from file written by heartbeat daemon with read lock
        if os.path.exists(HEARTBEAT_FILE):
            try:
                stats_data = read_stats_file_with_lock(HEARTBEAT_FILE)

                # Ensure we have a valid timestamp (not too old)
                current_time = time.time()
                file_age = current_time - stats_data.get('timestamp', 0)
                
                if file_age < 30:  # Use data if it's less than 30 seconds old
                    return jsonify(stats_data)
                else:
                    # File is too old, send error message
                    return jsonify({
                        'error': 'Stats data too old',
                        'file_age': file_age,
                        'timestamp': current_time
                    }), 503  # Service Unavailable
            except (json.JSONDecodeError, IOError) as e:
                # File exists but can't be read/parsed
                return jsonify({
                    'error': f'Stats file error: {e}',
                    'timestamp': time.time()
                }), 500  # Internal Server Error
        else:
            # Stats file doesn't exist (heartbeat daemon not running?)
            return jsonify({
                'error': 'Stats file not found - heartbeat daemon may not be running',
                'timestamp': time.time()
            }), 503  # Service Unavailable
    except Exception as e:
        return jsonify({
            'error': f'Unexpected error: {e}',
            'timestamp': time.time()
        }), 500

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
    settings['username'] = request.form.get('username', '').strip()
    settings['vehicle'] = request.form.get('vehicle', '').strip()
    settings['domain'] = request.form.get('domain', '').strip()
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
    
    # Restart GPS startup service if the start mode changed
    # Service is always enabled but will check settings to determine behavior
    new_gps_start_mode = settings['gps_start_mode']
    if old_gps_start_mode != new_gps_start_mode:
        try:
            # Always restart the service to pick up new settings
            subprocess.run(['sudo', 'systemctl', 'restart', 'gps-startup.service'], check=False)
            print(f"GPS startup service restarted for mode: {new_gps_start_mode}")
        except Exception as e:
            print(f"Warning: Could not restart GPS startup service: {e}")
    
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

def get_gyropedia_flights(gyropedia_id, vehicle=None, domain="gyropilots.org"):
    """
    Get list of flights from Gyropedia similar to creategyropediaflightlist JavaScript function.
    
    Args:
        gyropedia_id: The user's Gyropedia API key
        vehicle: Vehicle registration number to match flights (optional)
        domain: Domain to use for API calls (gyropilots.org or gapilots.org)
    
    Returns:
        tuple: (success, flight_id, error_message)
    """
    try:
        # Make GET request to get flight list
        response = requests.get(
            f'https://{domain}/ajaxservices.php',
            params={
                'command': 'getgyropediaflights',
                'key': gyropedia_id,
                'rand': str(random.random())  # Add random parameter like in JavaScript
            },
            timeout=10
        )
        
        if response.status_code == 200:
            try:
                parsed_data = response.json()
                
                if 'error' in parsed_data and parsed_data['error']:
                    return False, None, f"Gyropedia API error: {parsed_data.get('errormsg', 'Unknown error')}"
                
                # Look for flights, prefer planned flights (status 'P')
                if 'flight' in parsed_data and parsed_data['flight']:
                    planned_flights = [f for f in parsed_data['flight'] if f.get('status') == 'P']
                    
                    if planned_flights:
                        # If we have a vehicle, look for matching flights first
                        if vehicle and vehicle.strip():
                            matching_flights = [f for f in planned_flights if f.get('reg', '').strip().upper() == vehicle.strip().upper()]
                            
                            if matching_flights:
                                # Use first flight that matches the vehicle registration
                                first_flight = matching_flights[0]
                                flight_id = first_flight.get('flight_id')
                                print(f"Found planned Gyropedia flight for vehicle {vehicle}: {flight_id}")
                                return True, flight_id, None
                            else:
                                print(f"No planned flights found for vehicle registration {vehicle}, using first available planned flight")
                        
                        # Fallback: Use first planned flight (regardless of registration)
                        first_flight = planned_flights[0]
                        flight_id = first_flight.get('flight_id')
                        flight_reg = first_flight.get('reg', 'Unknown')
                        print(f"Found planned Gyropedia flight: {flight_id} (aircraft: {flight_reg})")
                        return True, flight_id, None
                    else:
                        # No planned flights available
                        return False, None, "No planned flights found in Gyropedia"
                else:
                    return False, None, "No flights found in Gyropedia response"
                    
            except json.JSONDecodeError as e:
                return False, None, f"Failed to parse Gyropedia flight list response: {e}"
        else:
            return False, None, f"Gyropedia flight list request failed with HTTP {response.status_code}"
            
    except requests.RequestException as e:
        return False, None, f"Network error getting Gyropedia flights: {e}"
    except Exception as e:
        return False, None, f"Unexpected error getting Gyropedia flights: {e}"

def update_gyropedia_flight(gyropedia_id, state, settings, track_id=None, vehicle=None, flight_id=None):
    """
    Update Gyropedia flight status similar to updategyropediaflight JavaScript function.
    
    Args:
        state: 'start' or 'stop'
        settings: Application settings dictionary
        track_id: GPS tracking ID if available
        vehicle: Vehicle registration number to match flights (optional)
        flight_id: Specific flight_id to update (if None, will get first available)
    
    Returns:
        tuple: (success, flight_id_used) - success boolean and the flight_id that was used
    """
    try:
        # Check if Gyropedia integration is configured
        username = settings.get('username', '').strip()
        domain = settings.get('domain', '').strip()
        
        if not gyropedia_id or not username:
            print("Gyropedia integration not configured (missing gyropedia_id or username), skipping flight update")
            return True, None  # Not an error, just not configured
        
        # If no flight_id provided, get the first available flight from Gyropedia
        if not flight_id:
            print(f"Getting first available flight from {domain}...")
            success, flight_id, error = get_gyropedia_flights(gyropedia_id, vehicle, domain)
            
            if not success or not flight_id:
                print(f"Could not get Gyropedia flight list: {error}")
                return True, None  # Don't fail the GPS tracking just because we can't get flight list
        
        print(f"Using Gyropedia flight: {flight_id}")
        
        # Determine flight status based on state
        if not state:
            status = ''
        elif state == 'start':
            status = 'F'  # Flying/Active
        else:  # stop
            status = 'L'  # Landed
        
        # Get current time for start/end times
        current_time = datetime.now().strftime('%H:%M')
        
        # Prepare source_id similar to JavaScript version
        source_id = f"{username}:{track_id}" if track_id else ''
        
        # Prepare data for POST request
        post_data = {
            'command': 'updategyropediaflight',
            'key': gyropedia_id,
            'flight_id': flight_id,
            'source_id': source_id,
            'starttime': current_time if status == 'F' else '',
            'endtime': current_time if status == 'L' else '',
            'status': status
        }
        
        # Make POST request to ajaxservices.php
        response = requests.post(
            f'https://{domain}/ajaxservices.php',
            data=post_data,
            timeout=10
        )
        
        if response.status_code == 200:
            try:
                parsed_data = response.json()
                if 'error' in parsed_data and parsed_data['error']:
                    print(f"Gyropedia flight update error: {parsed_data['error']}")
                    return False, flight_id
                
                print(f"Successfully updated Gyropedia flight {flight_id} to status '{status}'")
                return True, flight_id
                
            except json.JSONDecodeError as e:
                print(f"Failed to parse Gyropedia response: {e}")
                return False, flight_id
        else:
            print(f"Gyropedia flight update failed with HTTP {response.status_code}")
            return False, flight_id
            
    except requests.RequestException as e:
        print(f"Network error updating Gyropedia flight: {e}")
        return False, flight_id
    except Exception as e:
        print(f"Unexpected error updating Gyropedia flight: {e}")
        return False, flight_id

def save_gyropedia_flight_id(flight_id):
    """Save the current gyropedia flight_id to a file for persistence"""
    try:
        flight_id_file = os.path.join(STREAMER_DATA_DIR, 'current_gyropedia_flight_id.txt')
        with open(flight_id_file, 'w') as f:
            f.write(str(flight_id))
        print(f"Saved gyropedia flight_id: {flight_id}")
    except Exception as e:
        print(f"Error saving gyropedia flight_id: {e}")

def load_gyropedia_flight_id():
    """Load the current gyropedia flight_id from file"""
    try:
        flight_id_file = os.path.join(STREAMER_DATA_DIR, 'current_gyropedia_flight_id.txt')
        if os.path.exists(flight_id_file):
            with open(flight_id_file, 'r') as f:
                flight_id = f.read().strip()
                if flight_id:
                    print(f"Loaded gyropedia flight_id: {flight_id}")
                    return flight_id
    except Exception as e:
        print(f"Error loading gyropedia flight_id: {e}")
    return None

def clear_gyropedia_flight_id():
    """Clear the stored gyropedia flight_id"""
    try:
        flight_id_file = os.path.join(STREAMER_DATA_DIR, 'current_gyropedia_flight_id.txt')
        if os.path.exists(flight_id_file):
            os.remove(flight_id_file)
            print("Cleared stored gyropedia flight_id")
    except Exception as e:
        print(f"Error clearing gyropedia flight_id: {e}")

def start_flight():
    """
    Helper function to start GPS tracking. Returns (success, message, status_code)
    
    This function generates a track_id upfront and passes it to the GPS tracker process,
    eliminating the need to wait for track_id generation and complex polling logic.
    This approach is more reliable and provides immediate access to the track_id.
    """
    # Check if already tracking
    if is_gps_tracking():
        return False, 'GPS tracking is already active.', 200
    
    # Get username and domain from settings and validate
    settings = load_settings()
    username = settings['username'].strip()
    domain = settings.get('domain', '').strip()
    vehicle = settings.get('vehicle', '').strip()
    
    if not username:
        return False, 'Flight server username is not configured. Please set a username for GPS tracking in the flight settings.', 400
    
    if not domain:
        return False, 'Flight server domain is not configured. Please set a domain for GPS tracking in the flight settings.', 400
    
    # Sync flight parameters from hardware database to flight server.
    # Sets the selectedcamera and aicraft_reg
    # cannot use setuserfield as this is protected by login
    try:
        hardwareid = get_hardwareid()
        response = requests.post(
            f'https://{domain}/ajaxservices.php',
            data={
                'command': 'init_streamer_flightpars',
                'hardwareid': hardwareid
            },
            timeout=10
        )
        if response.status_code == 200:
            resp_json = response.json()
            if 'gyropedia_id' in resp_json:
                settings['gyropedia_id'] = resp_json['gyropedia_id']
                print(f"Updated gyropedia_id: {resp_json['gyropedia_id']}")
                # Save updated settings
                save_settings(settings)
            print(f"Successfully initialized flight parameters")
        else:
            return False, f'Failed to initialize flight parameters - HTTP {response.status_code}', 400
    except requests.RequestException as e:
        return False, f'Network error initializing flight parameters: {e}', 400
    except Exception as e:
        return False, f'Unexpected error initializing flight parameters: {e}', 400

    # Generate track_id upfront using centralized function
    track_id = generate_gps_track_id()
    print(f"Generated track ID: {track_id}")
    
    # Start GPS tracker with the pre-generated track_id
    try:
        subprocess.Popen(['python', 'gps_tracker.py', username, '--domain', domain, '--track_id', track_id])
        print(f"Started GPS tracker process with track ID: {track_id}")
    except Exception as e:
        return False, f'Failed to start GPS tracker process: {e}', 500
        
    gyropedia_id = settings.get('gyropedia_id', '').strip()
    if (gyropedia_id):
        # Update Gyropedia flight status to "Flying" (F) and store the flight_id
        vehicle = settings.get('vehicle', '').strip()
        success, flight_id = update_gyropedia_flight(gyropedia_id, 'start', settings, track_id, vehicle)
        # Store the flight_id for use when stopping the flight
        if success and flight_id:
            save_gyropedia_flight_id(flight_id)
        else:
            print("Warning: Could not get flight_id from Gyropedia, flight ending may not work properly")
    
    # If gps_stream_link is enabled, also start video streaming
    if settings['gps_stream_link']:
        print("Auto-starting video streaming with GPS tracking...")
        success, message, status_code = start_streaming()
        if success:
            print("Video streaming started automatically with GPS tracking.")
        else:
            return False, f'Failed to auto-start video streaming: {message}', status_code
    
    return True, 'started', 200

def stop_flight():
    """Helper function to stop GPS tracking. Returns (success, message, status_code)"""
    settings = load_settings()
    
    if is_gps_tracking():
        print("Stopping GPS tracking...")
        try:
            gps_status = get_gps_tracking_status()
            gps_pid = gps_status['pid']
            track_id = gps_status.get('track_id')
            
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
            
            # Update Gyropedia flight status to "Landed" (L) using the stored flight_id
            gyropedia_id = settings.get('gyropedia_id', '').strip()
            if (gyropedia_id):
                vehicle = settings.get('vehicle', '').strip()
                stored_flight_id = load_gyropedia_flight_id()
                update_gyropedia_flight(gyropedia_id, 'stop', settings, track_id, vehicle, stored_flight_id)
                # Clear the stored flight_id since the flight has ended
                clear_gyropedia_flight_id()
            
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
    
    gyropedia_id = settings.get('gyropedia_id', '').strip()
    if (gyropedia_id):
        # Update Gyropedia flight status even if GPS tracking wasn't active
        # (in case the tracking stopped unexpectedly but we still want to mark flight as landed)
        vehicle = settings.get('vehicle', '').strip()
        stored_flight_id = load_gyropedia_flight_id()
        update_gyropedia_flight(gyropedia_id, 'stop', settings, None, vehicle, stored_flight_id)
        # Always clear the stored flight_id when stopping flight
        clear_gyropedia_flight_id()
    
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

@app.route('/gps-tracks')
def gps_tracks():
    """Get list of GPS tracks stored on disk"""
    try:
        from utils import get_storage_path
        
        # Get tracks directory path
        tracks_path, usb_mount = get_storage_path('tracks')
        tracks_dir = tracks_path
        
        tracks = []
        
        if os.path.exists(tracks_dir):
            # Look for .tsv files (tab-separated GPS track files)
            for filename in os.listdir(tracks_dir):
                if filename.endswith('.tsv'):
                    file_path = os.path.join(tracks_dir, filename)
                    try:
                        # Get file stats
                        stat = os.stat(file_path)
                        file_size = stat.st_size
                        modified_time = datetime.fromtimestamp(stat.st_mtime)
                        
                        # Count lines to estimate track points
                        with open(file_path, 'r') as f:
                            line_count = sum(1 for line in f) - 1  # Subtract header line
                        
                        # Extract track info from filename
                        # Expected format: YYYYMMDD_HHMMSS_username_vehicle.tsv
                        name_parts = filename[:-4].split('_')  # Remove .tsv
                        
                        track_info = {
                            'filename': filename,
                            'file_path': file_path,
                            'size': file_size,
                            'size_mb': round(file_size / (1024 * 1024), 2),
                            'modified': modified_time.isoformat(),
                            'modified_display': modified_time.strftime('%Y-%m-%d %H:%M:%S'),
                            'points': line_count,
                            'duration_estimate': f"~{line_count} points"
                        }
                        
                        # Try to parse track metadata from filename
                        if len(name_parts) >= 4:
                            try:
                                date_str = name_parts[0]
                                time_str = name_parts[1]
                                username = name_parts[2]
                                vehicle = name_parts[3]
                                
                                # Parse date and time
                                track_date = datetime.strptime(f"{date_str}_{time_str}", '%Y%m%d_%H%M%S')
                                
                                track_info.update({
                                    'date': track_date.strftime('%Y-%m-%d'),
                                    'time': track_date.strftime('%H:%M:%S'),
                                    'username': username,
                                    'vehicle': vehicle,
                                    'display_name': f"{track_date.strftime('%Y-%m-%d %H:%M')} - {username}/{vehicle}"
                                })
                            except (ValueError, IndexError):
                                track_info['display_name'] = filename[:-4]  # Fallback to filename
                        else:
                            track_info['display_name'] = filename[:-4]  # Fallback to filename
                            
                        tracks.append(track_info)
                        
                    except Exception as e:
                        print(f"Error processing track file {filename}: {e}")
                        continue
        
        # Sort by modified time (newest first)
        tracks.sort(key=lambda x: x['modified'], reverse=True)
        
        return jsonify({
            'tracks': tracks,
            'total_tracks': len(tracks),
            'tracks_dir': tracks_dir
        })
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'tracks': [],
            'total_tracks': 0
        }), 500

@app.route('/download-track/<filename>')
def download_track(filename):
    """Download a GPS track file"""
    try:
        from utils import get_storage_path
        from flask import send_file
        
        # Security: Only allow .tsv files and sanitize filename
        if not filename.endswith('.tsv') or '..' in filename or '/' in filename:
            return jsonify({'error': 'Invalid filename'}), 400
        
        # Get tracks directory path
        tracks_path, usb_mount = get_storage_path('tracks')
        file_path = os.path.join(tracks_path, filename)
        
        if not os.path.exists(file_path):
            return jsonify({'error': 'Track file not found'}), 404
        
        return send_file(file_path, as_attachment=True, download_name=filename)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/delete-track', methods=['POST'])
def delete_track():
    """Delete a GPS track file"""
    try:
        from utils import get_storage_path
        
        data = request.get_json()
        filename = data.get('filename')
        
        if not filename:
            return jsonify({'success': False, 'error': 'Filename is required'}), 400
        
        # Security: Only allow .tsv files and sanitize filename
        if not filename.endswith('.tsv') or '..' in filename or '/' in filename:
            return jsonify({'success': False, 'error': 'Invalid filename'}), 400
        
        # Get tracks directory path
        tracks_path, usb_mount = get_storage_path('tracks')
        file_path = os.path.join(tracks_path, filename)
        
        if not os.path.exists(file_path):
            return jsonify({'success': False, 'error': 'Track file not found'}), 404
        
        # Delete the file
        os.remove(file_path)
        
        return jsonify({
            'success': True,
            'message': f'Track {filename} deleted successfully'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/hardware-id')
def hardwareid():
    """Get the hardware ID for this device"""
    try:
        hw_id = get_hardwareid()
        return jsonify({
            'hardwareid': hw_id,
            'status': 'success'
        })
    except Exception as e:
        return jsonify({
            'error': str(e),
            'status': 'error'
        }), 500

@app.route('/upload-recording', methods=['POST'])
def upload_recording():
    from werkzeug.utils import secure_filename
    
    settings = load_settings()
    upload_url = settings['upload_url'].strip()
    if not upload_url:
        return jsonify({'error': 'Upload URL is not set. Please configure it in Settings.'}), 400
    
    # Ensure command=replacerecordings is present
    if 'command=replacerecordings' not in upload_url:
        return jsonify({'error': 'Upload URL is incorrect. Please configure it in Settings.'}), 400

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

def get_auth_and_wifi():
    auth = {}
    auth_path = os.path.join(STREAMER_DATA_DIR, 'auth.json')
    
    if os.path.exists(auth_path):
        try:
            with open(auth_path) as f:
                auth = json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Could not parse auth.json: {e}")
            auth = {}
    
    # Load WiFi settings from separate file
    wifi = load_wifi_settings()
    
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
    
    # Load current WiFi settings and update client credentials
    wifi_settings = load_wifi_settings()
    wifi_settings['manual_ssid'] = ssid
    wifi_settings['manual_password'] = password
    save_wifi_settings(wifi_settings)
    
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
                'connection.autoconnect', 'yes',
                'connection.autoconnect-priority', '5'  # Lower than cellular (10) and ethernet (100)
            ]
            subprocess.run(create_cmd, check=True)
              # Activate the connection
            subprocess.run(['sudo', 'nmcli', 'connection', 'up', ssid], check=True)
        
        # Ensure NetworkManager is enabled for auto-start on boot
        subprocess.run(['sudo', 'systemctl', 'enable', 'NetworkManager'], check=False)
        
        # Note: Ethernet priority configuration is handled by install_rpi_streamer.sh
        # which sets ethernet priority to 100 (highest), so we don't modify it here
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to configure WiFi with NetworkManager: {e}'})
    return jsonify({'success': True})

def configure_wifi_hotspot(ssid, password, channel=6, ip_address="192.168.4.1"):
    """Configure WiFi hotspot using NetworkManager"""
    try:
        print("🔧 Configuring WiFi hotspot via NetworkManager...")
        
        # Ensure NetworkManager is running
        try:
            subprocess.run(['sudo', 'systemctl', 'start', 'NetworkManager'], check=True, timeout=10)
            subprocess.run(['sudo', 'systemctl', 'enable', 'NetworkManager'], check=True, timeout=10)
            print("✅ NetworkManager service ready")
        except Exception as e:
            return False, f"Failed to start NetworkManager: {e}"
        
        # Ensure WiFi radio is unblocked and enabled
        print("📡 Preparing WiFi interface...")
        try:
            subprocess.run(['sudo', 'rfkill', 'unblock', 'wifi'], check=False, timeout=5)
            subprocess.run(['sudo', 'nmcli', 'radio', 'wifi', 'on'], check=False, timeout=5)
            print("✅ WiFi radio enabled")
        except Exception as e:
            print(f"Warning: WiFi radio preparation: {e}")
        
        # Clean up existing hotspot connections (all AP mode connections)
        print("🧹 Cleaning up existing hotspot connections...")
        try:
            result = subprocess.run(['sudo', 'nmcli', '--mode', 'tabular', '--terse', '--fields', 'NAME,TYPE', 
                                   'connection', 'show'], capture_output=True, text=True, check=False, timeout=10)
            
            if result.returncode == 0:
                connections_to_delete = []
                for line in result.stdout.strip().split('\n'):
                    if line and ':' in line:
                        name, conn_type = line.split(':', 1)
                        # Look for WiFi connections that might be hotspots
                        if conn_type == 'wifi' or conn_type == '802-11-wireless':
                            # Check if this connection is configured as an AP
                            detail_result = subprocess.run(['sudo', 'nmcli', 'connection', 'show', name], 
                                                         capture_output=True, text=True, check=False, timeout=5)
                            if detail_result.returncode == 0 and 'wifi.mode:' in detail_result.stdout:
                                if 'ap' in detail_result.stdout.lower():
                                    connections_to_delete.append(name)
                
                # Delete all existing hotspot connections
                for conn_name in connections_to_delete:
                    try:
                        subprocess.run(['sudo', 'nmcli', 'connection', 'delete', conn_name], 
                                     check=True, timeout=10)
                        print(f"🗑️ Removed existing hotspot connection: {conn_name}")
                    except Exception as e:
                        print(f"Warning: Failed to remove connection {conn_name}: {e}")
                        
        except Exception as e:
            print(f"Warning: Error cleaning up existing connections: {e}")
        
        # Also specifically try to delete any connection with the target SSID name
        try:
            result = subprocess.run(['sudo', 'nmcli', 'connection', 'show', 'id', ssid], 
                                  capture_output=True, text=True, check=False, timeout=10)
            if result.returncode == 0:
                print(f"🗑️ Removing any existing connection with name: {ssid}")
                subprocess.run(['sudo', 'nmcli', 'connection', 'delete', ssid], check=True, timeout=10)
        except Exception as e:
            print(f"Note: No existing connection found with name {ssid}: {e}")
        
        # Disconnect wlan0 from any current connections
        try:
            subprocess.run(['sudo', 'nmcli', 'device', 'disconnect', 'wlan0'], check=False, timeout=10)
            print("✅ Disconnected wlan0 from existing connections")
        except Exception as e:
            print(f"Warning: Error disconnecting wlan0: {e}")
        
        # Calculate gateway and DHCP range from IP address
        ip_parts = ip_address.split('.')
        network_base = '.'.join(ip_parts[:3])
        gateway = ip_address
        dhcp_start = f"{network_base}.10"
        dhcp_end = f"{network_base}.50"
        
        print(f"📊 Hotspot configuration:")
        print(f"   SSID: {ssid}")
        print(f"   Channel: {channel}")
        print(f"   IP/Gateway: {gateway}")
        print(f"   DHCP Range: {dhcp_start} - {dhcp_end}")
        
        # Create hotspot connection using NetworkManager
        print("🏗️ Creating NetworkManager hotspot connection...")
        try:
            # Determine WiFi band based on channel
            if channel <= 14:
                wifi_band = "bg"  # 2.4GHz band (channels 1-14)
            else:
                wifi_band = "a"   # 5GHz band (channels 36+)
            
            # Create the hotspot connection
            create_cmd = [
                'sudo', 'nmcli', 'connection', 'add',
                'type', 'wifi',
                'ifname', 'wlan0',
                'con-name', ssid,
                'connection.autoconnect', 'yes',
                'connection.autoconnect-priority', '10',#need to set this in order to autoconnect on reboot
                'wifi.mode', 'ap',
                'wifi.ssid', ssid,
                'wifi.band', wifi_band,
                'wifi.channel', str(channel),
                'wifi-sec.key-mgmt', 'wpa-psk',
                'wifi-sec.psk', password,
                'ipv4.method', 'shared',
                'ipv4.address', f"{gateway}/24"
            ]
            
            result = subprocess.run(create_cmd, capture_output=True, text=True, check=True, timeout=30)
            print("✅ Hotspot connection created successfully")
            
        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to create hotspot connection: {e}"
            if e.stderr:
                error_msg += f"\nDetails: {e.stderr}"
            print(error_msg)
            return False, error_msg
        except Exception as e:
            return False, f"Error creating hotspot connection: {e}"
        
        # Activate the hotspot connection
        print("🚀 Activating hotspot connection...")
        try:
            activate_cmd = ['sudo', 'nmcli', 'connection', 'up', ssid]
            result = subprocess.run(activate_cmd, capture_output=True, text=True, check=True, timeout=30)
            print("✅ Hotspot activated successfully")
            
            # Wait for the connection to be fully established
            time.sleep(3)
            
        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to activate hotspot: {e}"
            if e.stderr:
                error_msg += f"\nDetails: {e.stderr}"
            print(error_msg)
            
            # Try to clean up the failed connection
            try:
                subprocess.run(['sudo', 'nmcli', 'connection', 'delete', ssid], check=False, timeout=10)
            except:
                pass
            
            return False, error_msg
        except Exception as e:
            return False, f"Error activating hotspot: {e}"
        
        # Verify the hotspot is working
        print("🔍 Verifying hotspot status...")
        try:
            # Check if wlan0 is in AP mode
            result = subprocess.run(['sudo', 'nmcli', 'device', 'status'], 
                                  capture_output=True, text=True, check=False, timeout=10)
            if 'wlan0' in result.stdout and 'connected' in result.stdout:
                print("✅ wlan0 is connected in AP mode")
            else:
                print("⚠️ wlan0 status unclear, but continuing...")
            
            # Check the connection details
            result = subprocess.run(['sudo', 'nmcli', 'connection', 'show', ssid], 
                                  capture_output=True, text=True, check=False, timeout=10)
            if result.returncode == 0:
                print("✅ Hotspot connection details verified")
            
        except Exception as e:
            print(f"Warning: Verification failed: {e}")
            # Don't fail the whole operation for verification issues
        
        print(f"🎉 WiFi hotspot '{ssid}' created successfully!")
        print(f"📶 Connect to SSID: {ssid}")
        print(f"🌐 Gateway IP: {gateway}")
        
        return True, f"Hotspot '{ssid}' configured successfully via NetworkManager"
        
    except Exception as e:
        return False, f"Error configuring hotspot: {e}"

def configure_wifi_client():
    """Switch back to WiFi client mode using NetworkManager"""
    try:
        print("🔄 Switching to WiFi client mode via NetworkManager...")
        
        # Ensure NetworkManager is running
        try:
            subprocess.run(['sudo', 'systemctl', 'start', 'NetworkManager'], check=True, timeout=10)
            subprocess.run(['sudo', 'systemctl', 'enable', 'NetworkManager'], check=True, timeout=10)
            print("✅ NetworkManager service ready")
        except Exception as e:
            return False, f"Failed to start NetworkManager: {e}"
        
        # Get list of hotspot connections to remove
        print("🧹 Cleaning up existing hotspot connections...")
        try:
            result = subprocess.run(['sudo', 'nmcli', '--mode', 'tabular', '--terse', '--fields', 'NAME,TYPE', 
                                   'connection', 'show'], capture_output=True, text=True, check=False, timeout=10)
            
            if result.returncode == 0:
                connections_to_delete = []
                for line in result.stdout.strip().split('\n'):
                    if line and ':' in line:
                        name, conn_type = line.split(':', 1)
                        # Look for WiFi connections that might be hotspots
                        if conn_type == 'wifi' or conn_type == '802-11-wireless':
                            # Check if this connection is configured as an AP
                            detail_result = subprocess.run(['sudo', 'nmcli', 'connection', 'show', name], 
                                                         capture_output=True, text=True, check=False, timeout=5)
                            if detail_result.returncode == 0 and 'wifi.mode:' in detail_result.stdout:
                                if 'ap' in detail_result.stdout.lower():
                                    connections_to_delete.append(name)
                
                # Delete hotspot connections
                for conn_name in connections_to_delete:
                    try:
                        subprocess.run(['sudo', 'nmcli', 'connection', 'delete', conn_name], 
                                     check=True, timeout=10)
                        print(f"✅ Removed hotspot connection: {conn_name}")
                    except Exception as e:
                        print(f"Warning: Failed to remove connection {conn_name}: {e}")
                        
        except Exception as e:
            print(f"Warning: Error cleaning up connections: {e}")
        
        # Disconnect wlan0 from any current connections
        try:
            subprocess.run(['sudo', 'nmcli', 'device', 'disconnect', 'wlan0'], check=False, timeout=10)
            print("✅ Disconnected wlan0 from hotspot mode")
        except Exception as e:
            print(f"Warning: Error disconnecting wlan0: {e}")
        
        # Enable WiFi and set wlan0 to managed mode
        print("📡 Configuring WiFi for client mode...")
        try:
            # Ensure WiFi radio is enabled
            subprocess.run(['sudo', 'rfkill', 'unblock', 'wifi'], check=False, timeout=5)
            subprocess.run(['sudo', 'nmcli', 'radio', 'wifi', 'on'], check=False, timeout=5)
            
            # Set wlan0 to be managed by NetworkManager with autoconnect
            subprocess.run(['sudo', 'nmcli', 'device', 'set', 'wlan0', 'autoconnect', 'yes'], 
                         check=False, timeout=10)
            subprocess.run(['sudo', 'nmcli', 'device', 'set', 'wlan0', 'managed', 'yes'], 
                         check=False, timeout=10)
            
            print("✅ WiFi interface configured for client mode")
        except Exception as e:
            print(f"Warning: WiFi configuration: {e}")
        
        # Wait for NetworkManager to initialize the interface
        print("⏳ Waiting for NetworkManager to initialize...")
        time.sleep(3)
        
        # Try to reconnect to saved WiFi networks
        print("🔗 Attempting to reconnect to saved networks...")
        try:
            # Get list of saved WiFi connections
            result = subprocess.run(['sudo', 'nmcli', '--mode', 'tabular', '--terse', '--fields', 'NAME,TYPE', 
                                   'connection', 'show'], capture_output=True, text=True, check=False, timeout=10)
            
            wifi_connections = []
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if line and ':' in line:
                        name, conn_type = line.split(':', 1)
                        if conn_type == 'wifi' or conn_type == '802-11-wireless':
                            # Verify this is not a hotspot connection
                            detail_result = subprocess.run(['sudo', 'nmcli', 'connection', 'show', name], 
                                                         capture_output=True, text=True, check=False, timeout=5)
                            if detail_result.returncode == 0:
                                # Check if this is a client connection (not AP mode)
                                if 'wifi.mode:' not in detail_result.stdout or 'infrastructure' in detail_result.stdout.lower():
                                    wifi_connections.append(name)
            
            # Try to connect to saved networks
            connected = False
            for conn_name in wifi_connections:
                try:
                    print(f"Trying to connect to: {conn_name}")
                    result = subprocess.run(['sudo', 'nmcli', 'connection', 'up', conn_name], 
                                          capture_output=True, text=True, check=False, timeout=20)
                    if result.returncode == 0:
                        print(f"✅ Connected to: {conn_name}")
                        connected = True
                        break
                    else:
                        print(f"Failed to connect to {conn_name}: {result.stderr}")
                except Exception as e:
                    print(f"Error connecting to {conn_name}: {e}")
                    continue
            
            if not connected and wifi_connections:
                print("⚠️ Could not connect to any saved networks")
                print("Available networks can be configured via the WiFi settings interface")
            elif not wifi_connections:
                print("ℹ️ No saved WiFi networks found")
                print("Configure WiFi networks via the WiFi settings interface")
            
        except Exception as e:
            print(f"Warning: Error reconnecting to networks: {e}")
        
        # Verify the switch was successful
        print("🔍 Verifying client mode status...")
        try:
            result = subprocess.run(['sudo', 'nmcli', 'device', 'status'], 
                                  capture_output=True, text=True, check=False, timeout=10)
            if 'wlan0' in result.stdout:
                print("✅ wlan0 status verified")
                print(f"Interface status: {result.stdout}")
            
        except Exception as e:
            print(f"Warning: Status verification failed: {e}")
        
        print("🎉 Successfully switched to WiFi client mode!")
        return True, "Switched to WiFi client mode successfully via NetworkManager"
        
    except Exception as e:
        return False, f"Error switching to client mode: {e}"

@app.route('/system-settings-wifi-mode', methods=['POST'])
def system_settings_wifi_mode():
    """Handle WiFi mode switching between client and hotspot"""
    data = request.get_json()
    mode = data.get('mode', 'client')
    
    # Load current WiFi settings
    wifi_settings = load_wifi_settings()
    
    if mode == 'hotspot':
        ssid = data.get('hotspot_ssid', wifi_settings['hotspot_ssid'])
        password = data.get('hotspot_password', wifi_settings['hotspot_password'])
        channel = int(data.get('hotspot_channel', wifi_settings['hotspot_channel']))
        ip_address = data.get('hotspot_ip', wifi_settings['hotspot_ip'])
        
        if len(password) < 8:
            return jsonify({'success': False, 'error': 'Hotspot password must be at least 8 characters'})
        
        # Update WiFi settings (but not wifi_mode since it's determined from hardware)
        wifi_settings.update({
            'hotspot_ssid': ssid,
            'hotspot_password': password,
            'hotspot_channel': channel,
            'hotspot_ip': ip_address
        })
        save_wifi_settings(wifi_settings)
        
        # Configure hotspot
        success, message = configure_wifi_hotspot(ssid, password, channel, ip_address)
        
        if success:
            return jsonify({'success': True, 'message': f'Hotspot "{ssid}" created successfully'})
        else:
            return jsonify({'success': False, 'error': message})
            
    elif mode == 'client':
        # No need to update wifi_mode in settings since it's determined from hardware
        # Just switch to client mode
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

@app.route('/system-settings-wifi-scan')
def system_settings_wifi_scan():
    """Scan for available WiFi networks"""
    try:
        # Rescan for networks
        try:
            subprocess.run(['sudo', 'nmcli', 'device', 'wifi', 'rescan'], timeout=10, check=True)
            time.sleep(2)  # Give time for scan to complete
        except subprocess.TimeoutExpired:
            pass  # Continue even if rescan times out
        except Exception:
            pass  # Continue even if rescan fails
        
        # Get list of networks using nmcli's tabular terse mode
        result = subprocess.run(['nmcli', '--mode', 'tabular', '--terse', '--fields', 'IN-USE,BSSID,SSID,MODE,CHAN,SIGNAL,SECURITY', 'device', 'wifi', 'list'], 
                               capture_output=True, text=True, timeout=10)
        
        networks = []
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if not line.strip():
                    continue
                    
                # The output uses colon separators, but BSSID has escaped colons (\:)
                # Format: IN-USE:BSSID:SSID:MODE:CHAN:SIGNAL:SECURITY
                # Example:  :D8\:EC\:5E\:6F\:E4\:CC:quarry villa:Infra:11:100:WPA2
                
                parts = line.split(':')
                if len(parts) >= 7:
                    try:
                        in_use = parts[0].strip() == '*'
                        
                        # BSSID is in parts[1] but has escaped colons - reconstruct it
                        # The BSSID takes up 6 parts due to the escaped colons
                        bssid_parts = parts[1:7]  # Next 6 parts form the BSSID
                        bssid = ':'.join(bssid_parts).replace('\\:', ':')
                        
                        # SSID starts at part 7
                        ssid_start = 7
                        # Find where SSID ends by looking for the last 4 fields (MODE:CHAN:SIGNAL:SECURITY)
                        ssid_end = len(parts) - 4
                        ssid = ':'.join(parts[ssid_start:ssid_end]) if ssid_end > ssid_start else parts[ssid_start] if ssid_start < len(parts) else ''
                        
                        # Last 4 parts are always MODE, CHAN, SIGNAL, SECURITY
                        if len(parts) >= 4:
                            mode = parts[-4]
                            channel = parts[-3]
                            signal = parts[-2]
                            security = parts[-1]
                        else:
                            continue
                        
                        # Handle hidden networks
                        if not ssid or ssid == '--':
                            ssid = '(Hidden Network)'
                        
                        # Convert signal to percentage (signal is already 0-100 in this format)
                        try:
                            signal_percent = int(signal)
                        except:
                            signal_percent = 0
                        
                        network = {
                            'ssid': ssid,
                            'bssid': bssid,
                            'mode': mode,
                            'channel': channel,
                            'signal': signal_percent,
                            'security': security if security and security != '--' else None,
                            'in_use': in_use
                        }
                        networks.append(network)
                    except (IndexError, ValueError):
                        continue
        
        # Sort by signal strength (strongest first)
        networks.sort(key=lambda x: x['signal'], reverse=True)
        
        return jsonify({
            'success': True,
            'networks': networks
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({
            'success': False,
            'error': 'WiFi scan timed out',
            'networks': []
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to scan WiFi networks: {e}',
            'networks': []
        })

@app.route('/system-settings-reboot', methods=['POST'])
def system_settings_reboot():
    try:
        subprocess.Popen(['sudo', 'reboot'])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/system-settings-shutdown', methods=['POST'])
def system_settings_shutdown():
    try:
        subprocess.Popen(['sudo', 'shutdown', '-h', 'now'])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/system-settings-factory-reset', methods=['POST'])
def system_settings_factory_reset():
    try:
        # Remove the settings file to reset to defaults
        if os.path.exists(SETTINGS_FILE):
            os.remove(SETTINGS_FILE)
        
        # Also remove any cached authentication files
        auth_file = os.path.join(STREAMER_DATA_DIR, 'auth.json')
        if os.path.exists(auth_file):
            os.remove(auth_file)
        
        # Remove WiFi credentials file if it exists
        wifi_file = os.path.join(STREAMER_DATA_DIR, 'wifi.json')
        if os.path.exists(wifi_file):
            os.remove(wifi_file)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/system-settings-auto-update-status')
def system_settings_auto_update_status():
    try:
        # Check if install_rpi_streamer.service is enabled
        result = subprocess.run(['systemctl', 'is-enabled', 'install_rpi_streamer.service'], 
                              capture_output=True, text=True)
        enabled = result.returncode == 0 and result.stdout.strip() == 'enabled'
        return jsonify({'enabled': enabled})
    except Exception as e:
        return jsonify({'enabled': False, 'error': str(e)})

@app.route('/system-settings-auto-update-toggle', methods=['POST'])
def system_settings_auto_update_toggle():
    try:
        data = request.get_json()
        enable = data.get('enabled', False)
        
        if enable:
            # Enable the service
            subprocess.run(['sudo', 'systemctl', 'enable', 'install_rpi_streamer.service'], 
                         check=True, capture_output=True)
            message = 'Automatic updates enabled. The system will check for updates at boot.'
        else:
            # Disable the service
            subprocess.run(['sudo', 'systemctl', 'disable', 'install_rpi_streamer.service'], 
                         check=True, capture_output=True)
            message = 'Automatic updates disabled. Updates will only be performed manually.'
        
        return jsonify({'success': True, 'message': message})
    except subprocess.CalledProcessError as e:
        error_msg = f"Failed to {'enable' if enable else 'disable'} auto-update service"
        if e.stderr:
            error_msg += f": {e.stderr.decode()}"
        return jsonify({'success': False, 'error': error_msg})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def get_current_git_branch():
    """
    Get the current git branch name to compare against the correct remote branch.
    Returns the branch name (e.g., 'main', 'development').
    """
    import subprocess
    try:
        result = subprocess.run(['git', 'branch', '--show-current'], capture_output=True, text=True)
        if result.returncode == 0:
            branch = result.stdout.strip()
            if branch:
                return branch
        
        # Fallback: try to get branch from git symbolic-ref
        result = subprocess.run(['git', 'symbolic-ref', '--short', 'HEAD'], capture_output=True, text=True)
        if result.returncode == 0:
            branch = result.stdout.strip()
            if branch:
                return branch
                
        # If we can't determine the branch, something is wrong with the git repository
        raise RuntimeError("Unable to determine current git branch")
    except Exception as e:
        raise RuntimeError(f"Failed to get current git branch: {e}")

@app.route('/system-check-update', methods=['POST'])
def system_check_update():
    """
    Check for any differences between local and remote tracked files, ignoring timestamps.
    Returns updates=True if any file content differs, is missing, or is extra locally.
    Uses the current git branch to compare against the correct remote branch.
    """
    import subprocess, os
    try:
        # Get the current branch to compare against the correct remote
        current_branch = get_current_git_branch()
        remote_branch = f'origin/{current_branch}'
        # Fetch latest info from remote
        fetch_result = subprocess.run(['git', 'fetch', 'origin'], capture_output=True, text=True)
        if fetch_result.returncode != 0:
            return jsonify({'success': False, 'error': fetch_result.stderr.strip()})
        # Compare local and remote tracked files (ignoring timestamps and permission)
        diff_result = subprocess.run(['git', '-c', 'core.filemode=false', 'diff', '--name-status', remote_branch], capture_output=True, text=True)
        if diff_result.returncode != 0:
            return jsonify({'success': False, 'error': diff_result.stderr.strip()})
        diff_output = diff_result.stdout.strip()
        # Also check for missing files (tracked in remote but missing locally)
        ls_remote = subprocess.run(['git', 'ls-tree', '-r', '--name-only', remote_branch], capture_output=True, text=True)
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
    Uses the current git branch to update from the correct remote branch.
    """
    import subprocess, os
    results = []
    try:
        # Get the current branch to update from the correct remote
        current_branch = get_current_git_branch()
        remote_branch = f'origin/{current_branch}'
        results.append(f'Updating from branch: {current_branch}')
        
        # Fetch latest changes
        fetch = subprocess.run(['git', 'fetch', 'origin'], capture_output=True, text=True)
        results.append('git fetch: ' + fetch.stdout.strip() + fetch.stderr.strip())
        if fetch.returncode != 0:
            return jsonify({'success': False, 'error': fetch.stderr.strip(), 'results': results})
        # Hard reset to the correct remote branch (restores missing/tracked files, removes local changes, removes extra tracked files)
        reset = subprocess.run(['git', 'reset', '--hard', remote_branch], capture_output=True, text=True)
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

@app.route('/active-recordings')
def active_recordings():
    """Get current active recordings as JSON"""
    active_files = []
    usb_mount_point = find_usb_storage()
    
    # Get local active recordings
    local_recording_path = os.path.join(STREAMER_DATA_DIR, 'recordings', 'webcam')
    add_files_from_path(active_files, local_recording_path, "", "Local", active_only=True)
    
    # Get USB active recordings if available
    if usb_mount_point:
        usb_recording_path = os.path.join(usb_mount_point, 'streamerData', 'recordings', 'webcam')
        add_files_from_path(active_files, usb_recording_path, "[USB] ", "USB", active_only=True)
    
    return jsonify({'files': active_files})

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

# Global list of monitored services
MONITORED_SERVICES = ['gps-daemon', 'gps-startup', 'mediamtx', 'heartbeat-daemon', 'ups-monitor']

@app.route('/service-logs-sse/<service>')
def service_logs_sse(service):
    """SSE endpoint for real-time service log monitoring."""
    import json
    
    # Validate service name
    if service not in MONITORED_SERVICES:
        return jsonify({'error': 'Invalid service name'}), 400
    
    def event_stream():
        client_timeout = 0
        max_timeout = 300  # 5 minutes for log streams
        
        try:
            # Send initial logs first
            initial_logs = get_service_logs(service, lines=50)
            if initial_logs:
                yield f"data: {json.dumps({'type': 'initial', 'lines': initial_logs})}\n\n"
            
            # Start following logs in real-time
            log_process = None
            try:
                log_process = subprocess.Popen(
                    ['journalctl', '-u', service, '-f', '--no-pager', '-o', 'json'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1
                )
                
                while client_timeout < max_timeout:
                    try:
                        # Read line with timeout
                        line = log_process.stdout.readline()
                        if line:
                            try:
                                # Parse journalctl JSON output
                                log_entry = json.loads(line.strip())
                                message = log_entry.get('MESSAGE', '')
                                timestamp = log_entry.get('__REALTIME_TIMESTAMP')
                                
                                # Convert timestamp from microseconds to milliseconds
                                if timestamp:
                                    timestamp = int(timestamp) // 1000
                                
                                yield f"data: {json.dumps({'type': 'log', 'line': message, 'timestamp': timestamp})}\n\n"
                                client_timeout = 0  # Reset timeout on activity
                            except json.JSONDecodeError:
                                # Handle non-JSON lines
                                yield f"data: {json.dumps({'type': 'log', 'line': line.strip(), 'timestamp': None})}\n\n"
                        else:
                            time.sleep(0.1)
                            client_timeout += 0.1
                            
                    except Exception as e:
                        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                        break
                        
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to start log monitoring: {str(e)}'})}\n\n"
                
            finally:
                if log_process:
                    log_process.terminate()
                    try:
                        log_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        log_process.kill()
                        
        except GeneratorExit:
            print(f"Service logs SSE client disconnected for {service}")
        except Exception as e:
            print(f"Service logs SSE error for {service}: {e}")
            
        print(f"Service logs SSE stream ended for {service}")
    
    return Response(event_stream(), mimetype='text/event-stream')

def get_service_logs(service, lines=50):
    """
    Get recent logs for a service.
    Returns a list of log entries with timestamp and message.
    """
    try:
        # Get recent logs using journalctl
        result = subprocess.run(
            ['journalctl', '-u', service, '-n', str(lines), '--no-pager', '-o', 'json'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return [{'line': f'Error getting logs: {result.stderr}', 'timestamp': None}]
        
        logs = []
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                try:
                    log_entry = json.loads(line)
                    message = log_entry.get('MESSAGE', '')
                    timestamp = log_entry.get('__REALTIME_TIMESTAMP')
                    
                    # Convert timestamp from microseconds to milliseconds
                    if timestamp:
                        timestamp = int(timestamp) // 1000
                    
                    logs.append({'line': message, 'timestamp': timestamp})
                except json.JSONDecodeError:
                    logs.append({'line': line.strip(), 'timestamp': None})
        
        return logs
        
    except subprocess.TimeoutExpired:
        return [{'line': 'Timeout getting service logs', 'timestamp': None}]
    except FileNotFoundError:
        return [{'line': 'journalctl not available', 'timestamp': None}]
    except Exception as e:
        return [{'line': f'Error: {str(e)}', 'timestamp': None}]

@app.route('/service-status-sse')
def service_status_sse():
    """SSE endpoint for service status monitoring."""
    import json
    
    def event_stream():
        last_status = None
        client_timeout = 0
        max_timeout = 30  # Stop after 30 seconds of no activity
        
        while client_timeout < max_timeout:
            try:
                status = get_service_status()
                
                # Only send if changed (to reduce bandwidth)
                if status != last_status:
                    yield f"data: {json.dumps(status)}\n\n"
                    last_status = status
                    client_timeout = 0  # Reset timeout on successful yield
                    
                time.sleep(3)  # Update every 3 seconds
            except GeneratorExit:
                # Client disconnected
                print("Service status SSE client disconnected")
                break
            except Exception as e:
                print(f"Service status SSE error: {e}")
                error_data = {'error': str(e)}
                yield f"data: {json.dumps(error_data)}\n\n"
                client_timeout += 1
                time.sleep(5)  # Wait longer on error
        print("Service status SSE stream ended")
    
    return Response(event_stream(), mimetype='text/event-stream')

def get_service_status():
    """
    Get status of system services including enabled/disabled status.
    Returns a dictionary with service statuses.
    """
    status = {}
    
    for service in MONITORED_SERVICES:
        try:
            # Use systemctl to get service status
            result = subprocess.run(
                ['systemctl', 'is-active', service],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            service_status = result.stdout.strip()
            
            # Get enabled status
            enabled_result = subprocess.run(
                ['systemctl', 'is-enabled', service],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            enabled_status = enabled_result.stdout.strip()
            is_enabled = enabled_status == 'enabled'
            
            # Get additional info if service is not active
            if service_status != 'active':
                # Get more detailed status
                detail_result = subprocess.run(
                    ['systemctl', 'show', service, '--property=ActiveState,SubState,LoadState'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                
                details = {}
                for line in detail_result.stdout.strip().split('\n'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        details[key] = value
                
                status[service] = {
                    'status': service_status if service_status else 'inactive',
                    'active_state': details.get('ActiveState', 'unknown'),
                    'sub_state': details.get('SubState', 'unknown'),
                    'load_state': details.get('LoadState', 'unknown'),
                    'enabled': is_enabled,
                    'enabled_status': enabled_status
                }
            else:
                status[service] = {
                    'status': service_status,
                    'active_state': 'active',
                    'sub_state': 'running',
                    'load_state': 'loaded',
                    'enabled': is_enabled,
                    'enabled_status': enabled_status
                }
                
        except subprocess.TimeoutExpired:
            status[service] = {
                'status': 'timeout',
                'error': 'systemctl command timed out',
                'enabled': False,
                'enabled_status': 'unknown'
            }
        except FileNotFoundError:
            # systemctl not found (not on Linux)
            status[service] = {
                'status': 'unavailable',
                'error': 'systemctl not available',
                'enabled': False,
                'enabled_status': 'unavailable'
            }
        except Exception as e:
            status[service] = {
                'status': 'error',
                'error': str(e),
                'enabled': False,
                'enabled_status': 'error'
            }
    
    return status

@app.route('/service-control', methods=['POST'])
def service_control():
    """Enable or disable a system service"""
    data = request.get_json()
    service = data.get('service')
    action = data.get('action')  # 'enable' or 'disable'
    
    if not service or service not in MONITORED_SERVICES:
        return jsonify({'success': False, 'error': 'Invalid service name'}), 400
    
    if action not in ['enable', 'disable']:
        return jsonify({'success': False, 'error': 'Invalid action. Use enable or disable'}), 400
    
    try:
        if action == 'enable':
            # Enable service for auto-start
            enable_result = subprocess.run(
                ['sudo', 'systemctl', 'enable', service],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if enable_result.returncode != 0:
                return jsonify({
                    'success': False, 
                    'error': f'Failed to enable service: {enable_result.stderr.strip()}',
                    'service': service,
                    'action': action
                }), 500
            
            # Start service immediately
            start_result = subprocess.run(
                ['sudo', 'systemctl', 'start', service],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if start_result.returncode != 0:
                return jsonify({
                    'success': False, 
                    'error': f'Service enabled but failed to start: {start_result.stderr.strip()}',
                    'service': service,
                    'action': action
                }), 500
                
            return jsonify({
                'success': True, 
                'message': f'Service {service} enabled and started successfully',
                'service': service,
                'action': action
            })
            
        elif action == 'disable':
            # Stop service immediately
            stop_result = subprocess.run(
                ['sudo', 'systemctl', 'stop', service],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if stop_result.returncode != 0:
                return jsonify({
                    'success': False, 
                    'error': f'Failed to stop service: {stop_result.stderr.strip()}',
                    'service': service,
                    'action': action
                }), 500
            
            # Disable service from auto-start
            disable_result = subprocess.run(
                ['sudo', 'systemctl', 'disable', service],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if disable_result.returncode != 0:
                return jsonify({
                    'success': False, 
                    'error': f'Service stopped but failed to disable: {disable_result.stderr.strip()}',
                    'service': service,
                    'action': action
                }), 500
                
            return jsonify({
                'success': True, 
                'message': f'Service {service} stopped and disabled successfully',
                'service': service,
                'action': action
            })
            
    except subprocess.TimeoutExpired:
        return jsonify({
            'success': False, 
            'error': f'Timeout {action}ing service',
            'service': service,
            'action': action
        }), 500
    except Exception as e:
        return jsonify({
            'success': False, 
            'error': f'Error {action}ing service: {str(e)}',
            'service': service,
            'action': action
        }), 500

@app.route('/service-status/<service>')
def service_enabled_status(service):
    """Get the enabled/disabled status of a service"""
    if service not in MONITORED_SERVICES:
        return jsonify({'error': 'Invalid service name'}), 400
    
    try:
        # Check if service is enabled
        result = subprocess.run(
            ['systemctl', 'is-enabled', service],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        enabled_status = result.stdout.strip()
        is_enabled = enabled_status == 'enabled'
        
        # Also get the current active status
        active_result = subprocess.run(
            ['systemctl', 'is-active', service],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        active_status = active_result.stdout.strip()
        
        return jsonify({
            'service': service,
            'enabled': is_enabled,
            'enabled_status': enabled_status,
            'active': active_status == 'active',
            'active_status': active_status
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Timeout getting service status'}), 500
    except Exception as e:
        return jsonify({'error': f'Error getting service status: {str(e)}'}), 500

@app.route('/diagnostics')
def diagnostics():
    """Simple JSON endpoint for system diagnostics - reads from heartbeat data."""
    try:
        # Try to read diagnostics from heartbeat file
        if os.path.exists(HEARTBEAT_FILE):
            try:
                with open(HEARTBEAT_FILE, 'r') as f:
                    heartbeat_data = json.load(f)
                
                # Check if diagnostics data is available
                if 'diagnostics' in heartbeat_data:
                    return jsonify(heartbeat_data['diagnostics'])
                
            except (json.JSONDecodeError, KeyError, IOError) as e:
                return jsonify({
                    'error': f'Error reading heartbeat data: {e}',
                    'timestamp': time.time()
                }), 500
        
        # If heartbeat file doesn't exist or has no diagnostics
        return jsonify({
            'error': 'Diagnostics data not available - heartbeat daemon may not be running',
            'timestamp': time.time()
        }), 503
        
    except Exception as e:
        return jsonify({
            'error': f'Diagnostics error: {e}',
            'timestamp': time.time()
        }), 500

def parse_throttled_status(throttled_output):
    """
    Parse the throttled status from vcgencmd get_throttled output.
    Returns a dict with parsed information about undervoltage and throttling.
    This is a minimal version for fallback use only.
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
    import atexit    
    try:
        app.run(host='0.0.0.0', port=80, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("Received interrupt signal, shutting down...")