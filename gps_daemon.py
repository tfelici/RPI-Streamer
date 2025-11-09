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
import struct

# Add parent directory to path to import utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from utils import load_settings
except ImportError:
    # Fallback if utils not available
    def load_settings():
        return {
            'gps_source': 'hardware',
            'xplane_udp_port': 49003,
            'xplane_bind_address': '0.0.0.0'
        }

def simulate_gps_data():
    """
    Simulate GPS coordinates for rectangular flight path from Oxford Airport UK
    
    Flight Pattern:
    1. Start at Oxford Airport (paused by default)
    2. Fly 010° for 1km climbing to 1500ft
    3. Left turn 180° (radius 1km)
    4. Fly straight for 2km at 1500ft
    5. Left turn 180° (radius 1km)
    6. Fly 010° for 1km descending to the ground (this should bring you back to the starting point)
    7. Pause after completing this loop
    
    Manual control via start_simulation(), stop_simulation(), reset_simulation()
    """
    
    # Oxford Airport (Kidlington) coordinates
    oxford_lat = 51.8369
    oxford_lon = -1.3200
    
    # Initialize simulation state if not exists
    if not hasattr(simulate_gps_data, 'start_time'):
        simulate_gps_data.start_time = time.time()
        simulate_gps_data.is_paused = True  # Start paused by default
        simulate_gps_data.pause_start_time = time.time()
        simulate_gps_data.total_pause_time = 0.0
    
    # Handle pause/resume state
    if hasattr(simulate_gps_data, 'is_paused') and simulate_gps_data.is_paused:
        # When paused, return last known position but with zero speed
        if hasattr(simulate_gps_data, 'paused_position') and simulate_gps_data.paused_position:
            # Return the stored paused position with zero speed
            paused_pos = simulate_gps_data.paused_position.copy()
            paused_pos['speed'] = 0.0
            paused_pos['simulation_status'] = 'paused'
            # Ensure accuracy fields are present
            if 'accuracy' not in paused_pos:
                paused_pos['accuracy'] = 5.0
            if 'altitudeAccuracy' not in paused_pos:
                paused_pos['altitudeAccuracy'] = 8.0
            return paused_pos
        else:
            # Fallback to Oxford Airport if no paused position stored
            return {
                'latitude': oxford_lat,
                'longitude': oxford_lon,
                'altitude': 0.0,
                'accuracy': 5.0,
                'altitudeAccuracy': 8.0,
                'heading': 0,
                'speed': 0.0,
                'simulation_status': 'paused'
            }
    
    # Calculate elapsed time since simulation started (excluding pause time)
    total_pause_time = 0.0
    if hasattr(simulate_gps_data, 'total_pause_time'):
        total_pause_time = simulate_gps_data.total_pause_time
    
    # If currently paused, add the current pause duration to total pause time
    current_pause_duration = 0.0
    if hasattr(simulate_gps_data, 'is_paused') and simulate_gps_data.is_paused:
        if hasattr(simulate_gps_data, 'pause_start_time'):
            current_pause_duration = time.time() - simulate_gps_data.pause_start_time
    
    total_elapsed_time = time.time() - simulate_gps_data.start_time - total_pause_time - current_pause_duration
    
    # Flight parameters
    max_altitude_meters = 457.2  # 1500 feet = 457.2 meters
    first_leg_distance_km = 1.0  # 1km first segment
    second_leg_distance_km = 2.0 # 2km middle segment  
    third_leg_distance_km = 1.0  # 1km final segment back to start
    turn_radius_km = 1.0         # 1km turn radius
    flight_speed_kmh = 150.0     # 150 km/h flight speed
    
    # Calculate phase durations (in seconds)
    first_leg_time = (first_leg_distance_km / flight_speed_kmh) * 3600   # Time for 1km first leg
    second_leg_time = (second_leg_distance_km / flight_speed_kmh) * 3600 # Time for 2km second leg
    third_leg_time = (third_leg_distance_km / flight_speed_kmh) * 3600   # Time for 1km third leg
    turn_circumference = math.pi * turn_radius_km  # Half circle = π * radius
    turn_time = (turn_circumference / flight_speed_kmh) * 3600  # Time for 180° turn
    
    # Total flight time: 3 straights + 2 turns
    total_flight_time = first_leg_time + turn_time + second_leg_time + turn_time + third_leg_time
    
    # Calculate which cycle we're in and position within that cycle
    if total_flight_time > 0:
        current_cycle = int(total_elapsed_time / total_flight_time)
        time_in_cycle = total_elapsed_time % total_flight_time
    else:
        current_cycle = 0
        time_in_cycle = total_elapsed_time
    
    # Auto-pause after completing one full loop (cycle 1)
    if current_cycle >= 1:
        # Complete loop - auto-pause at Oxford Airport
        if not hasattr(simulate_gps_data, 'is_paused') or not simulate_gps_data.is_paused:
            simulate_gps_data.is_paused = True
            simulate_gps_data.pause_start_time = time.time()
            # Clear paused position to ensure return to Oxford Airport
            if hasattr(simulate_gps_data, 'paused_position'):
                delattr(simulate_gps_data, 'paused_position')
        
        # Return to Oxford Airport position (paused)
        return {
            'latitude': oxford_lat,
            'longitude': oxford_lon,
            'altitude': 0.0,
            'accuracy': 5.0,
            'altitudeAccuracy': 8.0,
            'heading': 0,
            'speed': 0.0,
            'simulation_status': 'completed_loop'
        }
    
    # We're in the flight phase - determine which segment
    flight_time = time_in_cycle
    
    # Coordinate conversion factors
    lat_deg_per_km = 1.0 / 111.0
    lon_deg_per_km = 1.0 / (111.0 * math.cos(math.radians(oxford_lat)))
    
    # Phase 1: First straight segment (010° for 1km, climbing)
    if flight_time <= first_leg_time:
        progress = flight_time / first_leg_time
        distance_flown = progress * first_leg_distance_km
        
        # Calculate position along 010° bearing (10° from north)
        bearing_rad = math.radians(10)
        lat_offset = distance_flown * lat_deg_per_km * math.cos(bearing_rad)
        lon_offset = distance_flown * lon_deg_per_km * math.sin(bearing_rad)
        
        current_lat = oxford_lat + lat_offset
        current_lon = oxford_lon + lon_offset
        current_altitude = progress * (max_altitude_meters * 0.5)  # Climb to half altitude (750ft)
        heading = 10  # 010°
        speed = flight_speed_kmh
    
    # Phase 2: First turn (left 180°, radius 1km)
    elif flight_time <= first_leg_time + turn_time:
        turn_progress = (flight_time - first_leg_time) / turn_time
        turn_angle = turn_progress * math.pi  # 180° = π radians
        
        # End of first straight segment
        first_leg_end_lat = oxford_lat + (first_leg_distance_km * lat_deg_per_km * math.cos(math.radians(10)))
        first_leg_end_lon = oxford_lon + (first_leg_distance_km * lon_deg_per_km * math.sin(math.radians(10)))
        
        # For a left turn, turn center is 90° left (perpendicular) of current heading
        # From 010° heading, 90° left is 280° (010° - 90° = -80° = 280°)
        turn_center_bearing = math.radians(280)  # 90° left of 010°
        turn_center_lat = first_leg_end_lat + (turn_radius_km * lat_deg_per_km * math.cos(turn_center_bearing))
        turn_center_lon = first_leg_end_lon + (turn_radius_km * lon_deg_per_km * math.sin(turn_center_bearing))
        
        # For the radius vector from center to aircraft:
        # At start of turn: vector points from center back to entry point (100° = 280° + 180°)
        # During left turn: vector rotates counterclockwise
        initial_radius_bearing = math.radians(100)  # 280° + 180° = 100° (vector from center to entry point)
        current_radius_bearing = initial_radius_bearing - turn_angle  # Rotate counterclockwise for left turn
        
        lat_offset = turn_radius_km * lat_deg_per_km * math.cos(current_radius_bearing)
        lon_offset = turn_radius_km * lon_deg_per_km * math.sin(current_radius_bearing)
        
        current_lat = turn_center_lat + lat_offset
        current_lon = turn_center_lon + lon_offset
        # Continue climbing: start at 750ft (half altitude), reach 1500ft (full altitude) halfway through turn
        if turn_progress <= 0.5:
            # First half of turn: climb from 750ft to 1500ft
            climb_progress = turn_progress * 2  # Scale 0-0.5 to 0-1
            current_altitude = (max_altitude_meters * 0.5) + (climb_progress * max_altitude_meters * 0.5)
        else:
            # Second half of turn: maintain 1500ft
            current_altitude = max_altitude_meters
        heading = (10 + (turn_progress * 180)) % 360  # Turn from 010° to 190°
        speed = flight_speed_kmh
    
    # Phase 3: Second straight segment (190° for 2km, at altitude)
    elif flight_time <= first_leg_time + turn_time + second_leg_time:
        progress = (flight_time - first_leg_time - turn_time) / second_leg_time
        distance_flown = progress * second_leg_distance_km
        
        # Calculate where the first turn ended (this is our starting point for second straight)
        # End of first straight segment
        first_leg_end_lat = oxford_lat + (first_leg_distance_km * lat_deg_per_km * math.cos(math.radians(10)))
        first_leg_end_lon = oxford_lon + (first_leg_distance_km * lon_deg_per_km * math.sin(math.radians(10)))
        
        # First turn center
        turn_center_bearing = math.radians(280)  # 90° left of 010°
        turn_center_lat = first_leg_end_lat + (turn_radius_km * lat_deg_per_km * math.cos(turn_center_bearing))
        turn_center_lon = first_leg_end_lon + (turn_radius_km * lon_deg_per_km * math.sin(turn_center_bearing))
        
        # End of first turn (after 180° turn, radius vector points at 280°)
        turn_end_radius_bearing = math.radians(280)  # Final radius vector after 180° left turn
        second_leg_start_lat = turn_center_lat + (turn_radius_km * lat_deg_per_km * math.cos(turn_end_radius_bearing))
        second_leg_start_lon = turn_center_lon + (turn_radius_km * lon_deg_per_km * math.sin(turn_end_radius_bearing))
        
        # Calculate position along 190° bearing from the end of the first turn
        bearing_190 = math.radians(190)
        lat_offset = distance_flown * lat_deg_per_km * math.cos(bearing_190)
        lon_offset = distance_flown * lon_deg_per_km * math.sin(bearing_190)
        
        current_lat = second_leg_start_lat + lat_offset
        current_lon = second_leg_start_lon + lon_offset
        current_altitude = max_altitude_meters  # Maintain altitude
        heading = 190  # 190°
        speed = flight_speed_kmh
    
    # Phase 4: Second turn (left 180°, radius 1km, maintain altitude)
    elif flight_time <= first_leg_time + turn_time + second_leg_time + turn_time:
        turn_progress = (flight_time - first_leg_time - turn_time - second_leg_time) / turn_time
        turn_angle = turn_progress * math.pi  # 180° = π radians
        
        # Calculate where the second straight segment ends (start of second turn)
        # First, find where second straight segment starts (end of first turn)
        first_leg_end_lat = oxford_lat + (first_leg_distance_km * lat_deg_per_km * math.cos(math.radians(10)))
        first_leg_end_lon = oxford_lon + (first_leg_distance_km * lon_deg_per_km * math.sin(math.radians(10)))
        
        # First turn center and end position
        first_turn_center_bearing = math.radians(280)  # 90° left of 010°
        first_turn_center_lat = first_leg_end_lat + (turn_radius_km * lat_deg_per_km * math.cos(first_turn_center_bearing))
        first_turn_center_lon = first_leg_end_lon + (turn_radius_km * lon_deg_per_km * math.sin(first_turn_center_bearing))
        
        # End of first turn (start of second straight)
        turn_end_radius_bearing = math.radians(280)  # Final radius vector after 180° left turn
        second_leg_start_lat = first_turn_center_lat + (turn_radius_km * lat_deg_per_km * math.cos(turn_end_radius_bearing))
        second_leg_start_lon = first_turn_center_lon + (turn_radius_km * lon_deg_per_km * math.sin(turn_end_radius_bearing))
        
        # End of second straight segment (start of second turn)
        bearing_190 = math.radians(190)
        second_leg_end_lat = second_leg_start_lat + (second_leg_distance_km * lat_deg_per_km * math.cos(bearing_190))
        second_leg_end_lon = second_leg_start_lon + (second_leg_distance_km * lon_deg_per_km * math.sin(bearing_190))
        
        # For a left turn from 190°, turn center is 90° left (perpendicular) of current heading
        # From 190° heading, 90° left is 100° (190° - 90° = 100°)
        turn_center_bearing = math.radians(100)  # 90° left of 190°
        turn_center_lat = second_leg_end_lat + (turn_radius_km * lat_deg_per_km * math.cos(turn_center_bearing))
        turn_center_lon = second_leg_end_lon + (turn_radius_km * lon_deg_per_km * math.sin(turn_center_bearing))
        
        # For the radius vector from center to aircraft:
        # At start of turn: vector points from center back to entry point (280° = 100° + 180°)
        # During left turn: vector rotates counterclockwise
        initial_radius_bearing = math.radians(280)  # 100° + 180° = 280° (vector from center to entry point)
        current_radius_bearing = initial_radius_bearing - turn_angle  # Rotate counterclockwise for left turn
        
        lat_offset = turn_radius_km * lat_deg_per_km * math.cos(current_radius_bearing)
        lon_offset = turn_radius_km * lon_deg_per_km * math.sin(current_radius_bearing)
        
        current_lat = turn_center_lat + lat_offset
        current_lon = turn_center_lon + lon_offset
        # Start descending halfway through second turn: maintain 1500ft first half, then descend to 750ft
        if turn_progress <= 0.5:
            # First half of turn: maintain 1500ft
            current_altitude = max_altitude_meters
        else:
            # Second half of turn: descend from 1500ft to 750ft
            descent_progress = (turn_progress - 0.5) * 2  # Scale 0.5-1 to 0-1
            current_altitude = max_altitude_meters - (descent_progress * max_altitude_meters * 0.5)
        heading = (190 + (turn_progress * 180)) % 360  # Turn from 190° to 010°
        speed = flight_speed_kmh
    
    # Phase 5: Third straight segment (010° for 1km, descending back to ground)
    else:
        progress = (flight_time - first_leg_time - turn_time - second_leg_time - turn_time) / third_leg_time
        distance_flown = progress * third_leg_distance_km
        
        # Calculate where the second turn ended (this is our starting point for third straight)
        # We need to find the end position of the second turn
        first_leg_end_lat = oxford_lat + (first_leg_distance_km * lat_deg_per_km * math.cos(math.radians(10)))
        first_leg_end_lon = oxford_lon + (first_leg_distance_km * lon_deg_per_km * math.sin(math.radians(10)))
        
        # First turn center and end position
        first_turn_center_bearing = math.radians(280)
        first_turn_center_lat = first_leg_end_lat + (turn_radius_km * lat_deg_per_km * math.cos(first_turn_center_bearing))
        first_turn_center_lon = first_leg_end_lon + (turn_radius_km * lon_deg_per_km * math.sin(first_turn_center_bearing))
        
        # End of first turn (start of second straight)
        turn_end_radius_bearing = math.radians(280)
        second_leg_start_lat = first_turn_center_lat + (turn_radius_km * lat_deg_per_km * math.cos(turn_end_radius_bearing))
        second_leg_start_lon = first_turn_center_lon + (turn_radius_km * lon_deg_per_km * math.sin(turn_end_radius_bearing))
        
        # End of second straight segment
        bearing_190 = math.radians(190)
        second_leg_end_lat = second_leg_start_lat + (second_leg_distance_km * lat_deg_per_km * math.cos(bearing_190))
        second_leg_end_lon = second_leg_start_lon + (second_leg_distance_km * lon_deg_per_km * math.sin(bearing_190))
        
        # Second turn center
        second_turn_center_bearing = math.radians(100)
        second_turn_center_lat = second_leg_end_lat + (turn_radius_km * lat_deg_per_km * math.cos(second_turn_center_bearing))
        second_turn_center_lon = second_leg_end_lon + (turn_radius_km * lon_deg_per_km * math.sin(second_turn_center_bearing))
        
        # End of second turn (start of third straight) - after 180° left turn, radius vector points at 100°
        third_leg_start_radius_bearing = math.radians(100)
        third_leg_start_lat = second_turn_center_lat + (turn_radius_km * lat_deg_per_km * math.cos(third_leg_start_radius_bearing))
        third_leg_start_lon = second_turn_center_lon + (turn_radius_km * lon_deg_per_km * math.sin(third_leg_start_radius_bearing))
        
        # Calculate position along 010° bearing from the end of the second turn
        bearing_010 = math.radians(10)
        lat_offset = distance_flown * lat_deg_per_km * math.cos(bearing_010)
        lon_offset = distance_flown * lon_deg_per_km * math.sin(bearing_010)
        
        current_lat = third_leg_start_lat + lat_offset
        current_lon = third_leg_start_lon + lon_offset
        # Continue descending: start at 750ft (half altitude), reach ground level
        current_altitude = (max_altitude_meters * 0.5) * (1 - progress)  # Descend from 750ft to 0ft
        heading = 10  # 010°
        speed = flight_speed_kmh
    
    # Add realistic variation
    altitude_variation = random.uniform(-3, 3)
    speed_variation = random.uniform(0.95, 1.05)
    accuracy = random.uniform(3, 8)
    
    return {
        'latitude': current_lat,
        'longitude': current_lon,
        'altitude': max(0, current_altitude + altitude_variation),
        'accuracy': accuracy,
        'altitudeAccuracy': accuracy * 1.5,
        'heading': heading,
        'speed': flight_speed_kmh * speed_variation,
        'simulation_status': 'running'
    }


