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
from datetime import datetime

# Add the RPI Streamer directory to the path so we can import utils
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import DEFAULT_SETTINGS, SETTINGS_FILE, is_gps_tracking, load_settings, save_settings, calculate_distance, get_hardwareid
from gps_client import get_gnss_location

# Import GPS tracking function from app.py
from app import start_flight

# Module logger (configured in main)
logger = logging.getLogger('gps-startup')


def get_streamer_settings(poll_until_success=False, poll_interval=30):
    """
    Retrieve streamer settings from the server using the hardware ID.
    
    Args:
        poll_until_success (bool): If True, will keep polling until successful
        poll_interval (int): Seconds to wait between polling attempts
        
    Returns the response data or None if failed (when not polling).
    """
    hardwareid = get_hardwareid()
    url = f"https://streamer.lambda-tek.com/public_api.php?command=getstreamersettings&hardwareid={hardwareid}"
    
    # Import requests locally to avoid importing it before app.py performs
    # gevent monkey-patching (which may modify ssl). Importing requests
    # at module import time can cause monkey-patch ordering issues.
    import requests
    
    attempt = 1
    
    while True:
        try:
            if attempt > 1:
                logger.info(f"Retrieving streamer settings (attempt {attempt}) for hardware ID: {hardwareid}")
            else:
                logger.info(f"Retrieving streamer settings for hardware ID: {hardwareid}")
            logger.debug(f"Request URL: {url}")

            response = requests.get(url, timeout=10)
            response.raise_for_status()  # Raises an HTTPError for bad responses

            logger.info(f"Response status: {response.status_code}")
            logger.debug(f"Response content: {response.text[:500]}...")  # First 500 chars for debugging

            # Try to parse as JSON first
            try:
                json_data = response.json()
                logger.debug(f"Parsed JSON response: {json_data}")
                logger.info("Successfully retrieved streamer settings from server")
                return json_data
            except json.JSONDecodeError:
                logger.warning("Response is not valid JSON, returning as text")
                # If not JSON, return the text content
                result = {"text_response": response.text}
                logger.info("Successfully retrieved streamer settings from server (text format)")
                return result

        except Exception as e:
            logger.warning(f"Failed to retrieve streamer settings (attempt {attempt}): {e}")
            
            if not poll_until_success:
                logger.exception(f"Unexpected error in get_streamer_settings: {e}")
                return None
            
            # If polling, wait and try again
            logger.info(f"Will retry in {poll_interval} seconds...")
            time.sleep(poll_interval)
            attempt += 1


# Motion detection state
motion_detection_state = {
    'last_position': None,
    'last_position_time': None,
    'movement_threshold': 10.0,  # meters - larger threshold for startup detection
    'position_timeout': 30.0,   # seconds - how long to wait for GPS fix
}


def detect_motion():
    """
    Detect motion using GPS dongle by comparing current position with last known position
    Returns True if significant movement is detected, False otherwise
    """
    state = motion_detection_state
    current_time = time.time()

    try:
        # Get current GPS position using GPS daemon client
        success, location_data = get_gnss_location()

        gps_data = None
        if success and location_data and location_data.get('fix_status') == 'valid':
            # Convert speed from m/s to km/h if needed
            speed_ms = location_data.get('speed', 0) or 0
            speed_kmh = speed_ms * 3.6 if isinstance(speed_ms, (int, float)) else 0

            gps_data = {
                'latitude': location_data['latitude'],
                'longitude': location_data['longitude'],
                'altitude': location_data.get('altitude', 0),
                'speed': speed_kmh,  # Convert from m/s to km/h
                'heading': location_data.get('course', 0) or 0,
                'accuracy': 5.0  # Default accuracy estimate
            }

        if gps_data is None:
            logger.debug("No GPS fix available for motion detection")
            return False

        current_lat = gps_data['latitude']
        current_lon = gps_data['longitude']

        logger.debug(f"Motion detection GPS: lat={current_lat:.6f}, lon={current_lon:.6f}")

        # First position - store it and don't consider it motion
        if state['last_position'] is None:
            state['last_position'] = (current_lat, current_lon)
            state['last_position_time'] = current_time
            logger.debug("Stored first GPS position for motion detection")
            return False

        # Calculate distance from last position
        distance = calculate_distance(
            state['last_position'][0], state['last_position'][1],
            current_lat, current_lon
        )

        logger.debug(f"Distance from last position: {distance:.1f}m (threshold: {state['movement_threshold']}m)")

        # Check if movement exceeds threshold
        if distance >= state['movement_threshold']:
            logger.info(f"MOTION DETECTED! Aircraft moved {distance:.1f}m")
            # Update position for next comparison
            state['last_position'] = (current_lat, current_lon)
            state['last_position_time'] = current_time
            return True

        # Update position time even if no movement (for timeout detection)
        state['last_position_time'] = current_time
        return False

    except Exception as e:
        logger.exception(f"Error in GPS motion detection: {e}")
        return False


def monitor_motion():
    """Monitor for aircraft motion and start GPS tracking when detected"""
    logger.info("Motion detection monitoring started...")
    motion_threshold_count = 3  # Require motion detected 3 times to start
    motion_count = 0

    while True:
        try:
            if detect_motion():
                motion_count += 1
                logger.info(f"Motion detected ({motion_count}/{motion_threshold_count})")

                if motion_count >= motion_threshold_count:
                    if not is_gps_tracking():
                        logger.info("Aircraft motion detected! Starting GPS tracking...")
                        success, message, status_code = start_flight()
                        if success:
                            logger.info("GPS tracking started due to motion detection")
                            break
                        else:
                            logger.error(f"Failed to start GPS tracking: {message}")
                            motion_count = 0  # Reset counter on failure
                    else:
                        logger.info("GPS tracking already active")
                        break
            else:
                # Reset motion count if no motion detected
                if motion_count > 0:
                    motion_count = max(0, motion_count - 1)

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
        settings = load_settings()

        # Sync flight parameters from server and update settings
        logger.info("Syncing flight parameters from server...")
        remote_settings = get_streamer_settings(poll_until_success=True, poll_interval=30)
        
        # Update settings with flight parameters if they exist in the response
        if isinstance(remote_settings, dict) and 'text_response' not in remote_settings:
            # Handle JSON response - loop through all remote settings and override local ones
            settings_updated = False
            for key, value in remote_settings.items():
                if key in settings:
                    # Only update if the value is different
                    if settings[key] != value:
                        old_value = settings[key]
                        settings[key] = value
                        logger.info(f"Updated {key}: {old_value} -> {value}")
                        settings_updated = True
                else:
                    # Add new setting if it doesn't exist locally
                    settings[key] = value
                    logger.info(f"Added new setting {key}: {value}")
                    settings_updated = True

            # Save updated settings if any changes were made
            if settings_updated:
                save_settings(settings)
                logger.info("Updated settings with all flight parameters from server")
            else:
                logger.info("No setting changes needed - all values already match")
        else:
            logger.warning(f"Flight parameters response format not recognized: {type(remote_settings)}")

        gps_start_mode = settings.get('gps_start_mode', 'manual')

        logger.info(f"GPS start mode: {gps_start_mode}")

        if gps_start_mode == 'boot':
            logger.info("Auto-starting GPS tracking on boot...")
            success, message, status_code = start_flight()
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
    sys.exit(0)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='GPS Startup Manager')
    parser.add_argument('--daemon', action='store_true', default=False,
                        help='Run in daemon mode (no console output, log to runtime/journal)')
    args = parser.parse_args()

    # Basic logging configuration
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

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
