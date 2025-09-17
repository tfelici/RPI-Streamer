#!/usr/bin/env python3
"""
Simple Modem Recovery Daemon for SIM7600G-H

Monitors cellular connectivity and automatically recovers from connection failures.
"""

import os
import sys
import time
import signal
import logging
import subprocess
import threading
import argparse
import json
import re
import serial
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('modem_recovery')

# Configuration
CHECK_INTERVAL = 30

# Global state
shutdown_flag = threading.Event()


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, shutting down...")
    shutdown_flag.set()


def run_command(command, description=None):
    """Run shell command and return success status"""
    if description:
        logger.info(description)
    
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            if description:
                logger.info(f"✓ {description} - Success")
            return True
        else:
            logger.error(f"✗ Command failed: {command}")
            if result.stderr:
                logger.error(f"Error: {result.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"✗ Command timed out: {command}")
        return False
    except Exception as e:
        logger.error(f"✗ Command exception: {e}")
        return False


def wait_for_dongle_initialization(max_wait_time=60):
    """Wait for GPS dongle to be fully initialized and responsive (hardware detection only)"""
    logger.info("Waiting for GPS dongle hardware detection...")
    start_time = time.time()
    
    # Step 1: Wait for USB device to appear
    usb_ready = False
    while time.time() - start_time < max_wait_time and not usb_ready and not shutdown_flag.is_set():
        # Check for SIM7600G-H device by ID or common names
        if run_command("lsusb | grep -i 'simcom\\|7600\\|1e0e:9011\\|simtech\\|qualcomm'", None):
            usb_ready = True
            logger.info("✓ USB device detected")
        else:
            time.sleep(2)
    
    if shutdown_flag.is_set():
        logger.info("Shutdown requested during USB device detection")
        return False
    
    if not usb_ready:
        logger.error("✗ USB device not detected within timeout")
        return False
    
    # Step 2: Wait for serial ports to appear (only check existence, don't access)
    device_paths = ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyUSB2', '/dev/ttyUSB3', '/dev/ttyUSB4', '/dev/ttyUSB5', '/dev/ttyACM0', '/dev/ttyACM1']
    ports_ready = False
    while time.time() - start_time < max_wait_time and not ports_ready and not shutdown_flag.is_set():
        existing_ports = [path for path in device_paths if os.path.exists(path)]
        if existing_ports:
            ports_ready = True
            logger.info(f"✓ Serial ports available: {existing_ports}")
        else:
            time.sleep(2)
    
    if shutdown_flag.is_set():
        logger.info("Shutdown requested during serial port detection")
        return False
    
    if not ports_ready:
        logger.error("✗ Serial ports not available within timeout")
        return False
    
    # Step 3: Simple wait for device stabilization (no AT command testing during runtime)
    stabilization_time = 5
    logger.info(f"Waiting {stabilization_time}s for device stabilization...")
    time.sleep(stabilization_time)
    
    total_wait = time.time() - start_time
    logger.info(f"✓ GPS dongle hardware detection complete (waited {total_wait:.1f}s)")
    return True


def send_at_command(serial_port, command, timeout=10):
    """Send AT command and wait for complete response"""
    try:
        # Clear any pending data
        serial_port.reset_input_buffer()
        
        # Send command
        logger.info(f"Sending AT command: {command}")
        serial_port.write(f"{command}\r\n".encode('ascii'))
        
        # Poll for response
        response_lines = []
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if serial_port.in_waiting > 0:
                line = serial_port.readline().decode('ascii', errors='ignore').strip()
                if line:
                    response_lines.append(line)
                    logger.info(f"AT response line: {line}")
                    
                    # Check for completion indicators
                    if line in ['OK', 'ERROR'] or line.startswith('+CME ERROR') or line.startswith('+CMS ERROR'):
                        break
            else:
                time.sleep(0.1)  # Small delay to avoid busy waiting
        
        response = '\n'.join(response_lines)
        
        if not response_lines:
            logger.error(f"✗ AT command timeout: {command}")
            return None, False
        
        # Determine success
        success = any(line == 'OK' for line in response_lines)
        if success:
            logger.info(f"✓ AT command successful: {command}")
        else:
            logger.error(f"✗ AT command failed: {command}")
        
        return response, success
        
    except Exception as e:
        logger.error(f"✗ Exception sending AT command {command}: {e}")
        return None, False


def configure_modem():
    """Configure modem for RNDIS mode and GPS functionality"""
    logger.info("Starting RNDIS mode configuration and GPS initialization...")
    
    # Check for shutdown request before starting
    if shutdown_flag.is_set():
        logger.info("Shutdown requested - skipping modem configuration")
        return False
    
    # Wait for dongle to be ready
    if not wait_for_dongle_initialization():
        logger.error("Dongle initialization failed - hardware not ready")
        return False
    
    # Step 1: Stop ModemManager to avoid conflicts
    if not run_command("systemctl stop ModemManager", "Stopping ModemManager"):
        logger.warning("Warning: Could not stop ModemManager")
    
    # Wait a moment for ModemManager to fully stop
    time.sleep(2)
    
    # Step 2: Find the AT command port (usually /dev/ttyUSB2 or similar)
    at_ports = ['/dev/ttyUSB2', '/dev/ttyUSB3', '/dev/ttyACM2', '/dev/ttyACM3']
    at_port = None
    
    for port in at_ports:
        if os.path.exists(port):
            try:
                with serial.Serial(port, 115200, timeout=5) as ser:
                    # Test basic AT communication
                    response, success = send_at_command(ser, "AT")
                    if success:
                        at_port = port
                        logger.info(f"✓ Found AT command port: {port}")
                        break
            except Exception as e:
                logger.error(f"Could not test AT port {port}: {e}")
                continue
    
    if not at_port:
        logger.error("✗ Could not find AT command port")
        # Re-enable ModemManager before returning
        run_command("systemctl start ModemManager", "Re-enabling ModemManager")
        return False
    
    # Step 3: Configure RNDIS mode and GPS
    rndis_configured = False
    gps_enabled = False
    
    try:
        with serial.Serial(at_port, 115200, timeout=10) as ser:
            # Test basic AT communication
            logger.info("Testing AT communication...")
            response, success = send_at_command(ser, "AT")
            if not success:
                logger.error("✗ AT communication failed")
                raise Exception("AT communication failed")
            
            logger.info("✓ AT communication successful")
            
            # Check current USB PID switch setting
            logger.info("Checking current RNDIS mode setting...")
            response, success = send_at_command(ser, "AT+CUSBPIDSWITCH?")
            logger.info(f"Current USB PID switch response: {response}")
            
            # Check if RNDIS mode is already enabled
            if response and '9011' in response:
                logger.info("✓ RNDIS mode already enabled")
                rndis_configured = True
            else:
                logger.info("Setting RNDIS mode (9011,1,1)...")
                response, success = send_at_command(ser, "AT+CUSBPIDSWITCH=9011,1,1")
                logger.info(f"RNDIS mode set response: {response}")
                if success:
                    rndis_configured = True
                    logger.info("✓ RNDIS mode configured - device will need to reconnect")
                else:
                    logger.error("✗ Failed to set RNDIS mode")
            
            # GPS Configuration (similar to GPS daemon)
            # First, disable GPS to ensure clean state
            logger.info("Disabling GPS for clean configuration...")
            response, success = send_at_command(ser, "AT+CGPS=0")
            logger.info(f"GPS disable response: {response}")
            
            # Wait a moment for GPS to fully stop
            time.sleep(2)
            
            # Check if GALILEO is enabled
            logger.info("Checking if Galileo is enabled...")
            response, success = send_at_command(ser, "AT+CGNSSMODE?")
            logger.info(f"CGNSSMODE response: {response}")
            if response and '+CGNSSMODE:' in response:
                codes = response.split(':')[1].strip()
                if '15,1' in codes:
                    logger.info("✓ Galileo already enabled")
                else:
                    # Enable Galileo
                    logger.info("Enabling Galileo constellation...")
                    response, success = send_at_command(ser, "AT+CGNSSMODE=15,1")
                    logger.info(f"Galileo enable response: {response}")
            
            # Configure NMEA sentence types BEFORE enabling GPS
            logger.info("Configuring NMEA sentence types (enabling comprehensive sentence set)...")
            response, success = send_at_command(ser, "AT+CGPSNMEA=198143")
            logger.info(f"NMEA config response: {response}")
            
            # Set NMEA output rate to 1Hz BEFORE enabling GPS
            logger.info("Setting NMEA output rate...")
            response, success = send_at_command(ser, "AT+CGPSNMEARATE=0")
            logger.info(f"NMEA rate response: {response}")
            
            # Now enable GPS with the configured settings
            logger.info("Enabling GPS with configured NMEA settings...")
            response, success = send_at_command(ser, "AT+CGPS=1")
            logger.info(f"GPS enable response: {response}")
            
            # Verify GPS is enabled
            logger.info("Verifying final GPS status...")
            response, success = send_at_command(ser, "AT+CGPS?")
            logger.info(f"Final GPS status: {response}")
            
            if response and '+CGPS: 1' in response:
                gps_enabled = True
                logger.info("✓ GPS successfully enabled")
            else:
                logger.error("✗ GPS enable verification failed")
                
    except Exception as e:
        logger.error(f"✗ Error during RNDIS/GPS configuration: {e}")
    
    # Step 4: Re-enable ModemManager and wait for modem to reappear
    logger.info("Re-enabling ModemManager...")
    if not run_command("systemctl start ModemManager", "Re-enabling ModemManager"):
        logger.warning("Warning: Could not restart ModemManager")
        if rndis_configured and gps_enabled:
            logger.info("✓ RNDIS mode and GPS configuration completed successfully (ModemManager restart failed)")
            return True
        elif rndis_configured:
            logger.info("✓ RNDIS mode configured successfully (GPS configuration had issues, ModemManager restart failed)")
            return True
        else:
            logger.error("✗ RNDIS mode and GPS configuration failed")
            return False
    
    # Wait for modem to reappear in ModemManager after RNDIS configuration
    logger.info("Waiting for modem to reappear in ModemManager after configuration...")
    modem_reappeared = False
    wait_timeout = 60  # Wait up to 60 seconds
    start_time = time.time()
    
    while time.time() - start_time < wait_timeout:
        if check_modem_present():
            modem_reappeared = True
            logger.info("✓ Modem reappeared in ModemManager")
            break
        time.sleep(3)  # Check every 3 seconds
    
    if not modem_reappeared:
        logger.warning("⚠️ Modem did not reappear in ModemManager within timeout")
    
    if rndis_configured and gps_enabled:
        status_msg = "✓ RNDIS mode and GPS configuration completed successfully"
        if modem_reappeared:
            status_msg += " - Modem ready in ModemManager"
        else:
            status_msg += " - Modem not yet visible in ModemManager"
        logger.info(status_msg)
        return True
    elif rndis_configured:
        status_msg = "✓ RNDIS mode configured successfully (GPS configuration had issues)"
        if modem_reappeared:
            status_msg += " - Modem ready in ModemManager"
        else:
            status_msg += " - Modem not yet visible in ModemManager"
        logger.info(status_msg)
        return True
    else:
        logger.error("✗ RNDIS mode and GPS configuration failed")
        return False


def check_modem_present():
    """Check if modem is present in ModemManager"""
    try:
        result = subprocess.run(['mmcli', '-L', '--output-json'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            modem_list_json = json.loads(result.stdout)
            modem_paths = modem_list_json.get('modem-list', [])
            return len(modem_paths) > 0
    except Exception as e:
        logger.error(f"Error checking modem presence: {e}")
    return False


def check_usb_device_present():
    """Check if SIM7600G-H USB device is present"""
    try:
        result = subprocess.run(['lsusb'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            # Look for SIM7600G-H device (SimTech/Qualcomm)
            return ('SimTech' in result.stdout or 
                   'Qualcomm' in result.stdout)
    except Exception as e:
        logger.error(f"Error checking USB device: {e}")
    return False


def restart_modem_manager_and_wait(timeout=60, poll_interval=3):
    """Restart ModemManager and wait for modem to reappear"""
    try:
        logger.info("Restarting ModemManager...")
        restart_result = subprocess.run(['sudo', 'systemctl', 'restart', 'ModemManager'], 
                                      capture_output=True, text=True, timeout=10)
        
        if restart_result.returncode != 0:
            logger.error(f"Failed to restart ModemManager: {restart_result.stderr}")
            return False
        
        logger.info(f"Waiting for modem to reappear (timeout: {timeout}s)...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if shutdown_flag.is_set():
                return False
                
            if check_modem_present():
                logger.info("Modem reappeared in ModemManager")
                return True
                
            time.sleep(poll_interval)
        
        logger.warning(f"Modem did not reappear within {timeout} seconds")
        return False
        
    except Exception as e:
        logger.error(f"Error during ModemManager restart: {e}")
        return False


def main_loop():
    """Main monitoring loop"""
    logger.info("Starting modem recovery daemon")
    
    # Initialize RNDIS mode and GPS on startup (invoked by udev upon USB insertion)
    logger.info("Configuring RNDIS mode and GPS...")
    if configure_modem():
        logger.info("✓ RNDIS mode and GPS configuration successful")
    else:
        logger.warning("⚠️ RNDIS mode and GPS configuration failed - continuing with monitoring")
    
    while not shutdown_flag.is_set():
        try:
            # Check if modem is present in ModemManager
            modem_present = check_modem_present()
            
            if modem_present:
                logger.debug("Modem is present in ModemManager")
            else:
                logger.warning("Modem not detected in ModemManager")
                
                # Check if USB device is still physically present
                usb_present = check_usb_device_present()
                
                if usb_present:
                    logger.info("USB device still present, attempting recovery")
                    restart_modem_manager_and_wait()
                else:
                    logger.error("USB device not present - hardware issue or device unplugged")
            
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
        
        # Wait for next check
        shutdown_flag.wait(CHECK_INTERVAL)
     
    logger.info("Modem recovery daemon stopped")

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Modem Recovery Daemon')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon')
    args = parser.parse_args()
    
    # Set up signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if args.daemon:
        logger.info("Running in daemon mode")
    
    try:
        main_loop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()