def start_simulation():
    """Start/resume GPS simulation movement"""
    if hasattr(simulate_gps_data, 'is_paused') and simulate_gps_data.is_paused:
        # Resume from pause
        simulate_gps_data.is_paused = False
        if hasattr(simulate_gps_data, 'pause_start_time'):
            pause_duration = time.time() - simulate_gps_data.pause_start_time
            if not hasattr(simulate_gps_data, 'total_pause_time'):
                simulate_gps_data.total_pause_time = 0.0
            simulate_gps_data.total_pause_time += pause_duration
        
        # Log simulation start
        logging.info(f"GPS Simulation: Movement started/resumed")
        
        return {'status': 'resumed', 'message': 'GPS simulation resumed'}
    else:
        return {'status': 'already_running', 'message': 'GPS simulation already running'}


def stop_simulation():
    """Stop/pause GPS simulation movement at current position"""
    if not hasattr(simulate_gps_data, 'is_paused') or not simulate_gps_data.is_paused:
        # Get current position before pausing
        current_pos = simulate_gps_data()
        
        # Store the current position for when paused
        simulate_gps_data.paused_position = current_pos.copy()
        
        # Pause simulation
        simulate_gps_data.is_paused = True
        simulate_gps_data.pause_start_time = time.time()
        
        # Log simulation pause with position info
        lat = current_pos.get('latitude', 0)
        lon = current_pos.get('longitude', 0) 
        alt = current_pos.get('altitude', 0)
        logging.info(f"GPS Simulation: Paused at position {lat:.4f}, {lon:.4f}, {alt:.0f}m")
        
        return {'status': 'paused', 'message': 'GPS simulation paused at current position'}
    else:
        return {'status': 'already_stopped', 'message': 'GPS simulation already paused'}


