#!/usr/bin/env python3
"""
GPS Startup Manager - Handles automatic GPS tracking startup based on flight settings
"""
import sys
import os
import time
import json
import signal
import logging
import requests
from datetime import datetime

# Add the RPI Streamer directory to the path so we can import utils
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import is_gps_tracking, load_settings, calculate_distance, get_hardwareid, get_streamer_settings
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


def monitor_motion(updated_settings):
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
            monitor_motion(settings)

        elif gps_start_mode == 'manual':
            logger.info("Manual mode - GPS tracking will be started via web interface")

        else:
            logger.warning(f"Unknown GPS start mode: {gps_start_mode}")

    except Exception as e:
        logger.exception(f"Error in GPS startup manager: {e}")


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info("GPS Startup Manager shutting down...")
    sys.exit(0)


if __name__ == '__main__':
    import argparse
    from logging.handlers import RotatingFileHandler

    parser = argparse.ArgumentParser(description='GPS Startup Manager')
    parser.add_argument('--daemon', action='store_true', default=False,
                        help='Run in daemon mode (no console output, log to runtime/journal)')
    parser.add_argument('--debug', action='store_true', default=False,
                        help='Enable debug logging')
    args = parser.parse_args()

    # Basic logging configuration - set level based on debug flag
    log_level = logging.DEBUG if args.debug else logging.INFO
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    # Configure logging with both console and rotating file output
    # Use /var/log for system services or current directory for development
    log_file = '/var/log/gps_startup_manager.log' if args.daemon else 'gps_startup_manager.log'
    
    # Create rotating file handler to keep logs under 1MB
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=1024*1024,  # 1MB max size
        backupCount=3        # Keep 3 backup files
    )
    file_handler.setFormatter(logging.Formatter(log_format))
    
    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[
            file_handler,
            logging.StreamHandler()
        ]
    )

    # If running interactively (not daemon) and stdout is a TTY, ensure logs print to console
    try:
        if not args.daemon and sys.stdout.isatty():
            root_logger = logging.getLogger()
            has_stream = any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers)
            if not has_stream:
                sh = logging.StreamHandler(sys.stdout)
                sh.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
                root_logger.addHandler(sh)
    except Exception:
        pass

    # Install signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    main()
