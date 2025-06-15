#!/usr/bin/env python3
import json
import subprocess
import sys
import os
import signal
from utils import get_setting

def main():
    if len(sys.argv) < 2:
        print("Usage: python relay-ffmpeg.py <MTX_PATH>")
        sys.exit(1)
    mtx_path = sys.argv[1]
    stream_url = get_setting('stream_url')
    if not stream_url:
        print("Error: 'stream_url' must be set in settings.json")
        sys.exit(1)
    #rtsp_url = f"rtsp://localhost:8554/{mtx_path}"
    rtsp_url = f"srt://localhost:8890?streamid=read:{mtx_path}"

    # Save the PID of this process in a file unique to the MTX_PATH
    safe_mtx_path = mtx_path.replace('/', '_').replace('\\', '_')
    ACTIVE_PIDFILE = f"/tmp/relay-ffmpeg-{safe_mtx_path}.pid"
    try:
        with open(ACTIVE_PIDFILE, 'w') as f:
            f.write(str(os.getpid()) + '\n')
        print(f"Active PID file written: {ACTIVE_PIDFILE} with PID {os.getpid()}")
    except Exception as e:
        print(f"Warning: Could not write active PID file: {e}")

    # Determine protocol for -f option
    protocol = None
    if stream_url.startswith('rtsp://') or stream_url.startswith('rtsps://'):
        protocol = 'rtsp'
    elif stream_url.startswith('rtmp://') or stream_url.startswith('rtmps://'):
        protocol = 'flv'
    elif stream_url.startswith('srt://') or stream_url.startswith('udp://'):
        protocol = 'mpegts'
    elif stream_url.startswith('http://') or stream_url.startswith('https://'):
        protocol = 'mpegts'  # for HLS push, but may need to be customized
    elif stream_url.startswith('hls://'):
        protocol = 'hls'
    else:
        print(f"Error: Unsupported protocol in stream_url: {stream_url}")
        sys.exit(1)

    ffmpeg_cmd = [
        'ffmpeg',
        '-i', rtsp_url,
        '-c', 'copy',
        '-f', protocol,
        stream_url
    ]
    
    proc = None  # Track the ffmpeg process
    def handle_exit(signum, frame):
        print(f"Received exit signal {signum}, cleaning up...")
        # Terminate ffmpeg child process if running
        if proc and proc.poll() is None:
            print("killing ffmpeg child process...")
            proc.kill()
            # Wait for the process to exit
            for _ in range(20):  # wait up to 2 seconds
                if proc.poll() is not None:
                    break
                import time
                time.sleep(0.1)
        # Remove PID file
        try:
            if os.path.exists(ACTIVE_PIDFILE):
                os.remove(ACTIVE_PIDFILE)
        except Exception as e:
            print(f"Warning: Could not remove active PID file on exit: {e}")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    while True:
        print("Running:", ' '.join(ffmpeg_cmd))
        proc = subprocess.Popen(ffmpeg_cmd)
        proc.wait()
        print("ffmpeg exited, restarting in 1 second...")
        import time
        time.sleep(1)

if __name__ == "__main__":
    main()