def reset_simulation():
    """Reset GPS simulation to start position at Oxford Airport"""
    if hasattr(simulate_gps_data, 'start_time'):
        simulate_gps_data.start_time = time.time()
        simulate_gps_data.is_paused = True
        simulate_gps_data.pause_start_time = time.time()
        simulate_gps_data.total_pause_time = 0.0
        
        # Clear any stored paused position to force return to Oxford Airport
        if hasattr(simulate_gps_data, 'paused_position'):
            delattr(simulate_gps_data, 'paused_position')
        
        # Log simulation reset
        logging.info("GPS Simulation: Reset to Oxford Airport starting position")
            
        return {'status': 'reset', 'message': 'GPS simulation reset to Oxford Airport'}
    else:
        return {'status': 'not_initialized', 'message': 'GPS simulation not initialized'}

class XPlaneUDPParser:
    """
    Parser for X-Plane UDP data output.
    Handles the DREF data format that X-Plane sends when "Data Output" is enabled.
    """
    
    def __init__(self):
        # X-Plane data indices for GPS position data
        # These correspond to the "GPS position" data output in X-Plane
        self.GPS_LATITUDE_INDEX = 0    # sim/flightmodel/position/latitude (degrees)
        self.GPS_LONGITUDE_INDEX = 1   # sim/flightmodel/position/longitude (degrees)  
        self.GPS_ALTITUDE_INDEX = 2    # sim/flightmodel/position/elevation (meters MSL)
        self.GPS_HEADING_INDEX = 3     # sim/flightmodel/position/psi (degrees true)
        self.GPS_GROUNDSPEED_INDEX = 4 # sim/flightmodel/position/groundspeed (m/s)
        
    def parse_udp_packet(self, data):
        """
        Parse X-Plane UDP packet and extract GPS data.
        
        X-Plane UDP format:
        - Header: 5 bytes (b"DATA\x00" for data packets)
        - For DATA packets: Multiple 36-byte records, each containing:
          - 4 bytes: data type index (little-endian int32)
          - 8 x 4 bytes: 8 float values (little-endian float32)
        
        Args:
            data: Raw UDP packet data (bytes)
            
        Returns:
            dict: GPS location data or None if packet cannot be parsed
        """
        try:
            if len(data) < 4:
                return None
                
            # Check if this starts with DATA header  
            if data.startswith(b'DATA'):
                return self._parse_data_packet(data)
            else:
                # Unknown packet type - silently ignore
                return None
                
        except Exception as e:
            # Silently ignore parse errors - X-Plane sends various packet types
            return None
    
    def _parse_data_packet(self, data):
        """Parse X-Plane DATA packet based on official UDP format"""
        try:
            # Official X-Plane UDP format:
            # 5-byte MESSAGE PROLOGUE: "DATA" + index_byte
            # Multiple data_struct entries: int index + float data[8] = 36 bytes each
            
            if len(data) < 5:
                return None
            
            # Parse the 5-byte prologue
            prologue = data[:5]
            if not prologue.startswith(b'DATA'):
                return None
            
            # Parse payload containing multiple data_struct entries
            payload = data[5:]
            gps_data = {}
            offset = 0
            record_size = 36  # sizeof(int) + 8*sizeof(float) = 4 + 32 = 36
            
            while offset + record_size <= len(payload):
                record_data = payload[offset:offset + record_size]
                
                try:
                    # Parse data_struct: int index + float data[8]
                    data_index, *values = struct.unpack('<i8f', record_data)
                    
                    # Check for GPS position data (typically index 20 in X-Plane Data Output)
                    if data_index == 20:  # GPS position data
                        # X-Plane GPS position data format:
                        # [0] = latitude (degrees)
                        # [1] = longitude (degrees) 
                        # [2] = altitude MSL (meters)
                        # [3] = altitude AGL (meters)
                        # [4] = on runway flag (0 or 1)
                        # [5] = altitude indicator (?)
                        # [6] = latitude (degrees, duplicate?)
                        # [7] = longitude (degrees, duplicate?)
                        
                        latitude = values[0] if abs(values[0]) < 90 and values[0] != -999.0 else None
                        longitude = values[1] if abs(values[1]) < 180 and values[1] != -999.0 else None
                        altitude_feet = values[2] if values[2] != -999.0 else None  # X-Plane altitude in feet
                        
                        if latitude is not None and longitude is not None:
                            # Convert feet to meters (1 foot = 0.3048 meters)
                            altitude = altitude_feet * 0.3048 if altitude_feet is not None else 0
                            gps_data.update({
                                'latitude': latitude,
                                'longitude': longitude,
                                'altitude': altitude,
                                'fix_status': 'valid'
                            })
                    
                    # Check for velocities data (typically index 18)
                    elif data_index == 18:  # Velocities data
                        groundspeed_kts = values[3] if values[3] != -999.0 and values[3] >= 0 else None
                        if groundspeed_kts is not None:
                            # Convert knots to km/h
                            gps_data['speed'] = groundspeed_kts * 1.852
                    
                    # Check for attitude data (typically index 17)  
                    elif data_index == 17:  # Attitude data
                        heading = values[2] if values[2] != -999.0 else None
                        if heading is not None:
                            # Ensure heading is 0-360
                            heading = heading % 360
                            gps_data['heading'] = heading
                    
                    # Check for speeds data (typically index 3)
                    elif data_index == 3:  # Speeds data
                        groundspeed_kts = values[3] if values[3] != -999.0 and values[3] >= 0 else None
                        if groundspeed_kts is not None:
                            # Convert knots to km/h
                            gps_data['speed'] = groundspeed_kts * 1.852
                            
                except struct.error:
                    break
                
                # Move to next data_struct
                offset += record_size
            
            # Return GPS data if we have at least position
            if 'latitude' in gps_data and 'longitude' in gps_data:
                # Add simulated accuracy values
                gps_data.update({
                    'accuracy': 5.0,  # Simulated GPS accuracy in meters
                    'altitudeAccuracy': 10.0,  # Simulated altitude accuracy
                    'fix_type': '3D' if gps_data.get('altitude', 0) > 0 else '2D',
                    'satellites': {
                        'used': 8,
                        'total': 12,
                        'constellations': {
                            'GPS': {'visible': 8, 'used': 6, 'max_snr': 45},
                            'GLONASS': {'visible': 4, 'used': 2, 'max_snr': 42},
                            'Galileo': {'visible': 0, 'used': 0, 'max_snr': 0},
                            'BeiDou': {'visible': 0, 'used': 0, 'max_snr': 0}
                        }
                    }
                })
                
                return gps_data
                
        except struct.error:
            pass
        except Exception:
            pass
            
        return None
    
    def _parse_dref_packet(self, payload):
        """Parse DREF packet format (not typically used for GPS data but included for completeness)"""
        # DREF packets contain dataref values but are less structured
        # For now, we'll focus on DATA packets which are more reliable
        return None

