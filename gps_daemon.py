#!/usr/bin/env python3
"""
GPS Daemon for RPI Streamer
Continuously parses NMEA data from SIM7600G-H modem and serves location data to multiple clients.
Eliminates race conditions and provides real-time GPS data via Unix socket.
GPS hardware initialization is handled by modem_manager_daemon.
"""

import os
import sys
import time
import json
import serial
import socket
import threading
import signal
import argparse
import subprocess
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path
import math
import random

def simulate_gps_data(delay_seconds=0):
    """
    Simulate GPS coordinates for circular flight path from Oxford Airport UK
    
    Args:
        delay_seconds: Delay before starting movement (aircraft remains stationary)
    """
    
    # Oxford Airport (Kidlington) coordinates
    oxford_lat = 51.8369
    oxford_lon = -1.3200
    
    # Initialize simulation state if not exists
    if not hasattr(simulate_gps_data, 'start_time'):
        simulate_gps_data.start_time = time.time()
    
    # Calculate elapsed time since simulation started
    total_elapsed_time = time.time() - simulate_gps_data.start_time
    
    # Check if we're still in delay period
    if total_elapsed_time < delay_seconds:
        # Aircraft is stationary at Oxford Airport during delay
        ground_accuracy = random.uniform(2, 5)  # Good GPS accuracy on ground
        return {
            'latitude': oxford_lat,
            'longitude': oxford_lon,
            'altitude': 0,  # On ground
            'accuracy': ground_accuracy,
            'altitudeAccuracy': ground_accuracy * 1.2,  # Slightly worse altitude accuracy even on ground
            'heading': 0,  # Stationary
            'speed': 0  # No movement
        }
    
    # Calculate elapsed flight time (after delay)
    flight_elapsed_time = total_elapsed_time - delay_seconds
    
    # Flight parameters
    circle_radius_km = 2.5  # 5km diameter = 2.5km radius
    flight_duration = 60.0  # 60 seconds for complete circle
    max_altitude_meters = 304.8  # 1000 feet = 304.8 meters
    flight_speed_kmh = 94.2  # ~2.5km radius * 2 * pi / 60 seconds * 3.6 = 94.2 km/h
    
    # Calculate angle (0 to 2π over 60 seconds)
    angle_radians = (flight_elapsed_time / flight_duration) * 2 * math.pi
    
    # Calculate altitude profile based on flight phase
    # 0 to π/2 (0-90°): Takeoff - climb from 0 to 1000 feet
    # π/2 to 3π/2 (90-270°): Cruise at 1000 feet
    # 3π/2 to 2π (270-360°): Landing - descend from 1000 feet to 0
    angle_normalized = angle_radians % (2 * math.pi)
    
    if angle_normalized <= math.pi / 2:
        # Takeoff phase (0 to 90 degrees)
        altitude_progress = angle_normalized / (math.pi / 2)
        current_altitude = altitude_progress * max_altitude_meters
    elif angle_normalized <= 3 * math.pi / 2:
        # Cruise phase (90 to 270 degrees)
        current_altitude = max_altitude_meters
    else:
        # Landing phase (270 to 360 degrees)
        descent_progress = (angle_normalized - 3 * math.pi / 2) / (math.pi / 2)
        current_altitude = max_altitude_meters * (1 - descent_progress)
    
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
    altitude_variation = random.uniform(-3, 3)  # ±3 meters variation
    speed_variation = random.uniform(0.95, 1.05)  # ±5% speed variation
    accuracy = random.uniform(3, 8)  # GPS accuracy in meters (slightly worse at altitude)
    
    return {
        'latitude': current_lat,
        'longitude': current_lon,
        'altitude': max(0, current_altitude + altitude_variation),  # Ensure altitude doesn't go negative
        'accuracy': accuracy,
        'altitudeAccuracy': accuracy * 1.5,  # Altitude accuracy typically 1.5x horizontal accuracy
        'heading': heading,
        'speed': flight_speed_kmh * speed_variation  # km/h
    }

