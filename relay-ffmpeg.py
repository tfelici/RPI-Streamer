#!/usr/bin/env python3
import json
import subprocess
import sys
import os
from utils import get_setting

def main():
    if len(sys.argv) < 3:
        print("Usage: python relay-ffmpeg.py <RTSP_PORT> <MTX_PATH>")
        sys.exit(1)
    rtsp_port = sys.argv[1]
    mtx_path = sys.argv[2]
    stream_url = get_setting('stream_url')
    if not stream_url:
        print("Error: 'stream_url' must be set in settings.json")
        sys.exit(1)
    #rtsp_url = f"rtsp://localhost:{rtsp_port}/{mtx_path}"
    rtsp_url = f"srt://localhost:8890?streamid=read:{mtx_path}"

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
    print("Running:", ' '.join(ffmpeg_cmd))
    proc = subprocess.Popen(ffmpeg_cmd)
    proc.wait()

if __name__ == "__main__":
    main()
