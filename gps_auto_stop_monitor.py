#!/usr/bin/env python3
"""
GPS Auto-Stop Monitor - Monitors aircraft movement and automatically stops GPS tracking 
when the aircraft remains stationary for a configured duration.

This script is designed to run as a systemd service and will be managed by systemctl.

ENHANCED: Now uses shared motion_detection.py module for sophisticated directional
motion detection before starting auto-stop monitoring.
"""
import sys
import os
import time
import signal
import logging
import requests
import subprocess
from datetime import datetime

# Add the RPI Streamer directory to the path so we can import utils
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import calculate_distance, load_settings
from gps_client import get_gnss_location
from motion_detection import wait_for_motion

logger = logging.getLogger(__name__)

# Configuration constants
MOVEMENT_THRESHOLD_METERS = 50  # Consider movement if distance exceeds this threshold
MIN_GPS_ACCURACY = 20  # Only consider GPS readings with accuracy better than this (meters)
POSITION_CHECK_INTERVAL = 30  # Check GPS position every 30 seconds

class AutoStopMonitor:
    def __init__(self):
        self.running = True
        self.reference_position = None  # Position when we start monitoring for stationary period
        self.stationary_start_time = None
        self.auto_stop_minutes = 10  # Default, will be updated from settings
        self.initial_movement_detected = False  # Track if we've seen any significant movement since start
    
    def load_auto_stop_settings(self):
        """Load auto-stop configuration from settings"""
        try:
            settings = load_settings()
            enabled = settings.get('gps_auto_stop_enabled', False)
            minutes = settings.get('gps_auto_stop_minutes', 10)
            
            if not enabled:
                logger.warning("Auto-stop is disabled in settings")
                return False, minutes
            
            logger.info(f"Auto-stop enabled: {minutes} minutes timeout")
            return True, minutes
            
        except Exception as e:
            logger.error(f"Failed to load settings: {e}")
            return False, 10
    
    def get_current_gps_position(self):
        """Get current GPS position with accuracy check"""
        try:
            success, location_data = get_gnss_location()
            
            if not success or not location_data or 'latitude' not in location_data or 'longitude' not in location_data:
                return None
            
            # Check GPS accuracy if available
            if 'accuracy' in location_data:
                accuracy = location_data.get('accuracy', float('inf'))
                if accuracy > MIN_GPS_ACCURACY:
                    logger.debug(f"GPS accuracy too low: {accuracy}m (threshold: {MIN_GPS_ACCURACY}m)")
                    return None
            
            return {
                'latitude': location_data['latitude'],
                'longitude': location_data['longitude'],
                'accuracy': location_data.get('accuracy', None),
                'timestamp': datetime.now()
            }
            
        except Exception as e:
            logger.error(f"Error getting GPS position: {e}")
            return None
    
    def check_movement(self, current_position):
        """Check if aircraft has moved significantly"""
        if not current_position:
            return False
        
        # If no reference position, set it and start monitoring
        if not self.reference_position:
            self.reference_position = current_position
            self.stationary_start_time = datetime.now()
            logger.info("Initial GPS position recorded, starting auto-stop monitoring")
            return False
        
        # Calculate distance from reference position (where monitoring started)
        try:
            distance_from_reference = calculate_distance(
                self.reference_position['latitude'], self.reference_position['longitude'],
                current_position['latitude'], current_position['longitude']
            )
        except Exception as e:
            logger.error(f"Error calculating distance: {e}")
            return False
        
        logger.debug(f"Distance from reference position: {distance_from_reference:.1f}m (threshold: {MOVEMENT_THRESHOLD_METERS}m)")
        
        if distance_from_reference >= MOVEMENT_THRESHOLD_METERS:
            # Aircraft has moved significantly - reset monitoring from this new position
            self.reference_position = current_position
            self.stationary_start_time = datetime.now()
            self.initial_movement_detected = True
            logger.info(f"Movement detected: {distance_from_reference:.1f}m from reference position - resetting auto-stop timer")
            return True
        else:
            # Aircraft is still within threshold - continue monitoring from same reference point
            logger.debug(f"Aircraft within threshold: {distance_from_reference:.1f}m from reference position")
            return False
    
    def should_stop_tracking(self):
        """Check if GPS tracking should be stopped due to inactivity"""
        if not self.stationary_start_time or not self.initial_movement_detected:
            return False
        
        stationary_duration = datetime.now() - self.stationary_start_time
        stationary_minutes = stationary_duration.total_seconds() / 60
        
        logger.debug(f"Stationary for {stationary_minutes:.1f} minutes (threshold: {self.auto_stop_minutes} minutes)")
        
        if stationary_minutes >= self.auto_stop_minutes:
            logger.info(f"Auto-stop threshold reached: aircraft has not moved more than {MOVEMENT_THRESHOLD_METERS}m in {stationary_minutes:.1f} minutes")
            return True
        
        return False
    
    def stop_gps_tracking(self):
        """Stop GPS tracking via API call"""
        try:
            logger.info("Stopping GPS tracking via auto-stop...")
            
            response = requests.post(
                'http://localhost:80/gps-control',
                json={'action': 'stop'},
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"Successfully stopped GPS tracking via auto-stop: {result.get('status', 'Unknown')}")
                
                # Re-enable GPS startup service if gps_start_mode is 'motion'
                self.restart_gps_startup_service_if_needed()
                
                return True
            else:
                try:
                    error_result = response.json()
                    error_msg = error_result.get('error', f'HTTP {response.status_code}')
                    logger.error(f"Failed to stop GPS tracking via auto-stop: {error_msg}")
                except:
                    logger.error(f"Failed to stop GPS tracking via auto-stop: HTTP {response.status_code}")
                return False
                
        except requests.exceptions.ConnectionError:
            logger.error("Cannot connect to GPS control API (connection refused)")
            return False
        except requests.exceptions.Timeout:
            logger.error("Timeout calling GPS control API")
            return False
        except Exception as e:
            logger.error(f"Error stopping GPS tracking: {e}")
            return False
    
    def restart_gps_startup_service_if_needed(self):
        """Restart GPS startup service if gps_start_mode is 'motion'"""
        try:
            settings = load_settings()
            gps_start_mode = settings.get('gps_start_mode', 'manual')
            
            if gps_start_mode == 'motion':
                logger.info("GPS start mode is 'motion' - restarting GPS startup service to monitor for next aircraft movement...")
                
                result = subprocess.run(['sudo', 'systemctl', 'restart', 'gps-startup.service'], 
                                      capture_output=True, timeout=30, text=True)
                
                if result.returncode == 0:
                    logger.info("GPS startup service restarted successfully - monitoring for next aircraft movement")
                else:
                    logger.error(f"Failed to restart GPS startup service: {result.stderr}")
            else:
                logger.info(f"GPS start mode is '{gps_start_mode}' - not restarting GPS startup service")
                
        except Exception as e:
            logger.error(f"Error restarting GPS startup service: {e}")


    def run(self):
        """Main monitoring loop"""
        logger.info("GPS Auto-Stop Monitor starting...")
        
        # Wait for initial movement before starting monitoring
        logger.info("Waiting for initial aircraft movement before starting auto-stop monitoring...")
        if not wait_for_motion():
            logger.info("Auto-stop monitor stopped before initial movement was detected")
            return
        
        logger.info("Initial movement detected - starting auto-stop monitoring")
        self.initial_movement_detected = True
        
        try:
            while self.running:
                # Load current settings (allows for dynamic updates)
                enabled, auto_stop_minutes = self.load_auto_stop_settings()
                
                if not enabled:
                    logger.info("Auto-stop disabled in settings, stopping monitor")
                    break
                
                self.auto_stop_minutes = auto_stop_minutes
                
                # Get current GPS position
                current_position = self.get_current_gps_position()
                
                if current_position:
                    # Check for movement
                    movement = self.check_movement(current_position)
                    
                    # Check if we should stop tracking
                    if self.should_stop_tracking():
                        if self.stop_gps_tracking():
                            logger.info("GPS tracking stopped successfully via auto-stop")
                            break
                        else:
                            logger.error("Failed to stop GPS tracking, will retry")
                            # Reset stationary timer to retry later
                            self.stationary_start_time = datetime.now()
                else:
                    logger.warning("No GPS position available for auto-stop monitoring")
                
                # Sleep before next check
                time.sleep(POSITION_CHECK_INTERVAL)
        
        except Exception as e:
            logger.error(f"Unexpected error in auto-stop monitor: {e}")
        
        finally:
            logger.info("GPS Auto-Stop Monitor stopped")


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info("GPS Auto-Stop Monitor shutting down...")
    sys.exit(0)


def main():
    """Main monitoring loop"""
    monitor = AutoStopMonitor()
    monitor.run()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='GPS Auto-Stop Monitor')
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