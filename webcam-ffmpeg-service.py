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
    static_img = os.path.join(os.path.dirname(__file__), 'no_camera.png')

    def find_usb_audio_device():
        """
        Return (device_str, channels) if audio_input is set, else (None, 2).
        """
        audio_input = get_setting('audio_input', None)
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
        if audio_input:
            device_str = audio_input
            return device_str, get_channels(device_str)
        return None, 2

    def find_video_device():
        """
        Return video_input if set, else None.
        """
        video_input = get_setting('video_input', None)
        if video_input:
            return video_input
        return None

    def build_ffmpeg_cmd(video_device, audio_device_tuple):
        video_opts = []
        audio_opts = []
        vcodec = 'h264_v4l2m2m'
        if video_device:
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
        if audio_device_tuple and audio_device_tuple[0]:
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
