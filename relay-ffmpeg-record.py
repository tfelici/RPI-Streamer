#!/usr/bin/env python3
import os
import subprocess
import sys
import time
import signal
from utils import get_storage_path, cleanup_pidfile, copy_settings_and_executables_to_usb

def main():
    if len(sys.argv) < 2:
        print("Usage: python relay-ffmpeg-record.py <MTX_PATH>")
        sys.exit(1)
    mtx_path = sys.argv[1]
    stream_name = mtx_path
    # Make PID file unique per MTX_PATH
    safe_mtx_path = mtx_path.replace('/', '_').replace('\\', '_')
    ACTIVE_PIDFILE = f"/tmp/relay-ffmpeg-record-{safe_mtx_path}.pid"

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
        cleanup_pidfile(ACTIVE_PIDFILE, sync_usb=True)
        print("Exiting gracefully...")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    # Get storage path for recordings
    record_dir, usb_mount = get_storage_path('recordings', stream_name)
    
    # Copy settings and executables to USB if using USB storage
    if usb_mount:
        copy_result = copy_settings_and_executables_to_usb(usb_mount)
        print(f"USB settings copy result: {copy_result}")
    
    os.makedirs(record_dir, exist_ok=True)
    #if the current space used is more than 90% of the total space, delete the oldest files until we are below 80%
    statvfs = os.statvfs(record_dir)
    total_space = statvfs.f_frsize * statvfs.f_blocks
    free_space = statvfs.f_frsize * statvfs.f_bavail
    used_space = total_space - free_space
    used_percent = used_space / total_space * 100
    print(f"Storage path: {record_dir}, Total space: {total_space/(1024*1024*1024):.2f} GB, Used space: {used_space/(1024*1024*1024):.2f} GB ({used_percent:.2f}%)")
    if used_percent > 90:
        print("Storage space used is above 90%, deleting oldest files...")
        files = sorted([os.path.join(record_dir, f) for f in os.listdir(record_dir) if os.path.isfile(os.path.join(record_dir, f))], key=os.path.getctime)
        while used_percent > 80 and files:
            oldest_file = files.pop(0)
            try:
                file_size = os.path.getsize(oldest_file)
                os.remove(oldest_file)
                used_space -= file_size
                used_percent = used_space / total_space * 100
                print(f"Deleted {oldest_file}, new used space: {used_space/(1024*1024*1024):.2f} GB ({used_percent:.2f}%)")
            except Exception as e:
                print(f"Error deleting file {oldest_file}: {e}")
                break
        if used_percent > 90:
            print("Warning: Unable to free enough space, recordings may fail.")
    
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
        cleanup_pidfile(ACTIVE_PIDFILE, sync_usb=True)
        print("ffmpeg exited, restarting in 1 second...")
        time.sleep(1)

if __name__ == "__main__":
    main()
