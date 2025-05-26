import subprocess
import re
import os
import json

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
    Lists all /dev/video* devices, even if busy.
    """
    devices = []
    for i in range(10):
        dev = f'/dev/video{i}'
        if os.path.exists(dev):
            name_path = f'/sys/class/video4linux/video{i}/name'
            label = None
            try:
                with open(name_path, 'r') as f:
                    name = f.read().strip()
                    # Only use the part before ':' if present
                    short = name.split(':', 1)[0].strip() if ':' in name else name
                    if not short:
                        short = f'video{i}'
                    label = f'{short} ({dev})'
            except Exception:
                label = f'video{i} ({dev})'
            devices.append({"id": dev, "label": label})
    return devices

def get_setting(key, default=None):
    """
    Load a single setting from the settings.json file in ../encoderData.
    """
    SETTINGS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), '../encoderData/settings.json'))
    try:
        with open(SETTINGS_FILE, 'r') as f:
            s = json.load(f)
        return s.get(key, default)
    except Exception:
        return default
