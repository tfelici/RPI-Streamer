def get_video_duration_ffprobe(filepath):
    """
    Return the duration of a video file in seconds using ffprobe. Returns None on error.
    """
    try:
        # ffprobe must be installed and in PATH
        result = subprocess.run([
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            filepath
        ], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            duration_str = result.stdout.strip()
            try:
                return float(duration_str)
            except ValueError:
                return None
        else:
            return None
    except Exception:
        return None
import subprocess
import re
import os
import json
import atexit
import psutil
from datetime import datetime

# Settings constants and defaults
STREAMER_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'streamerData'))
SETTINGS_FILE = os.path.join(STREAMER_DATA_DIR, 'settings.json')
STREAM_PIDFILE = "/tmp/relay-ffmpeg-webcam.pid"

DEFAULT_SETTINGS = {
    "stream_url": "",
    "upload_url": "",
    "framerate": 30,
    "crf": 32,
    "resolution": "1280x720",
    "vbitrate": 768,
    "abitrate": "16k",
    "ar": 16000,
    "volume": 100,
    "gop": 30,
    "dynamicBitrate": False,
    "use_gstreamer": False,
    "audio_input": None,
    "video_input": None,
    "video_stabilization": False,
    "gps_username": "",
    "aircraft_registration": "",
    "gps_stream_link": False,
    "gps_start_mode": "manual",
    "gps_stop_on_power_loss": False,
    "gps_stop_power_loss_minutes": 1,
    "wifi_mode": "client",  # "client" or "hotspot"
    "hotspot_ssid": "RPI-Streamer",
    "hotspot_password": "rpistreamer123",
    "hotspot_channel": 6,
    "hotspot_ip": "192.168.4.1"
}

def list_audio_inputs():
    """
    Returns a list of dicts: {"id": device_str, "label": friendly_name}
    where device_str is e.g. 'hw:1,0' and friendly_name is e.g. 'USB Audio Device (hw:1,0)'.
    """
    try:
        result = subprocess.run(['arecord', '-l'], capture_output=True, text=True)
        devices = []
        for line in result.stdout.splitlines():
            if 'card' in line and 'device' in line:
                m = re.search(r'card (\d+): ([^\[]+)\[([^\]]+)\], device (\d+): ([^\[]+)', line)
                if m:
                    cardnum = m.group(1)
                    cardname = m.group(3).strip()
                    devnum = m.group(4)
                    devname = m.group(5).strip()
                    device_str = f'hw:{cardnum},{devnum}'
                    label = f'{cardname} ({device_str})'
                else:
                    device_str = line.strip()
                    label = line.strip()
                devices.append({"id": device_str, "label": label})
        return devices
    except Exception:
        return []

def list_video_inputs():
    """
    Returns a list of dicts: {"id": device_path, "label": friendly_name}
    where device_path is e.g. '/dev/video0' and friendly_name is a short name plus the device string.
    Lists all /dev/video* devices that are actual video capture interfaces, filtering out duplicates.
    """
    devices = []
    seen_names = set()
    
    for i in range(10):
        dev = f'/dev/video{i}'
        if os.path.exists(dev):
            name_path = f'/sys/class/video4linux/video{i}/name'
            try:
                with open(name_path, 'r') as f:
                    name = f.read().strip()
                    # Only use the part before ':' if present
                    short = name.split(':', 1)[0].strip() if ':' in name else name
                    
                    # Skip if we've already seen this device name
                    if short in seen_names:
                        continue
                    
                    # Filter out non-video interfaces (metadata, control, etc.)
                    name_lower = name.lower()
                    if ('metadata' in name_lower or 
                        'control' in name_lower or 
                        'output' in name_lower):
                        continue
                    
                    if not short:
                        short = f'video{i}'
                    
                    label = f'{short} ({dev})'
                    devices.append({"id": dev, "label": label})
                    seen_names.add(short)
                    
            except Exception:
                # If we can't read the name, include it as a fallback
                label = f'video{i} ({dev})'
                if label not in [d["label"] for d in devices]:
                    devices.append({"id": dev, "label": label})
                    
    return devices

# Settings cache to avoid re-reading file on every call
_settings_cache = None
_settings_cache_mtime = None

