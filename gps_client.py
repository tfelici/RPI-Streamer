#!/usr/bin/env python3
"""
GPS Daemon Client Library
Provides a simple interface to communicate with the GPS daemon.
"""

import json
import socket
import time
from typing import Optional, Dict, Any, Tuple


class GPSClient:
    """Client for communicating with GPS daemon"""
    
    def __init__(self, socket_path='/tmp/gps_daemon.sock', timeout=5):
        """
        Initialize GPS client.
        
        Args:
            socket_path: Path to GPS daemon Unix socket
            timeout: Connection timeout in seconds
        """
        self.socket_path = socket_path
        self.timeout = timeout
    
    def _send_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Send request to GPS daemon and return response"""
        try:
            # Create Unix socket connection
            client_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client_socket.settimeout(self.timeout)
            client_socket.connect(self.socket_path)
            
            # Send request
            request_data = json.dumps(request).encode('utf-8')
            client_socket.send(request_data)
            
            # Receive response
            response_data = client_socket.recv(8192)  # Increased buffer for full location data
            response = json.loads(response_data.decode('utf-8'))
            
            client_socket.close()
            return response
            
        except (socket.error, json.JSONDecodeError, ConnectionRefusedError) as e:
            # GPS daemon not running or communication error
            return None
        except Exception as e:
            return None
    
    def get_location(self) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Get current GPS location data from daemon.
        
        Returns:
            Tuple[bool, Optional[Dict[str, Any]]]: (success, location_data)
            
            On success, location_data contains:
            {
                'fix_status': 'valid' | 'no_fix',
                'latitude': float,
                'longitude': float, 
                'altitude': float,
                'speed': float,  # m/s
                'course': float,  # degrees
                'fix_type': '2D' | '3D',
                'hdop': float,  # horizontal dilution of precision
                'satellites': {
                    'used': int,
                    'total': int,
                    'constellations': {
                        'GPS': {'visible': int, 'used': int, 'max_snr': int},
                        'GLONASS': {'visible': int, 'used': int, 'max_snr': int},
                        'Galileo': {'visible': int, 'used': int, 'max_snr': int},
                        'BeiDou': {'visible': int, 'used': int, 'max_snr': int}
                    }
                },
                'timestamp': float,  # UNIX timestamp
                'daemon_status': str,
                'daemon_stats': {
                    'uptime': float,
                    'sentences_parsed': int,
                    'last_fix_time': float,
                    'current_device': str
                }
            }
        """
        response = self._send_request({'command': 'get_location'})
        
        if response is None:
            return False, {
                'error': 'GPS daemon not available',
                'status': 'GPS daemon is not running or not accessible'
            }
        
        if 'error' in response:
            return False, response
        
        # Check if we have a valid GPS fix
        if response.get('fix_status') == 'valid':
            return True, response
        else:
            # No fix but provide detailed satellite info if available
            satellite_summary = []
            total_visible = 0
            
            constellations = response.get('satellites', {}).get('constellations', {})
            for constellation, data in constellations.items():
                if data.get('visible', 0) > 0:
                    visible = data.get('visible', 0)
                    used = data.get('used', 0)
                    max_snr = data.get('max_snr', 0)
                    satellite_summary.append(f"{constellation}: {visible} visible, {used} used, max SNR: {max_snr}dB")
                    total_visible += visible
            
            error_response = {
                'error': 'No GPS fix available',
                'satellites_visible': total_visible,
                'constellation_details': constellations,
                'daemon_status': response.get('daemon_status', 'unknown'),
                'status': f'Satellites visible but no position fix - {", ".join(satellite_summary) if satellite_summary else "No satellites detected"}'
            }
            
            # Include daemon stats if available
            if 'daemon_stats' in response:
                error_response['daemon_stats'] = response['daemon_stats']
            
            return False, error_response
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get GPS daemon status information.
        
        Returns:
            Dict with daemon status, fix status, and statistics
        """
        response = self._send_request({'command': 'get_status'})
        
        if response is None:
            return {
                'daemon_status': 'not_running',
                'fix_status': 'unknown',
                'error': 'GPS daemon not available'
            }
        
        return response
    
    def is_daemon_running(self) -> bool:
        """Check if GPS daemon is running and responding"""
        status = self.get_status()
        return 'error' not in status


def get_gnss_location() -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Get GNSS location using GPS daemon client.
    This function replaces the direct NMEA parsing approach and provides
    the same interface for backward compatibility.
    
    Returns:
        Tuple[bool, Optional[Dict[str, Any]]]: (success, location_data)
        Same format as the original get_gnss_location function
    """
    client = GPSClient()
    return client.get_location()


def get_gps_daemon_status() -> Dict[str, Any]:
    """
    Get GPS daemon status information.
    
    Returns:
        Dict with daemon status, fix status, and statistics
    """
    client = GPSClient()
    return client.get_status()


# For testing and standalone usage
if __name__ == '__main__':
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description='GPS Daemon Client')
    parser.add_argument('--location', action='store_true',
                        help='Get current location')
    parser.add_argument('--status', action='store_true',
                        help='Get daemon status')
    parser.add_argument('--socket', default='/tmp/gps_daemon.sock',
                        help='GPS daemon socket path')
    
    args = parser.parse_args()
    
    client = GPSClient(socket_path=args.socket)
    
    if args.location:
        success, data = client.get_location()
        print(f"Location Success: {success}")
        print(json.dumps(data, indent=2))
    
    elif args.status:
        status = client.get_status()
        print("Daemon Status:")
        print(json.dumps(status, indent=2))
    
    else:
        # Default: show both
        print("=== GPS Daemon Status ===")
        status = client.get_status()
        print(json.dumps(status, indent=2))
        
        print("\n=== Current Location ===")
        success, data = client.get_location()
        print(f"Success: {success}")
        print(json.dumps(data, indent=2))
