#!/usr/bin/env python3
import os
import subprocess
import sys
import time
import signal
from utils import cleanup_pidfile, copy_executables_to_usb, get_setting

def postprocess_recording(recording_file):
    """
    Post-process a recording file to optimize it for playback by adding faststart.
    
    Args:
        recording_file (str): Path to the recording file to process
        
    Returns:
        bool: True if post-processing was successful, False otherwise
    """
    if not os.path.exists(recording_file) or os.path.getsize(recording_file) == 0:
        print(f"Recording file {recording_file} doesn't exist or is empty, skipping post-processing")
        return False
    
    print(f"Post-processing {recording_file} for optimal playback...")
    temp_file = recording_file + ".processing.mp4"
    
    postprocess_cmd = [
        'ffmpeg',
        '-i', recording_file,
        '-c', 'copy',
        '-f', 'mp4',
        '-movflags', '+faststart',
        '-y', temp_file
    ]
    
    try:
        print("Running post-process:", ' '.join(postprocess_cmd))
        postprocess_proc = subprocess.run(postprocess_cmd, capture_output=True, text=True, timeout=60)
        
        if postprocess_proc.returncode == 0 and os.path.exists(temp_file):
            # Replace original with processed version
            os.replace(temp_file, recording_file)
            print(f"Successfully post-processed {recording_file}")
            return True
        else:
            print(f"Post-processing failed: {postprocess_proc.stderr}")
            # Clean up temp file if it exists
            if os.path.exists(temp_file):
                os.remove(temp_file)
            return False
    except subprocess.TimeoutExpired:
        print("Post-processing timed out")
        if os.path.exists(temp_file):
            os.remove(temp_file)
        return False
    except Exception as e:
        print(f"Post-processing error: {e}")
        if os.path.exists(temp_file):
            os.remove(temp_file)
        return False

def main():
    if len(sys.argv) < 2:
        print("Usage: python relay-ffmpeg-record.py <MTX_PATH>")
        sys.exit(1)
    mtx_path = sys.argv[1]
    stream_name = mtx_path
    # Make PID file unique per MTX_PATH
    safe_mtx_path = mtx_path.replace('/', '_').replace('\\', '_')
    ACTIVE_PIDFILE = f"/tmp/relay-ffmpeg-record-{safe_mtx_path}.pid"

    stream_url = get_setting('stream_url')
    if not stream_url:
        print("Error: 'stream_url' must be set in settings.json")
        sys.exit(1)

    # extract rtmpkey and domain from stream_url
    # stream_url is of form srt://gyropilots.org:8890?streamid=publish:<domain>/<rtmpkey>&...
    import re
    match = re.search(r'streamid=publish:([^/]+)/([^&]+)', stream_url)
    if not match:
        print(f"Error: Could not extract domain and rtmpkey from stream_url: {stream_url}")
        sys.exit(1)
    
    domain, rtmpkey = match.groups()
    print(f"Extracted domain: {domain}, rtmpkey: {rtmpkey}")

    proc = None  # Track the ffmpeg process
    current_recording_file = None  # Track the current recording file
    
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
        
        # Post-process the current recording file if it exists
        if current_recording_file:
            print(f"Post-processing interrupted recording...")
            postprocess_recording(current_recording_file)
        
        cleanup_pidfile(ACTIVE_PIDFILE, sync_usb=True)
        print("Exiting gracefully...")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    # Get storage path for recordings - check for USB storage first
    from utils import find_usb_storage, STREAMER_DATA_DIR
    
    usb_mount = find_usb_storage()
    
    if usb_mount:
        print(f"Using USB storage at {usb_mount} for recordings")
        record_dir = os.path.join(usb_mount, 'streamerData', 'recordings', stream_name)
        
        # Copy executables to USB if using USB storage
        copy_result = copy_executables_to_usb(usb_mount)
        print(f"USB executables copy result: {copy_result}")
    else:
        print("No USB storage found, using local disk for recordings")
        record_dir = os.path.join(STREAMER_DATA_DIR, 'recordings', stream_name)
    
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
        recording_file = os.path.join(record_dir, domain, rtmpkey, f"{timestamp}.mp4")
        current_recording_file = recording_file  # Update the global tracking variable
        
        # Ensure the domain/rtmpkey directory structure exists
        os.makedirs(os.path.dirname(recording_file), exist_ok=True)

        rtsp_url = f"srt://localhost:8890?streamid=read:{mtx_path}"
        ffmpeg_cmd = [
            'ffmpeg',
            '-i', rtsp_url,
            '-c', 'copy',
            '-f', 'mp4',
            '-movflags', '+empty_moov+frag_keyframe',
            '-reset_timestamps', '1',
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
        
        # Post-process the recording to ensure proper MP4 structure with faststart
        postprocess_recording(recording_file)
        
        print("ffmpeg exited, restarting in 1 second...")
        time.sleep(1)

if __name__ == "__main__":
    main()