def get_setting(key):
    """
    Load a single setting from the settings.json file in ../streamerData.
    Uses centralized defaults if the key is not found.
    Caches settings in memory and only re-reads file when modified.
    """
    global _settings_cache, _settings_cache_mtime
    
    try:
        # Check if we need to reload the file
        current_mtime = None
        if os.path.exists(SETTINGS_FILE):
            current_mtime = os.path.getmtime(SETTINGS_FILE)
        
        # Load from cache if file hasn't changed
        if (_settings_cache is not None and 
            current_mtime == _settings_cache_mtime):
            # Use cached settings
            if key in _settings_cache:
                return _settings_cache[key]
            return DEFAULT_SETTINGS.get(key, None)
        
        # File changed or no cache yet - reload
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                _settings_cache = json.load(f)
            _settings_cache_mtime = current_mtime
        else:
            # File doesn't exist - use empty cache
            _settings_cache = {}
            _settings_cache_mtime = None
        
        # Return value from newly loaded cache
        if key in _settings_cache:
            return _settings_cache[key]
        return DEFAULT_SETTINGS.get(key, None)
        
    except Exception:
        # On error, fall back to defaults
        return DEFAULT_SETTINGS.get(key, None)

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

def get_active_gps_tracking_info():
    """Return (pid, username, host, track_id) if GPS tracking is active, else (None, None, None, None)"""
    GPS_PIDFILE = "/tmp/gps-tracker.pid"
    if os.path.exists(GPS_PIDFILE):
        try:
            with open(GPS_PIDFILE, 'r') as f:
                line = f.read().strip()
                if line:
                    parts = line.split(':', 3)
                    if len(parts) >= 4:
                        pid_str, username, host, track_id = parts
                        pid = int(pid_str)
                        if is_pid_running(pid):
                            return pid, username, host, track_id
        except Exception:
            pass
    return None, None, None, None

def is_gps_tracking():
    """Check if GPS tracking is currently active"""
    pid, _, _, _ = get_active_gps_tracking_info()
    return pid is not None

def load_settings():
    """
    Load settings from the settings.json file, merging with DEFAULT_SETTINGS.
    Returns a dictionary with all settings, using defaults for any missing keys.
    """
    settings = DEFAULT_SETTINGS.copy()
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                settings.update(json.load(f))
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Could not parse settings.json: {e}")
            # Keep default settings
    return settings

def save_settings(settings):
    """
    Save settings to the settings.json file.
    """
    # Ensure the directory exists
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)

# =============================================================================
# USB Storage Functions
# ==============================================================================

