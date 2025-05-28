#!/usr/bin/env python3
import os
import subprocess
import sys
import time
import threading
from utils import list_audio_inputs, list_video_inputs, get_setting


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
    volume = get_setting('volume', 100)  # Audio input volume percent
    static_img = os.path.join(os.path.dirname(__file__), 'no_camera.png')

    def find_usb_audio_device():
        """
        Return audio_input if set and available, else None.
        """
        configured_device = get_setting('audio_input', None)
        if not configured_device:
            return None
          # Check if the configured device is in the list of available devices
        available_devices = list_audio_inputs()
        for device in available_devices:
            if device['id'] == configured_device:
                return configured_device
        
        # Device not found in available devices
        return None
    
    def find_video_device():
        """
        Return video_input if set and available, else None.
        """
        configured_device = get_setting('video_input', None)
        if not configured_device:
            return None
        
        # Check if the configured device is in the list of available devices
        available_devices = list_video_inputs()
        for device in available_devices:
            if device['id'] == configured_device:
                return configured_device
        
        # Device not found in available devices
        return None

    def build_ffmpeg_cmd(video_device, audio_device):
        # Set hardware volume using amixer if audio_device and volume are set
        if audio_device and volume is not None:
            import re
            m = re.search(r'(?:plug)?hw:(\d+)', str(audio_device))
            if m:
                cardnum = m.group(1)
                try:
                    subprocess.run([
                        'amixer',
                        '-c', str(cardnum),
                        'sset', 'Mic', f'{volume}%'
                    ], check=True)
                except Exception as e:
                    print(f"Warning: Failed to set mic volume with amixer: {e}")

        def probe_hardware_encoder(video_opts):
            # Try h264_v4l2m2m first (RPi hardware encoder)
            try:
                probe_cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error'] + video_opts + [
                    '-vcodec', 'h264_v4l2m2m', 
                    '-pix_fmt', 'yuv420p',
                    '-f', 'null', 
                    '-frames:v', '1', 
                    '-t', '1', 
                    '-y', 'NUL' if os.name == 'nt' else '/dev/null'
                ]
                result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    print("Using hardware encoder: h264_v4l2m2m")
                    return 'h264_v4l2m2m'
            except Exception as e:
                print(f"h264_v4l2m2m probe failed: {e}")

            # Check if hardware encoder devices exist
            hw_devices = ['/dev/video10', '/dev/video11', '/dev/video12']
            available_hw = [dev for dev in hw_devices if os.path.exists(dev)]
            if available_hw:
                print(f"Hardware encoder devices found: {available_hw}, but probing failed")
            else:
                print("No hardware encoder devices found")
            
            print("Hardware encoders not supported, falling back to libx264")
            return 'libx264'

        if video_device:
            video_opts = [
                '-f', 'v4l2',
                '-framerate', str(framerate),
                '-video_size', str(resolution),
                '-i', video_device
            ]
        else:
            video_opts = [
                '-stream_loop', '-1',
                '-re',
                '-i', static_img
            ]

        vcodec = probe_hardware_encoder(video_opts)

        if audio_device:
            # Prepend 'plug' to the audio device string to ensure compatibility with ALSA
            audio_opts = [
                '-f', 'alsa',
                '-i', f'plug{audio_device}'
            ]
        else:
            audio_opts = ['-an']

        output_opts = [
            '-vcodec', vcodec,
            '-preset', 'ultrafast',
            '-pix_fmt', 'yuv420p',
            '-crf', str(crf),
            '-b:v', f'{vbitrate}k',
            '-tune', 'zerolatency',
            '-g', str(gop),
            '-keyint_min', '1',
            '-acodec', 'libopus',
            '-ar', str(ar),
            '-b:a', str(abitrate),
            '-f', 'rtsp',
            '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
            f'rtsp://localhost:8554/{stream_name}'
        ]
        return ['ffmpeg'] + video_opts + audio_opts + output_opts

    state = {'video': None, 'audio': None, 'proc': None, 'should_restart': False}
    check_interval = 2

    def monitor_devices():
        prev_video_device = None
        prev_audio_device = None
        while True:
            video_device = find_video_device()
            audio_device = find_usb_audio_device()
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
        # If neither device is set, stream the default image with no audio
        cmd = build_ffmpeg_cmd(video_device, audio_device)
        print("Running:", ' '.join(str(x) for x in cmd))
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
