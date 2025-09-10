#!/usr/bin/env python3
"""
RPI Streamer GPS Tracker

Background GPS tracking application using GPS daemon client for GPS access.
Tracks GPS coordinates and synchronizes them with the Gyropilots server.

Features:
- GNSS support via GPS daemon client (GPS + GLONASS + Galileo + BeiDou)
- Background coordinate synchronization
- Hardware resilience and auto-reconnection

Requirements for real GPS hardware:
- Compatible GPS/GNSS hardware (USB GPS, HAT, SIM7600G-H cellular modem, etc.)
- GPS daemon running (automatically started when hardware is detected)
"""

import json
import time
import requests
import argparse
import logging
import sys
import threading
import queue
import signal
import re
import os
from datetime import datetime
from typing import Dict, List, Optional
from utils import generate_gps_track_id, calculate_distance, get_storage_path, cleanup_pidfile
from gps_client import get_gnss_location

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('gps_tracker.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# Log GPS functionality
logger.info("GPS functionality available via GPS daemon client")

# Status file for tracking GPS hardware state
GPS_STATUS_FILE = "/tmp/gps-tracker-status.json"

def write_gps_status(hardware_status, status_message, last_gps_data=None):
    """Write GPS tracking status to status file for web interface"""
    try:
        status_data = {
            'hardware_status': hardware_status,
            'status_message': status_message,
            'last_update': datetime.now().isoformat(),
            'last_gps_data': last_gps_data
        }
        
        with open(GPS_STATUS_FILE, 'w') as f:
            json.dump(status_data, f)
            
    except Exception as e:
        logger.debug(f"Could not write GPS status file: {e}")

def cleanup_gps_status():
    """Remove GPS status file on exit"""
    try:
        if os.path.exists(GPS_STATUS_FILE):
            os.remove(GPS_STATUS_FILE)
    except Exception:
        pass


class GPSTracker:
    def __init__(self, username: str, domain: str, track_id: Optional[str] = None):
        # Validate required parameters
        if not username or not username.strip():
            raise ValueError("Username is required and cannot be empty")
        if not domain or not domain.strip():
            raise ValueError("Domain is required and cannot be empty")
        
        self.username = username.strip()
        self.domain = domain.strip()
        self.base_url = f'https://{self.domain}'
        self.track_id = track_id if track_id else None
        self.coordinates_to_sync = []
        self.sync_active = False
        self.tracking_active = False
        self.sync_queue = queue.Queue()
        self.session = requests.Session()
        
        # GPS state tracking
        self.gps_started = False
        
        # Track file storage
        self.track_file_path = None
        self.usb_mount = None
        
        # Movement detection
        self.last_position = None
        self.movement_threshold = 5.0  # meters - minimum distance to consider as movement
        self.last_recorded_time = None
        self.is_stationary = False  # Track current movement state
        
        # Configuration - matching the mobile app settings
        self.sync_interval = 2.0  # seconds
        self.sync_timeout = 10.0  # seconds
        self.max_retry_attempts = 3
        self.sync_threshold = 100  # max coordinates to hold before forced sync
        
        # Platform identifier
        self.platform = "RPI-Streamer-Python/1.0"
        
        logger.info(f"GPS Tracker initialized for user: {username} on domain: {domain}")

    def _setup_track_storage(self) -> bool:
        """Set up track file storage (USB or local), similar to video recording storage"""
        # Get storage path for tracks
        track_dir, self.usb_mount = get_storage_path('tracks')
        
        # Create tracks directory
        os.makedirs(track_dir, exist_ok=True)
        
        # Create track file path - using .tsv (tab-separated values) for crash resistance
        self.track_file_path = os.path.join(track_dir, f"{self.track_id}.tsv")
        
        # Initialize track file with header and metadata as comments
        try:
            with open(self.track_file_path, 'w') as f:
                # Write metadata as comments at the top
                f.write(f"# Track ID: {self.track_id}\n")
                f.write(f"# Username: {self.username}\n")
                f.write(f"# Domain: {self.domain}\n")
                f.write(f"# Start Time: {datetime.now().isoformat()}\n")
                f.write(f"# Platform: {self.platform}\n")
                f.write("#\n")
                # Write header row
                f.write("timestamp\tlatitude\tlongitude\taltitude\taccuracy\taltitudeAccuracy\theading\tspeed\n")
            logger.info(f"Track file initialized: {self.track_file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize track file: {e}")
            return False

    def _save_coordinate_to_file(self, coordinate: Dict) -> bool:
        """Save a coordinate to the track file as a tab-delimited line"""
        if not self.track_file_path:
            return False
            
        try:
            # Extract location data
            location = coordinate['location']
            timestamp = coordinate['timestamp']
            
            # Format values, using empty string for None values
            values = [
                str(timestamp),
                str(location['latitude']),
                str(location['longitude']),
                str(location.get('altitude', '')),
                str(location.get('accuracy', '')),
                str(location.get('altitudeAccuracy', '')),
                str(location.get('heading', '')),
                str(location.get('speed', ''))
            ]
            
            # Write as tab-delimited line
            with open(self.track_file_path, 'a') as f:
                f.write('\t'.join(values) + '\n')
            
            # Note: USB sync is handled by the cleanup_pidfile function and stop_tracking()
            # No need to sync after every coordinate - this would hurt performance
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to save coordinate to file: {e}")
            return False

    def start_tracking(self) -> bool:
        """Start a new tracking session"""
        if self.tracking_active:
            logger.warning("Tracking is already active")
            return False
            
        if not self.track_id:
            self.track_id = generate_gps_track_id()
        self.coordinates_to_sync = []
        self.tracking_active = True
        
        # Set up track file storage
        if not self._setup_track_storage():
            logger.error("Failed to set up track storage")
            self.tracking_active = False
            return False
        
        # Reset movement detection
        self.last_position = None
        self.last_recorded_time = None
        self.is_stationary = False
        
        logger.info(f"Started tracking session with ID: {self.track_id}")
        
        # Start the sync worker thread
        self.sync_thread = threading.Thread(target=self._sync_worker, daemon=True)
        self.sync_thread.start()
        
        return True

    def stop_tracking(self) -> bool:
        """Stop the current tracking session"""
        if not self.tracking_active:
            logger.warning("No active tracking session to stop")
            return False
            
        logger.info(f"Stopping tracking session: {self.track_id}")
        
        # Sync any remaining coordinates before stopping
        if self.coordinates_to_sync:
            logger.info("Syncing remaining coordinates before stop...")
            self._sync_coordinates_to_server()
        
        # Final sync to disk if using USB
        if self.usb_mount:
            logger.info("Syncing final track data to USB drive...")
            try:
                import subprocess
                subprocess.run(['sync'], check=True)
                time.sleep(2)  # Give extra time for exFAT/USB
                logger.info("Final track sync completed. It is now safe to remove the USB drive.")
            except Exception as e:
                logger.warning(f"Final track sync failed: {e}")
        
        # Send tracking ended signal
        self._send_tracking_ended()
        
        # GPS hardware continues running via daemon
        logger.info("GPS continues running via daemon")
        
        self.tracking_active = False
        self.track_id = None
        self.coordinates_to_sync = []
        self.track_file_path = None
        self.usb_mount = None
        
        logger.info("Tracking session stopped")
        return True

    def _should_record_location(self, latitude: float, longitude: float) -> bool:
        """Determine if a location should be recorded based on movement state transitions"""
        current_time = time.time()
        
        # Always record the first position
        if self.last_position is None or self.last_recorded_time is None:
            self.last_position = (latitude, longitude)
            self.last_recorded_time = current_time
            self.is_stationary = False  # Assume we start moving
            logger.debug("Recording first GPS position")
            return True
        
        # Calculate distance from last recorded position
        distance = calculate_distance(
            self.last_position[0], self.last_position[1],
            latitude, longitude
        )
        
        # Determine if currently moving based on distance threshold
        currently_moving = distance >= self.movement_threshold
        
        # Check for state transitions
        if not self.is_stationary and not currently_moving:
            # Transition: Moving -> Stationary
            logger.debug(f"Aircraft stopped: recording stationary position (moved {distance:.1f}m)")
            self.last_position = (latitude, longitude)
            self.last_recorded_time = current_time
            self.is_stationary = True
            return True
            
        elif self.is_stationary and currently_moving:
            # Transition: Stationary -> Moving
            logger.debug(f"Aircraft started moving: {distance:.1f}m from stationary position")
            self.last_position = (latitude, longitude)
            self.last_recorded_time = current_time
            self.is_stationary = False
            return True
            
        elif not self.is_stationary and currently_moving:
            # Continuing to move - record this position
            logger.debug(f"Continued movement: {distance:.1f}m from last position")
            self.last_position = (latitude, longitude)
            self.last_recorded_time = current_time
            return True
        
        # Still stationary - don't record redundant positions
        logger.debug(f"Still stationary: movement {distance:.1f}m < {self.movement_threshold}m threshold")
        return False

    def add_location(self, latitude: float, longitude: float, 
                    altitude: Optional[float] = None, 
                    accuracy: Optional[float] = None,
                    altitudeAccuracy: Optional[float] = None,
                    heading: Optional[float] = None, 
                    speed: Optional[float] = None) -> bool:
        """Add a GPS location point to the tracking session (only if movement detected)"""
        if not self.tracking_active:
            logger.warning("Cannot add location - tracking is not active")
            return False
        
        # Check if location should be recorded based on movement
        if not self._should_record_location(latitude, longitude):
            return False  # Location not recorded due to insufficient movement
            
        timestamp = int(time.time())
        
        coordinate = {
            'timestamp': timestamp,
            'location': {
                'latitude': latitude,
                'longitude': longitude,
                'altitude': altitude,
                'accuracy': accuracy,
                'altitudeAccuracy': altitudeAccuracy,
                'heading': heading,
                'speed': speed
            }
        }
        
        self.coordinates_to_sync.append(coordinate)
        
        # Also save coordinate to local file
        self._save_coordinate_to_file(coordinate)
        
        speed_info = f", speed={speed:.1f}m/s" if speed is not None and speed > 0 else ""
        alt_info = f", alt={altitude:.1f}m" if altitude is not None else ""
        acc_info = f", acc={accuracy:.1f}m" if accuracy is not None else ""
        logger.info(f"Recorded location: lat={latitude:.6f}, lon={longitude:.6f}{alt_info}{speed_info}{acc_info}")
        
        # Queue sync if we have too many coordinates
        if len(self.coordinates_to_sync) >= self.sync_threshold:
            logger.info("Sync threshold reached, queuing immediate sync")
            self.sync_queue.put('sync_now')
            
        return True

    def _sync_worker(self):
        """Background worker thread for coordinate synchronization"""
        logger.info("Sync worker thread started")
        
        while self.tracking_active:
            try:
                # Wait for sync signal or timeout
                try:
                    signal = self.sync_queue.get(timeout=self.sync_interval)
                    if signal == 'sync_now':
                        self._sync_coordinates_to_server()
                except queue.Empty:
                    # Regular interval sync
                    if self.coordinates_to_sync:
                        self._sync_coordinates_to_server()
                        
            except Exception as e:
                logger.error(f"Error in sync worker: {e}")
                time.sleep(1)  # Brief pause before retrying
                
        logger.info("Sync worker thread stopped")

    def _sync_coordinates_to_server(self) -> bool:
        """Sync coordinates to server (replicates syncTrackToServer function)"""
        if self.sync_active or not self.coordinates_to_sync:
            return False
            
        self.sync_active = True
        
        try:
            logger.info(f"Syncing {len(self.coordinates_to_sync)} coordinates to server")
            
            # Prepare data for server - matching the mobile app format
            data = {
                'username': self.username,
                'command': 'addpoints',
                'platform': self.platform,
                'trackid': self.track_id,
                'coordinates': json.dumps(self.coordinates_to_sync)
            }
            
            # Make the request
            response = self.session.post(
                f'{self.base_url}/trackflight.php',
                data=data,
                timeout=self.sync_timeout
            )
            
            if response.status_code == 200:
                try:
                    parsed_data = response.json()
                    
                    if 'error' in parsed_data and parsed_data['error']:
                        logger.error(f"Server error: {parsed_data['error']}")
                        return False
                    
                    # Remove successfully synced coordinates
                    if 'timestamps' in parsed_data:
                        synced_timestamps = parsed_data['timestamps']
                        logger.info(f"Server confirmed {len(synced_timestamps)} coordinates synced")
                        
                        # Remove synced coordinates from local storage
                        self.coordinates_to_sync = [
                            coord for coord in self.coordinates_to_sync 
                            if coord['timestamp'] not in synced_timestamps
                        ]
                        
                    logger.info(f"Sync successful. {len(self.coordinates_to_sync)} coordinates remaining")
                    return True
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse server response: {e}")
                    logger.debug(f"Response content: {response.text}")
                    return False
                    
            else:
                logger.error(f"HTTP error {response.status_code}: {response.text}")
                return False
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error during sync: {e}")
            return False
            
        except Exception as e:
            logger.error(f"Unexpected error during sync: {e}")
            return False
            
        finally:
            self.sync_active = False

    def _send_tracking_ended(self):
        """Send tracking ended signal to server"""
        try:
            data = {
                'command': 'trackingended',
                'username': self.username,
                'trackid': self.track_id
            }
            
            response = self.session.post(
                f'{self.base_url}/trackflight.php',
                data=data,
                timeout=self.sync_timeout
            )
            
            if response.status_code == 200:
                logger.info("Successfully sent tracking ended signal")
            else:
                logger.warning(f"Failed to send tracking ended signal: HTTP {response.status_code}")
                
        except Exception as e:
            logger.error(f"Error sending tracking ended signal: {e}")

    def get_status(self) -> Dict:
        """Get current tracker status"""
        return {
            'tracking_active': self.tracking_active,
            'track_id': self.track_id,
            'pending_coordinates': len(self.coordinates_to_sync),
            'sync_active': self.sync_active,
            'username': self.username,
            'track_file_path': self.track_file_path,
            'usb_storage': self.usb_mount is not None
        }

    def start_gps_tracking(self, update_interval: float = 2.0):
        """Start GPS coordinate collection with real hardware"""
        if not self.tracking_active:
            logger.error("Tracking session not started. Call start_tracking() first.")
            return False
        
        logger.info("Starting real GPS hardware tracking")
        
        try:
            while self.tracking_active:
                # Attempt to get coordinates from GPS daemon
                try:
                    # Use the GPS daemon client
                    success, location_data = get_gnss_location()
                    
                    if success and location_data and location_data.get('fix_status') == 'valid':
                        # Get accuracy directly from GPS daemon (no longer need to calculate from HDOP)
                        gps_accuracy = location_data.get('accuracy', 10.0)  # Default to 10m if not available
                        
                        # Convert speed from m/s to km/h if needed
                        speed_ms = location_data.get('speed', 0) or 0
                        
                        # Ensure speed is numeric for calculation
                        if isinstance(speed_ms, (int, float)):
                            speed_kmh = speed_ms * 3.6
                        else:
                            speed_kmh = 0
                        
                        # Convert GNSS data format to GPS format for compatibility
                        gps_data = {
                            'latitude': location_data['latitude'],
                            'longitude': location_data['longitude'],
                            'altitude': location_data.get('altitude'),  # meters above sea level
                            'accuracy': gps_accuracy,  # meters (horizontal accuracy)
                            'altitudeAccuracy': location_data.get('altitudeAccuracy'),  # meters (vertical accuracy)
                            'heading': location_data.get('heading'),  # degrees relative to true north
                            'speed': speed_ms  # meters per second (not converted to km/h)
                        }
                        # Add the location to tracking
                        self.add_location(**gps_data)
                        speed_display = f", speed={speed_ms:.1f}m/s" if speed_ms and speed_ms > 0 else ""
                        alt_display = f", alt={gps_data['altitude']:.1f}m" if gps_data['altitude'] is not None else ""
                        logger.debug(f"GPS coordinates: lat={gps_data['latitude']:.6f}, lon={gps_data['longitude']:.6f}{alt_display}{speed_display}")
                        write_gps_status("active", f"GPS active - lat: {gps_data['latitude']:.6f}, lon: {gps_data['longitude']:.6f}", gps_data)
                    elif success and location_data and location_data.get('fix_status') == 'no_fix':
                        logger.debug("GPS data not available (no satellite fix)")
                        write_gps_status("connected", "GPS hardware connected but no satellite fix")
                    else:
                        # Error case
                        error_msg = location_data.get('error', 'GPS function error') if location_data else 'GPS function failed'
                        logger.warning(f"GPS error: {error_msg}")
                        write_gps_status("error", f"GPS error: {error_msg}")
                        
                except Exception as e:
                    logger.warning(f"Error getting GPS coordinates: {e}")
                    write_gps_status("error", f"GPS coordinate error: {e}")
                
                # Always sleep at the end of each cycle
                time.sleep(update_interval)
                
        except KeyboardInterrupt:
            logger.info("Real GPS tracking interrupted")
        finally:
            # GPS hardware continues running via daemon
            logger.info("GPS tracking session ended (GPS continues running via daemon)")
        
        return True


def main():
    parser = argparse.ArgumentParser(description='RPI Streamer GPS Tracker')
    parser.add_argument('username', help='Username for tracking session')
    parser.add_argument('--domain', required=True, help='Server domain (gyropilots.org or gapilots.org)')
    parser.add_argument('--interval', type=float, default=2.0, help='GPS update interval in seconds')
    parser.add_argument('--duration', type=int, help='Duration to run in seconds')
    parser.add_argument('--track_id', type=str, help='Optional: Use a specific track ID for the session')
    
    args = parser.parse_args()
    
    # GPS tracker PID file - only one GPS tracker should be active at a time
    GPS_PIDFILE = "/tmp/gps-tracker.pid"

    def handle_exit(signum, frame):
        logger.info(f"Received exit signal {signum}, cleaning up...")
        cleanup_pidfile(GPS_PIDFILE, cleanup_gps_status, sync_usb=True, logger=logger)
        logger.info("Exiting gracefully...")
        sys.exit(0)

    # Set up signal handlers for graceful cleanup
    signal.signal(signal.SIGTERM, handle_exit)
    signal.signal(signal.SIGINT, handle_exit)
    
    # Create GPS tracker instance
    tracker = GPSTracker(args.username, args.domain, track_id=args.track_id)
    
    try:
        # Start tracking
        if not tracker.start_tracking():
            logger.error("Failed to start tracking")
            sys.exit(1)
        
        # Write active PID file immediately after successful tracking start
        try:
            with open(GPS_PIDFILE, 'w') as f:
                f.write(f"{os.getpid()}:{args.username}:{args.domain}:{tracker.track_id}\n")
            logger.info(f"Active PID file written: {GPS_PIDFILE} with PID {os.getpid()}, user {args.username}, domain {args.domain}, track ID {tracker.track_id}")
        except Exception as e:
            logger.warning(f"Could not write active PID file: {e}")
        
        # Start GPS coordinate collection with real hardware
        tracker.start_gps_tracking(args.interval)
                    
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
        
    finally:
        # Stop tracking
        tracker.stop_tracking()
        cleanup_pidfile(GPS_PIDFILE, cleanup_gps_status, sync_usb=True, logger=logger)
        logger.info("GPS Tracker stopped")


if __name__ == '__main__':
    main()
