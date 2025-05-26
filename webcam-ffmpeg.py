#!/usr/bin/env python3
# This script (webcam-ffmpeg.py) is a process manager for video streaming on a Linux system. It controls the lifecycle of a background streaming process (delegated to webcam-ffmpeg-service.py).
#
# Main features:
# - Start streaming: Launches webcam-ffmpeg-service.py as a subprocess with the stream name, ensuring only one instance runs at a time. Tracks the process using a PID file.
# - Stop streaming: Reads the PID file, verifies the process is running, and sends a termination signal to stop it. Cleans up the PID file.
# - Uses file locking (fcntl) to prevent race conditions when reading/writing the PID file.
# - Can be run as 'python webcam-ffmpeg.py start [stream_name]' or 'python webcam-ffmpeg.py stop [stream_name]'. 
# - Prints status and warnings to the console for user feedback.
#
# In summary, this script acts as a safe, single-instance launcher and stopper for a video streaming service, delegating the actual streaming work to another Python script.

import json
import os
import subprocess
import sys
import fcntl
import time

PIDFILE = "/tmp/webcam-ffmpeg.pid"

def is_pid_running(pid):
    return os.path.exists(f"/proc/{pid}")

def read_pidfile(lockfile):
    lockfile.seek(0)
    content = lockfile.read().strip()
    if content:
        try:
            pid, running_stream = content.split(':', 1)
            return int(pid), running_stream.strip()
        except Exception:
            return int(content), None
    return None, None

def write_pidfile(lockfile, pid, stream_name):
    lockfile.seek(0)
    lockfile.truncate(0)
    lockfile.write(f"{pid}:{stream_name}\n")
    lockfile.flush()

def clear_pidfile(lockfile):
    lockfile.seek(0)
    lockfile.truncate(0)
    lockfile.flush()

def start(stream_name):
    with open(PIDFILE, 'a+') as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        pid, running_stream = read_pidfile(lockfile)
        if pid and is_pid_running(pid):
            print(f"webcam-ffmpeg-service is already running (PID {pid}, stream {running_stream})")
            fcntl.flock(lockfile, fcntl.LOCK_UN)
            return
        elif pid:
            print("Stale PID file found. Removing.")
            clear_pidfile(lockfile)
        if not stream_name:
            print("Error: stream_name must be provided as a command-line argument.")
            fcntl.flock(lockfile, fcntl.LOCK_UN)
            return
        # Replace direct ffmpeg-service_cmd execution with a wrapper script
        ffmpeg_service_cmd = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), 'webcam-ffmpeg-service.py'),
            stream_name
        ]
        # Start the process in a new process group so we can kill all children later
        proc = subprocess.Popen(ffmpeg_service_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
        # Wait for webcam-ffmpeg-service process to actually start
        for _ in range(20):  # wait up to 2 seconds
            if is_pid_running(proc.pid):
                break
            time.sleep(0.1)
        else:
            print(f"Warning: webcam-ffmpeg-service (PID {proc.pid}) did not appear to start in time.")
        write_pidfile(lockfile, proc.pid, stream_name)
        print(f"Started webcam-ffmpeg-service.py (PID {proc.pid}) for stream '{stream_name}'")
        fcntl.flock(lockfile, fcntl.LOCK_UN)

def stop(path=None):
    import signal
    if not os.path.exists(PIDFILE):
        print("webcam-ffmpeg-service is not running.")
        return
    with open(PIDFILE, 'r+') as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        pid, running_stream = read_pidfile(lockfile)
        if not pid:
            print("webcam-ffmpeg-service is not running.")
            fcntl.flock(lockfile, fcntl.LOCK_UN)
            return
        if path is not None and (not running_stream or running_stream != path):
            print(f"webcam-ffmpeg-service is running for stream '{running_stream}', not stopping (requested: '{path}')")
            fcntl.flock(lockfile, fcntl.LOCK_UN)
            return
        try:
            # Terminate the process group to ensure all child ffmpeg processes are killed
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            for _ in range(20):  # wait up to 2 seconds
                if not is_pid_running(pid):
                    break
                time.sleep(0.1)
            else:
                print(f"Warning: webcam-ffmpeg-service process (PID {pid}) did not terminate in time.")
            clear_pidfile(lockfile)
            print("Stopped webcam-ffmpeg-service and all child processes.")
        except ProcessLookupError:
            print("webcam-ffmpeg-service not running, removing stale PID file.")
            clear_pidfile(lockfile)
        except Exception as e:
            print(f"Error stopping process group: {e}")
        fcntl.flock(lockfile, fcntl.LOCK_UN)

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("start", "stop"):
        print("Usage: python webcam-ffmpeg.py start|stop [stream_name]")
        sys.exit(1)
    if sys.argv[1] == "start":
        if len(sys.argv) < 3:
            print("Error: stream_name must be provided as a command-line argument.")
            print("Usage: python webcam-ffmpeg.py start [stream_name]")
            sys.exit(1)
        stream_name = sys.argv[2]
        start(stream_name)
    elif sys.argv[1] == "stop":
        path = sys.argv[2] if len(sys.argv) > 2 else None
        stop(path)

if __name__ == "__main__":
    main()
