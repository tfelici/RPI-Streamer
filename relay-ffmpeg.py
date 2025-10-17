#!/usr/bin/env python3
import json
import sys
import os
import signal
import time
import subprocess
from utils import get_setting

def main():
    """Main relay process that manages streaming subprocesses."""
    if len(sys.argv) < 2:
        print("Usage: python relay-ffmpeg.py <MTX_PATH>")
        sys.exit(1)
        
    mtx_path = sys.argv[1]
    stream_url = get_setting('stream_url')
    if not stream_url:
        print("Error: 'stream_url' must be set in settings.json")
        sys.exit(1)
    
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
    
    ACTIVE_STATUSFILE = f"/tmp/relay_status_{safe_mtx_path}.json"

    def handle_exit(signum, frame):
        """Clean shutdown handler."""
        print(f"Received exit signal {signum}, cleaning up...")
        # Kill subprocess first
        if current_process and current_process.poll() is None:
            print("Terminating subprocess...")
            try:
                # Kill process group to ensure all child processes die
                os.killpg(os.getpgid(current_process.pid), signal.SIGTERM)
                
                # Poll for up to 5 seconds for graceful termination
                for i in range(50):  # 50 * 0.1 = 5 seconds
                    if current_process.poll() is not None:
                        print(f"Subprocess terminated gracefully after {i * 0.1:.1f} seconds")
                        break
                    time.sleep(0.1)
                else:
                    # If still running after 5 seconds, force kill
                    print("Subprocess did not terminate gracefully, force killing...")
                    os.killpg(os.getpgid(current_process.pid), signal.SIGKILL)
            except Exception as e:
                print(f"Error terminating subprocess: {e}")
        # Remove PID file
        try:
            if os.path.exists(ACTIVE_PIDFILE):
                os.remove(ACTIVE_PIDFILE)
        except Exception as e:
            print(f"Warning: Could not remove active PID file on exit: {e}")
        # Remove Status file
        try:
            if os.path.exists(ACTIVE_STATUSFILE):
                os.remove(ACTIVE_STATUSFILE)
        except Exception as e:
            print(f"Warning: Could not remove active status file on exit: {e}")
        print("Cleanup complete, exiting.")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    
    # Path to the subprocess script
    subprocess_script = os.path.join(os.path.dirname(__file__), "relay-ffmpeg-subprocess.py")
    
    # Track current subprocess for cleanup
    current_process = None
    
    while True:
        # Get current settings
        dynamicBitrate = get_setting('dynamicBitrate')

        if stream_url.startswith('https://') and '/whip' in stream_url:
            print("WHIP streaming is not supported in this version. Exiting.")
            break
        
        # Determine pipeline type
        pipeline_type = "dynamic" if dynamicBitrate else "static"
        
        print(f"Starting {pipeline_type} pipeline subprocess...")
        print(f"Source: {rtsp_url}")
        print(f"Destination: {stream_url}")
        
        # Write initial status
        initial_status = {
            'bitrate': 'dynamic' if dynamicBitrate else 'passthrough',
            'measured_bitrate': 0,
            'network_status': 'starting',
            'pipeline_status': 'starting',
            'stream_health': 'initializing',
            'timestamp': int(time.time())
        }
        try:
            with open(ACTIVE_STATUSFILE, 'w') as f:
                json.dump(initial_status, f)
        except Exception as e:
            print(f"Warning: Could not write initial status file: {e}")
        
        # Start the subprocess with process group for automatic cleanup
        try:
            process = subprocess.Popen([
                sys.executable, "-u", subprocess_script, 
                pipeline_type, rtsp_url, stream_url, ACTIVE_STATUSFILE
            ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
               universal_newlines=True, bufsize=0,
               preexec_fn=os.setsid)
            
            current_process = process  # Track for cleanup
            print(f"Subprocess started with PID: {process.pid}")
            
            # Monitor the subprocess - just read output as it comes
            while process.poll() is None:  # While subprocess is running
                try:
                    line = process.stdout.readline()
                    if line:
                        print(f"Subprocess: {line.strip()}")
                        sys.stdout.flush()
                except Exception as e:
                    print(f"Error reading subprocess output: {e}")
                    break
            
            # Get the exit code
            exit_code = process.poll()
            
            # Determine what happened
            if exit_code is not None:
                if exit_code == 0:
                    result_type = "success"
                    message = "Subprocess completed normally"
                elif exit_code < 0:
                    result_type = "crash"
                    message = f"Subprocess killed by signal {-exit_code} (likely GLib assertion)"
                else:
                    result_type = "error"
                    message = f"Subprocess exited with code {exit_code}"
            else:
                result_type = "timeout"
                message = "Subprocess timeout"
            
            # Write final status
            stop_status = {
                'bitrate': 'dynamic' if dynamicBitrate else 'passthrough',
                'measured_bitrate': 0,
                'network_status': 'disconnected' if result_type == 'crash' else 'stopped',
                'pipeline_status': result_type,
                'stream_health': 'error' if result_type in ['crash', 'error'] else 'stopped',
                'error_message': message,
                'exit_code': exit_code,
                'timestamp': int(time.time())
            }
            
            try:
                with open(ACTIVE_STATUSFILE, 'w') as f:
                    json.dump(stop_status, f)
            except Exception as e:
                print(f"Warning: Could not write stop status file: {e}")
            
            # Log the result
            if result_type == "crash":
                print(f"Pipeline subprocess crashed (likely GLib assertion): {message}")
                print("Subprocess isolation working correctly - main process survived the crash")
            elif result_type == "error":
                print(f"Pipeline subprocess error: {message}")
            elif result_type == "timeout":
                print(f"Pipeline subprocess timeout: {message}")
            else:
                print(f"Pipeline subprocess completed: {message}")
                
        except Exception as e:
            print(f"Error starting subprocess: {e}")
        
        print("Restarting pipeline in 5 seconds...")
        time.sleep(5)

if __name__ == "__main__":
    main()
