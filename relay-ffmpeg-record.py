#!/usr/bin/env python3
import os
import subprocess
import sys
import time
import signal
from utils import get_setting, find_usb_storage

def main():
    if len(sys.argv) < 2:
        print("Usage: python relay-ffmpeg-record.py <MTX_PATH>")
        sys.exit(1)
    mtx_path = sys.argv[1]
    stream_name = mtx_path
    # Make PID file unique per MTX_PATH
    safe_mtx_path = mtx_path.replace('/', '_').replace('\\', '_')
    ACTIVE_PIDFILE = f"/tmp/relay-ffmpeg-record-{safe_mtx_path}.pid"

    def cleanup_pidfile():
        # Ensure all data is flushed to disk (USB drive)
        print("Syncing all data to disk (including USB drives)...")
        try:
            subprocess.run(['sync'], check=True)
            time.sleep(2)  # Give extra time for exFAT/USB
            print("Sync completed. It is now safe to remove the USB drive.")
        except Exception as e:
            print(f"Warning: Final sync failed: {e}")
        try:
            if os.path.exists(ACTIVE_PIDFILE):
                os.remove(ACTIVE_PIDFILE)
        except Exception as e:
            print(f"Warning: Could not remove active PID file on exit: {e}")

    proc = None  # Track the ffmpeg process
    def handle_exit(signum, frame):
        print(f"Received exit signal {signum}, cleaning up...")
        # Terminate ffmpeg child process if running
        #and wait for it to exit
        if proc and proc.poll() is None:
            print("Killing ffmpeg child process...")
            proc.kill()
            # Wait for the process to exit
            for _ in range(20):  # wait up to 2 seconds
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
        cleanup_pidfile()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    usb_mount = find_usb_storage()
    if usb_mount:
        import shutil
        script_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(script_dir)
        print(f"Recording to USB storage at {usb_mount}")
        record_dir = os.path.join(usb_mount, 'streamerData', 'recordings', stream_name)
        # Copy settings.json
        print("Copying settings.json to USB drive...")
        src_settings = os.path.join(parent_dir, 'streamerData', 'settings.json')
        dst_streamerData = os.path.join(usb_mount, 'streamerData')
        dst_settings = os.path.join(dst_streamerData, 'settings.json')
        os.makedirs(dst_streamerData, exist_ok=True)
        if os.path.exists(src_settings):
            shutil.copy2(src_settings, dst_settings)
            #print final size of the copied file in KB
            size_kb = os.path.getsize(dst_settings) / 1024
            print(f"Copied settings.json ({size_kb:.2f} KB) to USB: {dst_settings}")
        else:
            print(f"Warning: {src_settings} not found, skipping settings.json copy.")
        # Copy executables
        print("Copying executables to USB drive...")
        src_exec_dir = os.path.join(parent_dir, 'executables')
        if os.path.isdir(src_exec_dir):
            for fname in os.listdir(src_exec_dir):
                src_f = os.path.join(src_exec_dir, fname)
                dst_f = os.path.join(usb_mount, fname)
                if os.path.isfile(src_f):
                    shutil.copy2(src_f, dst_f)
                    #print final size of the copied file in MB
                    size_mb = os.path.getsize(dst_f) / (1024 * 1024)
                    print(f"Copied executable {fname} ({size_mb:.2f} MB) to USB: {dst_f}")
        else:
            print(f"Warning: {src_exec_dir} not found, skipping executables copy.")
        
        # Force sync all data to USB drive
        print("Syncing data to USB drive...")
        try:
            subprocess.run(['sync'], check=True)
            time.sleep(2)  # Give extra time for exFAT filesystem
            print("USB sync completed successfully")
        except Exception as e:
            print(f"Warning: USB sync failed: {e}")
    else:
        print("No USB storage found, recording to local disk")
        record_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'streamerData', 'recordings', stream_name))
    os.makedirs(record_dir, exist_ok=True)
    while True:
        timestamp = int(time.time())
        recording_file = os.path.join(record_dir, f"{timestamp}.mp4")

        rtsp_url = f"srt://localhost:8890?streamid=read:{mtx_path}"
        ffmpeg_cmd = [
            'ffmpeg',
            '-i', rtsp_url,
            '-c', 'copy',
            '-f', 'mp4',
            '-movflags', '+faststart+frag_keyframe+empty_moov+default_base_moof',
            '-frag_duration', '2000000',
            recording_file
        ]
        print("Running:", ' '.join(ffmpeg_cmd))
        #start synchronous ffmpeg process
        # This will block until ffmpeg exits, allowing us to handle cleanup
        proc = subprocess.Popen(ffmpeg_cmd)
        # Write active PID file with PID of the main program and recording file
        # This allows us to track the active ffmpeg process and its recording file
        try:
            with open(ACTIVE_PIDFILE, 'w') as f:
                f.write(f"{os.getpid()}:{recording_file}\n")
            print(f"Active PID file written: {ACTIVE_PIDFILE} with PID {os.getpid()} and file {recording_file}")
        except Exception as e:
            print(f"Warning: Could not write active PID file: {e}")
        proc.wait()
        cleanup_pidfile()
        print("ffmpeg exited, restarting in 1 second...")
        time.sleep(1)

if __name__ == "__main__":
    main()
