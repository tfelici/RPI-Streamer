#!/usr/bin/env python3
"""
GPS Startup Manager - Handles automatic GPS tracking startup based on flight settings
Also manages GPS daemon startup for non-hardware GPS sources (X-Plane, simulation)
"""
import sys
import os
import time
import json
import signal
import logging
import requests
import subprocess
from datetime import datetime

# Add the RPI Streamer directory to the path so we can import utils
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import is_gps_tracking, calculate_distance, get_streamer_settings, load_settings
from gps_client import get_gnss_location
import math

def start_flight_via_api():
    """
    Start GPS tracking by calling the web service API instead of importing app.py
    This avoids importing the entire Flask application and its dependencies
    """
    try:
        # Make POST request to the gps-control endpoint
        response = requests.post(
            'http://localhost:80/gps-control',
            json={'action': 'start'},
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            if 'status' in result:
                logger.info(f"Successfully started GPS tracking via API: {result['status']}")
                return True, result['status'], 200
            else:
                logger.error(f"API returned success but unexpected format: {result}")
                return False, "Unexpected API response format", 500
        else:
            try:
                error_result = response.json()
                error_msg = error_result.get('error', f'HTTP {response.status_code}')
                logger.error(f"Failed to start GPS tracking via API: {error_msg}")
                return False, error_msg, response.status_code
            except:
                logger.error(f"Failed to start GPS tracking via API: HTTP {response.status_code}")
                return False, f'HTTP {response.status_code}', response.status_code
                
    except requests.RequestException as e:
        logger.error(f"Network error calling GPS tracking API: {e}")
        return False, f'Network error: {e}', 500
    except Exception as e:
        logger.error(f"Unexpected error calling GPS tracking API: {e}")
        return False, f'Unexpected error: {e}', 500

# Module logger (configured in main)
logger = logging.getLogger('gps-startup')


def is_gps_daemon_running():
    """Check if GPS daemon service is currently running"""
    try:
        result = subprocess.run(['systemctl', 'is-active', '--quiet', 'gps-daemon.service'],
                                capture_output=True, timeout=10)
        return result.returncode == 0
    except Exception as e:
        logger.warning(f"Failed to check GPS daemon status: {e}")
        return False


def start_gps_daemon():
    """Start the GPS daemon service"""
    try:
        logger.info("Starting GPS daemon service...")
        result = subprocess.run(['systemctl', 'start', 'gps-daemon.service'],
                                capture_output=True, timeout=30, text=True)
        if result.returncode == 0:
            logger.info("GPS daemon service started successfully")
            return True
        else:
            logger.error(f"Failed to start GPS daemon service: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error starting GPS daemon service: {e}")
        return False


def stop_gps_daemon():
    """Stop the GPS daemon service"""
    try:
        logger.info("Stopping GPS daemon service...")
        result = subprocess.run(['systemctl', 'stop', 'gps-daemon.service'],
                                capture_output=True, timeout=30, text=True)
        if result.returncode == 0:
            logger.info("GPS daemon service stopped successfully")
            return True
        else:
            logger.error(f"Failed to stop GPS daemon service: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error stopping GPS daemon service: {e}")
        return False


def should_manage_gps_daemon():
    """
    Determine if GPS startup manager should manage the GPS daemon
    Returns True if GPS source is NOT 'hardware' (since hardware case is handled by udev)
    """
    try:
        settings = load_settings()
        gps_source = settings.get('gps_source', 'hardware')
        logger.debug(f"GPS source from settings: {gps_source}")
        return gps_source != 'hardware'
    except Exception as e:
        logger.warning(f"Failed to load GPS source setting, defaulting to hardware: {e}")
        return False


def calculate_bearing(lat1, lon1, lat2, lon2):
    """
    Calculate the bearing (direction) from point 1 to point 2 in degrees (0-360)
    """
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lon = math.radians(lon2 - lon1)
    
    y = math.sin(delta_lon) * math.cos(lat2_rad)
    x = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lon)
    
    bearing = math.atan2(y, x)
    bearing = math.degrees(bearing)
    bearing = (bearing + 360) % 360  # Normalize to 0-360 degrees
    
    return bearing


def angle_difference(angle1, angle2):
    """
    Calculate the smallest angle difference between two bearings (0-180 degrees)
    """
    diff = abs(angle1 - angle2)
    if diff > 180:
        diff = 360 - diff
    return diff


