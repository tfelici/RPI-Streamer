#!/usr/bin/env python3
import os
import subprocess
import sys
import time
import threading
from utils import list_audio_inputs, list_video_inputs, get_setting

def start(stream_name, record_to_disk=False):
    if not stream_name:
        print("Error: stream_name must be provided as a command-line argument.")
        return
    
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
    
    def build_ffmpeg_cmd(video_device, audio_device, framerate_val, resolution_val, crf_val, gop_val, vbitrate_val, ar_val, abitrate_val, volume_val, record_to_disk=False, stream_name=None):
        # Set hardware volume using amixer if audio_device and volume are set
        if audio_device and volume_val is not None:
            import re
            m = re.search(r'(?:plug)?hw:(\d+)', str(audio_device))
            if m:
                cardnum = m.group(1)
                try:
                    subprocess.run([
                        'amixer',
                        '-c', str(cardnum),
                        'sset', 'Mic', f'{volume_val}%'
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
        base_opts = []
        # Four cases: both present, only video, only audio, neither
        if video_device and audio_device:
            # Both present
            video_opts = [
                '-f', 'v4l2',
                '-input_format', 'mjpeg',# Use MJPEG for better compression
                '-framerate', str(framerate_val),
                '-video_size', str(resolution_val),
                '-use_wallclock_as_timestamps', '1',
                '-i', video_device
            ]
            vcodec = probe_hardware_encoder(video_opts)
            audio_opts = [
                '-f', 'alsa',
                '-i', f'plug{audio_device}'
            ]
            if crf_val not in (None, '', 0, '0'):
                base_opts += ['-crf', str(crf_val)]
            base_opts += [
                '-vcodec', vcodec,
                '-preset', 'ultrafast',
                '-pix_fmt', 'yuv420p',
                '-b:v', f'{vbitrate_val}k',
                '-tune', 'zerolatency',
                '-g', str(gop_val),
                '-keyint_min', '1',
                '-acodec', 'libopus',
                '-ar', str(ar_val),
                '-b:a', str(abitrate_val),
            ]
        elif video_device and not audio_device:
            # Only video, generate silent audio
            video_opts = [
                '-f', 'v4l2',
                '-input_format', 'mjpeg', # Use MJPEG for better compression
                '-framerate', str(framerate_val),
                '-video_size', str(resolution_val),
                '-use_wallclock_as_timestamps', '1',
                '-i', video_device
            ]
            vcodec = probe_hardware_encoder(video_opts)
            audio_opts = [
                '-f', 'lavfi',
                '-i', f'anullsrc=r={ar_val}:cl=mono'
            ]
            if crf_val not in (None, '', 0, '0'):
                base_opts += ['-crf', str(crf_val)]
            base_opts += [
                '-shortest',
                '-vcodec', vcodec,
                '-preset', 'ultrafast',
                '-pix_fmt', 'yuv420p',
                '-b:v', f'{vbitrate_val}k',
                '-tune', 'zerolatency',
                '-g', str(gop_val),
                '-keyint_min', '1',
                '-acodec', 'libopus',
                '-ar', str(ar_val),
                '-b:a', str(abitrate_val),
            ]
        elif audio_device and not video_device:
            # Only audio device present: static image + real audio (minimum bitrate settings)
            static_crf = 45      # Maximum CRF for lowest quality
            static_vbitrate = 10 # Very low video bitrate (kbps)
            static_gop = 1       # All keyframes
            static_framerate = 5  # Very low framerate
            video_opts = [
                '-re',
                '-stream_loop', '-1',
                '-framerate', str(static_framerate),
                '-i', static_img
            ]
            vcodec = probe_hardware_encoder(video_opts)
            audio_opts = [
                '-f', 'alsa',
                '-i', f'plug{audio_device}'
            ]
            base_opts = [
                '-shortest',
                '-vcodec', vcodec,
                '-preset', 'ultrafast',
                '-pix_fmt', 'yuv420p',
                '-crf', str(static_crf),
                '-b:v', f'{static_vbitrate}k',
                '-tune', 'zerolatency',
                '-g', str(static_gop),
                '-keyint_min', '1',
                '-acodec', 'libopus',
                '-ar', str(ar_val),
                '-b:a', abitrate_val,
            ]
        elif not video_device and not audio_device:
            # Neither present: static image + silent audio (lowest possible bitrate)
            static_crf = 51      # Maximum CRF for lowest quality
            static_vbitrate = 10 # Very low video bitrate (kbps)
            static_gop = 1       # All keyframes
            static_framerate = 1           # Very low framerate
            static_abitrate = '8k' # Very low audio bitrate
            video_opts = [
                '-re',
                '-stream_loop', '-1',
                '-framerate', str(static_framerate),
                '-i', static_img
            ]
            vcodec = probe_hardware_encoder(video_opts)
            audio_opts = [
                '-f', 'lavfi',
                '-i', f'anullsrc=r={ar_val}:cl=mono'
            ]
            base_opts = [
                '-shortest',
                '-vcodec', vcodec,
                '-preset', 'ultrafast',
                '-pix_fmt', 'yuv420p',
                '-crf', str(static_crf),
                '-b:v', f'{static_vbitrate}k',
                '-tune', 'zerolatency',
                '-g', str(static_gop),
                '-keyint_min', '1',
                '-acodec', 'libopus',
                '-ar', str(ar_val),
                '-b:a', static_abitrate,
            ]
        recording_file = None
        if record_to_disk and stream_name:
            import time
            from utils import find_usb_storage
            usb_mount = find_usb_storage()
            if usb_mount:
                print(f"Recording to USB storage at {usb_mount}")
                record_dir = os.path.join(usb_mount, 'streamerData', 'recordings', stream_name)
            else:
                print("No USB storage found, recording to local disk")
                record_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'streamerData', 'recordings', stream_name))
            os.makedirs(record_dir, exist_ok=True)
            timestamp = int(time.time())
            recording_file = os.path.join(record_dir, f"{timestamp}.mp4")
            # Move scale filter into filter_complex
            output_opts = [
                '-filter_complex', '[0:v:0]scale=trunc(iw/2)*2:trunc(ih/2)*2,split=2[v1][v2];[1:a:0]asplit=2[a1][a2]',
                # SRT output
                '-map', '[v1]', '-map', '[a1]'
            ] + base_opts + [
                '-f', 'mpegts', f'srt://localhost:8890?streamid=publish:{stream_name}&pkt_size=1316',
                # MP4 output
                '-map', '[v2]', '-map', '[a2]'
            ] + base_opts + [
                '-f', 'mp4', recording_file
            ]
        else:
            output_opts = ['-f', 'mpegts', f'srt://localhost:8890?streamid=publish:{stream_name}&pkt_size=1316']
        cmd = ['ffmpeg'] + video_opts + audio_opts + base_opts + output_opts
        return cmd, recording_file

    state = {'video': None, 'audio': None, 'proc': None, 'should_restart': False}
    check_interval = 2

    def monitor_devices():
        prev_video_device = None
        prev_audio_device = None
        prev_settings = {
            'framerate': get_setting('framerate', 5),
            'resolution': get_setting('resolution', '1280x720'),
            'crf': get_setting('crf', 30),
            'gop': get_setting('gop', 5),
            'vbitrate': get_setting('vbitrate', 1000),
            'ar': get_setting('ar', 8000),
            'abitrate': get_setting('abitrate', '128k'),
            'volume': get_setting('volume', 100)
        }
        
        while True:
            video_device = find_video_device()
            audio_device = find_usb_audio_device()
            
            # Check for current settings
            current_settings = {
                'framerate': get_setting('framerate', 5),
                'resolution': get_setting('resolution', '1280x720'),
                'crf': get_setting('crf', 30),
                'gop': get_setting('gop', 5),
                'vbitrate': get_setting('vbitrate', 1000),
                'ar': get_setting('ar', 8000),
                'abitrate': get_setting('abitrate', '128k'),
                'volume': get_setting('volume', 100)
            }
            
            # Check for device changes
            device_changed = (video_device != prev_video_device or audio_device != prev_audio_device)
            
            # Check for settings changes
            settings_changed = current_settings != prev_settings
            
            if device_changed or settings_changed:
                if device_changed:
                    print(f"Device change detected. Video: {video_device}, Audio: {audio_device}")
                if settings_changed:
                    changed_settings = [k for k in current_settings if current_settings[k] != prev_settings[k]]
                    print(f"Settings change detected: {changed_settings}")
                    print(f"Previous: {[(k, prev_settings[k]) for k in changed_settings]}")
                    print(f"Current: {[(k, current_settings[k]) for k in changed_settings]}")
                
                state['should_restart'] = True
                if state['proc'] and state['proc'].poll() is None:
                    state['proc'].terminate()
                    
            prev_video_device = video_device
            prev_audio_device = audio_device
            prev_settings = current_settings.copy()
            time.sleep(check_interval)

    # Start monitoring thread
    t = threading.Thread(target=monitor_devices, daemon=True)
    t.start()

    ACTIVE_PIDFILE = "/tmp/webcam-ffmpeg-service.pid"
    while True:
        # Get current settings each iteration
        current_framerate = get_setting('framerate', 5)
        current_resolution = get_setting('resolution', '1280x720')
        current_crf = get_setting('crf', 30)
        current_gop = get_setting('gop', 5)
        current_vbitrate = get_setting('vbitrate', 1000)
        current_ar = get_setting('ar', 8000)
        current_abitrate = get_setting('abitrate', '128k')
        current_volume = get_setting('volume', 100)

        video_device = find_video_device()
        audio_device = find_usb_audio_device()

        # Build command with current settings
        cmd, recording_file = build_ffmpeg_cmd(
            video_device, audio_device, current_framerate, current_resolution,
            current_crf, current_gop, current_vbitrate, current_ar,
            current_abitrate, current_volume, record_to_disk, stream_name
        )

        print("Running:", ' '.join(str(x) for x in cmd))
        proc = subprocess.Popen(cmd)
        state['proc'] = proc

        # Write active PID file if recording to disk
        if record_to_disk and recording_file:
            try:
                with open(ACTIVE_PIDFILE, 'w') as f:
                    f.write(f"{proc.pid}:{recording_file}\n")
                print(f"Active PID file written: {ACTIVE_PIDFILE} with PID {proc.pid} and file {recording_file}")
            except Exception as e:
                print(f"Warning: Could not write active PID file: {e}")
        else:
            print("Not writing active PID file since not recording to disk")
        # Wait for ffmpeg to exit
        proc.wait()

        # Remove active PID file if it matches this process
        if record_to_disk and recording_file:
            try:
                # Only remove if the PID and file match
                if os.path.exists(ACTIVE_PIDFILE):
                    with open(ACTIVE_PIDFILE, 'r') as f:
                        line = f.read().strip()
                        if line:
                            pid_str, file_str = line.split(':', 1)
                            if str(proc.pid) == pid_str and file_str == recording_file:
                                os.remove(ACTIVE_PIDFILE)
            except Exception as e:
                print(f"Warning: Could not remove active PID file: {e}")

        if not state['should_restart']:
            print(f"ffmpeg exited with code {proc.returncode}. Restarting in {check_interval} seconds...")
            time.sleep(check_interval)
        else:
            print("Restarting ffmpeg due to device or settings change...")
        state['should_restart'] = False


def main():
    stream_name = sys.argv[1] if len(sys.argv) > 1 else None
    record_to_disk = False
    if len(sys.argv) > 2:
        val = sys.argv[2].lower()
        if val in ('1', 'true', 'yes', 'on'):
            record_to_disk = True
    if not stream_name:
        print("Error: stream_name must be provided as a command-line argument.")
        return
    start(stream_name, record_to_disk)

if __name__ == "__main__":
    main()
