#!/usr/bin/env python3
"""
Motion Detection Module - Shared motion detection functionality for GPS tracking systems

This module provides sophisticated motion detection capabilities using GPS data,
including directional movement analysis and bearing consistency checks.
"""
import time
import math
import logging
from gps_client import get_gnss_location
from utils import calculate_distance

# Module logger
logger = logging.getLogger(__name__)


def calculate_bearing(lat1, lon1, lat2, lon2):
    """
    Calculate the bearing (direction) from point 1 to point 2 in degrees (0-360)
    """
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lon = math.radians(lon2 - lon1)
    
    y = math.sin(delta_lon) * math.cos(lat2_rad)
    x = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lon)
    
    bearing = math.atan2(y, x)
    bearing = math.degrees(bearing)
    bearing = (bearing + 360) % 360  # Normalize to 0-360 degrees
    
    return bearing


def angle_difference(angle1, angle2):
    """
    Calculate the smallest angle difference between two bearings (0-180 degrees)
    """
    diff = abs(angle1 - angle2)
    if diff > 180:
        diff = 360 - diff
    return diff


class MotionDetector:
    """
    Advanced motion detection using GPS data with directional analysis
    """
    
    def __init__(self, movement_threshold=10.0, bearing_tolerance=30.0, max_history=3):
        """
        Initialize motion detector
        
        Args:
            movement_threshold (float): Minimum movement distance in meters
            bearing_tolerance (float): Maximum bearing difference for directional movement
            max_history (int): Number of positions to keep in history
        """
        self.movement_threshold = movement_threshold
        self.bearing_tolerance = bearing_tolerance
        self.max_history = max_history
        self.position_timeout = 30.0  # seconds - how long to wait for GPS fix
        self.position_history = []    # Store last few positions for direction analysis
        self.bearing_history = []     # Store bearings between consecutive positions
        
    def reset(self):
        """Reset motion detection state"""
        self.position_history = []
        self.bearing_history = []
        logger.debug("Motion detector state reset")
        
    def detect_motion(self):
        """
        Detect motion using GPS dongle by comparing current position with last known position
        Returns:
            True - significant directional movement detected (two consecutive movements within tolerance)
            False - no movement or inconsistent movement detected  
            None - GPS error, ignore this result
        """
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
            current_position = (current_lat, current_lon, current_time)

            logger.debug(f"Motion detection GPS: lat={current_lat:.6f}, lon={current_lon:.6f}, accuracy={gps_accuracy:.1f}m")

            # Add current position to history
            self.position_history.append(current_position)
            
            # Keep only the last max_history positions
            if len(self.position_history) > self.max_history:
                self.position_history = self.position_history[-self.max_history:]

            # Need at least 2 positions to detect movement
            if len(self.position_history) < 2:
                logger.debug("Need more position history for motion detection")
                return False

            # Get the last two positions
            prev_pos = self.position_history[-2]
            curr_pos = self.position_history[-1]
            
            # Calculate distance from previous position
            distance = calculate_distance(
                prev_pos[0], prev_pos[1],
                curr_pos[0], curr_pos[1]
            )

            # Use the larger of movement threshold or GPS accuracy as minimum distance
            effective_threshold = max(self.movement_threshold, gps_accuracy * 2)
            
            logger.debug(f"Distance from last position: {distance:.1f}m (threshold: {effective_threshold:.1f}m, GPS accuracy: {gps_accuracy:.1f}m)")

            # If movement is below threshold, return False
            if distance < effective_threshold:
                return False

            # Calculate bearing for this movement
            bearing = calculate_bearing(prev_pos[0], prev_pos[1], curr_pos[0], curr_pos[1])
            self.bearing_history.append(bearing)
            
            # Keep bearing history aligned with position history
            if len(self.bearing_history) > self.max_history - 1:
                self.bearing_history = self.bearing_history[-(self.max_history - 1):]

            logger.debug(f"Movement detected: {distance:.1f}m at bearing {bearing:.1f}°")

            # Need at least 2 bearings to check directional consistency
            if len(self.bearing_history) < 2:
                logger.debug("Need more bearing history for directional analysis")
                return False

            # Check if the last two movements are within tolerance of each other
            last_bearing = self.bearing_history[-1]
            prev_bearing = self.bearing_history[-2]
            bearing_diff = angle_difference(last_bearing, prev_bearing)
            
            logger.debug(f"Bearing comparison: previous={prev_bearing:.1f}°, current={last_bearing:.1f}°, difference={bearing_diff:.1f}°")

            if bearing_diff <= self.bearing_tolerance:
                logger.info(f"DIRECTIONAL MOTION DETECTED! Two consecutive movements within {bearing_diff:.1f}° (bearings: {prev_bearing:.1f}° → {last_bearing:.1f}°)")
                return True
            else:
                logger.debug(f"Movement detected but not directional: bearing difference {bearing_diff:.1f}° > {self.bearing_tolerance}°")
                return False

        except Exception as e:
            logger.exception(f"Error in GPS motion detection: {e}")
            return None  # GPS error - ignore this result


def wait_for_motion(motion_threshold_count=3, stationary_timeout=60, 
                   movement_threshold=10.0, bearing_tolerance=30.0, 
                   check_interval=2):
    """
    Wait for aircraft motion to be detected and return when motion is confirmed
    
    Args:
        motion_threshold_count (int): Number of consecutive motion detections required
        stationary_timeout (float): Seconds to wait before resetting motion count
        movement_threshold (float): Minimum movement distance in meters
        bearing_tolerance (float): Maximum bearing difference for directional movement
        check_interval (float): Time between GPS checks in seconds
    
    Returns:
        True when directional motion is detected, False if interrupted
    """
    logger.info("Motion detection monitoring started...")
    
    detector = MotionDetector(movement_threshold, bearing_tolerance)
    motion_count = 0
    last_motion_time = None

    while True:
        try:
            motion_result = detector.detect_motion()
            
            if motion_result is True:
                # Motion detected
                motion_count += 1
                last_motion_time = time.time()
                logger.info(f"Motion detected ({motion_count}/{motion_threshold_count})")

                if motion_count >= motion_threshold_count:
                    logger.info("Aircraft motion confirmed!")
                    return True
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

            time.sleep(check_interval)

        except KeyboardInterrupt:
            logger.info("Motion monitoring stopped by user")
            return False
        except Exception as e:
            logger.exception(f"Error in motion monitoring: {e}")
            time.sleep(5)  # Wait longer on error