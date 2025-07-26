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
    
    static_img = os.path.join(os.path.dirname(__file__), 'no_camera.png')

    def find_usb_audio_device():
        """
        Return audio_input if set and available, else None.
        """
        configured_device = get_setting('audio_input')
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
        configured_device = get_setting('video_input')
        if not configured_device:
            return None
        
        # Check if the configured device is in the list of available devices
        available_devices = list_video_inputs()
        for device in available_devices:
            if device['id'] == configured_device:
                return configured_device        
        # Device not found in available devices
        return None

    def build_gstreamer_cmd(video_device, audio_device, framerate_val, resolution_val, crf_val, gop_val, vbitrate_val, ar_val, abitrate_val, volume_val, stream_name=None):
        # Switch to control output format: True for WHIP, False for SRT
        usewhip = False

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

        def probe_hardware_encoder_pars(crf_val, gop_val, vbitrate_val):
            # Actually test v4l2h264enc by running a minimal pipeline with simplified controls
            encoder = 'v4l2h264enc'
            test_cmd = [
                'gst-launch-1.0',
                'videotestsrc', 'num-buffers=10', '!',
                'video/x-raw,width=320,height=240,framerate=5/1', '!',
                f'{encoder}', '!',
                'video/x-h264,profile=baseline', '!',
                'fakesink'
            ]
            try:
                result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    print(f"Hardware encoder {encoder} test succeeded, using it.")
                    return f'{encoder}'
                else:
                    print(f"Hardware encoder {encoder} test failed, return code {result.returncode}.")
            except Exception as e:
                print(f"{encoder} test pipeline failed: {e}")
            print("Hardware encoder not supported or failed, falling back to x264enc")
            if crf_val not in (None, '', 0, '0'):
                # Use CRF mode for x264enc
                return f'x264enc tune=zerolatency quantizer={crf_val} speed-preset=ultrafast key-int-max={gop_val}'
            else:
                # Use bitrate mode for x264enc
                return f'x264enc tune=zerolatency bitrate={vbitrate_val} speed-preset=ultrafast key-int-max={gop_val}'

        # Parse resolution
        width, height = map(int, str(resolution_val).split('x'))
        
        if video_device and audio_device:
            # Both video and audio available
            encoder_pars = probe_hardware_encoder_pars(crf_val, gop_val, vbitrate_val)
            if usewhip:
                video_part = f'v4l2src device={video_device} ! image/jpeg,width={width},height={height},framerate={framerate_val}/1 ! jpegdec ! videoconvert ! video/x-raw,format=I420 ! {encoder_pars} ! video/x-h264,profile=baseline ! queue ! sink.'
                audio_part = f'alsasrc device=plug{audio_device} ! audioresample ! audio/x-raw,rate={ar_val} ! opusenc bitrate={int(abitrate_val.rstrip("k")) * 1000} ! queue ! sink.'
                output_part = f'whipclientsink name=sink signaller::whip-endpoint=http://localhost:8889/{stream_name}/whip stun-server=stun://stun.l.google.com:19302 congestion-control=disabled'
            else:
                video_part = f'v4l2src device={video_device} ! image/jpeg,width={width},height={height},framerate={framerate_val}/1 ! jpegdec ! videoconvert ! video/x-raw,format=I420 ! {encoder_pars} ! video/x-h264,profile=baseline ! mux.'
                audio_part = f'alsasrc device=plug{audio_device} ! audioresample ! audio/x-raw,rate={ar_val} ! opusenc bitrate={int(abitrate_val.rstrip("k")) * 1000} ! mux.'
                output_part = f'mpegtsmux name=mux ! srtsink uri="srt://localhost:8890?streamid=publish:{stream_name}&pkt_size=1316"'
            
            pipeline = f'{video_part}   {audio_part}   {output_part}'
        elif video_device and not audio_device:
            # Video only - no audio
            encoder_pars = probe_hardware_encoder_pars(crf_val, gop_val, vbitrate_val)
            # Streaming only
            if usewhip:
                pipeline = f'v4l2src device={video_device} ! image/jpeg,width={width},height={height},framerate={framerate_val}/1 ! jpegdec ! videoconvert ! video/x-raw,format=I420 ! {encoder_pars} ! video/x-h264,profile=baseline ! whipclientsink signaller::whip-endpoint=http://localhost:8889/{stream_name}/whip stun-server=stun://stun.l.google.com:19302 congestion-control=disabled'
            else:
                pipeline = f'v4l2src device={video_device} ! image/jpeg,width={width},height={height},framerate={framerate_val}/1 ! jpegdec ! videoconvert ! video/x-raw,format=I420 ! {encoder_pars} ! video/x-h264,profile=baseline ! mpegtsmux ! srtsink uri="srt://localhost:8890?streamid=publish:{stream_name}&pkt_size=1316"'
        elif audio_device and not video_device:
            # Audio with static image placeholder
            encoder_pars = probe_hardware_encoder_pars(None, gop_val, 100)  # Use low bitrate for static image
            # Streaming only
            if usewhip:
                video_part = f'multifilesrc location={static_img} loop=true ! pngdec ! imagefreeze ! videoscale ! video/x-raw,width={width},height={height} ! videorate ! video/x-raw,framerate={framerate_val}/1 ! videoconvert ! video/x-raw,format=I420 ! {encoder_pars} ! video/x-h264,profile=baseline ! queue ! sink.'
                audio_part = f'alsasrc device=plug{audio_device} ! audioresample ! audio/x-raw,rate={ar_val} ! opusenc bitrate={int(abitrate_val.rstrip("k")) * 1000} ! queue ! sink.'
                output_part = f'whipclientsink name=sink signaller::whip-endpoint=http://localhost:8889/{stream_name}/whip stun-server=stun://stun.l.google.com:19302 congestion-control=disabled'
            else:
                video_part = f'multifilesrc location={static_img} loop=true ! pngdec ! imagefreeze ! videoscale ! video/x-raw,width={width},height={height} ! videorate ! video/x-raw,framerate={framerate_val}/1 ! videoconvert ! video/x-raw,format=I420 ! {encoder_pars} ! video/x-h264,profile=baseline ! mux.'
                audio_part = f'alsasrc device=plug{audio_device} ! audioresample ! audio/x-raw,rate={ar_val} ! opusenc bitrate={int(abitrate_val.rstrip("k")) * 1000} ! mux.'
                output_part = f'mpegtsmux name=mux ! srtsink uri="srt://localhost:8890?streamid=publish:{stream_name}&pkt_size=1316"'
            pipeline = f'{video_part}   {audio_part}   {output_part}'
        else:
            # Neither video nor audio - static image placeholder only
            encoder_pars = probe_hardware_encoder_pars(None, gop_val, 100) # Use low bitrate for static image
            if usewhip:
                pipeline = f'multifilesrc location={static_img} loop=true ! pngdec ! imagefreeze ! videoscale ! video/x-raw,width={width},height={height} ! videorate ! video/x-raw,framerate={framerate_val}/1 ! videoconvert ! video/x-raw,format=I420 ! {encoder_pars} ! video/x-h264,profile=baseline ! whipclientsink signaller::whip-endpoint=http://localhost:8889/{stream_name}/whip stun-server=stun://stun.l.google.com:19302 congestion-control=disabled'
            else:
                pipeline = f'multifilesrc location={static_img} loop=true ! pngdec ! imagefreeze ! videoscale ! video/x-raw,width={width},height={height} ! videorate ! video/x-raw,framerate={framerate_val}/1 ! videoconvert ! video/x-raw,format=I420 ! {encoder_pars} ! video/x-h264,profile=baseline ! mpegtsmux ! srtsink uri="srt://localhost:8890?streamid=publish:{stream_name}&pkt_size=1316"'
        
        # Clean up the pipeline string and split into arguments
        pipeline_clean = ' '.join(pipeline.split())  # Remove extra whitespace and line breaks
        
        # Build final GStreamer command
        cmd = ['gst-launch-1.0'] + pipeline_clean.split()
        
        # Set up environment for GStreamer with WHIP plugin path
        env = os.environ.copy()
        env['GST_PLUGIN_PATH'] = '/usr/local/lib/gstreamer-1.0'

        return cmd, env

    def build_ffmpeg_cmd(video_device, audio_device, framerate_val, resolution_val, crf_val, gop_val, vbitrate_val, ar_val, abitrate_val, volume_val, stream_name=None):
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
        output_opts = [
            '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
            '-f', 'mpegts', f'srt://localhost:8890?streamid=publish:{stream_name}&pkt_size=1316']
        cmd = ['ffmpeg'] + video_opts + audio_opts + base_opts + output_opts
        return cmd, None

    state = {'video': None, 'audio': None, 'proc': None, 'should_restart': False}
    check_interval = 2

    def monitor_devices():
        prev_video_device = None
        prev_audio_device = None
        prev_settings = {
            'framerate': get_setting('framerate'),
            'resolution': get_setting('resolution'),
            'crf': get_setting('crf'),
            'gop': get_setting('gop'),
            'vbitrate': get_setting('vbitrate'),
            'ar': get_setting('ar'),
            'abitrate': get_setting('abitrate'),
            'volume': get_setting('volume'),
            'use_gstreamer': get_setting('use_gstreamer')
        }
        
        while True:
            video_device = find_video_device()
            audio_device = find_usb_audio_device()
            
            # Check for current settings
            current_settings = {
                'framerate': get_setting('framerate'),
                'resolution': get_setting('resolution'),
                'crf': get_setting('crf'),
                'gop': get_setting('gop'),
                'vbitrate': get_setting('vbitrate'),
                'ar': get_setting('ar'),
                'abitrate': get_setting('abitrate'),
                'volume': get_setting('volume'),
                'use_gstreamer': get_setting('use_gstreamer')
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

    while True:
        # Get current settings each iteration
        current_framerate = get_setting('framerate')
        current_resolution = get_setting('resolution')
        current_crf = get_setting('crf')
        current_gop = get_setting('gop')
        current_vbitrate = get_setting('vbitrate')
        current_ar = get_setting('ar')
        current_abitrate = get_setting('abitrate')
        current_volume = get_setting('volume')

        video_device = find_video_device()
        audio_device = find_usb_audio_device()

        # Get streaming engine preference
        use_gstreamer = get_setting('use_gstreamer')

        # Build command with current settings using selected engine
        if use_gstreamer:
            cmd, env = build_gstreamer_cmd(
                video_device, audio_device, current_framerate, current_resolution,
                current_crf, current_gop, current_vbitrate, current_ar,
                current_abitrate, current_volume, stream_name
            )
            print("Using GStreamer pipeline")
        else:
            cmd, env = build_ffmpeg_cmd(
                video_device, audio_device, current_framerate, current_resolution,
                current_crf, current_gop, current_vbitrate, current_ar,
                current_abitrate, current_volume, stream_name
            )
            print("Using FFmpeg pipeline")
        
        print("Running:", ' '.join(str(x) for x in cmd))
        proc = subprocess.Popen(cmd, env=env)
        state['proc'] = proc

        proc.wait()

        if not state['should_restart']:
            print(f"ffmpeg exited with code {proc.returncode}. Restarting in {check_interval} seconds...")
            time.sleep(check_interval)
        else:
            print("Restarting ffmpeg due to device or settings change...")
        state['should_restart'] = False


def main():
    stream_name = sys.argv[1] if len(sys.argv) > 1 else None
    if not stream_name:
        print("Error: stream_name must be provided as a command-line argument.")
        return
    start(stream_name)

if __name__ == "__main__":
    main()
