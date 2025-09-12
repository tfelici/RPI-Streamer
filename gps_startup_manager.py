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

from utils import is_gps_tracking, load_settings, save_settings, calculate_distance, get_hardwareid
from gps_client import get_gnss_location

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
    Returns:
        True - significant movement detected
        False - no movement detected  
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

        logger.debug(f"Motion detection GPS: lat={current_lat:.6f}, lon={current_lon:.6f}, accuracy={gps_accuracy:.1f}m")

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

        # Use the larger of movement threshold or GPS accuracy as minimum distance
        effective_threshold = max(state['movement_threshold'], gps_accuracy * 2)
        
        logger.debug(f"Distance from last position: {distance:.1f}m (threshold: {effective_threshold:.1f}m, GPS accuracy: {gps_accuracy:.1f}m)")

        # Check if movement exceeds both the threshold and GPS accuracy margin
        if distance >= effective_threshold:
            logger.info(f"MOTION DETECTED! Aircraft moved {distance:.1f}m (above accuracy margin of {gps_accuracy:.1f}m)")
            # Update position for next comparison
            state['last_position'] = (current_lat, current_lon)
            state['last_position_time'] = current_time
            return True

        # Update position time even if no movement (for timeout detection)
        state['last_position_time'] = current_time
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
                        # Save updated settings just before starting GPS tracking
                        save_settings(updated_settings)
                        logger.info("Settings saved before starting GPS tracking")
                        
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
        settings = load_settings()

        # Sync flight parameters from server and update settings
        logger.info("Syncing flight parameters from server...")
        remote_settings = get_streamer_settings(poll_until_success=True, poll_interval=30)
        
        # Update settings with flight parameters if they exist in the response
        # But don't save them yet - only save when start_flight is actually called
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

            # Log changes but don't save yet
            if settings_updated:
                logger.info("Flight parameters updated from server (will save only when GPS tracking starts)")
            else:
                logger.info("No setting changes needed - all values already match")
        else:
            logger.warning(f"Flight parameters response format not recognized: {type(remote_settings)}")

        gps_start_mode = settings.get('gps_start_mode', 'manual')

        logger.info(f"GPS start mode: {gps_start_mode}")

        if gps_start_mode == 'boot':
            logger.info("Auto-starting GPS tracking on boot...")
            # Save updated settings just before starting GPS tracking
            save_settings(settings)
            logger.info("Settings saved before starting GPS tracking")
            
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
