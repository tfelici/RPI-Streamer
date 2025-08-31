#!/usr/bin/env python3
"""
RPI Streamer GPS Tracker

Background GPS tracking application using gpsd daemon for GPS access.
Tracks GPS coordinates and synchronizes them with the Gyropilots server.

Features:
- GNSS support via gpsd daemon (GPS + GLONASS + Galileo + BeiDou)
- Background coordinate synchronization
- Simulation mode for testing
- Hardware resilience and auto-reconnection

Requirements for real GPS hardware:
- gpsd daemon service
- Compatible GPS/GNSS hardware (USB GPS, HAT, etc.)
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
import math
import random
import os
from datetime import datetime
from typing import Dict, List, Optional
from utils import generate_gps_track_id, calculate_distance, get_gnss_location

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False

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
logger.info("GPS functionality available via gpsd daemon")

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

    def start_tracking(self) -> bool:
        """Start a new tracking session"""
        if self.tracking_active:
            logger.warning("Tracking is already active")
            return False
            
        if not self.track_id:
            self.track_id = generate_gps_track_id()
        self.coordinates_to_sync = []
        self.tracking_active = True
        
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
        
        # Send tracking ended signal
        self._send_tracking_ended()
        
        # GPS runs continuously via gpsd - no need to stop it
        logger.info("GPS continues running via gpsd (not stopped)")
        
        self.tracking_active = False
        self.track_id = None
        self.coordinates_to_sync = []
        
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
                'altitudeAccuracy': None,  # Not available in this implementation
                'heading': heading,
                'speed': speed
            }
        }
        
        self.coordinates_to_sync.append(coordinate)
        logger.info(f"Recorded location: lat={latitude:.6f}, lon={longitude:.6f}, alt={altitude}")
        
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
            'username': self.username
        }

    def start_gps_tracking(self, update_interval: float = 2.0, simulate: bool = False):
        """Start GPS coordinate collection with either real hardware or simulation"""
        if not self.tracking_active:
            logger.error("Tracking session not started. Call start_tracking() first.")
            return False
        
        if simulate:
            logger.info("Starting GPS simulation mode")
            logger.info("Running circular flight simulation from Oxford Airport UK")
            write_gps_status("simulation", "GPS simulation mode active - Oxford Airport circular flight")
            start_time = time.time()
            
            try:
                while self.tracking_active:
                    # Generate and add simulated GPS data
                    gps_data = simulate_gps_data()
                    self.add_location(**gps_data)
                    
                    # Update GPS status with current simulated position
                    write_gps_status("simulation", f"GPS simulation - lat: {gps_data['latitude']:.6f}, lon: {gps_data['longitude']:.6f}", gps_data)
                    
                    # Log progress for circular flight
                    elapsed = time.time() - start_time
                    if elapsed <= 60:  # During the circular flight
                        circle_progress = (elapsed / 60.0) * 100
                        logger.info(f"Circle progress: {circle_progress:.1f}% - Heading: {gps_data['heading']:.1f}°")
                    
                    time.sleep(update_interval)
            except KeyboardInterrupt:
                logger.info("GPS simulation interrupted")
                write_gps_status("simulation", "GPS simulation interrupted")
        else:
            logger.info("Starting real GPS hardware tracking")
            
            try:
                while self.tracking_active:
                    # Attempt to get coordinates from gpsd daemon
                    try:
                        # Use the new gpsd-based GPS function from utils
                        success, location_data = get_gnss_location()
                        
                        if success and location_data and location_data.get('fix_status') == 'valid':
                            # Calculate accuracy from DOP values
                            # HDOP (Horizontal DOP) is most relevant for position accuracy
                            # Rule of thumb: accuracy ≈ HDOP * URA (User Range Accuracy, ~3-5m for GPS)
                            hdop = location_data.get('hdop', 2.0)  # Default to 2.0 if not available
                            base_accuracy = 3.5  # Base GPS accuracy in meters
                            
                            # Ensure hdop is numeric for calculation
                            if isinstance(hdop, (int, float)):
                                calculated_accuracy = hdop * base_accuracy
                            else:
                                calculated_accuracy = base_accuracy * 2.0
                            
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
                                'altitude': location_data.get('altitude', 0),
                                'speed': speed_kmh,  # Convert from m/s to km/h
                                'heading': location_data.get('course', 0) or 0,
                                'accuracy': calculated_accuracy  # Calculated from HDOP
                            }
                            # Add the location to tracking
                            self.add_location(**gps_data)
                            logger.debug(f"GPS coordinates: lat={gps_data['latitude']:.6f}, lon={gps_data['longitude']:.6f}")
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
                # GPS runs continuously via gpsd - no need to stop
                logger.info("GPS tracking session ended (GPS continues running via gpsd)")
        
        return True


def simulate_gps_data():
    """Simulate GPS coordinates for circular flight path from Oxford Airport UK"""
    
    # Oxford Airport (Kidlington) coordinates
    oxford_lat = 51.8369
    oxford_lon = -1.3200
    
    # Initialize simulation state if not exists
    if not hasattr(simulate_gps_data, 'start_time'):
        simulate_gps_data.start_time = time.time()
        simulate_gps_data.altitude = 150  # Starting altitude in meters
    
    # Flight parameters
    circle_radius_km = 2.5  # 5km diameter = 2.5km radius
    flight_duration = 60.0  # 60 seconds for complete circle
    flight_altitude = 150  # meters above ground
    flight_speed_kmh = 94.2  # ~2.5km radius * 2 * pi / 60 seconds * 3.6 = 94.2 km/h
    
    # Calculate elapsed time
    elapsed_time = time.time() - simulate_gps_data.start_time
    
    # Calculate angle (0 to 2π over 60 seconds)
    angle_radians = (elapsed_time / flight_duration) * 2 * math.pi
    
    # Convert radius from km to degrees (approximate)
    # 1 degree latitude ≈ 111 km
    # 1 degree longitude ≈ 111 km * cos(latitude)
    lat_deg_per_km = 1.0 / 111.0
    lon_deg_per_km = 1.0 / (111.0 * math.cos(math.radians(oxford_lat)))
    
    # Calculate circular position relative to Oxford Airport
    lat_offset = circle_radius_km * lat_deg_per_km * math.sin(angle_radians)
    lon_offset = circle_radius_km * lon_deg_per_km * math.cos(angle_radians)
    
    # Calculate current position
    current_lat = oxford_lat + lat_offset
    current_lon = oxford_lon + lon_offset
    
    # Calculate heading (direction of travel)
    # Heading is perpendicular to radius, so add 90 degrees to angle
    heading = (math.degrees(angle_radians) + 90) % 360
    
    # Add some realistic variation
    altitude_variation = random.uniform(-5, 5)  # ±5 meters
    speed_variation = random.uniform(0.9, 1.1)  # ±10%
    accuracy = random.uniform(2, 8)  # GPS accuracy in meters
    
    return {
        'latitude': current_lat,
        'longitude': current_lon,
        'altitude': flight_altitude + altitude_variation,
        'accuracy': accuracy,
        'heading': heading,
        'speed': flight_speed_kmh * speed_variation  # km/h
    }


def main():
    parser = argparse.ArgumentParser(description='RPI Streamer GPS Tracker')
    parser.add_argument('username', help='Username for tracking session')
    parser.add_argument('--domain', required=True, help='Server domain (gyropilots.org or gapilots.org)')
    parser.add_argument('--interval', type=float, default=2.0, help='GPS update interval in seconds')
    parser.add_argument('--simulate', action='store_true', help='Run with simulated GPS data for testing')
    parser.add_argument('--duration', type=int, help='Duration to run in seconds (for simulation)')
    parser.add_argument('--track_id', type=str, help='Optional: Use a specific track ID for the session')
    
    args = parser.parse_args()
    
    # GPS tracker PID file - only one GPS tracker should be active at a time
    GPS_PIDFILE = "/tmp/gps-tracker.pid"

    def cleanup_pidfile():
        try:
            if os.path.exists(GPS_PIDFILE):
                os.remove(GPS_PIDFILE)
                logger.info(f"Removed PID file: {GPS_PIDFILE}")
        except Exception as e:
            logger.warning(f"Could not remove active PID file on exit: {e}")
        
        # Also cleanup status file
        cleanup_gps_status()

    def handle_exit(signum, frame):
        logger.info(f"Received exit signal {signum}, cleaning up...")
        cleanup_pidfile()
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
        
        if args.simulate:
            # Start GPS coordinate collection with simulation
            tracker.start_gps_tracking(args.interval, simulate=True)
        else:
            # Start GPS coordinate collection with real hardware
            # Always start the tracking process - it will handle hardware initialization internally
            tracker.start_gps_tracking(args.interval, simulate=False)
                    
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
        
    finally:
        # Stop tracking
        tracker.stop_tracking()
        cleanup_pidfile()
        logger.info("GPS Tracker stopped")


if __name__ == '__main__':
    main()
