#!/usr/bin/env python3
"""
GPS Startup Manager - Handles automatic GPS tracking startup based on flight settings
"""
import sys
import os
import time
import json
import subprocess
import signal

# Add the RPI Streamer directory to the path so we can import utils
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import DEFAULT_SETTINGS, SETTINGS_FILE, is_gps_tracking, load_settings

# Import GPS tracking function directly from app.py
from app import start_gps_tracking

def detect_motion():
    """
    Simple motion detection using accelerometer or GPS movement
    This is a placeholder - in a real implementation, you would:
    1. Read from accelerometer sensors
    2. Monitor GPS coordinate changes
    3. Detect vibrations or movement patterns
    
    For now, this returns False to avoid automatic startup during development
    """
    # TODO: Implement actual motion detection
    # This could use:
    # - /sys/class/hwmon sensors
    # - GPS coordinate monitoring
    # - Accelerometer data from sensors
    return False

def monitor_motion():
    """Monitor for aircraft motion and start GPS tracking when detected"""
    print("Motion detection monitoring started...")
    motion_threshold_count = 3  # Require motion detected 3 times to start
    motion_count = 0
    
    while True:
        try:
            if detect_motion():
                motion_count += 1
                print(f"Motion detected ({motion_count}/{motion_threshold_count})")
                
                if motion_count >= motion_threshold_count:
                    if not is_gps_tracking():
                        print("Aircraft motion detected! Starting GPS tracking...")
                        success, message, status_code = start_gps_tracking()
                        if success:
                            print("GPS tracking started due to motion detection")
                            break
                        else:
                            print(f"Failed to start GPS tracking: {message}")
                            motion_count = 0  # Reset counter on failure
                    else:
                        print("GPS tracking already active")
                        break
            else:
                # Reset motion count if no motion detected
                if motion_count > 0:
                    motion_count = max(0, motion_count - 1)
            
            time.sleep(2)  # Check every 2 seconds
            
        except KeyboardInterrupt:
            print("Motion monitoring stopped by user")
            break
        except Exception as e:
            print(f"Error in motion monitoring: {e}")
            time.sleep(5)  # Wait longer on error

def main():
    """Main startup logic"""
    print("GPS Startup Manager starting...")
    
    # Wait for system to fully boot and network to be available
    time.sleep(10)
    
    try:
        settings = load_settings()
        gps_start_mode = settings['gps_start_mode']
        
        print(f"GPS start mode: {gps_start_mode}")
        
        if gps_start_mode == 'boot':
            print("Auto-starting GPS tracking on boot...")
            success, message, status_code = start_gps_tracking()
            if success:
                print("GPS tracking started successfully on boot")
            else:
                print(f"Failed to start GPS tracking on boot: {message}")
                
        elif gps_start_mode == 'motion':
            print("Starting motion detection monitoring...")
            monitor_motion()
            
        elif gps_start_mode == 'manual':
            print("Manual mode - GPS tracking will be started via web interface")
            
        else:
            print(f"Unknown GPS start mode: {gps_start_mode}")
            
    except Exception as e:
        print(f"Error in GPS startup manager: {e}")

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    print("GPS Startup Manager shutting down...")
    sys.exit(0)

if __name__ == '__main__':
    # Handle shutdown signals
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    main()
