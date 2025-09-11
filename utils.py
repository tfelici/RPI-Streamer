import subprocess
import re
import os
import json
import psutil
import time
import math
import socket
import glob
from datetime import datetime
from typing import Optional

# Use pymediainfo for fast video duration extraction
try:
    from pymediainfo import MediaInfo
except ImportError:
    MediaInfo = None

def generate_gps_track_id() -> str:
    """Generate a unique GPS track ID based on current timestamp"""
    return str(int(time.time()))

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two GPS coordinates using Haversine formula (returns meters)"""
    # Earth radius in meters
    R = 6371000
    
    # Convert latitude and longitude to radians
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    # Haversine formula
    a = (math.sin(delta_lat / 2) ** 2 + 
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    # Distance in meters
    distance = R * c
    return distance

# Settings constants and defaults
STREAMER_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'streamerData'))
SETTINGS_FILE = os.path.join(STREAMER_DATA_DIR, 'settings.json')
STREAM_PIDFILE = "/tmp/relay-ffmpeg-webcam.pid"
HEARTBEAT_FILE = "/tmp/rpi_streamer_heartbeat.json"

def get_default_hotspot_ssid():
    """Get the system hostname to use as default hotspot SSID"""
    try:
        hostname = socket.gethostname()
        # Clean up hostname to be WiFi-safe (alphanumeric and hyphens only)
        clean_hostname = re.sub(r'[^a-zA-Z0-9-]', '-', hostname)
        return clean_hostname if clean_hostname else "RPI-Streamer"
    except:
        return "RPI-Streamer"

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
    "audio_input": "auto-detect",
    "video_input": "auto-detect",
    "video_stabilization": False,
    "domain": "",
    "username": "",
    "vehicle": "",
    "gps_stream_link": False,
    "gps_start_mode": "manual",
    "gps_stop_on_power_loss": False,
    "gps_stop_power_loss_minutes": 1,
    "power_monitor_sleep_time": 60
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

def get_gps_tracking_status():
    """
    Get detailed GPS tracking status including hardware state.
    Returns dict with: running, pid, username, domain, track_id, hardware_status, last_update
    """
    GPS_PIDFILE = "/tmp/gps-tracker.pid"
    GPS_STATUS_FILE = "/tmp/gps-tracker-status.json"
    
    # Check if process is running
    pid, username, domain, track_id = None, None, None, None
    
    if os.path.exists(GPS_PIDFILE):
        try:
            with open(GPS_PIDFILE, 'r') as f:
                line = f.read().strip()
                if line:
                    parts = line.split(':', 3)
                    if len(parts) >= 4:
                        pid_str, username, domain, track_id = parts
                        pid = int(pid_str)
                        if not is_pid_running(pid):
                            pid, username, domain, track_id = None, None, None, None
        except Exception:
            pass
    
    # Start with default status - check for detailed status first before deciding if tracking is active
    tracking_status = {
        'running': pid is not None and is_pid_running(pid),
        'pid': pid,
        'username': username,
        'domain': domain,
        'track_id': track_id,
        'hardware_status': 'unknown',
        'status_message': 'GPS tracking is not active' if pid is None else 'GPS tracking starting - checking hardware status...',
        'last_update': None
    }
    
    # Try to read detailed status from status file - this might have more recent info than PID file
    if os.path.exists(GPS_STATUS_FILE):
        try:
            with open(GPS_STATUS_FILE, 'r') as f:
                detailed_status = json.load(f)
                # Update the default status with detailed info
                tracking_status.update(detailed_status)
                # If we have status file info but no running process, keep process info as False
                if pid is None:
                    tracking_status['running'] = False
                    tracking_status['pid'] = None
                    tracking_status['username'] = None
                    tracking_status['domain'] = None
                    tracking_status['track_id'] = None
        except (json.JSONDecodeError, ValueError, IOError):
            pass  # Use default status if file is corrupted or unreadable
    
    return tracking_status

def is_gps_tracking():
    """Check if GPS tracking is currently active"""
    status = get_gps_tracking_status()
    return status['running']

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
        
        # Determine mount options based on filesystem type
        def get_mount_options(fs_type):
            """Get appropriate mount options for the filesystem type"""
            if fs_type in ['vfat', 'fat32', 'fat16', 'msdos', 'ntfs', 'exfat']:
                # FAT/NTFS filesystems support uid/gid/umask options
                return 'uid=1000,gid=1000,umask=022'
            elif fs_type in ['ext4', 'ext3', 'ext2', 'ext']:
                # Linux native filesystems use different ownership approach
                return 'defaults'
            else:
                # Unknown filesystem - use minimal options
                return 'defaults'
        
        # First try: Use auto-detection with appropriate options
        mount_options = get_mount_options(fstype or 'auto')
        mount_cmd = [
            'sudo', 'mount', 
            '-t', 'auto',
            '-o', mount_options,
            device_path, mount_point
        ]
        
        log_message(f"Mounting {device_path} at {mount_point} using auto-detection with options: {mount_options}")
        log_message(f"Mount command: {' '.join(mount_cmd)}")
        
        result = subprocess.run(mount_cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            # Verify the mount was successful
            if os.path.ismount(mount_point):
                # For ext filesystems, fix ownership after mounting
                if fstype and fstype.startswith('ext'):
                    try:
                        subprocess.run(['sudo', 'chown', '-R', '1000:1000', mount_point], 
                                     capture_output=True, timeout=5)
                        subprocess.run(['sudo', 'chmod', '-R', '755', mount_point], 
                                     capture_output=True, timeout=5)
                    except Exception as e:
                        log_message(f"Warning: Could not fix ownership for {mount_point}: {e}")
                
                # Check if writable
                if os.access(mount_point, os.W_OK):
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
                fs_mount_options = get_mount_options(fstype)
                mount_cmd_fs = [
                    'sudo', 'mount', 
                    '-t', fstype,
                    '-o', fs_mount_options,
                    device_path, mount_point
                ]
                
                result_fs = subprocess.run(mount_cmd_fs, capture_output=True, text=True, timeout=10)
                
                if result_fs.returncode == 0:
                    if os.path.ismount(mount_point):
                        # Fix ownership for ext filesystems
                        if fstype.startswith('ext'):
                            try:
                                subprocess.run(['sudo', 'chown', '-R', '1000:1000', mount_point], 
                                             capture_output=True, timeout=5)
                                subprocess.run(['sudo', 'chmod', '-R', '755', mount_point], 
                                             capture_output=True, timeout=5)
                            except Exception as e:
                                log_message(f"Warning: Could not fix ownership for {mount_point}: {e}")
                        
                        if os.access(mount_point, os.W_OK):
                            log_message(f"Successfully mounted {device_path} at {mount_point} using {fstype}")
                            return mount_point
                        else:
                            log_message(f"Filesystem-specific mount succeeded but directory not writable")
                            subprocess.run(['sudo', 'umount', mount_point], capture_output=True)
                else:
                    log_message(f"Filesystem-specific mount failed: {result_fs.stderr}")
            
            # Final fallback: Try common filesystem types with appropriate options
            common_fs_types = ['vfat', 'exfat', 'ntfs', 'ext4', 'ext3', 'ext2']
            for fs_type in common_fs_types:
                if fs_type == fstype:  # Skip if we already tried this
                    continue
                    
                log_message(f"Trying fallback mount with filesystem type: {fs_type}")
                fs_mount_options = get_mount_options(fs_type)
                mount_cmd_fallback = [
                    'sudo', 'mount', 
                    '-t', fs_type,
                    '-o', fs_mount_options,
                    device_path, mount_point
                ]
                
                result_fallback = subprocess.run(mount_cmd_fallback, capture_output=True, text=True, timeout=10)
                
                if result_fallback.returncode == 0:
                    if os.path.ismount(mount_point):
                        # Fix ownership for ext filesystems
                        if fs_type.startswith('ext'):
                            try:
                                subprocess.run(['sudo', 'chown', '-R', '1000:1000', mount_point], 
                                             capture_output=True, timeout=5)
                                subprocess.run(['sudo', 'chmod', '-R', '755', mount_point], 
                                             capture_output=True, timeout=5)
                            except Exception as e:
                                log_message(f"Warning: Could not fix ownership for {mount_point}: {e}")
                        
                        if os.access(mount_point, os.W_OK):
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

def get_storage_path(data_type: str, subfolder: Optional[str] = None) -> tuple:
    """
    Get storage path for different data types (recordings, tracks, etc.)
    Returns (storage_path, usb_mount) where usb_mount is None if using local storage
    
    Args:
        data_type: Type of data ('recordings', 'tracks', etc.)
        subfolder: Optional subfolder within the data type directory
    
    Returns:
        tuple: (full_path, usb_mount_path_or_none)
    """
    import subprocess
    import time
    
    # Check for USB storage first
    usb_mount = find_usb_storage()
    
    if usb_mount:
        print(f"Using USB storage at {usb_mount} for {data_type}")
        if subfolder:
            storage_path = os.path.join(usb_mount, 'streamerData', data_type, subfolder)
        else:
            storage_path = os.path.join(usb_mount, 'streamerData', data_type)
        
        # Copy settings and executables to USB
        copy_result = copy_settings_and_executables_to_usb(usb_mount)
        
        # Force sync all data to USB drive
        print("Syncing data to USB drive...")
        try:
            subprocess.run(['sync'], check=True)
            time.sleep(2)  # Give extra time for exFAT filesystem
            print("USB sync completed successfully")
        except Exception as e:
            print(f"Warning: USB sync failed: {e}")
            
        return storage_path, usb_mount
    else:
        print(f"No USB storage found, using local disk for {data_type}")
        if subfolder:
            storage_path = os.path.join(STREAMER_DATA_DIR, data_type, subfolder)
        else:
            storage_path = os.path.join(STREAMER_DATA_DIR, data_type)
            
        return storage_path, None


def cleanup_pidfile(pidfile_path: str, cleanup_callback=None, sync_usb: bool = True, logger=None):
    """
    Generic PID file cleanup function with optional USB sync and custom cleanup
    
    Args:
        pidfile_path: Path to the PID file to remove
        cleanup_callback: Optional function to call before removing PID file
        sync_usb: Whether to perform USB sync (default: True)
        logger: Optional logger for messages (uses print if None)
    """
    def log_message(msg: str, level: str = "info"):
        if logger:
            if level == "info":
                logger.info(msg)
            elif level == "warning":
                logger.warning(msg)
            elif level == "debug":
                logger.debug(msg)
        else:
            print(msg)
    
    # Perform USB sync if requested
    if sync_usb:
        log_message("Syncing all data to disk (including USB drives)...")
        try:
            import subprocess
            subprocess.run(['sync'], check=True)
            time.sleep(2)  # Give extra time for exFAT/USB
            log_message("Sync completed. It is now safe to remove the USB drive.")
        except Exception as e:
            log_message(f"Warning: Final sync failed: {e}", "warning")
    
    # Call custom cleanup callback if provided
    if cleanup_callback:
        try:
            cleanup_callback()
        except Exception as e:
            log_message(f"Warning: Cleanup callback failed: {e}", "warning")
    
    # Remove PID file
    try:
        if os.path.exists(pidfile_path):
            os.remove(pidfile_path)
            log_message(f"Removed PID file: {pidfile_path}")
    except Exception as e:
        log_message(f"Warning: Could not remove PID file on exit: {e}", "warning")


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


# Recording and file management functions

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


def get_video_duration_mediainfo(path):
    """Fast duration extraction using pymediainfo"""
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


def add_files_from_path(recording_files, path, source_label="", location="Local", active_only=False):
    """
    Helper function to add files from a given path. Appends to the passed-in recording_files list.
    
    Args:
        recording_files: List to append files to
        path: Directory path to scan for files
        source_label: Label prefix for file display names
        location: Location identifier (e.g., "Local", "USB")
        active_only: If True, only include files that are currently being recorded
    """
    active_pid, active_file = get_active_recording_info()
    if os.path.isdir(path):
        files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
        files.sort(key=lambda f: os.path.getmtime(os.path.join(path, f)), reverse=True)
        for f in files:
            file_path = os.path.join(path, f)
            file_size = os.path.getsize(file_path)
            display_name = f"{source_label}{f}" if source_label else f
            is_active = (active_file is not None and os.path.abspath(file_path) == os.path.abspath(active_file) and is_pid_running(active_pid))
            
            # Skip non-active files if active_only is True
            if active_only and not is_active:
                continue
                
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

def get_hardwareid():
    """
    Get the hardware ID using the same logic as the installation script.
    First tries to get CPU serial from /proc/cpuinfo, then falls back to MAC address.
    """
    try:
        # First try to get CPU serial from /proc/cpuinfo (Raspberry Pi)
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if line.startswith('Serial'):
                    serial = line.split(':')[1].strip()
                    if serial and serial != '0000000000000000':
                        return serial
    except (FileNotFoundError, IOError, IndexError):
        pass
    
    # Fallback to MAC address
    try:
        for interface_path in glob.glob('/sys/class/net/*/address'):
            try:
                with open(interface_path, 'r') as f:
                    mac = f.read().strip()
                    if mac and mac != '00:00:00:00:00:00':
                        # Remove colons like in the installation script
                        return mac.replace(':', '')
            except (IOError, OSError):
                continue
    except Exception:
        pass
    
    # Final fallback - generate based on timestamp like installation script
    return f"fallback-{int(time.time())}"

def get_app_version():
    """Get the application version based on latest file modification time"""
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

def load_wifi_settings():
    """Load WiFi settings from wifi.json with defaults"""
    wifi_path = os.path.join(STREAMER_DATA_DIR, 'wifi.json')
    
    # Default WiFi settings (wifi_mode is now determined from hardware state)
    wifi_defaults = {
        "hotspot_ssid": get_default_hotspot_ssid(),
        "hotspot_password": "rpistreamer123",
        "hotspot_channel": 6,
        "hotspot_ip": "192.168.4.1",
        "manual_ssid": "",  # User's manual WiFi SSID
        "manual_password": ""  # User's manual WiFi password
    }
    
    if os.path.exists(wifi_path):
        try:
            with open(wifi_path, 'r') as f:
                wifi_settings = json.load(f)
                # Merge with defaults to ensure all keys exist
                for key, default_value in wifi_defaults.items():
                    if key not in wifi_settings:
                        wifi_settings[key] = default_value
                return wifi_settings
        except (json.JSONDecodeError, ValueError):
            # Silent error handling in utils
            pass
    
    return wifi_defaults

def save_wifi_settings(wifi_settings):
    """Save WiFi settings to wifi.json"""
    wifi_path = os.path.join(STREAMER_DATA_DIR, 'wifi.json')
    try:
        os.makedirs(STREAMER_DATA_DIR, exist_ok=True)
        with open(wifi_path, 'w') as f:
            json.dump(wifi_settings, f, indent=2)
    except Exception:
        # Silent error handling in utils - let calling code handle errors
        pass

def get_wifi_mode_status():
    """Get current WiFi mode and connection status using simplified NetworkManager commands"""
    wifi_settings = load_wifi_settings()
    
    # Default values
    wifi_mode = 'client'
    hotspot_active = False
    current_ip = None
    current_ssid = None
    wifi_connected = False
    
    try:
        # Get connection name and IP address directly
        result = subprocess.run(['nmcli', '-g', 'GENERAL.CONNECTION,IP4.ADDRESS', 'device', 'show', 'wlan0'], 
                              capture_output=True, text=True, timeout=5)
        
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                current_ssid = lines[0].strip() if lines[0].strip() != '--' else None
                ip_with_mask = lines[1].strip() if lines[1].strip() != '--' else None
                
                if current_ssid and ip_with_mask:
                    wifi_connected = True
                    current_ip = ip_with_mask.split('/')[0]  # Remove subnet mask
                    
                    # Check WiFi mode using the connection name
                    try:
                        mode_result = subprocess.run(['nmcli', '-g', '802-11-wireless.mode', 
                                                    'connection', 'show', current_ssid], 
                                                   capture_output=True, text=True, timeout=3)
                        
                        if mode_result.returncode == 0:
                            mode_output = mode_result.stdout.strip().lower()
                            if 'ap' in mode_output:
                                wifi_mode = 'hotspot'
                                hotspot_active = True
                            else:
                                wifi_mode = 'client'
                                hotspot_active = False
                                
                    except Exception:
                        # Silent fallback - don't print errors in utils
                        pass
            
    except Exception:
        # Silent error handling in utils
        pass
    
    # Additional information based on mode
    client_count = 0
    signal_strength = None
    signal_percent = None
    
    if wifi_connected:
        if hotspot_active:
            # Count connected clients for hotspot mode
            try:
                # Count connected clients using iw command
                clients_result = subprocess.run(['sudo', 'iw', 'dev', 'wlan0', 'station', 'dump'], 
                                              capture_output=True, text=True, timeout=3)
                if clients_result.returncode == 0:
                    client_count = clients_result.stdout.count('Station ')
            except Exception:
                client_count = 0
        else:
            # Client mode - get signal strength using nmcli
            try:
                wifi_result = subprocess.run(['nmcli', '-g', 'IN-USE,SSID,SIGNAL', 'device', 'wifi', 'list'], 
                                           capture_output=True, text=True, timeout=3)
                if wifi_result.returncode == 0:
                    for line in wifi_result.stdout.strip().split('\n'):
                        if line.startswith('*:'):  # Currently connected network
                            parts = line.split(':')
                            if len(parts) >= 3:
                                signal_percent = int(parts[2]) if parts[2].isdigit() else None
                                if signal_percent is not None:
                                    # Convert percentage to approximate dBm (reverse of our earlier calculation)
                                    signal_strength = int((signal_percent * 60 / 100) - 90)
                                break
            except Exception:
                pass
    
    return {
        'mode': wifi_mode,
        'hotspot_active': hotspot_active,
        'current_ip': current_ip,
        'current_ssid': current_ssid,
        'wifi_connected': wifi_connected,
        'hotspot_ssid': wifi_settings['hotspot_ssid'],
        'hotspot_ip': wifi_settings['hotspot_ip'],
        'client_count': client_count,
        'signal_strength': signal_strength,
        'signal_percent': int(signal_percent) if signal_percent is not None else None
    }
