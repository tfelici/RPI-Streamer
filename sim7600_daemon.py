#!/usr/bin/env python3
"""
SIM7600 Daemon Communication Manager

This module provides a daemon-based, system-wide singleton for communicating with the SIM7600G-H modem
to prevent serial port conflicts between different parts of the application (GPS tracker, 
4G status checking, etc.).

Features:
- Thread-safe access using threading.Lock
- TCP server for multi-process communication
- Automatic reconnection and error recovery
- System-wide singleton access via daemon
- Continuous GPS operation (auto-start on connect, stop on shutdown only)

GPS Management:
- GPS is automatically started when daemon connects to hardware
- GPS runs continuously to prevent race conditions between multiple clients
- GPS is only stopped when daemon shuts down
- Client interface provides GPS data access only (no start/stop control)

Usage:
1. Start daemon: python sim7600_daemon.py --host localhost --port 7600
2. Connect from clients: use SIM7600Client class or get_sim7600_client()
"""

import serial
import threading
import time
import logging
import re
import os
import socket
import json
import signal
import sys
import argparse
from typing import Tuple, Optional, Dict, Any
from contextlib import contextmanager

# Set up logging
logger = logging.getLogger(__name__)

class SIM7600Daemon:
    """
    Daemon server for SIM7600G-H modem communication.
    Provides system-wide singleton access via TCP server to prevent serial port conflicts
    between different parts of the application (GPS tracker, 4G status checking, etc.).
    """
    
    def __init__(self, port='/dev/ttyUSB2', baud_rate=115200, timeout=2, host='localhost', daemon_port=7600):
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.ser = None
        self._lock = threading.Lock()
        self._connection_attempts = 0
        self._max_connection_attempts = 3
        self._last_error = None
        
        # Daemon server attributes
        self.host = host
        self.daemon_port = daemon_port
        self.daemon_sock = None
        self.daemon_running = False
        
        # GPS management
        self._gps_started = False
        
    def _connect(self) -> bool:
        """
        Establish connection to the modem.
        Returns True if successful, False otherwise.
        """
        try:
            if self.ser and self.ser.is_open:
                return True
                
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                timeout=self.timeout,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE
            )
            
            # Test connection with simple AT command
            if self._send_raw_command('AT', 'OK', 1.0):
                logger.info(f"SIM7600 connection established on {self.port}")
                self._connection_attempts = 0
                
                # Auto-start GPS on successful connection if not already started
                self._auto_start_gps()
                
                return True
            else:
                self._disconnect()
                return False
                
        except Exception as e:
            self._last_error = str(e)
            logger.error(f"Failed to connect to SIM7600 on {self.port}: {e}")
            self._disconnect()
            return False
    
    def _disconnect(self):
        """Safely close the serial connection."""
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception as e:
            logger.warning(f"Error closing SIM7600 connection: {e}")
        finally:
            self.ser = None
    
    def _send_raw_command(self, command: str, expected_response: str, timeout: float) -> Tuple[bool, str]:
        """
        Send raw AT command without locking (internal use only).
        Returns (success, response).
        """
        if not self.ser or not self.ser.is_open:
            return False, "No connection"
            
        try:
            # Clear input buffer
            self.ser.reset_input_buffer()
            
            # Send command
            self.ser.write((command + '\r\n').encode())
            time.sleep(timeout)
            
            # Read response
            response = ""
            if self.ser.in_waiting:
                time.sleep(0.01)  # Small delay for complete response
                response = self.ser.read(self.ser.in_waiting).decode(errors='ignore')
            
            success = expected_response in response
            if success:
                logger.debug(f"AT Command success: {command} -> {response.strip()}")
            else:
                logger.warning(f"AT Command failed: {command} -> {response.strip()}")
                
            return success, response
            
        except Exception as e:
            logger.error(f"Error sending AT command '{command}': {e}")
            return False, str(e)
    
    @contextmanager
    def _get_connection(self):
        """
        Context manager for safe connection handling.
        Automatically acquires lock and ensures connection.
        """
        with self._lock:
            # Try to establish connection if needed
            if not self._connect():
                self._connection_attempts += 1
                if self._connection_attempts >= self._max_connection_attempts:
                    raise Exception(f"Failed to connect to SIM7600 after {self._max_connection_attempts} attempts. Last error: {self._last_error}")
                else:
                    raise Exception(f"Failed to connect to SIM7600. Attempt {self._connection_attempts}/{self._max_connection_attempts}")
            
            try:
                yield self.ser
            except Exception as e:
                logger.error(f"Error during SIM7600 operation: {e}")
                # On error, disconnect to force reconnection next time
                self._disconnect()
                raise
    
    def _auto_start_gps(self):
        """
        Automatically start GPS if not already started.
        Called during connection establishment.
        """
        if not self._gps_started:
            success, response = self._send_raw_command('AT+CGPS=1,1', 'OK', 1)
            if success:
                self._gps_started = True
                logger.info("GPS auto-started successfully")
            else:
                logger.warning(f"Failed to auto-start GPS: {response}")
    
    def _auto_stop_gps(self):
        """
        Automatically stop GPS during shutdown.
        Called during daemon shutdown.
        """
        if self._gps_started:
            success, response = self._send_raw_command('AT+CGPS=0', 'OK', 1)
            if success:
                self._gps_started = False
                logger.info("GPS auto-stopped successfully")
            else:
                logger.warning(f"Failed to auto-stop GPS: {response}")
    
    def get_network_status(self) -> Dict[str, Any]:
        """
        Get comprehensive network status using multiple AT commands.
        Returns dictionary with network information.
        """
        status = {
            "connected": False,
            "signal_strength": None,
            "operator": None,
            "network_type": None,
            "registration_status": None
        }
        
        try:
            with self._get_connection():
                # Check network registration
                success, response = self._send_raw_command('AT+CREG?', '+CREG:', 0.5)
                if success and '+CREG:' in response:
                    creg_match = re.search(r'\+CREG:\s*\d+,(\d+)', response)
                    if creg_match:
                        reg_status = int(creg_match.group(1))
                        status["registration_status"] = reg_status
                        if reg_status in [1, 5]:  # 1=home network, 5=roaming
                            status["connected"] = True
                
                # Get signal strength
                success, response = self._send_raw_command('AT+CSQ', '+CSQ:', 0.5)
                if success and '+CSQ:' in response:
                    csq_match = re.search(r'\+CSQ:\s*(\d+),\d+', response)
                    if csq_match:
                        rssi = int(csq_match.group(1))
                        if rssi != 99:  # 99 = unknown
                            signal_dbm = -113 + (rssi * 2)
                            status["signal_strength"] = f"{signal_dbm} dBm"
                
                # Get operator and network type
                success, response = self._send_raw_command('AT+COPS?', '+COPS:', 0.5)
                if success and '+COPS:' in response:
                    # Parse operator name
                    cops_match = re.search(r'\+COPS:\s*\d+,\d+,"([^"]+)"', response)
                    if cops_match:
                        status["operator"] = cops_match.group(1)
                    
                    # Parse network type
                    net_type_match = re.search(r'\+COPS:\s*\d+,\d+,"[^"]+",(\d+)', response)
                    if net_type_match:
                        net_type = int(net_type_match.group(1))
                        if net_type == 7:
                            status["network_type"] = "LTE"
                        elif net_type == 2:
                            status["network_type"] = "3G"
                        elif net_type == 0:
                            status["network_type"] = "2G"
                        else:
                            status["network_type"] = f"Type {net_type}"
                            
        except Exception as e:
            logger.error(f"Error getting network status: {e}")
            status["error"] = str(e)
        
        return status
    
    def get_gnss_location(self) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Get parsed GNSS location data using AT+CGNSSINFO (GPS + GLONASS + Galileo + BeiDou).
        Returns (success, location_dict) where location_dict contains:
        - mode: Fix mode (2=2D fix, 3=3D fix)
        - satellites: GPS, GLONASS, BEIDOU satellite counts
        - latitude, longitude, altitude
        - speed (knots), course (degrees)  
        - date, utc_time
        - pdop, hdop, vdop (dilution of precision)
        - fix_status, source
        """
        try:
            with self._get_connection():
                success, response = self._send_raw_command('AT+CGNSSINFO', '+CGNSSINFO:', 1)
                if not success:
                    return False, None
                
                # Parse CGNSSINFO response format:
                # +CGNSSINFO: [mode],[GPS-SVs],[GLONASS-SVs],[BEIDOU-SVs],[lat],[N/S],[lon],[E/W],[date],[UTC-time],[alt],[speed],[course],[PDOP],[HDOP],[VDOP]
                if '+CGNSSINFO:' in response:
                    # Extract the data part after +CGNSSINFO:
                    data_match = re.search(r'\+CGNSSINFO:\s*(.+)', response)
                    if data_match:
                        data_parts = data_match.group(1).strip().split(',')
                        
                        # Check if we have valid GNSS fix (mode and coordinates present)
                        if len(data_parts) >= 16 and data_parts[0] and data_parts[4] and data_parts[6]:
                            try:
                                # Parse fix mode
                                mode = int(data_parts[0]) if data_parts[0] else None
                                
                                # Parse satellite counts
                                gps_sats = int(data_parts[1]) if data_parts[1] else 0
                                glonass_sats = int(data_parts[2]) if data_parts[2] else 0
                                beidou_sats = int(data_parts[3]) if data_parts[3] else 0
                                
                                # Parse coordinates (format: ddmm.mmmmmm)
                                lat_raw = float(data_parts[4])
                                lat_dir = data_parts[5]
                                lon_raw = float(data_parts[6])
                                lon_dir = data_parts[7]
                                
                                # Convert DDMM.MMMMMM format to decimal degrees
                                lat_deg = int(lat_raw / 100)
                                lat_min = lat_raw - (lat_deg * 100)
                                latitude = lat_deg + (lat_min / 60.0)
                                if lat_dir == 'S':
                                    latitude = -latitude
                                
                                lon_deg = int(lon_raw / 100)
                                lon_min = lon_raw - (lon_deg * 100)
                                longitude = lon_deg + (lon_min / 60.0)
                                if lon_dir == 'W':
                                    longitude = -longitude
                                
                                # Parse other fields
                                date = data_parts[8] if data_parts[8] else None
                                utc_time = data_parts[9] if data_parts[9] else None
                                altitude = float(data_parts[10]) if data_parts[10] else None  # meters
                                speed_knots = float(data_parts[11]) if data_parts[11] else None  # knots
                                course = float(data_parts[12]) if data_parts[12] else None  # degrees
                                pdop = float(data_parts[13]) if data_parts[13] else None
                                hdop = float(data_parts[14]) if data_parts[14] else None
                                vdop = float(data_parts[15]) if data_parts[15] else None
                                
                                # Convert speed from knots to km/h for consistency
                                speed_kmh = speed_knots * 1.852 if speed_knots is not None else None
                                
                                location_data = {
                                    'mode': mode,
                                    'fix_type': '2D' if mode == 2 else '3D' if mode == 3 else f'Unknown({mode})',
                                    'satellites': {
                                        'gps': gps_sats,
                                        'glonass': glonass_sats,
                                        'beidou': beidou_sats,
                                        'total': gps_sats + glonass_sats + beidou_sats
                                    },
                                    'latitude': latitude,
                                    'longitude': longitude,
                                    'altitude': altitude,  # meters
                                    'speed': speed_kmh,    # km/h (converted from knots)
                                    'speed_knots': speed_knots,  # original knots value
                                    'course': course,      # degrees
                                    'date': date,          # ddmmyy format
                                    'utc_time': utc_time,  # hhmmss.s format
                                    'pdop': pdop,          # Position dilution of precision
                                    'hdop': hdop,          # Horizontal dilution of precision
                                    'vdop': vdop,          # Vertical dilution of precision
                                    'fix_status': 'valid',
                                    'source': 'GNSS'
                                }
                                
                                return True, location_data
                                
                            except (ValueError, IndexError) as e:
                                logger.warning(f"Error parsing GNSS coordinates: {e}")
                                return False, {'fix_status': 'invalid', 'error': 'Parse error', 'source': 'GNSS'}
                        else:
                            # Handle "no fix" case - response will be: +CGNSSINFO: ,,,,,,,,,,,,,,,
                            return True, {'fix_status': 'no_fix', 'error': 'No GNSS fix available', 'source': 'GNSS'}
                
                return False, {'fix_status': 'no_data', 'error': 'No GNSS data in response', 'source': 'GNSS'}
                
        except Exception as e:
            logger.error(f"Error getting GNSS location: {e}")
            return False, None
    
    def is_available(self) -> bool:
        """
        Check if the SIM7600 device is available and responsive.
        Returns True if device responds to AT commands.
        """
        try:
            with self._get_connection():
                success, _ = self._send_raw_command('AT', 'OK', 0.5)
                return success
        except Exception:
            return False
    
    def close(self):
        """Close the connection and cleanup resources."""
        with self._lock:
            # Stop GPS before disconnecting
            if self.ser and self.ser.is_open:
                self._auto_stop_gps()
            
            self._disconnect()
            if self.daemon_running:
                self.stop_daemon()
    
    def start_daemon(self):
        """Start daemon server mode"""
        self.daemon_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.daemon_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.daemon_sock.bind((self.host, self.daemon_port))
        self.daemon_sock.listen(5)
        
        self.daemon_running = True
        logger.info(f"SIM7600 daemon started on {self.host}:{self.daemon_port}")
        
        try:
            while self.daemon_running:
                try:
                    conn, addr = self.daemon_sock.accept()
                    client_thread = threading.Thread(target=self._handle_daemon_client, args=(conn,))
                    client_thread.daemon = True
                    client_thread.start()
                except Exception as e:
                    if self.daemon_running:
                        logger.error(f"Error accepting connection: {e}")
        except KeyboardInterrupt:
            logger.info("Daemon interrupted by user")
        finally:
            self.stop_daemon()
    
    def _handle_daemon_client(self, conn):
        """Handle daemon client requests"""
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                    
                try:
                    request = json.loads(data.decode())
                    response = self._process_daemon_request(request)
                    conn.send(json.dumps(response).encode())
                except Exception as e:
                    error_response = {'success': False, 'error': str(e)}
                    conn.send(json.dumps(error_response).encode())
                    
        except Exception as e:
            logger.error(f"Client handling error: {e}")
        finally:
            conn.close()
    
    def _process_daemon_request(self, request):
        """Process daemon client requests and call appropriate methods"""
        method = request.get('method')
        
        try:
            if method == 'get_network_status':
                result = self.get_network_status()
                return {'success': True, 'result': result}
                
            elif method == 'get_gnss_location':
                success, location_data = self.get_gnss_location()
                return {'success': success, 'result': {'success': success, 'data': location_data}}
                
            elif method == 'is_available':
                result = self.is_available()
                return {'success': True, 'result': result}
                
            else:
                return {'success': False, 'error': f'Unknown method: {method}'}
                
        except Exception as e:
            logger.error(f"Error processing {method}: {e}")
            return {'success': False, 'error': str(e)}
    
    def stop_daemon(self):
        """Stop daemon server"""
        logger.info("Stopping SIM7600 daemon...")
        
        # Stop GPS before shutting down
        if self.ser and self.ser.is_open:
            self._auto_stop_gps()
        
        self.daemon_running = False
        if self.daemon_sock:
            try:
                self.daemon_sock.close()
            except:
                pass
            self.daemon_sock = None
    
    def __del__(self):
        """Destructor - automatically cleanup when object is destroyed."""
        try:
            self.close()
        except Exception:
            # Ignore errors during cleanup in destructor
            pass

# Global singleton instance
_sim7600_daemon = None
_daemon_lock = threading.Lock()

def get_sim7600_daemon(**kwargs) -> SIM7600Daemon:
    """
    Get the global SIM7600Daemon singleton instance.
    Thread-safe initialization.
    
    Args:
        **kwargs: Arguments passed to SIM7600Daemon constructor
    """
    global _sim7600_daemon
    
    if _sim7600_daemon is None:
        with _daemon_lock:
            if _sim7600_daemon is None:
                _sim7600_daemon = SIM7600Daemon(**kwargs)
    
    return _sim7600_daemon


class SIM7600Client:
    """
    Client that connects to a SIM7600Manager running in daemon mode.
    Drop-in replacement for direct SIM7600Manager usage.
    """
    
    def __init__(self, host='localhost', port=7600):
        self.host = host
        self.port = port
        self._sock = None
    
    def _connect(self):
        """Connect to the daemon"""
        if self._sock:
            return True
            
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.connect((self.host, self.port))
            return True
        except Exception as e:
            logger.error(f"Failed to connect to SIM7600 daemon at {self.host}:{self.port}: {e}")
            self._sock = None
            return False
    
    def _send_request(self, method: str) -> Dict[str, Any]:
        """Send request to daemon and get response"""
        if not self._connect():
            return {'success': False, 'error': 'Cannot connect to daemon'}
        
        try:
            request = {'method': method}
            if self._sock:
                self._sock.send(json.dumps(request).encode())
                response = self._sock.recv(4096)
                return json.loads(response.decode())
            else:
                return {'success': False, 'error': 'No connection'}
            
        except Exception as e:
            logger.error(f"Error communicating with daemon: {e}")
            self._disconnect()
            return {'success': False, 'error': str(e)}
    
    def _disconnect(self):
        """Disconnect from daemon"""
        if self._sock:
            try:
                self._sock.close()
            except:
                pass
            self._sock = None
    
    def get_network_status(self) -> Dict[str, Any]:
        """Get network status from daemon"""
        response = self._send_request('get_network_status')
        return response.get('result', {}) if response.get('success') else {}
    
    def get_gnss_location(self) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Get GNSS location via daemon"""
        response = self._send_request('get_gnss_location')
        if response.get('success'):
            result = response['result']
            return result['success'], result['data']
        return False, None
    
    def is_available(self) -> bool:
        """Check if SIM7600 is available via daemon"""
        response = self._send_request('is_available')
        return response.get('result', False) if response.get('success') else False
    
    def close(self):
        """Close connection to daemon"""
        self._disconnect()
    
    def __del__(self):
        """Cleanup on destruction"""
        self.close()


def get_sim7600_client(host='localhost', port=7600):
    """
    Get a SIM7600Client instance that connects to a daemon.
    Use this when you want to connect to a SIM7600Daemon.
    """
    return SIM7600Client(host, port)


def main():
    """Main function for running the daemon"""
    parser = argparse.ArgumentParser(description='SIM7600 Communication Daemon')
    parser.add_argument('--host', default='localhost', help='Host to bind daemon server')
    parser.add_argument('--port', type=int, default=7600, help='Port for daemon server')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging')
    
    args = parser.parse_args()
    
    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('/var/log/sim7600_daemon.log')
        ]
    )
    
    logger.info("Starting SIM7600 daemon...")
    daemon = get_sim7600_daemon(host=args.host, daemon_port=args.port)
    
    # Handle shutdown signals
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        daemon.stop_daemon()
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        daemon.start_daemon()
    except Exception as e:
        logger.error(f"Daemon error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