# Motion detection state
motion_detection_state = {
    'last_position': None,
    'last_position_time': None,
    'movement_threshold': 10.0,  # meters - larger threshold for startup detection
    'position_timeout': 30.0,   # seconds - how long to wait for GPS fix
    'position_history': [],      # Store last few positions for direction analysis
    'bearing_history': [],       # Store bearings between consecutive positions
    'max_history': 3,           # Keep last 3 positions for direction calculation
}


def detect_motion():
    """
    Detect motion using GPS dongle by comparing current position with last known position
    Returns:
        True - significant directional movement detected (two consecutive movements within 30 degrees)
        False - no movement or inconsistent movement detected  
        None - GPS error, ignore this result
    """
    state = motion_detection_state
    current_time = time.time()

    try:
        # Get current GPS position using GPS daemon client
        success, location_data = get_gnss_location()

        if not (success and location_data and location_data.get('fix_status') == 'valid'):
            logger.debug("No GPS fix available for motion detection")
            return None  # GPS error - ignore this result

        current_lat = location_data['latitude']
        current_lon = location_data['longitude']
        gps_accuracy = location_data.get('accuracy', 5.0)  # Default to 5m if accuracy not available
        current_position = (current_lat, current_lon, current_time)

        logger.debug(f"Motion detection GPS: lat={current_lat:.6f}, lon={current_lon:.6f}, accuracy={gps_accuracy:.1f}m")

        # Add current position to history
        state['position_history'].append(current_position)
        
        # Keep only the last max_history positions
        if len(state['position_history']) > state['max_history']:
            state['position_history'] = state['position_history'][-state['max_history']:]

        # Need at least 2 positions to detect movement
        if len(state['position_history']) < 2:
            logger.debug("Need more position history for motion detection")
            return False

        # Get the last two positions
        prev_pos = state['position_history'][-2]
        curr_pos = state['position_history'][-1]
        
        # Calculate distance from previous position
        distance = calculate_distance(
            prev_pos[0], prev_pos[1],
            curr_pos[0], curr_pos[1]
        )

        # Use the larger of movement threshold or GPS accuracy as minimum distance
        effective_threshold = max(state['movement_threshold'], gps_accuracy * 2)
        
        logger.debug(f"Distance from last position: {distance:.1f}m (threshold: {effective_threshold:.1f}m, GPS accuracy: {gps_accuracy:.1f}m)")

        # If movement is below threshold, return False
        if distance < effective_threshold:
            return False

        # Calculate bearing for this movement
        bearing = calculate_bearing(prev_pos[0], prev_pos[1], curr_pos[0], curr_pos[1])
        state['bearing_history'].append(bearing)
        
        # Keep bearing history aligned with position history
        if len(state['bearing_history']) > state['max_history'] - 1:
            state['bearing_history'] = state['bearing_history'][-(state['max_history'] - 1):]

        logger.debug(f"Movement detected: {distance:.1f}m at bearing {bearing:.1f}°")

        # Need at least 2 bearings to check directional consistency
        if len(state['bearing_history']) < 2:
            logger.debug("Need more bearing history for directional analysis")
            return False

        # Check if the last two movements are within 30 degrees of each other
        last_bearing = state['bearing_history'][-1]
        prev_bearing = state['bearing_history'][-2]
        bearing_diff = angle_difference(last_bearing, prev_bearing)
        
        logger.debug(f"Bearing comparison: previous={prev_bearing:.1f}°, current={last_bearing:.1f}°, difference={bearing_diff:.1f}°")

        if bearing_diff <= 30.0:
            logger.info(f"DIRECTIONAL MOTION DETECTED! Two consecutive movements within {bearing_diff:.1f}° (bearings: {prev_bearing:.1f}° → {last_bearing:.1f}°)")
            return True
        else:
            logger.debug(f"Movement detected but not directional: bearing difference {bearing_diff:.1f}° > 30°")
            return False

    except Exception as e:
        logger.exception(f"Error in GPS motion detection: {e}")
        return None  # GPS error - ignore this result