class GPSDaemon:
    def __init__(self, socket_path='/tmp/gps_daemon.sock', baudrate=115200, daemon_mode=False, 
                 gps_source='hardware', xplane_udp_port=49003, 
                 xplane_bind_address='0.0.0.0'):
        """
        Initialize GPS daemon.
        
        Args:
            socket_path: Unix socket path for client communication
            baudrate: Serial communication baudrate
            daemon_mode: Whether running in daemon mode (affects logging)
            gps_source: GPS data source ('hardware', 'xplane', 'simulation')
            xplane_udp_port: UDP port to listen for X-Plane data
            xplane_bind_address: IP address to bind UDP listener
        """
        self.socket_path = socket_path
        self.device_paths = ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyUSB2', '/dev/ttyUSB3', '/dev/ttyUSB4', '/dev/ttyUSB5', '/dev/ttyACM0', '/dev/ttyACM1']
        self.baudrate = baudrate
        self.daemon_mode = daemon_mode
        
        # GPS source configuration
        self.gps_source = gps_source if gps_source in ['hardware', 'xplane', 'simulation'] else 'hardware'
        
        # X-Plane UDP configuration  
        self.xplane_udp_port = xplane_udp_port
        self.xplane_bind_address = xplane_bind_address
        self.xplane_parser = XPlaneUDPParser()  # Always create parser for potential use
        self.udp_socket = None
        
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
        
        if self.gps_source == 'simulation':
            self.log("GPS simulation mode enabled")
            self.simulation_worker()
        elif self.gps_source == 'xplane':
            self.log("X-Plane GPS mode enabled")
            self.xplane_worker()
        else:
            self.log("Real GPS hardware mode enabled")
            self.real_gps_worker()
    
    def simulation_worker(self):
        """Worker for GPS simulation mode"""
        self.location_data['daemon_status'] = 'simulation'
        
        self.log("GPS simulation starting with manual control")
        
        while self.running:
            try:
                # Generate simulated GPS data
                sim_data = simulate_gps_data()
                
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
    
    def xplane_worker(self):
        """Worker for X-Plane UDP GPS data mode"""
        self.location_data['daemon_status'] = 'xplane_connecting'
        self.log(f"X-Plane GPS mode starting - listening on {self.xplane_bind_address}:{self.xplane_udp_port}")
        
        connection_attempts = 0
        
        while self.running:
            try:
                connection_attempts += 1
                
                # Create UDP socket for X-Plane data
                self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.udp_socket.settimeout(5.0)  # 5 second timeout
                
                # Bind to the specified address and port
                self.udp_socket.bind((self.xplane_bind_address, self.xplane_udp_port))
                
                # Reset connection attempts on successful bind
                connection_attempts = 0
                
                self.location_data['daemon_status'] = 'xplane_listening'
                self.log(f"Successfully bound to UDP port {self.xplane_udp_port}, waiting for X-Plane data...")
                
                # Main UDP listening loop
                while self.running and self.udp_socket:
                    try:
                        # Receive UDP packet
                        data, addr = self.udp_socket.recvfrom(1024)
                        
                        if not data:
                            continue
                        
                        # Parse X-Plane UDP packet
                        gps_data = self.xplane_parser.parse_udp_packet(data)
                        
                        if gps_data:
                            
                            # Update location data with parsed X-Plane data
                            self.location_data.update({
                                'timestamp': time.time(),
                                'latitude': gps_data.get('latitude'),
                                'longitude': gps_data.get('longitude'),
                                'altitude': gps_data.get('altitude', 0),
                                'accuracy': gps_data.get('accuracy', 5.0),
                                'altitudeAccuracy': gps_data.get('altitudeAccuracy', 10.0),
                                'heading': gps_data.get('heading'),
                                'speed': gps_data.get('speed'),
                                'fix_status': gps_data.get('fix_status', 'valid'),
                                'fix_type': gps_data.get('fix_type', '3D'),
                                'daemon_status': 'xplane_data_valid',
                                'satellites': gps_data.get('satellites', {
                                    'total': 8,
                                    'used': 6,
                                    'constellations': {
                                        'GPS': {'visible': 6, 'used': 4, 'max_snr': 45},
                                        'GLONASS': {'visible': 2, 'used': 2, 'max_snr': 40},
                                        'Galileo': {'visible': 0, 'used': 0, 'max_snr': 0},
                                        'BeiDou': {'visible': 0, 'used': 0, 'max_snr': 0}
                                    }
                                })
                            })
                            
                            self.last_fix_time = time.time()
                        
                    except socket.timeout:
                        # Check if we've lost X-Plane connection
                        if self.last_fix_time and (time.time() - self.last_fix_time) > 30:
                            self.location_data['daemon_status'] = 'xplane_timeout'
                            self.location_data['fix_status'] = 'no_fix'
                        continue
                        
                    except Exception as e:
                        self.log(f"Error receiving X-Plane UDP data: {e}")
                        continue
                        
            except OSError as e:
                if self.running:  # Only log if we're still supposed to be running
                    self.log(f"UDP socket error (attempt {connection_attempts}): {e}")
                    
                    # Use progressive retry intervals
                    if connection_attempts <= 6:  # First minute: 10-second intervals
                        retry_interval = 10
                        self.log(f"X-Plane UDP not available, retrying in {retry_interval} seconds...")
                    elif connection_attempts <= 18:  # Next 2 minutes: 10-second intervals
                        retry_interval = 10
                        self.log(f"X-Plane UDP still unavailable (attempt {connection_attempts}), continuing search every {retry_interval} seconds...")
                    else:  # After 3 minutes: 30-second intervals
                        retry_interval = 30
                        self.log(f"X-Plane UDP unavailable (attempt {connection_attempts}), long-term scanning every {retry_interval} seconds...")
                    
                    self.location_data['daemon_status'] = 'xplane_no_connection'
                    
                    # Close socket before retrying
                    if self.udp_socket:
                        try:
                            self.udp_socket.close()
                        except:
                            pass
                        self.udp_socket = None
                    
                    time.sleep(retry_interval)
                    
            except Exception as e:
                if self.running:
                    self.log(f"X-Plane worker error: {e}")
                    self.location_data['daemon_status'] = 'xplane_error'
                    
                    # Close socket on error
                    if self.udp_socket:
                        try:
                            self.udp_socket.close()
                        except:
                            pass
                        self.udp_socket = None
                    
                    time.sleep(5)  # Wait before retrying
        
        # Cleanup
        if self.udp_socket:
            try:
                self.udp_socket.close()
            except:
                pass
        
        self.log("X-Plane GPS worker thread stopped")
    
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
                    
                elif request.get('command') == 'simulation_start':
                    # Start GPS simulation movement (only works in simulation mode)
                    if self.gps_source == 'simulation':
                        response = start_simulation()
                    else:
                        response = {'error': 'Simulation control only available in simulation mode'}
                        
                elif request.get('command') == 'simulation_stop':
                    # Stop GPS simulation movement (only works in simulation mode)
                    if self.gps_source == 'simulation':
                        response = stop_simulation()
                    else:
                        response = {'error': 'Simulation control only available in simulation mode'}
                        
                elif request.get('command') == 'simulation_reset':
                    # Reset GPS simulation to start position (only works in simulation mode)
                    if self.gps_source == 'simulation':
                        response = reset_simulation()
                    else:
                        response = {'error': 'Simulation control only available in simulation mode'}
                        
                elif request.get('command') == 'get_simulation_status':
                    # Get simulation status (only works in simulation mode)
                    if self.gps_source == 'simulation':
                        if hasattr(simulate_gps_data, 'is_paused'):
                            status = 'paused' if simulate_gps_data.is_paused else 'running'
                        else:
                            status = 'not_initialized'
                        response = {
                            'simulation_status': status,
                            'gps_source': self.gps_source
                        }
                    else:
                        response = {
                            'simulation_status': 'not_available',
                            'gps_source': self.gps_source
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
        self.log(f"Starting GPS Daemon in {self.gps_source} mode")        
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
        
        # Close UDP socket
        if self.udp_socket:
            try:
                self.udp_socket.close()
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
    parser.add_argument('--gps-source', choices=['hardware', 'xplane', 'simulation'], 
                        help='GPS data source (overrides settings file)')
    parser.add_argument('--xplane-port', type=int, default=49003,
                        help='UDP port for X-Plane data (default: 49003)')
    parser.add_argument('--xplane-bind', default='0.0.0.0',
                        help='IP address to bind UDP listener for X-Plane (default: 0.0.0.0)')
    
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
    
    # Load settings from file
    try:
        settings = load_settings()
        logging.info("GPS daemon loaded settings from file")
    except Exception as e:
        logging.warning(f"Could not load settings, using defaults: {e}")
        settings = {
            'gps_source': 'hardware',
            'xplane_udp_port': 49003,
            'xplane_bind_address': '0.0.0.0'
        }
    
    # Determine GPS source (command line overrides settings file)
    gps_source = getattr(args, 'gps_source', None) or settings.get('gps_source', 'hardware')
    
    # Get X-Plane settings (command line overrides settings file)
    xplane_port = getattr(args, 'xplane_port', None) or settings.get('xplane_udp_port', 49003)
    xplane_bind = getattr(args, 'xplane_bind', None) or settings.get('xplane_bind_address', '0.0.0.0')
    
    logging.info(f"GPS daemon starting with source: {gps_source}")
    if gps_source == 'xplane':
        logging.info(f"X-Plane UDP configuration: {xplane_bind}:{xplane_port}")
    elif gps_source == 'simulation':
        logging.info("GPS simulation with manual control (start/stop via web interface)")

    # Create and start daemon
    global daemon
    daemon = GPSDaemon(
        socket_path=args.socket,
        baudrate=args.baudrate,
        daemon_mode=args.daemon,
        gps_source=gps_source,
        xplane_udp_port=xplane_port,
        xplane_bind_address=xplane_bind
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
