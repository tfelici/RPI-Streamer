#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import fcntl
import time
import threading

SETTINGS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), '../encoderData/settings.json'))

def get_setting(key, default=None):
    try:
        with open(SETTINGS_FILE, 'r') as f:
            s = json.load(f)
        return s.get(key, default)
    except Exception:
        return default

def start(stream_name):
    if not stream_name:
        print("Error: stream_name must be provided as a command-line argument.")
        return
    framerate = get_setting('framerate', 5)
    resolution = get_setting('resolution', '1280x720')
    crf = get_setting('crf', 30)
    gop = get_setting('gop', 5)  # Keyframe interval in seconds
    vbitrate = get_setting('vbitrate', 1000)  # in kbps
    ar = get_setting('ar', 8000)  # Audio sample rate in Hz
    abitrate = get_setting('abitrate', '128k')  # Audio bitrate, default to 128k
    static_img = os.path.join(os.path.dirname(__file__), 'no_camera.png')

    def find_usb_audio_device():
        """
        Detect and return the audio input device as specified in settings (audio_input),
        or auto-detect the first available USB/Headset/Microphone audio capture device and its supported channel count.
        Returns (device_str, channels) or (None, 2) if not found.
        """
        audio_input = get_setting('audio_input', None)
        def parse_device_line(line):
            parts = line.split()
            if 'card' in parts and 'device' in parts:
                card_idx = parts.index('card') + 1
                device_idx = parts.index('device') + 1
                cardnum = parts[card_idx].replace(':', '')
                devnum = parts[device_idx].replace(':', '')
                if cardnum.isdigit() and devnum.isdigit():
                    return f'hw:{cardnum},{devnum}'
            return None

        def get_channels(device_str):
            try:
                try:
                    params = subprocess.check_output(
                        ['arecord', '-D', device_str, '--dump-hw-params'],
                        stderr=subprocess.STDOUT, text=True, timeout=2
                    )
                except subprocess.CalledProcessError as e:
                    params = e.output
                except Exception as e:
                    print(f"Error running arecord for {device_str}: {e}")
                    return 2
                for line in params.splitlines():
                    try:
                        line = line.strip()
                        if line.startswith('CHANNELS:'):
                            val = line[len('CHANNELS:'):].replace('[', '').replace(']', '').strip()
                            nums = [int(x) for x in val.split() if x.isdigit()]
                            if nums:
                                return min(nums)
                    except Exception:
                        continue
                return 2
            except Exception as e:
                print(f"Error in get_channels for {device_str}: {e}")
                return 2

        # If user has selected an audio input, use it
        if audio_input:
            # Try to get channels for the selected device string
            return audio_input, get_channels(audio_input)

        try:
            output = subprocess.check_output(['arecord', '-l'], stderr=subprocess.STDOUT, text=True)
            for line in output.splitlines():
                if 'USB' in line or 'Headset' in line or 'Microphone' in line:
                    device_str = parse_device_line(line)
                    if device_str:
                        return device_str, get_channels(device_str)
            for line in output.splitlines():
                if 'card' in line and 'device' in line:
                    device_str = parse_device_line(line)
                    if device_str:
                        return device_str, get_channels(device_str)
        except Exception:
            pass
        return None, 2  # fallback to stereo

    def find_video_device():
        # Check for the first available /dev/video* device
        for i in range(10):
            dev = f'/dev/video{i}'
            if os.path.exists(dev):
                return dev
        return None

    def build_ffmpeg_cmd(video_device, audio_device_tuple):
        video_opts = []
        audio_opts = []

        # Test for h264_v4l2m2m compatibility
        vcodec = 'h264_v4l2m2m'
        if video_device:
            # Try probing h264_v4l2m2m support
            video_opts = [
                '-f', 'v4l2',
                '-framerate', str(framerate),
                '-video_size', str(resolution),
                '-i', video_device
            ]
            try:
                probe_cmd = ['ffmpeg', '-hide_banner'] + video_opts + ['-vcodec', 'h264_v4l2m2m', '-f', 'null', '-t', '1', '-y', '/dev/null']
                subprocess.check_output(probe_cmd, stderr=subprocess.STDOUT, timeout=5)
            except Exception:
                vcodec = 'libx264'
                print("h264_v4l2m2m not supported, falling back to libx264")
        else:
            video_opts = [
                '-stream_loop', '-1',
                '-re',
                '-i', static_img
            ]
            vcodec = 'libx264'

        if audio_device_tuple:
            audio_device, audio_channels = audio_device_tuple
            audio_opts = [
                '-f', 'alsa',
                '-ac', str(audio_channels),
                '-i', audio_device
            ]
        else:
            audio_opts = ['-an']

        output_opts = [
            '-vcodec', vcodec,
            '-preset', 'ultrafast',
            '-crf', str(crf),
            '-b:v', f'{vbitrate}k',
            '-tune', 'zerolatency',
            '-g', str(gop),
            '-keyint_min', '1',
            '-acodec', 'libopus',
            '-ar', str(ar),
            '-b:a', str(abitrate),
            '-f', 'rtsp',
            f'rtsp://localhost:8554/{stream_name}'
        ]

        return ['ffmpeg'] + video_opts + audio_opts + output_opts

    # Shared state
    state = {'video': None, 'audio': None, 'proc': None, 'should_restart': False}
    check_interval = 2  # seconds

    def monitor_devices():
        prev_video_device = None
        prev_audio_device = None
        while True:
            video_device = find_video_device()
            audio_device = find_usb_audio_device()
            # Restart if device path changes (including device added/removed)
            if video_device != prev_video_device or audio_device != prev_audio_device:
                print(f"Device change detected. Video: {video_device}, Audio: {audio_device}")
                state['should_restart'] = True
                if state['proc'] and state['proc'].poll() is None:
                    state['proc'].terminate()
            prev_video_device = video_device
            prev_audio_device = audio_device
            time.sleep(check_interval)

    t = threading.Thread(target=monitor_devices, daemon=True)
    t.start()

    while True:
        video_device = find_video_device()
        audio_device = find_usb_audio_device()
        cmd = build_ffmpeg_cmd(
            video_device,
            audio_device
        )
        print("Running:", ' '.join(cmd))
        proc = subprocess.Popen(cmd)
        state['proc'] = proc
        proc.wait()
        if not state['should_restart']:
            print(f"ffmpeg exited with code {proc.returncode}. Restarting in {check_interval} seconds...")
            time.sleep(check_interval)
        state['should_restart'] = False


def main():
    stream_name = sys.argv[1] if len(sys.argv) > 1 else None
    if not stream_name:
        print("Error: stream_name must be provided as a command-line argument.")
        return
    start(stream_name)
if __name__ == "__main__":
    main()
