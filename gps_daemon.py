#!/usr/bin/env python3
"""
GPS Daemon for RPI Streamer
Continuously parses NMEA data from SIM7600G-H modem and serves location data to multiple clients.
Eliminates race conditions and provides real-time GPS data via Unix socket.
Includes GPS dongle initialization procedure.
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

class GPSDaemon:
    def __init__(self, socket_path='/tmp/gps_daemon.sock', device_paths=None, baudrate=115200, daemon_mode=False):
        """
        Initialize GPS daemon.
        
        Args:
            socket_path: Unix socket path for client communication
            device_paths: List of GPS device paths to try
            baudrate: Serial communication baudrate
            daemon_mode: Whether running in daemon mode (affects logging)
        """
        self.socket_path = socket_path
        self.device_paths = device_paths or ['/dev/ttyUSB1', '/dev/ttyUSB0', '/dev/ttyACM0', '/dev/ttyACM1']
        self.baudrate = baudrate
        self.daemon_mode = daemon_mode
        
        # Initialize logging
        self.logger = logging.getLogger('gps-daemon')
        self.logger.setLevel(logging.INFO)
        
        if self.daemon_mode:
            # For daemon mode, log to syslog/journal
            try:
                # Try to use SysLogHandler for systemd journal integration
                handler = logging.handlers.SysLogHandler(address='/dev/log')
                formatter = logging.Formatter('gps-daemon[%(process)d]: %(message)s')
                handler.setFormatter(formatter)
                self.logger.addHandler(handler)
            except:
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
            'course': None,
            'fix_type': None,
            'hdop': None,
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
    
    def send_at_command(self, serial_port, command, timeout=10):
        """Send AT command and wait for complete response"""
        try:
            # Clear any pending data
            serial_port.reset_input_buffer()
            
            # Send command
            self.log(f"Sending AT command: {command}")
            serial_port.write(f"{command}\r\n".encode('ascii'))
            
            # Poll for response
            response_lines = []
            start_time = time.time()
            
            while time.time() - start_time < timeout:
                if serial_port.in_waiting > 0:
                    line = serial_port.readline().decode('ascii', errors='ignore').strip()
                    if line:
                        response_lines.append(line)
                        self.log(f"AT response line: {line}")
                        
                        # Check for completion indicators
                        if line in ['OK', 'ERROR'] or line.startswith('+CME ERROR') or line.startswith('+CMS ERROR'):
                            break
                else:
                    time.sleep(0.1)  # Small delay to avoid busy waiting
            
            response = '\n'.join(response_lines)
            
            if not response_lines:
                self.log(f"✗ AT command timeout: {command}")
                return None, False
            
            # Determine success
            success = any(line == 'OK' for line in response_lines)
            if success:
                self.log(f"✓ AT command successful: {command}")
            else:
                self.log(f"✗ AT command failed: {command}")
            
            return response, success
            
        except Exception as e:
            self.log(f"✗ Exception sending AT command {command}: {e}")
            return None, False

    def initialize_gps_dongle(self):
        """Initialize GPS dongle by enabling GPS functionality"""
        self.log("Starting GPS dongle initialization...")
        
        # Wait for dongle to be ready
        if not self.wait_for_dongle_initialization():
            self.log("GPS dongle initialization failed - hardware not ready")
            return False
        
        # Step 1: Stop ModemManager to avoid conflicts
        self.log("Stopping ModemManager...")
        if not self.run_command("systemctl stop ModemManager", "Stopping ModemManager"):
            self.log("Warning: Could not stop ModemManager")
        
        # Wait a moment for ModemManager to fully stop
        time.sleep(2)
        
        # Step 2: Find the AT command port (usually /dev/ttyUSB2 or similar)
        at_ports = ['/dev/ttyUSB2', '/dev/ttyUSB3', '/dev/ttyACM2', '/dev/ttyACM3']
        at_port = None
        
        for port in at_ports:
            if os.path.exists(port):
                try:
                    with serial.Serial(port, 115200, timeout=5) as ser:
                        # Test basic AT communication with new function
                        response, success = self.send_at_command(ser, "AT")
                        if success:
                            at_port = port
                            self.log(f"✓ Found AT command port: {port}")
                            break
                except Exception as e:
                    self.log(f"Could not test AT port {port}: {e}")
                    continue
        
        if not at_port:
            self.log("✗ Could not find AT command port")
            # Re-enable ModemManager before returning
            self.run_command("systemctl start ModemManager", "Re-enabling ModemManager")
            return False
        
        # Step 3: Send GPS configuration commands
        gps_enabled = False
        try:
            with serial.Serial(at_port, 115200, timeout=10) as ser:
                # Test basic AT communication
                self.log("Testing AT communication...")
                response, success = self.send_at_command(ser, "AT")
                if not success:
                    self.log("✗ AT communication failed")
                    raise Exception("AT communication failed")
                
                self.log("✓ AT communication successful")
                
                # First, disable GPS to ensure clean state
                self.log("Disabling GPS for clean configuration...")
                response, success = self.send_at_command(ser, "AT+CGPS=0")
                self.log(f"GPS disable response: {response}")
                
                # Wait a moment for GPS to fully stop
                time.sleep(2)
                
                # Configure NMEA sentence types BEFORE enabling GPS
                self.log("Configuring NMEA sentence types (enabling comprehensive sentence set)...")
                response, success = self.send_at_command(ser, "AT+CGPSNMEA=198143")
                self.log(f"NMEA config response: {response}")
                
                # Set NMEA output rate to 1Hz BEFORE enabling GPS
                self.log("Setting NMEA output rate...")
                response, success = self.send_at_command(ser, "AT+CGPSNMEARATE=0")
                self.log(f"NMEA rate response: {response}")
                
                # Now enable GPS with the configured settings
                self.log("Enabling GPS with configured NMEA settings...")
                response, success = self.send_at_command(ser, "AT+CGPS=1")
                self.log(f"GPS enable response: {response}")
                
                # Verify GPS is enabled
                self.log("Verifying final GPS status...")
                response, success = self.send_at_command(ser, "AT+CGPS?")
                self.log(f"Final GPS status: {response}")
                
                if response and '+CGPS: 1' in response:
                    gps_enabled = True
                    self.log("✓ GPS successfully enabled")
                else:
                    self.log("✗ GPS enable verification failed")
                
        except Exception as e:
            self.log(f"✗ Error during GPS initialization: {e}")
        
        # Step 4: Re-enable ModemManager
        self.log("Re-enabling ModemManager...")
        if not self.run_command("systemctl start ModemManager", "Re-enabling ModemManager"):
            self.log("Warning: Could not restart ModemManager")
        
        if gps_enabled:
            self.log("✓ GPS dongle initialization completed successfully")
            return True
        else:
            self.log("✗ GPS dongle initialization failed")
            return False
        
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
            # Determine if this is latitude (DDMM.MMMM) or longitude (DDDMM.MMMM)
            if len(coord_str) >= 7:
                if len(coord_str) >= 8 and coord_str[3] != '.':
                    # Longitude format: DDDMM.MMMM
                    degrees = int(coord_str[:3])
                    minutes = float(coord_str[3:])
                else:
                    # Latitude format: DDMM.MMMM
                    degrees = int(coord_str[:2])
                    minutes = float(coord_str[2:])
                
                coordinate = degrees + minutes / 60.0
                
                # Apply direction
                if direction in ['S', 'W']:
                    coordinate = -coordinate
                    
                return coordinate
        except (ValueError, IndexError):
            pass
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
                        self.location_data['hdop'] = float(hdop)
                    
                    if num_sats:
                        self.location_data['satellites']['used'] = int(num_sats)
                    
                    # Determine fix type
                    self.location_data['fix_type'] = '3D' if altitude else '2D'
                    return True
                    
        except (ValueError, IndexError):
            pass
        return False
    
    def parse_rmc_sentence(self, parts):
        """Parse RMC sentence (speed, course, date/time)"""
        if len(parts) < 12:
            return False
            
        try:
            status = parts[2]
            lat_str = parts[3]
            lat_dir = parts[4]
            lon_str = parts[5]
            lon_dir = parts[6]
            speed_knots = parts[7]
            course = parts[8]
            
            # Check if we have valid data
            if status == 'A':  # A = Active/Valid
                # Parse speed (convert knots to m/s)
                if speed_knots:
                    self.location_data['speed'] = float(speed_knots) * 0.514444
                
                # Parse course
                if course:
                    self.location_data['course'] = float(course)
                
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
        """Find and open GPS device (simplified - just check if device exists and can be opened)"""
        for device_path in self.device_paths:
            if not os.path.exists(device_path):
                continue
                
            try:
                self.log(f"Trying GPS device: {device_path}")
                # Just try to open the device - don't read NMEA data here to avoid race conditions
                ser = serial.Serial(device_path, self.baudrate, timeout=5)  # Increased timeout for NMEA reading
                
                # Simple test - just verify we can open the port
                self.log(f"GPS device opened successfully: {device_path}")
                self.current_device = device_path
                return ser
                    
            except Exception as e:
                self.log(f"Failed to open {device_path}: {e}")
                continue
        
        # No GPS device found - check if any expected devices exist at all
        expected_devices_exist = any(os.path.exists(path) for path in self.device_paths)
        if not expected_devices_exist:
            self.log("No GPS hardware detected - no expected device paths exist")
            return None
        else:
            self.log("GPS devices exist but could not be opened")
            return None
    
    def gps_worker(self):
        """Main GPS parsing worker thread - ONLY function that reads NMEA data"""
        self.log("Starting GPS worker thread")
        
        # GPS should already be initialized at this point
        self.location_data['daemon_status'] = 'connecting'
        
        while self.running:
            try:
                # Try to find and open GPS device
                self.serial_connection = self.find_gps_device()
                if not self.serial_connection:
                    self.log("GPS device not available, retrying in 10 seconds...")
                    self.location_data['daemon_status'] = 'no_device'
                    time.sleep(10)
                    continue
                
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
        
        self.log("GPS worker thread stopped")
    
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
        self.log("Starting GPS Daemon")
        
        # Initialize GPS hardware once at startup
        self.log("Initializing GPS hardware...")
        if not self.initialize_gps_dongle():
            self.log("Warning: GPS initialization failed - daemon will continue and retry connection")
            # Don't fail completely - daemon will attempt to connect anyway
        
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
    parser.add_argument('--devices', nargs='+', 
                        default=['/dev/ttyUSB1', '/dev/ttyUSB0', '/dev/ttyACM0', '/dev/ttyACM1'],
                        help='GPS device paths to try')
    parser.add_argument('--baudrate', type=int, default=115200,
                        help='Serial baudrate (default: 115200)')
    parser.add_argument('--daemon', action='store_true',
                        help='Run as daemon (fork to background)')
    parser.add_argument('--pidfile', default='/tmp/gps_daemon.pid',
                        help='PID file path (default: /tmp/gps_daemon.pid)')
    
    args = parser.parse_args()
    
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Fork to background if daemon mode
    if args.daemon:
        try:
            pid = os.fork()
            if pid > 0:
                # Parent process - write PID file and exit
                with open(args.pidfile, 'w') as f:
                    f.write(str(pid))
                print(f"GPS Daemon started with PID {pid}")
                sys.exit(0)
        except OSError as e:
            print(f"Fork failed: {e}")
            sys.exit(1)
        
        # Child process - continue as daemon
        os.setsid()  # Create new session
        os.chdir('/')  # Change to root directory
        
        # Don't redirect stdout/stderr - let syslog handle logging
    
    # Create and start daemon
    global daemon
    daemon = GPSDaemon(
        socket_path=args.socket,
        device_paths=args.devices,
        baudrate=args.baudrate,
        daemon_mode=args.daemon
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
        if args.daemon and os.path.exists(args.pidfile):
            os.unlink(args.pidfile)


if __name__ == '__main__':
    main()