def log_message(message):
    """
    Print a message with timestamp for better debugging.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

def detect_usb_devices():
    """
    Detect USB storage devices that are plugged in but not necessarily mounted.
    Returns a list of device paths (e.g., ['/dev/sda1', '/dev/sdb1']).
    Uses multiple detection methods for better compatibility.
    """
    usb_devices = []
    
    # Method 1: Use lsblk to find removable storage devices
    try:
        log_message("Detecting USB devices using lsblk...")
        result = subprocess.run(['lsblk', '-n', '-o', 'NAME,TYPE,MOUNTPOINT,HOTPLUG,RM'], 
                               capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 4:
                        name_raw, device_type = parts[0], parts[1]
                        hotplug = parts[3] if len(parts) > 3 else '0'
                        removable = parts[4] if len(parts) > 4 else '0'
                        
                        # Clean up the device name - remove tree drawing characters
                        name = name_raw
                        for char in ['└─', '├─', '│ ', '├ ', '└ ']:
                            name = name.replace(char, '')
                        name = name.strip()
                        
                        # Look for partitions on removable/hotplug devices
                        if (device_type == 'part' and 
                            (hotplug == '1' or removable == '1') and
                            not name.startswith('mmcblk')):  # Exclude SD card
                            
                            device_path = f'/dev/{name}'
                            if device_path not in usb_devices:
                                usb_devices.append(device_path)
                                log_message(f"Found USB device via lsblk: {device_path}")
    except Exception as e:
        log_message(f"lsblk detection failed: {e}")
    
    # Method 2: Check /dev for sd* devices (fallback method)
    try:
        log_message("Detecting USB devices using /dev scan...")
        for device_file in os.listdir('/dev'):
            # Look for USB storage devices (sd* pattern, excluding sda which is usually the SD card)
            if (device_file.startswith('sd') and 
                len(device_file) == 4 and 
                device_file[-1].isdigit() and
                device_file not in ['sda1', 'sda2']):  # Exclude main SD card partitions
                
                device_path = f'/dev/{device_file}'
                
                # Verify it's a block device and not already found
                if device_path not in usb_devices:
                    try:
                        result = subprocess.run(['lsblk', '-n', '-o', 'TYPE', device_path], 
                                              capture_output=True, text=True, timeout=5)
                        if result.returncode == 0 and 'part' in result.stdout:
                            usb_devices.append(device_path)
                            log_message(f"Found USB device via /dev scan: {device_path}")
                    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                        continue
                        
    except Exception as e:
        log_message(f"Error in /dev scan: {e}")
    
    # Method 3: Check for USB devices using udevadm (most reliable)
    try:
        log_message("Detecting USB devices using udevadm...")
        result = subprocess.run(['find', '/dev/disk/by-id/', '-name', '*usb*', '-type', 'l'], 
                               capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            for usb_link in result.stdout.strip().split('\n'):
                if usb_link.strip():
                    try:
                        # Resolve the symbolic link to get the actual device path
                        real_device = os.path.realpath(usb_link)
                        
                        # Only include partitions, not whole disks
                        if real_device and real_device[-1].isdigit():
                            if real_device not in usb_devices:
                                usb_devices.append(real_device)
                                log_message(f"Found USB device via udevadm: {real_device} (from {usb_link})")
                    except Exception as e:
                        log_message(f"Error resolving USB link {usb_link}: {e}")
                        
    except Exception as e:
        log_message(f"udevadm detection failed: {e}")
    
    # Remove duplicates while preserving order
    unique_devices = []
    for device in usb_devices:
        if device not in unique_devices:
            unique_devices.append(device)
    
    log_message(f"Total USB devices detected: {len(unique_devices)}")
    return unique_devices

def get_filesystem_type(device_path):
    """
    Get the filesystem type of a device.
    Returns the filesystem type string or None if detection fails.
    """
    try:
        result = subprocess.run(['blkid', '-o', 'value', '-s', 'TYPE', device_path], 
                               capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            fstype = result.stdout.strip()
            log_message(f"Device {device_path} has filesystem: {fstype}")
            return fstype
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        log_message(f"Failed to detect filesystem for {device_path}: {e}")
    return None

def mount_usb_device(device_path, fstype):
    """
    Mount a USB device with auto-detection and fallback options.
    Returns the mount point path if successful, None otherwise.
    """
    # Create a mount point
    device_name = os.path.basename(device_path)
    mount_point = f'/mnt/usb_{device_name}'
    
    try:
        # Create mount point directory
        os.makedirs(mount_point, exist_ok=True)
        
        # First try: Use auto-detection with options for common filesystems
        mount_cmd = [
            'sudo', 'mount', 
            '-t', 'auto',
            '-o', 'uid=1000,gid=1000,umask=022',
            device_path, mount_point
        ]
        
        log_message(f"Mounting {device_path} at {mount_point} using auto-detection")
        log_message(f"Mount command: {' '.join(mount_cmd)}")
        
        result = subprocess.run(mount_cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            # Verify the mount was successful and is writable
            if os.path.ismount(mount_point) and os.access(mount_point, os.W_OK):
                log_message(f"Successfully mounted {device_path} at {mount_point} using auto-detection")
                return mount_point
            else:
                log_message(f"Mount succeeded but directory not writable: {mount_point}")
                # Try to unmount if not writable
                subprocess.run(['sudo', 'umount', mount_point], capture_output=True)
        else:
            log_message(f"Auto-detection mount failed: {result.stderr}")
            
            # Fallback: Try with detected filesystem type if available
            if fstype:
                log_message(f"Trying fallback mount with detected filesystem type: {fstype}")
                mount_cmd_fs = [
                    'sudo', 'mount', 
                    '-t', fstype,
                    '-o', 'uid=1000,gid=1000,umask=022',
                    device_path, mount_point
                ]
                
                result_fs = subprocess.run(mount_cmd_fs, capture_output=True, text=True, timeout=10)
                
                if result_fs.returncode == 0:
                    if os.path.ismount(mount_point) and os.access(mount_point, os.W_OK):
                        log_message(f"Successfully mounted {device_path} at {mount_point} using {fstype}")
                        return mount_point
                    else:
                        log_message(f"Filesystem-specific mount succeeded but directory not writable")
                        subprocess.run(['sudo', 'umount', mount_point], capture_output=True)
                else:
                    log_message(f"Filesystem-specific mount failed: {result_fs.stderr}")
            
            # Final fallback: Try common filesystem types
            common_fs_types = ['vfat', 'exfat', 'ntfs', 'ext4', 'ext3', 'ext2']
            for fs_type in common_fs_types:
                if fs_type == fstype:  # Skip if we already tried this
                    continue
                    
                log_message(f"Trying fallback mount with filesystem type: {fs_type}")
                mount_cmd_fallback = [
                    'sudo', 'mount', 
                    '-t', fs_type,
                    '-o', 'uid=1000,gid=1000,umask=022',
                    device_path, mount_point
                ]
                
                result_fallback = subprocess.run(mount_cmd_fallback, capture_output=True, text=True, timeout=10)
                
                if result_fallback.returncode == 0:
                    if os.path.ismount(mount_point) and os.access(mount_point, os.W_OK):
                        log_message(f"Successfully mounted {device_path} at {mount_point} using {fs_type}")
                        return mount_point
                    else:
                        log_message(f"Mount with {fs_type} succeeded but directory not writable")
                        subprocess.run(['sudo', 'umount', mount_point], capture_output=True)
                        
    except Exception as e:
        log_message(f"Error mounting {device_path}: {e}")
    
    # Clean up mount point if all mount attempts failed
    try:
        os.rmdir(mount_point)
    except:
        pass
    
    return None

def find_usb_storage():
    """
    Find and mount the first available USB storage device on Raspberry Pi Lite.
    Returns the mount point path if found and mounted, None otherwise.
    """
    log_message("Detecting USB storage devices...")
    
    # First check if any USB devices are already mounted
    try:
        with open('/proc/mounts', 'r') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3:
                    device, mount_point, fstype = parts[0], parts[1], parts[2]
                    # Look for already mounted USB devices
                    if (device.startswith('/dev/sd') and 
                        device != '/dev/sda' and  # Exclude main SD card (if it exists)
                        not device.endswith('a') and  # Skip whole disks, only partitions
                        fstype in ['vfat', 'exfat', 'ntfs', 'ext4', 'ext3', 'ext2'] and
                        mount_point != '/'):  # Ignore root mount point
                        
                        # Verify the device still exists and mount point is accessible
                        if (os.path.exists(device) and 
                            os.path.exists(mount_point) and 
                            os.access(mount_point, os.R_OK) and
                            os.access(mount_point, os.W_OK)):
                            
                            # Double-check it's actually mounted using os.path.ismount
                            if os.path.ismount(mount_point):
                                log_message(f"Found already mounted USB storage: {mount_point} (device: {device}, filesystem: {fstype})")
                                return mount_point
                            else:
                                log_message(f"Mount point {mount_point} appears in /proc/mounts but is not actually mounted")
                        else:
                            log_message(f"USB device {device} or mount point {mount_point} no longer accessible")
    except Exception as e:
        log_message(f"Error checking mounted devices: {e}")
    
    # Detect unmounted USB devices
    usb_devices = detect_usb_devices()
    
    if not usb_devices:
        log_message("No USB storage devices detected")
        return None
    
    # Try to mount the first detected USB device
    for device_path in usb_devices:
        log_message(f"Attempting to mount USB device: {device_path}")
        
        # Skip devices with invalid paths (containing tree characters)
        if any(char in device_path for char in ['└', '├', '─']):
            log_message(f"Skipping device with tree characters: {device_path}")
            continue
        
        # Check if this device is already mounted
        try:
            with open('/proc/mounts', 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == device_path:
                        mount_point = parts[1]
                        if os.path.exists(mount_point) and os.access(mount_point, os.W_OK):
                            log_message(f"Device {device_path} is already mounted at {mount_point}")
                            return mount_point
                        break
        except Exception as e:
            log_message(f"Error checking if {device_path} is mounted: {e}")
        
        # Get filesystem type
        fstype = get_filesystem_type(device_path)
        if not fstype:
            log_message(f"Could not determine filesystem type for {device_path}, skipping")
            continue
        
        # Try to mount the device
        mount_point = mount_usb_device(device_path, fstype)
        if mount_point:
            return mount_point
    
    log_message("Failed to mount any USB storage devices")
    return None


def move_file_to_usb(file_path, usb_path):
    """
    Move a file to the USB storage device.
    Returns a dict with success status and destination path or error message.
    """
    try:
        if not os.path.exists(file_path):
            return {'success': False, 'error': 'Source file does not exist'}
        
        if not usb_path or not os.path.exists(usb_path):
            return {'success': False, 'error': 'USB storage not found or not accessible'}
        
        # Create proper directory structure on USB: streamerData/recordings/webcam
        usb_recordings_dir = os.path.join(usb_path, 'streamerData', 'recordings', 'webcam')
        os.makedirs(usb_recordings_dir, exist_ok=True)
        
        # Get filename and create destination path
        filename = os.path.basename(file_path)
        destination = os.path.join(usb_recordings_dir, filename)
        
        # Check if file already exists and create unique name if needed
        counter = 1
        base_name, ext = os.path.splitext(filename)
        while os.path.exists(destination):
            new_filename = f"{base_name}_{counter}{ext}"
            destination = os.path.join(usb_recordings_dir, new_filename)
            counter += 1
        
        # Move the file
        import shutil
        shutil.move(file_path, destination)
        
        return {'success': True, 'destination': destination}
        
    except Exception as e:
        return {'success': False, 'error': str(e)}


def copy_settings_and_executables_to_usb(usb_path):
    """
    Copy settings.json and executables to USB drive if they don't exist or are outdated.
    
    Args:
        usb_path: Path to the USB mount point
    
    Returns:
        dict with information about what was copied: {
            'settings_copied': bool,
            'executables_copied': int,
            'errors': list
        }
    """
    import shutil
    
    # Determine the parent directory containing streamerData and executables
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)
    
    result = {
        'settings_copied': False,
        'executables_copied': 0,
        'errors': []
    }
    
    try:
        # Copy settings.json if it doesn't exist or is outdated
        print("Checking settings.json on USB drive...")
        src_settings = os.path.join(parent_dir, 'streamerData', 'settings.json')
        dst_streamerData = os.path.join(usb_path, 'streamerData')
        dst_settings = os.path.join(dst_streamerData, 'settings.json')
        os.makedirs(dst_streamerData, exist_ok=True)
        
        if os.path.exists(src_settings):
            # Check if USB settings file is missing or outdated
            copy_settings = False
            if not os.path.exists(dst_settings):
                copy_settings = True
                print("Settings file not found on USB, copying...")
            else:
                # Compare modification times
                src_mtime = os.path.getmtime(src_settings)
                dst_mtime = os.path.getmtime(dst_settings)
                if src_mtime > dst_mtime:
                    copy_settings = True
                    print("Local settings file is newer, updating USB...")
            
            if copy_settings:
                shutil.copy2(src_settings, dst_settings)
                size_kb = os.path.getsize(dst_settings) / 1024
                print(f"Copied settings.json ({size_kb:.2f} KB) to USB: {dst_settings}")
                result['settings_copied'] = True
            else:
                print("USB settings.json is up to date")
        else:
            print(f"Warning: {src_settings} not found, skipping settings.json copy.")
            result['errors'].append(f"Settings file not found: {src_settings}")
        
        # Copy executables if they don't exist or are outdated
        print("Checking executables on USB drive...")
        src_exec_dir = os.path.join(parent_dir, 'executables')
        if os.path.isdir(src_exec_dir):
            for fname in os.listdir(src_exec_dir):
                src_f = os.path.join(src_exec_dir, fname)
                dst_f = os.path.join(usb_path, fname)
                if os.path.isfile(src_f):
                    # Check if executable is missing or outdated
                    copy_exec = False
                    if not os.path.exists(dst_f):
                        copy_exec = True
                        print(f"Executable {fname} not found on USB, copying...")
                    else:
                        # Compare modification times
                        src_mtime = os.path.getmtime(src_f)
                        dst_mtime = os.path.getmtime(dst_f)
                        if src_mtime > dst_mtime:
                            copy_exec = True
                            print(f"Local executable {fname} is newer, updating USB...")
                    
                    if copy_exec:
                        shutil.copy2(src_f, dst_f)
                        size_mb = os.path.getsize(dst_f) / (1024 * 1024)
                        print(f"Copied executable {fname} ({size_mb:.2f} MB) to USB: {dst_f}")
                        result['executables_copied'] += 1
                    else:
                        print(f"USB executable {fname} is up to date")
        else:
            print(f"Warning: {src_exec_dir} not found, skipping executables copy.")
            result['errors'].append(f"Executables directory not found: {src_exec_dir}")
        
    except Exception as e:
        error_msg = f"Error copying files to USB: {str(e)}"
        print(error_msg)
        result['errors'].append(error_msg)
    
    return result
