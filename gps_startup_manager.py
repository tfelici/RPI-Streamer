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
import math
from datetime import datetime, timedelta

# Add the RPI Streamer directory to the path so we can import utils
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import DEFAULT_SETTINGS, SETTINGS_FILE, is_gps_tracking, load_settings, calculate_distance

# Import GPS tracking function directly from app.py
from app import start_flight

# Import GPS hardware for motion detection
from gps_tracker import SIM7600GHardware

# Motion detection state
motion_detection_state = {
    'last_position': None,
    'last_position_time': None,
    'gps_hardware': None,
    'initialization_attempts': 0,
    'max_init_attempts': 3,
    'movement_threshold': 10.0,  # meters - larger threshold for startup detection
    'position_timeout': 30.0,   # seconds - how long to wait for GPS fix
}

def initialize_gps_hardware():
    """Initialize GPS hardware for motion detection"""
    state = motion_detection_state
    
    if state['gps_hardware'] is not None:
        return True  # Already initialized
    
    if state['initialization_attempts'] >= state['max_init_attempts']:
        print(f"GPS hardware initialization failed after {state['max_init_attempts']} attempts")
        return False
    
    state['initialization_attempts'] += 1
    print(f"Attempting to initialize GPS hardware (attempt {state['initialization_attempts']})...")
    
    try:
        state['gps_hardware'] = SIM7600GHardware()
        
        if not state['gps_hardware'].initialize_hardware():
            print("Failed to initialize GPS hardware")
            state['gps_hardware'] = None
            return False
        
        if not state['gps_hardware'].start_gps():
            print("GPS hardware initialized but failed to start GPS module")
            state['gps_hardware'].cleanup()
            state['gps_hardware'] = None
            return False
        
        print("GPS hardware initialized successfully for motion detection")
        return True
        
    except Exception as e:
        print(f"Exception during GPS hardware initialization: {e}")
        if state['gps_hardware']:
            try:
                state['gps_hardware'].cleanup()
            except:
                pass
            state['gps_hardware'] = None
        return False

def cleanup_gps_hardware():
    """Clean up GPS hardware resources"""
    state = motion_detection_state
    if state['gps_hardware']:
        try:
            state['gps_hardware'].cleanup()
        except:
            pass
        state['gps_hardware'] = None
        print("GPS hardware cleaned up")

def detect_motion():
    """
    Detect motion using GPS dongle by comparing current position with last known position
    Returns True if significant movement is detected, False otherwise
    """
    state = motion_detection_state
    current_time = time.time()
    
    # Initialize GPS hardware if needed
    if state['gps_hardware'] is None:
        if not initialize_gps_hardware():
            return False
    
    # Check if hardware is still responsive
    if not state['gps_hardware'].check_hardware_presence():
        print("GPS hardware disconnected, cleaning up...")
        cleanup_gps_hardware()
        return False
    
    try:
        # Get current GPS position
        gps_data = state['gps_hardware'].get_gps_data()
        
        if gps_data is None:
            print("No GPS fix available for motion detection")
            return False
        
        current_lat = gps_data['latitude']
        current_lon = gps_data['longitude']
        
        print(f"Motion detection GPS: lat={current_lat:.6f}, lon={current_lon:.6f}")
        
        # First position - store it and don't consider it motion
        if state['last_position'] is None:
            state['last_position'] = (current_lat, current_lon)
            state['last_position_time'] = current_time
            print("Stored first GPS position for motion detection")
            return False
        
        # Calculate distance from last position
        distance = calculate_distance(
            state['last_position'][0], state['last_position'][1],
            current_lat, current_lon
        )
        
        print(f"Distance from last position: {distance:.1f}m (threshold: {state['movement_threshold']}m)")
        
        # Check if movement exceeds threshold
        if distance >= state['movement_threshold']:
            print(f"MOTION DETECTED! Aircraft moved {distance:.1f}m")
            # Update position for next comparison
            state['last_position'] = (current_lat, current_lon)
            state['last_position_time'] = current_time
            return True
        
        # Update position time even if no movement (for timeout detection)
        state['last_position_time'] = current_time
        return False
        
    except Exception as e:
        print(f"Error in GPS motion detection: {e}")
        # On error, try to reinitialize next time
        cleanup_gps_hardware()
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
                        success, message, status_code = start_flight()
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
            success, message, status_code = start_flight()
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
    cleanup_gps_hardware()
    sys.exit(0)

if __name__ == '__main__':
    # Handle shutdown signals
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    main()