class GPSDaemon:
    def __init__(self, socket_path='/tmp/gps_daemon.sock', baudrate=115200, daemon_mode=False, simulate=False, delay_seconds=30):
        """
        Initialize GPS daemon.
        
        Args:
            socket_path: Unix socket path for client communication
            baudrate: Serial communication baudrate
            daemon_mode: Whether running in daemon mode (affects logging)
            simulate: Whether to run in GPS simulation mode
            delay_seconds: Delay in seconds before starting movement in simulation mode
        """
        self.socket_path = socket_path
        self.device_paths = ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyUSB2', '/dev/ttyUSB3', '/dev/ttyUSB4', '/dev/ttyUSB5', '/dev/ttyACM0', '/dev/ttyACM1']
        self.baudrate = baudrate
        self.daemon_mode = daemon_mode
        self.simulate = simulate
        self.delay_seconds = delay_seconds
        
        # Initialize logging
        # Use a named logger and avoid adding duplicate handlers if multiple
        # GPSDaemon instances are created. For daemon_mode attempt syslog,
        # otherwise fall back to console. Also disable propagation to avoid
        # duplicate messages to the root logger.
        self.logger = logging.getLogger('gps-daemon')
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        # Only configure handlers if none exist yet for this logger
        if not self.logger.handlers:
            if self.daemon_mode:
                # For daemon mode, prefer syslog/journal
                try:
                    handler = logging.handlers.SysLogHandler(address='/dev/log')
                    formatter = logging.Formatter('gps-daemon[%(process)d]: %(message)s')
                    handler.setFormatter(formatter)
                    self.logger.addHandler(handler)
                except Exception:
                    # Fallback to console if syslog not available
                    handler = logging.StreamHandler(sys.stdout)
                    formatter = logging.Formatter('[%(asctime)s] GPS Daemon: %(message)s')
                    handler.setFormatter(formatter)
                    self.logger.addHandler(handler)
            else:
                # For interactive mode, log to console
                handler = logging.StreamHandler(sys.stdout)
                formatter = logging.Formatter('[%(asctime)s] GPS Daemon: %(message)s')
                handler.setFormatter(formatter)
                self.logger.addHandler(handler)
        
        # Current location data
        self.location_data = {
            'fix_status': 'no_fix',
            'latitude': None,
            'longitude': None,
            'altitude': None,
            'speed': None,
            'heading': None,
            'fix_type': None,
            'accuracy': None,
            'altitudeAccuracy': None,
            'satellites': {
                'used': 0,
                'total': 0,
                'constellations': {
                    'GPS': {'visible': 0, 'used': 0, 'max_snr': 0},
                    'GLONASS': {'visible': 0, 'used': 0, 'max_snr': 0},
                    'Galileo': {'visible': 0, 'used': 0, 'max_snr': 0},
                    'BeiDou': {'visible': 0, 'used': 0, 'max_snr': 0}
                }
            },
            'timestamp': time.time(),
            'daemon_status': 'starting'
        }
        
        # Thread control
        self.running = True
        self.gps_thread = None
        self.server_thread = None
        self.current_device = None
        self.serial_connection = None
        
        # Status tracking
        self.last_fix_time = None
        self.total_sentences_parsed = 0
        self.daemon_start_time = time.time()
        
    def log(self, message):
        """Log message using Python logging system"""
        self.logger.info(message)
    
    def run_command(self, command, description=None):
        """Run shell command and return success status"""
        if description:
            self.log(description)
        
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                if description:
                    self.log(f"✓ {description} - Success")
                return True
            else:
                self.log(f"✗ Command failed: {command}")
                if result.stderr:
                    self.log(f"Error: {result.stderr.strip()}")
                return False
        except subprocess.TimeoutExpired:
            self.log(f"✗ Command timed out: {command}")
            return False
        except Exception as e:
            self.log(f"✗ Command exception: {e}")
            return False
    
    def wait_for_dongle_initialization(self, max_wait_time=60):
        """Wait for GPS dongle to be fully initialized and responsive (hardware detection only)"""
        self.log("Waiting for GPS dongle hardware detection...")
        start_time = time.time()
        
        # Step 1: Wait for USB device to appear
        usb_ready = False
        while time.time() - start_time < max_wait_time and not usb_ready:
            # Check for SIM7600G-H device by ID or common names
            if self.run_command("lsusb | grep -i 'simcom\\|7600\\|1e0e:9011\\|simtech'", None):
                usb_ready = True
                self.log("✓ USB device detected")
            else:
                time.sleep(2)
        
        if not usb_ready:
            self.log("✗ USB device not detected within timeout")
            return False
        
        # Step 2: Wait for NMEA serial ports to appear (only check existence, don't access)
        ports_ready = False
        while time.time() - start_time < max_wait_time and not ports_ready:
            existing_ports = [path for path in self.device_paths if os.path.exists(path)]
            if existing_ports:
                ports_ready = True
                self.log(f"✓ NMEA ports available: {existing_ports}")
            else:
                time.sleep(2)
        
        if not ports_ready:
            self.log("✗ NMEA ports not available within timeout")
            return False
        
        # Step 3: Simple wait for device stabilization (no AT command testing during runtime)
        # AT command testing only happens during initialization when ModemManager is stopped
        stabilization_time = 5
        self.log(f"Waiting {stabilization_time}s for device stabilization...")
        time.sleep(stabilization_time)
        
        total_wait = time.time() - start_time
        self.log(f"✓ GPS dongle hardware detection complete (waited {total_wait:.1f}s)")
        return True
    


    
        
    def validate_nmea_checksum(self, line):
        """Validate NMEA sentence checksum"""
        if '*' not in line:
            return False
            
        sentence, checksum = line.rsplit('*', 1)
        calculated_checksum = 0
        for char in sentence[1:]:  # Skip the '$'
            calculated_checksum ^= ord(char)
        return f"{calculated_checksum:02X}" == checksum.upper()
    
    def parse_coordinate(self, coord_str, direction):
        """Parse NMEA coordinate (DDMM.MMMM or DDDMM.MMMM format)"""
        if not coord_str or not direction:
            return None
            
        try:
            # Remove any trailing/leading whitespace
            coord_str = coord_str.strip()
            
            # Find the decimal point to determine the format
            if '.' not in coord_str:
                return None
                
            dot_index = coord_str.find('.')
            
            # Determine if this is latitude (DDMM.MMMM) or longitude (DDDMM.MMMM)
            # based on the position of the decimal point
            if dot_index == 4:
                # Latitude format: DDMM.MMMM (decimal point at position 4)
                degrees = int(coord_str[:2])
                minutes = float(coord_str[2:])
            elif dot_index == 5:
                # Longitude format: DDDMM.MMMM (decimal point at position 5)
                degrees = int(coord_str[:3])
                minutes = float(coord_str[3:])
            else:
                # Invalid format
                return None
                
            coordinate = degrees + minutes / 60.0
            
            # Apply direction
            if direction in ['S', 'W']:
                coordinate = -coordinate
                
            return coordinate
        except (ValueError, IndexError):
            return None
    
    def parse_gga_sentence(self, parts):
        """Parse GGA sentence (position, altitude, fix quality)"""
        if len(parts) < 15:
            return False
            
        try:
            lat_str = parts[2]
            lat_dir = parts[3]
            lon_str = parts[4]
            lon_dir = parts[5]
            fix_quality = parts[6]
            num_sats = parts[7]
            hdop = parts[8]
            altitude = parts[9]
            
            # Check if we have a valid fix
            if fix_quality and fix_quality != '0' and lat_str and lon_str:
                latitude = self.parse_coordinate(lat_str, lat_dir)
                longitude = self.parse_coordinate(lon_str, lon_dir)
                
                if latitude is not None and longitude is not None:
                    self.location_data['fix_status'] = 'valid'
                    self.location_data['latitude'] = latitude
                    self.location_data['longitude'] = longitude
                    self.last_fix_time = time.time()
                    
                    if altitude:
                        self.location_data['altitude'] = float(altitude)
                    
                    if hdop:
                        # Convert HDOP to horizontal accuracy in meters
                        # Typical conversion: accuracy ≈ HDOP * 5 meters for consumer GPS
                        hdop_value = float(hdop)
                        self.location_data['accuracy'] = hdop_value * 5.0
                        
                        # Calculate altitude accuracy (typically worse than horizontal)
                        # VDOP is usually 1.5x HDOP, so altitude accuracy ≈ HDOP * 7.5 meters
                        if altitude:
                            self.location_data['altitudeAccuracy'] = hdop_value * 7.5
                        else:
                            self.location_data['altitudeAccuracy'] = None
                    
                    if num_sats:
                        self.location_data['satellites']['used'] = int(num_sats)
                    
                    # Determine fix type
                    self.location_data['fix_type'] = '3D' if altitude else '2D'
                    return True
                    
        except (ValueError, IndexError):
            pass
        return False
    
    def parse_rmc_sentence(self, parts):
        """Parse RMC sentence (speed, heading, date/time)"""
        if len(parts) < 12:
            return False
            
        try:
            status = parts[2]
            lat_str = parts[3]
            lat_dir = parts[4]
            lon_str = parts[5]
            lon_dir = parts[6]
            speed_knots = parts[7]
            heading = parts[8]
            
            # Check if we have valid data
            if status == 'A':  # A = Active/Valid
                # Parse speed (convert knots to m/s)
                if speed_knots:
                    self.location_data['speed'] = float(speed_knots) * 0.514444
                
                # Parse heading
                if heading:
                    self.location_data['heading'] = float(heading)
                
                # If we don't have position from GGA, get it from RMC
                if self.location_data['fix_status'] == 'no_fix' and lat_str and lon_str:
                    latitude = self.parse_coordinate(lat_str, lat_dir)
                    longitude = self.parse_coordinate(lon_str, lon_dir)
                    
                    if latitude is not None and longitude is not None:
                        self.location_data['fix_status'] = 'valid'
                        self.location_data['latitude'] = latitude
                        self.location_data['longitude'] = longitude
                        self.location_data['fix_type'] = '2D'  # RMC doesn't have altitude
                        self.last_fix_time = time.time()
                
                return True
                
        except (ValueError, IndexError):
            pass
        return False
    
    def parse_gsv_sentence(self, parts):
        """Parse GSV sentence (satellites in view)"""
        if len(parts) < 4:
            return False
            
        try:
            sentence_id = parts[0]
            total_msgs = int(parts[1])
            msg_num = int(parts[2])
            total_sats_in_constellation = int(parts[3])
            
            # Determine constellation based on sentence ID
            constellation = None
            if sentence_id.startswith('$GP'):
                constellation = 'GPS'
            elif sentence_id.startswith('$GL'):
                constellation = 'GLONASS'
            elif sentence_id.startswith('$GA'):
                constellation = 'Galileo'
            elif sentence_id.startswith('$BD') or sentence_id.startswith('$GB'):
                constellation = 'BeiDou'
            elif sentence_id.startswith('$GN'):
                # Generic GNSS - skip to avoid double counting
                return False
            
            if constellation and constellation in self.location_data['satellites']['constellations']:
                # Update constellation satellite count (only from first message of sequence)
                if msg_num == 1:
                    self.location_data['satellites']['constellations'][constellation]['visible'] = total_sats_in_constellation
                    # Reset used count for this constellation
                    self.location_data['satellites']['constellations'][constellation]['used'] = 0
                    self.location_data['satellites']['constellations'][constellation]['max_snr'] = 0
                
                # Parse individual satellite data in this message
                for sat_idx in range(4):
                    base_idx = 4 + (sat_idx * 4)
                    if base_idx + 3 < len(parts):
                        try:
                            sat_id = parts[base_idx]
                            elevation = parts[base_idx + 1]
                            azimuth = parts[base_idx + 2]
                            snr = parts[base_idx + 3]
                            
                            # If satellite has SNR data, process it
                            if snr and snr.strip():
                                snr_val = int(snr)
                                if snr_val > 0:
                                    # Track maximum SNR for this constellation
                                    if snr_val > self.location_data['satellites']['constellations'][constellation]['max_snr']:
                                        self.location_data['satellites']['constellations'][constellation]['max_snr'] = snr_val
                                    
                                    # Count as used satellite (satellites with good SNR)
                                    if snr_val >= 25:  # Threshold for "used" satellite
                                        self.location_data['satellites']['constellations'][constellation]['used'] += 1
                                        
                        except (ValueError, IndexError):
                            continue
                
                # Update total counts
                total_visible = sum(const['visible'] for const in self.location_data['satellites']['constellations'].values())
                total_used = sum(const['used'] for const in self.location_data['satellites']['constellations'].values())
                
                self.location_data['satellites']['total'] = total_visible
                # Don't override 'used' count if we got it from GGA sentence (more accurate)
                if self.location_data['satellites']['used'] == 0:
                    self.location_data['satellites']['used'] = total_used
                
                return True
                
        except (ValueError, IndexError):
            pass
        return False
    
    def find_gps_device(self):
        """Find and open GPS device with comprehensive port scanning and status reporting"""
        # First, get a snapshot of all existing serial devices
        existing_ports = [path for path in self.device_paths if os.path.exists(path)]
        
        self.log(f"Scanning {len(self.device_paths)} potential GPS ports... ({len(existing_ports)} ports exist)")
        
        if not existing_ports:
            self.log("No serial ports detected - waiting for SIM7600G-H hardware to appear")
            self.log(f"Expected ports: {', '.join(self.device_paths)}")
            return None
        
        self.log(f"Found existing ports: {existing_ports}")
        
        # Try to open each existing port
        for i, device_path in enumerate(existing_ports, 1):
            self.log(f"[{i}/{len(existing_ports)}] Testing GPS port: {device_path}")
            
            try:
                # Attempt to open the serial port
                ser = serial.Serial(device_path, self.baudrate, timeout=5)
                
                # Test if port is responsive (quick check)
                try:
                    # Try to read a line with a short timeout to see if data is flowing
                    ser.timeout = 2  # Short timeout for connection test
                    test_line = ser.readline().decode('ascii', errors='ignore').strip()
                    
                    # Even if no data, if we can open the port without error, consider it good
                    self.log(f"✓ [{i}/{len(existing_ports)}] GPS port opened successfully: {device_path}")
                    if test_line:
                        self.log(f"   Sample data received: {test_line[:50]}...")
                    else:
                        self.log(f"   Port open but no immediate data (GPS may be starting up)")
                    
                    # Reset timeout to normal value for actual operation
                    ser.timeout = 5
                    self.current_device = device_path
                    return ser
                    
                except Exception as read_e:
                    # If we can't read, still return the connection if it opened successfully
                    self.log(f"✓ [{i}/{len(existing_ports)}] Port {device_path} opened (read test failed: {read_e})")
                    ser.timeout = 5  # Reset timeout
                    self.current_device = device_path
                    return ser
                    
            except Exception as e:
                self.log(f"✗ [{i}/{len(existing_ports)}] Failed to open {device_path}: {e}")
                continue
        
        # No working GPS device found
        self.log(f"GPS scan complete - no accessible ports found")
        self.log(f"Available ports: {existing_ports}")
        self.log(f"Searched ports: {self.device_paths}")
        
        # Provide helpful diagnostic information
        if existing_ports:
            self.log("Serial ports exist but are not accessible - this may indicate:")
            self.log("  • Ports are in use by another process")
            self.log("  • Permission issues (check dialout group membership)")
            self.log("  • Hardware initialization still in progress")
        else:
            self.log("No serial ports detected - this may indicate:")
            self.log("  • SIM7600G-H hardware not connected")
            self.log("  • USB enumeration still in progress")
            self.log("  • Hardware driver issues")
        
        return None
    
    def gps_worker(self):
        """Main GPS parsing worker thread - handles both real GPS and simulation"""
        self.log("Starting GPS worker thread")
        
        if self.simulate:
            self.log("GPS simulation mode enabled")
            self.simulation_worker()
        else:
            self.log("Real GPS mode enabled")
            self.real_gps_worker()
    
    def simulation_worker(self):
        """Worker for GPS simulation mode"""
        self.location_data['daemon_status'] = 'simulation'
        
        if self.delay_seconds > 0:
            self.log(f"GPS simulation starting with {self.delay_seconds}s delay before movement")
        else:
            self.log("GPS simulation starting immediately")
        
        while self.running:
            try:
                # Generate simulated GPS data with delay parameter
                sim_data = simulate_gps_data(self.delay_seconds)
                
                # Update location data with simulated values
                self.location_data.update({
                    'timestamp': time.time(),
                    'latitude': sim_data['latitude'],
                    'longitude': sim_data['longitude'],
                    'altitude': sim_data['altitude'],
                    'accuracy': sim_data['accuracy'],
                    'altitudeAccuracy': sim_data['altitudeAccuracy'],
                    'heading': sim_data['heading'],
                    'speed': sim_data['speed'],
                    'fix_status': 'valid',
                    'daemon_status': 'fix_valid',
                    'satellites': {
                        'total': 12,  # Simulated satellite count
                        'used': 8,
                        'gps': 6,
                        'glonass': 4,
                        'galileo': 2,
                        'beidou': 0
                    }
                })
                
                # Sleep for 2 seconds to simulate GPS update rate
                time.sleep(2.0)
                
            except Exception as e:
                self.log(f"GPS simulation error: {e}")
                time.sleep(5)
        
        self.log("GPS simulation worker stopped")
    
    def real_gps_worker(self):
        """Worker for real GPS hardware"""
        # GPS should already be initialized at this point
        self.location_data['daemon_status'] = 'scanning_for_device'
        connection_attempts = 0
        
        while self.running:
            try:
                connection_attempts += 1
                
                # Try to find and open GPS device with progressive retry
                self.serial_connection = self.find_gps_device()
                if not self.serial_connection:
                    # Use shorter intervals initially, then longer intervals
                    if connection_attempts <= 6:  # First minute: 10-second intervals
                        retry_interval = 10
                        self.log(f"GPS device not available (attempt {connection_attempts}), retrying in {retry_interval} seconds...")
                    elif connection_attempts <= 18:  # Next 2 minutes: 10-second intervals  
                        retry_interval = 10
                        self.log(f"GPS device not available (attempt {connection_attempts}), continuing search every {retry_interval} seconds...")
                    else:  # After 3 minutes: 30-second intervals
                        retry_interval = 30
                        self.log(f"GPS device not available (attempt {connection_attempts}), long-term scanning every {retry_interval} seconds...")
                    
                    self.location_data['daemon_status'] = 'no_device'
                    time.sleep(retry_interval)
                    continue
                
                # Successfully connected - reset attempt counter
                connection_attempts = 0
                
                self.location_data['daemon_status'] = 'connected'
                self.log(f"Connected to GPS device: {self.current_device}")
                
                # Main NMEA parsing loop - ONLY place that reads NMEA data continuously
                while self.running and self.serial_connection:
                    try:
                        line = self.serial_connection.readline().decode('ascii', errors='ignore').strip()
                        
                        # Silently ignore empty lines - GPS module may not be outputting data yet
                        if not line:
                            continue
                            
                        if not line.startswith('$'):
                            continue
                        
                        # Validate checksum
                        if not self.validate_nmea_checksum(line):
                            continue
                        
                        # Update timestamp for each valid sentence
                        self.location_data['timestamp'] = time.time()
                        self.total_sentences_parsed += 1
                        
                        parts = line.split(',')
                        sentence_id = parts[0]
                        
                        # Parse different sentence types
                        if sentence_id.endswith('GGA'):
                            self.parse_gga_sentence(parts)
                        elif sentence_id.endswith('RMC'):
                            self.parse_rmc_sentence(parts)
                        elif sentence_id.endswith('GSV'):
                            self.parse_gsv_sentence(parts)
                        
                        # Update daemon status based on fix
                        if self.location_data['fix_status'] == 'valid':
                            self.location_data['daemon_status'] = 'fix_valid'
                        else:
                            # Check if we have satellites but no fix
                            total_sats = self.location_data['satellites']['total']
                            if total_sats > 0:
                                self.location_data['daemon_status'] = 'searching_fix'
                            else:
                                self.location_data['daemon_status'] = 'no_satellites'
                        
                    except Exception as e:
                        self.log(f"Error parsing NMEA data: {e}")
                        continue
                
            except Exception as e:
                self.log(f"GPS worker error: {e}")
                if self.serial_connection:
                    try:
                        self.serial_connection.close()
                    except:
                        pass
                    self.serial_connection = None
                
                self.location_data['daemon_status'] = 'error'
                time.sleep(5)  # Wait before retrying
        
        # Cleanup
        if self.serial_connection:
            try:
                self.serial_connection.close()
            except:
                pass
        
        self.log("Real GPS worker thread stopped")
    
    def handle_client(self, client_socket):
        """Handle client connection and requests"""
        try:
            while self.running:
                # Receive request from client
                data = client_socket.recv(1024)
                if not data:
                    break
                
                try:
                    request = json.loads(data.decode('utf-8'))
                except json.JSONDecodeError:
                    # Send error response
                    error_response = {'error': 'Invalid JSON request'}
                    client_socket.send(json.dumps(error_response).encode('utf-8'))
                    continue
                
                # Handle different request types
                if request.get('command') == 'get_location':
                    # Send current location data
                    response = self.location_data.copy()
                    # Add daemon statistics
                    response['daemon_stats'] = {
                        'uptime': time.time() - self.daemon_start_time,
                        'sentences_parsed': self.total_sentences_parsed,
                        'last_fix_time': self.last_fix_time,
                        'current_device': self.current_device
                    }
                    
                elif request.get('command') == 'get_status':
                    # Send only daemon status
                    response = {
                        'daemon_status': self.location_data['daemon_status'],
                        'fix_status': self.location_data['fix_status'],
                        'timestamp': self.location_data['timestamp'],
                        'daemon_stats': {
                            'uptime': time.time() - self.daemon_start_time,
                            'sentences_parsed': self.total_sentences_parsed,
                            'last_fix_time': self.last_fix_time,
                            'current_device': self.current_device
                        }
                    }
                    
                else:
                    response = {'error': 'Unknown command'}
                
                # Send response
                response_data = json.dumps(response).encode('utf-8')
                client_socket.send(response_data)
                
        except Exception as e:
            self.log(f"Client handler error: {e}")
        finally:
            try:
                client_socket.close()
            except:
                pass
    
    def server_worker(self):
        """Unix socket server worker thread"""
        self.log(f"Starting server on socket: {self.socket_path}")
        
        # Remove existing socket file
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        
        try:
            # Create Unix socket
            server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server_socket.bind(self.socket_path)
            server_socket.listen(5)
            
            # Set socket permissions
            os.chmod(self.socket_path, 0o666)
            
            self.log("GPS daemon server ready for connections")
            
            while self.running:
                try:
                    client_socket, _ = server_socket.accept()
                    # Handle each client in a separate thread
                    client_thread = threading.Thread(
                        target=self.handle_client,
                        args=(client_socket,),
                        daemon=True
                    )
                    client_thread.start()
                    
                except Exception as e:
                    if self.running:
                        self.log(f"Server accept error: {e}")
                    break
        
        except Exception as e:
            self.log(f"Server error: {e}")
        finally:
            try:
                server_socket.close()
            except:
                pass
            
            # Remove socket file
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        
        self.log("Server worker thread stopped")
    
    def start(self):
        """Start the GPS daemon"""        
        if self.simulate:
            self.log("Starting GPS Daemon in simulation mode")
        else:
            self.log("Starting GPS Daemon in real GPS mode")        
        # Start GPS worker thread
        self.gps_thread = threading.Thread(target=self.gps_worker, daemon=True)
        self.gps_thread.start()
        
        # Start server worker thread
        self.server_thread = threading.Thread(target=self.server_worker, daemon=True)
        self.server_thread.start()
        
        self.log("GPS Daemon started successfully")
    
    def stop(self):
        """Stop the GPS daemon"""
        self.log("Stopping GPS Daemon")
        self.running = False
        
        # Close serial connection
        if self.serial_connection:
            try:
                self.serial_connection.close()
            except:
                pass
        
        # Wait for threads to finish
        if self.gps_thread:
            self.gps_thread.join(timeout=5)
        if self.server_thread:
            self.server_thread.join(timeout=5)
        
        # Remove socket file
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        
        self.log("GPS Daemon stopped")


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    global daemon
    if daemon:
        daemon.stop()
    sys.exit(0)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='GPS Daemon for RPI Streamer')
    parser.add_argument('--socket', default='/tmp/gps_daemon.sock',
                        help='Unix socket path (default: /tmp/gps_daemon.sock)')
    parser.add_argument('--baudrate', type=int, default=115200,
                        help='Serial baudrate (default: 115200)')
    parser.add_argument('--pidfile', default='/tmp/gps_daemon.pid',
                        help='PID file path (default: /tmp/gps_daemon.pid)')
    parser.add_argument('--daemon', action='store_true', default=False,
                        help='Run in daemon mode (use syslog/journal for logging)')
    parser.add_argument('--simulate', action='store_true', default=False,
                        help='Run in GPS simulation mode for testing')
    parser.add_argument('--delay', type=int, default=0,
                        help='Delay in seconds before starting movement in simulation mode (default: 0)')
    
    args = parser.parse_args()
    
    # Import RotatingFileHandler for log rotation
    from logging.handlers import RotatingFileHandler
    
    # Configure logging with rotation to keep logs under 1MB
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    log_file = '/var/log/gps_daemon.log' if args.daemon else 'gps_daemon.log'
    
    # Create rotating file handler
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=1024*1024,  # 1MB max size
        backupCount=3        # Keep 3 backup files
    )
    file_handler.setFormatter(logging.Formatter(log_format))
    
    logging.basicConfig(
        level=logging.INFO, 
        format=log_format,
        handlers=[
            file_handler,
            logging.StreamHandler()
        ]
    )
    
    # If running interactively (not daemon) and stdout is a TTY, ensure
    # plain logging.info() calls print to the command line.
    try:
        if not args.daemon and sys.stdout.isatty():
            root_logger = logging.getLogger()
            # Add a StreamHandler to stdout if none exists
            has_stream = any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers)
            if not has_stream:
                sh = logging.StreamHandler(sys.stdout)
                sh.setFormatter(logging.Formatter(log_format))
                root_logger.addHandler(sh)
    except Exception:
        # If anything goes wrong checking the TTY, fall back to basicConfig only
        pass

    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Write PID file for systemd
    try:
        with open(args.pidfile, 'w') as f:
            f.write(str(os.getpid()))
        logging.info(f"GPS Daemon starting with PID {os.getpid()}")
    except Exception as e:
        logging.error(f"Failed to write PID file: {e}")
    
    # Create and start daemon
    global daemon
    daemon = GPSDaemon(
        socket_path=args.socket,
        baudrate=args.baudrate,
        daemon_mode=args.daemon,
        simulate=args.simulate,
        delay_seconds=args.delay
    )
    
    try:
        daemon.start()
        
        # Keep main thread alive
        while daemon.running:
            time.sleep(1)
            
    except KeyboardInterrupt:
        pass
    finally:
        daemon.stop()
        
        # Remove PID file
        if os.path.exists(args.pidfile):
            try:
                os.unlink(args.pidfile)
                logging.info("GPS Daemon stopped, PID file removed")
            except Exception as e:
                logging.error(f"Failed to remove PID file: {e}")


if __name__ == '__main__':
    main()
