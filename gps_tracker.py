#!/usr/bin/env python3
"""
GPS Tracker for RPI Streamer
Replicates the background geolocation tracking functionality from the Gyropilots mobile app
Uses the "non-native" mode for coordinate synchronization

Requirements for real GPS hardware:
- pyserial (pip install pyserial)
- RPi.GPIO (pip install RPi.GPIO)
- Waveshare SIM7600G-H 4G DONGLE properly connected

Hardware Setup:
- Connect SIM7600G-H to Raspberry Pi UART (default: /dev/ttyS0)
- Power key connected to GPIO pin 6 (configurable)
- Ensure UART is enabled in raspi-config
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
import serial
import re
import math
import random
import os
from datetime import datetime
from typing import Dict, List, Optional

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    logging.warning("RPi.GPIO not available - GPS hardware functionality will be limited")

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


class SIM7600GHardware:
    """Hardware interface for Waveshare SIM7600G-H 4G DONGLE GPS functionality"""
    
    def __init__(self, serial_port='/dev/ttyS0', baud_rate=115200, power_key=6):
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.power_key = power_key
        self.ser = None  # Will be initialized as serial.Serial
        self.gps_active = False
        self.initialized = False
        
        logger.info(f"SIM7600G Hardware interface initialized - Port: {serial_port}, Baud: {baud_rate}")
    
    def initialize_hardware(self):
        """Initialize the SIM7600G hardware"""
        if not GPIO_AVAILABLE:
            logger.error("RPi.GPIO not available - cannot initialize GPS hardware")
            return False
            
        try:
            # Initialize serial connection
            self.ser = serial.Serial(self.serial_port, self.baud_rate)
            self.ser.reset_input_buffer()
            
            # Power on the module
            self._power_on()
            self.initialized = True
            logger.info("SIM7600G hardware initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize SIM7600G hardware: {e}")
            return False
    
    def _power_on(self):
        """Power on the SIM7600G module"""
        logger.info('SIM7600G is starting...')
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.power_key, GPIO.OUT)
        time.sleep(0.1)
        GPIO.output(self.power_key, GPIO.HIGH)
        time.sleep(2)
        GPIO.output(self.power_key, GPIO.LOW)
        time.sleep(20)
        self.ser.reset_input_buffer()
        logger.info('SIM7600G is ready')
    
    def _power_down(self):
        """Power down the SIM7600G module"""
        if not GPIO_AVAILABLE or not self.initialized:
            return
            
        logger.info('SIM7600G is powering off...')
        GPIO.output(self.power_key, GPIO.HIGH)
        time.sleep(3)
        GPIO.output(self.power_key, GPIO.LOW)
        time.sleep(18)
        logger.info('SIM7600G powered down')
    
    def _send_at_command(self, command, expected_response, timeout):
        """Send AT command and wait for response"""
        if not self.ser:
            return False, ""
            
        try:
            # Clear input buffer
            self.ser.reset_input_buffer()
            
            # Send command
            self.ser.write((command + '\r\n').encode())
            time.sleep(timeout)
            
            # Read response
            response = ""
            if self.ser.in_waiting:
                time.sleep(0.01)
                response = self.ser.read(self.ser.in_waiting).decode()
            
            if response:
                if expected_response in response:
                    logger.debug(f"AT Command success: {command} -> {response.strip()}")
                    return True, response
                else:
                    logger.warning(f"AT Command failed: {command} -> {response.strip()}")
                    return False, response
            else:
                logger.warning(f"AT Command timeout: {command}")
                return False, ""
                
        except Exception as e:
            logger.error(f"AT Command error: {command} -> {e}")
            return False, ""
    
    def start_gps(self):
        """Start GPS functionality"""
        if not self.initialized:
            logger.error("Hardware not initialized")
            return False
            
        logger.info("Starting GPS session...")
        success, response = self._send_at_command('AT+CGPS=1,1', 'OK', 1)
        
        if success:
            self.gps_active = True
            logger.info("GPS started successfully")
            time.sleep(2)  # Allow GPS to initialize
            return True
        else:
            logger.error("Failed to start GPS")
            return False
    
    def stop_gps(self):
        """Stop GPS functionality"""
        if not self.initialized or not self.gps_active:
            return True
            
        logger.info("Stopping GPS session...")
        success, response = self._send_at_command('AT+CGPS=0', 'OK', 1)
        
        if success:
            self.gps_active = False
            logger.info("GPS stopped successfully")
            return True
        else:
            logger.error("Failed to stop GPS")
            return False
    
    def get_gps_data(self):
        """Get current GPS position data"""
        if not self.initialized or not self.gps_active:
            return None
            
        success, response = self._send_at_command('AT+CGPSINFO', '+CGPSINFO:', 1)
        
        if not success:
            return None
            
        # Parse GPS response
        # Format: +CGPSINFO: lat,N/S,lon,E/W,date,UTC time,alt,speed,course
        try:
            # Extract the GPS info line
            lines = response.strip().split('\n')
            gps_line = None
            
            for line in lines:
                if '+CGPSINFO:' in line:
                    gps_line = line
                    break
            
            if not gps_line:
                return None
                
            # Remove the command prefix
            gps_data = gps_line.split('+CGPSINFO: ')[1].strip()
            
            # Check if GPS has a fix (empty fields indicate no fix)
            if ',,,,,,' in gps_data:
                logger.debug("GPS fix not available")
                return None
            
            # Parse the comma-separated values
            parts = gps_data.split(',')
            
            if len(parts) < 9:
                logger.warning(f"Incomplete GPS data: {gps_data}")
                return None
            
            # Convert coordinates from DDMM.MMMM to decimal degrees
            lat_raw = parts[0]
            lat_dir = parts[1]
            lon_raw = parts[2] 
            lon_dir = parts[3]
            
            if not lat_raw or not lon_raw:
                return None
                
            # Convert DDMM.MMMM to decimal degrees
            lat_deg = float(lat_raw[:2]) + float(lat_raw[2:]) / 60.0
            if lat_dir == 'S':
                lat_deg = -lat_deg
                
            lon_deg = float(lon_raw[:3]) + float(lon_raw[3:]) / 60.0
            if lon_dir == 'W':
                lon_deg = -lon_deg
            
            # Extract other data
            altitude = float(parts[6]) if parts[6] else None
            speed_knots = float(parts[7]) if parts[7] else None
            course = float(parts[8]) if parts[8] else None
            
            # Convert speed from knots to km/h
            speed_kmh = speed_knots * 1.852 if speed_knots else None
            
            gps_result = {
                'latitude': lat_deg,
                'longitude': lon_deg,
                'altitude': altitude,
                'speed': speed_kmh,
                'heading': course,
                'accuracy': 5.0  # Approximate GPS accuracy in meters
            }
            
            logger.debug(f"GPS data: lat={lat_deg:.6f}, lon={lon_deg:.6f}, alt={altitude}, speed={speed_kmh}")
            return gps_result
            
        except Exception as e:
            logger.error(f"Error parsing GPS data: {e}")
            logger.debug(f"Raw GPS response: {response}")
            return None
    
    def check_hardware_presence(self):
        """Quick check if hardware is physically present and responding"""
        if not os.path.exists(self.serial_port):
            return False
        
        if not self.ser or not self.ser.is_open:
            return False
            
        try:
            # Quick AT command to verify hardware is responding
            success, _ = self._send_at_command('AT', 'OK', 0.5)
            return success
        except Exception:
            return False

    def cleanup(self):
        """Clean up hardware resources"""
        if self.gps_active:
            self.stop_gps()
            
        self._power_down()
        
        if self.ser:
            try:
                self.ser.close()
            except:
                pass
                
        if GPIO_AVAILABLE:
            try:
                GPIO.cleanup()
            except:
                pass
                
        self.initialized = False
        logger.info("SIM7600G hardware cleanup completed")


class GPSTracker:
    def __init__(self, username: str, host: str = 'gyropilots.org'):
        self.username = username
        self.host = host
        self.base_url = f'https://{host}'
        self.track_id = None
        self.coordinates_to_sync = []
        self.sync_active = False
        self.tracking_active = False
        self.sync_queue = queue.Queue()
        self.session = requests.Session()
        
        # GPS Hardware
        self.gps_hardware = None
        
        # Configuration - matching the mobile app settings
        self.sync_interval = 2.0  # seconds
        self.sync_timeout = 10.0  # seconds
        self.max_retry_attempts = 3
        self.sync_threshold = 100  # max coordinates to hold before forced sync
        
        # Platform identifier
        self.platform = "RPI-Streamer-Python/1.0"
        
        logger.info(f"GPS Tracker initialized for user: {username}")

    def generate_track_id(self) -> str:
        """Generate a unique track ID based on current timestamp"""
        return str(int(time.time()))

    def start_tracking(self) -> bool:
        """Start a new tracking session"""
        if self.tracking_active:
            logger.warning("Tracking is already active")
            return False
            
        self.track_id = self.generate_track_id()
        self.coordinates_to_sync = []
        self.tracking_active = True
        
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
        
        # Clean up GPS hardware if active
        if self.gps_hardware:
            self.gps_hardware.cleanup()
            self.gps_hardware = None
        
        self.tracking_active = False
        self.track_id = None
        self.coordinates_to_sync = []
        
        logger.info("Tracking session stopped")
        return True

    def add_location(self, latitude: float, longitude: float, 
                    altitude: Optional[float] = None, 
                    accuracy: Optional[float] = None,
                    heading: Optional[float] = None, 
                    speed: Optional[float] = None) -> bool:
        """Add a GPS location point to the tracking session"""
        if not self.tracking_active:
            logger.warning("Cannot add location - tracking is not active")
            return False
            
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
        logger.info(f"Added location: lat={latitude}, lon={longitude}, alt={altitude}")
        
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
            
            hardware_initialized = False
            
            try:
                while self.tracking_active:
                    # 1) Check if hardware needs initialization or re-initialization
                    if not hardware_initialized or not self._is_hardware_ready():
                        hardware_initialized = self._initialize_gps_hardware()
                        if not hardware_initialized:
                            # Failed to initialize, wait and try again next cycle
                            time.sleep(update_interval)
                            continue
                    
                    # 2) Hardware is ready, attempt to get coordinates
                    try:
                        if self.gps_hardware:  # Additional None check for type safety
                            gps_data = self.gps_hardware.get_gps_data()
                            
                            if gps_data:
                                # Add the location to tracking
                                self.add_location(**gps_data)
                                logger.debug(f"GPS coordinates: lat={gps_data['latitude']:.6f}, lon={gps_data['longitude']:.6f}")
                                write_gps_status("active", f"GPS active - lat: {gps_data['latitude']:.6f}, lon: {gps_data['longitude']:.6f}", gps_data)
                            else:
                                logger.debug("GPS data not available (no satellite fix)")
                                write_gps_status("connected", "GPS hardware connected but no satellite fix")
                        else:
                            # Hardware disappeared
                            logger.warning("GPS hardware object is None")
                            hardware_initialized = False
                            
                    except Exception as e:
                        logger.warning(f"Error getting GPS coordinates: {e}")
                        write_gps_status("error", f"GPS coordinate error: {e}")
                        # Mark for re-initialization on next cycle
                        hardware_initialized = False
                    
                    # Always sleep at the end of each cycle
                    time.sleep(update_interval)
                    
            except KeyboardInterrupt:
                logger.info("Real GPS tracking interrupted")
            finally:
                # Clean up GPS hardware
                if self.gps_hardware:
                    self.gps_hardware.cleanup()
                    self.gps_hardware = None
        
        return True
    
    def _initialize_gps_hardware(self):
        """Initialize or re-initialize GPS hardware"""
        write_gps_status("waiting", "Attempting to connect to GPS hardware...")
        logger.info("Attempting to initialize GPS hardware...")
        
        # Clean up any existing hardware
        if self.gps_hardware:
            try:
                self.gps_hardware.cleanup()
            except:
                pass
            self.gps_hardware = None
        
        # Try to initialize new hardware instance
        try:
            self.gps_hardware = SIM7600GHardware()
            
            if not self.gps_hardware.initialize_hardware():
                logger.warning("Failed to initialize GPS hardware")
                write_gps_status("disconnected", "GPS hardware not found")
                return False
            
            if not self.gps_hardware.start_gps():
                logger.warning("GPS hardware initialized but failed to start GPS module")
                write_gps_status("error", "GPS hardware found but failed to start GPS module")
                self.gps_hardware.cleanup()
                self.gps_hardware = None
                return False
            
            logger.info("GPS hardware initialized successfully")
            write_gps_status("connected", "GPS hardware connected and active")
            return True
            
        except Exception as e:
            logger.error(f"Exception during GPS hardware initialization: {e}")
            write_gps_status("error", f"GPS hardware initialization error: {e}")
            if self.gps_hardware:
                try:
                    self.gps_hardware.cleanup()
                except:
                    pass
                self.gps_hardware = None
            return False
    
    def _is_hardware_ready(self):
        """Check if GPS hardware is ready and responsive"""
        if not self.gps_hardware or not self.gps_hardware.initialized:
            return False
        
        # Quick check for hardware presence
        return self.gps_hardware.check_hardware_presence()


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
    parser.add_argument('--host', default='gyropilots.org', help='Server hostname')
    parser.add_argument('--interval', type=float, default=2.0, help='GPS update interval in seconds')
    parser.add_argument('--simulate', action='store_true', help='Run with simulated GPS data for testing')
    parser.add_argument('--duration', type=int, help='Duration to run in seconds (for simulation)')
    
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
    tracker = GPSTracker(args.username, args.host)
    
    try:
        # Start tracking
        if not tracker.start_tracking():
            logger.error("Failed to start tracking")
            sys.exit(1)
        
        # Write active PID file immediately after successful tracking start
        try:
            with open(GPS_PIDFILE, 'w') as f:
                f.write(f"{os.getpid()}:{args.username}:{args.host}:{tracker.track_id}\n")
            logger.info(f"Active PID file written: {GPS_PIDFILE} with PID {os.getpid()}, user {args.username}, track ID {tracker.track_id}")
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