def monitor_motion():
    """Monitor for aircraft motion and start GPS tracking when detected"""
    logger.info("Motion detection monitoring started...")
    motion_threshold_count = 3  # Require motion detected 3 times to start
    motion_count = 0
    stationary_timeout = 60  # Reset motion count only after 60 seconds of no motion
    last_motion_time = None  # Initialize to None

    while True:
        try:
            motion_result = detect_motion()
            
            if motion_result is True:
                # Motion detected
                motion_count += 1
                last_motion_time = time.time()
                logger.info(f"Motion detected ({motion_count}/{motion_threshold_count})")

                if motion_count >= motion_threshold_count:
                    if not is_gps_tracking():
                        logger.info("Aircraft motion detected! Starting GPS tracking...")
                        
                        success, message, status_code = start_flight_via_api()
                        if success:
                            logger.info("GPS tracking started due to motion detection")
                            break
                        else:
                            logger.error(f"Failed to start GPS tracking: {message}")
                            motion_count = 0  # Reset counter on failure
                            last_motion_time = None
                    else:
                        logger.info("GPS tracking already active")
                        break
            elif motion_result is False:
                # Below threshold movement - check if vehicle has been stationary long enough
                current_time = time.time()
                if last_motion_time is not None and motion_count > 0:
                    time_since_motion = current_time - last_motion_time
                    if time_since_motion > stationary_timeout:
                        logger.info(f"Vehicle stationary for {time_since_motion:.1f}s, resetting motion count")
                        motion_count = max(0, motion_count - 1)
                        last_motion_time = current_time  # Reset timer
            # motion_result is None - GPS error, ignore this result and don't change motion_count

            time.sleep(2)  # Check every 2 seconds

        except KeyboardInterrupt:
            logger.info("Motion monitoring stopped by user")
            break
        except Exception as e:
            logger.exception(f"Error in motion monitoring: {e}")
            time.sleep(5)  # Wait longer on error


def main():
    """Main startup logic"""
    logger.info("GPS Startup Manager starting...")

    # GPS Daemon Management: Start GPS daemon for non-hardware sources
    # Hardware sources are managed by udev rules, but X-Plane and simulation need manual control
    if should_manage_gps_daemon():
        logger.info("GPS source is not 'hardware' - GPS startup manager will manage GPS daemon")
        
        if not is_gps_daemon_running():
            logger.info("GPS daemon is not running, starting it for non-hardware GPS source...")
            if start_gps_daemon():
                # Wait a moment for daemon to initialize
                time.sleep(2)
            else:
                logger.error("Failed to start GPS daemon - GPS functionality may not work")
        else:
            logger.info("GPS daemon is already running")
    else:
        logger.info("GPS source is 'hardware' - GPS daemon will be managed by udev rules")

    # Check if GPS tracking is already active
    if is_gps_tracking():
        logger.info("GPS tracking is already active, exiting startup manager")
        return

    try:
        # Sync flight parameters from server and load/update settings
        logger.info("Syncing flight parameters from server...")
        success, settings, response_data = get_streamer_settings(
            logger, 
            poll_until_success=True, 
            poll_interval=30
        )
        
        if success:
            logger.info("Flight parameters synced successfully from server")
        else:
            logger.warning("Failed to sync flight parameters from server, using local settings")

        gps_start_mode = settings.get('gps_start_mode', 'manual')

        logger.info(f"GPS start mode: {gps_start_mode}")

        if gps_start_mode == 'boot':
            logger.info("Auto-starting GPS tracking on boot...")
            
            success, message, status_code = start_flight_via_api()
            if success:
                logger.info("GPS tracking started successfully on boot")
            else:
                logger.error(f"Failed to start GPS tracking on boot: {message}")

        elif gps_start_mode == 'motion':
            logger.info("Starting motion detection monitoring...")
            monitor_motion()

        elif gps_start_mode == 'manual':
            logger.info("Manual mode - GPS tracking will be started via web interface")

        else:
            logger.warning(f"Unknown GPS start mode: {gps_start_mode}")

    except Exception as e:
        logger.exception(f"Error in GPS startup manager: {e}")


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info("GPS Startup Manager shutting down...")
    
    # Stop GPS daemon if we started it (non-hardware sources only)
    if should_manage_gps_daemon() and is_gps_daemon_running():
        logger.info("Stopping GPS daemon (managed by GPS startup manager)")
        stop_gps_daemon()
    
    sys.exit(0)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='GPS Startup Manager')
    parser.add_argument('--daemon', action='store_true', default=False,
                        help='Run in daemon mode (systemd service)')
    args = parser.parse_args()

    # Configure logging based on daemon mode
    if args.daemon:
        # Daemon mode: output to systemd journal (stdout/stderr)
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
    else:
        # Interactive mode: output to console with cleaner format
        logging.basicConfig(
            level=logging.INFO,
            format='%(levelname)s: %(message)s',
            stream=sys.stdout
        )

    # Install signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    main()
