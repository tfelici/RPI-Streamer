#!/usr/bin/env python3
"""
Simple Modem Recovery Daemon for SIM7600G-H

Monitors cellular connectivity and automatically recovers from connection failures.
Configures modem for NON-RNDIS mode (9001) with ModemManager coexistence and GPS functionality.
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

# Import shared utilities
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import send_at_command, find_working_at_port, load_cellular_settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('modem_manager')

# Configuration
CHECK_INTERVAL = 30

# Global state
shutdown_flag = threading.Event()


def update_networkmanager_apn():
    """Ensure NetworkManager cellular connection exists - all settings now configured via AT commands"""
    try:
        logger.info("Verifying NetworkManager cellular connection exists...")
        
        # Just check if cellular-auto connection exists, create basic one if needed
        check_cmd = 'nmcli connection show cellular-auto'
        result = subprocess.run(check_cmd, shell=True, capture_output=True, text=True, timeout=10)
        
        if result.returncode != 0:
            logger.info("Creating basic NetworkManager cellular connection...")
            # Create basic cellular connection - all settings handled by AT commands
            create_cmd = 'sudo nmcli connection add type gsm ifname "*" con-name cellular-auto'
            create_result = subprocess.run(create_cmd, shell=True, capture_output=True, text=True, timeout=10)
            
            if create_result.returncode == 0:
                logger.info("✓ Basic NetworkManager cellular connection created")
                return True
            else:
                logger.error(f"✗ Failed to create cellular connection: {create_result.stderr}")
                return False
        else:
            logger.info("✓ NetworkManager cellular connection already exists")
            return True
            
    except Exception as e:
        logger.error(f"Error checking NetworkManager cellular connection: {e}")
        return False


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
        if run_command("lsusb | grep -i 'simcom\\|7600\\|1e0e:9011\\|1e0e:9001\\|simtech\\|qualcomm'", None):
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



def configure_modem():
    """Configure modem for NON-RNDIS mode and GPS functionality"""
    usb_pid = '9001'
    mode_name = 'NON-RNDIS'
    logger.info(f"Starting {mode_name} mode configuration (USB PID: {usb_pid}) and GPS initialization...")
    
    # Update NetworkManager with APN from settings before AT configuration
    update_networkmanager_apn()
    
    # Check for shutdown request before starting
    if shutdown_flag.is_set():
        logger.info("Shutdown requested - skipping modem configuration")
        return False
    
    # Wait for dongle to be ready
    if not wait_for_dongle_initialization():
        logger.error("Dongle initialization failed - hardware not ready")
        return False
    
    # Step 1: ModemManager port management - MM hogs AT ports, need to temporarily stop it for configuration
    logger.info("Temporarily stopping ModemManager for modem configuration (MM hogs AT ports)")
    
    # Stop ModemManager to free up AT ports
    mm_stop_result = subprocess.run(['sudo', 'systemctl', 'stop', 'ModemManager'], 
                                   capture_output=True, text=True, timeout=10)
    if mm_stop_result.returncode != 0:
        logger.warning(f"Could not stop ModemManager: {mm_stop_result.stderr}")
        logger.info("Continuing with configuration attempt despite ModemManager running")
    else:
        logger.info("✓ ModemManager stopped - AT ports should now be available")
        time.sleep(3)  # Give time for ports to be released
    
    # Step 2: Find the AT command port using shared utility function
    logger.info("Searching for working AT command port...")
    at_port = find_working_at_port()
    
    if not at_port:
        logger.error("✗ Could not find available AT command port even after stopping ModemManager")
        
        # Restart ModemManager since we couldn't use AT ports anyway
        logger.info("Restarting ModemManager since AT configuration failed")
        subprocess.run(['sudo', 'systemctl', 'start', 'ModemManager'], 
                      capture_output=True, text=True, timeout=10)
        return False

    # Step 3: Configure NON-RNDIS mode and GPS
    non_rndis_configured = False
    non_rndis_changed = False  # Track if we actually changed NON-RNDIS mode
    gps_enabled = False
    
    try:
        with serial.Serial(at_port, 115200, timeout=10) as ser:
            # AT port is already verified by find_working_at_port(), proceed with configuration
            logger.info(f"Using verified AT port {at_port} for modem configuration")
            
            # Check current USB PID switch setting
            logger.info(f"Checking current {mode_name} mode setting...")
            response, success = send_at_command(ser, "AT+CUSBPIDSWITCH?")
            logger.info(f"Current USB PID switch response: {response}")
            
            # Check if the desired mode is already enabled
            if response and usb_pid in response:
                logger.info(f"✓ {mode_name} mode already enabled")
                rndis_configured = True
                rndis_changed = False  # Already configured, no change made
            else:
                logger.info(f"Setting {mode_name} mode ({usb_pid},1,1)...")
                response, success = send_at_command(ser, f"AT+CUSBPIDSWITCH={usb_pid},1,1")
                logger.info(f"{mode_name} mode set response: {response}")
                if success:
                    rndis_configured = True
                    rndis_changed = True  # We just changed the mode
                    logger.info(f"✓ {mode_name} mode configured - device will need to reconnect")
                    #modem will now reboot/reconnect, so we return special value indicating mode was changed
                    return "mode_changed"
                else:
                    logger.error(f"✗ Failed to set {mode_name} mode")
            
            # Network Mode Configuration - Force LTE only for stability
            logger.info("Configuring LTE-only network mode for improved stability...")
            
            # Check current network mode
            response, success = send_at_command(ser, "AT+CNMP?")
            logger.info(f"Current network mode: {response}")
            
            # Set to LTE only mode (38 = LTE only)
            if response and '+CNMP: 38' not in response:
                logger.info("Setting network mode to LTE only (AT+CNMP=38)...")
                response, success = send_at_command(ser, "AT+CNMP=38")
                logger.info(f"LTE-only mode response: {response}")
                if success and (not response or 'OK' in str(response)):
                    logger.info("✓ LTE-only mode configured successfully")
                else:
                    logger.warning("⚠ LTE-only mode setting may not have been applied")
            else:
                logger.info("✓ LTE-only mode already configured")
            
            # Verify the network mode setting
            logger.info("Verifying network mode configuration...")
            response, success = send_at_command(ser, "AT+CNMP?")
            logger.info(f"Verified network mode: {response}")
            
            # Cellular APN Configuration - Set APN, username, and password from settings
            logger.info("Configuring cellular APN, username, and password...")
            settings = load_cellular_settings()
            apn = settings.get('cellular_apn', 'internet')
            username = settings.get('cellular_username', '')
            password = settings.get('cellular_password', '')
            
            # Check current PDP context 1 settings
            response, success = send_at_command(ser, "AT+CGDCONT?")
            logger.info(f"Current PDP contexts: {response}")
            
            # Configure PDP context 1 with APN
            logger.info(f"Setting PDP context 1 APN to: {apn}")
            response, success = send_at_command(ser, f'AT+CGDCONT=1,"IP","{apn}"')
            logger.info(f"PDP context APN response: {response}")
            if success and (not response or 'OK' in str(response)):
                logger.info(f"✓ PDP context 1 APN set to: {apn}")
            else:
                logger.warning(f"⚠ Failed to set APN: {apn}")
            
            # Configure authentication if username and password are provided
            if username and password:
                logger.info(f"Setting authentication - Username: {username}, Password: {password}")
                # AT+CGAUTH syntax: AT+CGAUTH=<cid>,<auth_type>,<password>,<username>
                # auth_type: 0=none, 1=PAP, 2=CHAP, 3=PAP or CHAP
                response, success = send_at_command(ser, f'AT+CGAUTH=1,3,"{password}","{username}"')
                logger.info(f"Authentication response: {response}")
                if success and (not response or 'OK' in str(response)):
                    logger.info(f"✓ Authentication configured - Username: {username}")
                else:
                    logger.warning(f"⚠ Failed to configure authentication")
            elif username or password:
                logger.warning("⚠ Only username or password provided - both are required for authentication")
            else:
                logger.info("No authentication credentials provided - using APN only")
            
            # Network Selection Configuration - Set MCC/MNC if provided
            mcc = settings.get('cellular_mcc', '')
            mnc = settings.get('cellular_mnc', '')
            
            if mcc and mnc:
                logger.info(f"Configuring manual network selection - MCC: {mcc}, MNC: {mnc}")
                # AT+COPS=1,2,"MCCMNC" - Manual network selection by MCC/MNC
                network_id = f"{mcc}{mnc}"
                response, success = send_at_command(ser, f'AT+COPS=1,2,"{network_id}"')
                logger.info(f"Network selection response: {response}")
                if success and (not response or 'OK' in str(response)):
                    logger.info(f"✓ Manual network selection configured - Network ID: {network_id}")
                else:
                    logger.warning(f"⚠ Failed to configure network selection: {network_id}")
                    # Don't fail completely if network selection fails - modem may auto-select
            else:
                logger.info("Using automatic network selection (no MCC/MNC specified)")
                # AT+COPS=0 - Automatic network selection
                response, success = send_at_command(ser, "AT+COPS=0")
                logger.info(f"Automatic network selection response: {response}")
                if success:
                    logger.info("✓ Automatic network selection enabled")
                else:
                    logger.warning("⚠ Failed to enable automatic network selection")
            
            # Verify network selection configuration
            logger.info("Verifying network selection configuration...")
            response, success = send_at_command(ser, "AT+COPS?")
            logger.info(f"Current network selection: {response}")
            
            # Verify cellular configuration
            logger.info("Verifying cellular configuration...")
            response, success = send_at_command(ser, "AT+CGDCONT?")
            logger.info(f"Verified PDP contexts: {response}")
            response, success = send_at_command(ser, "AT+CGAUTH?")
            logger.info(f"Verified authentication: {response}")
            
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
        return False
    
    # Step 4: Restart ModemManager after configuration and wait for modem detection
    logger.info("Restarting ModemManager after configuration...")
    mm_start_result = subprocess.run(['sudo', 'systemctl', 'start', 'ModemManager'], 
                                    capture_output=True, text=True, timeout=10)
    if mm_start_result.returncode != 0:
        logger.error(f"Failed to restart ModemManager: {mm_start_result.stderr}")
    else:
        logger.info("✓ ModemManager restarted successfully")
        
        # Wait for ModemManager to detect the modem (up to 30 seconds)
        logger.info("Waiting for ModemManager to detect the modem...")
        modem_detected = False
        detection_start = time.time()
        detection_timeout = 30
        
        while time.time() - detection_start < detection_timeout and not shutdown_flag.is_set():
            if check_modem_present():
                modem_detected = True
                detection_time = time.time() - detection_start
                logger.info(f"✓ Modem detected by ModemManager after {detection_time:.1f}s")
                break
            time.sleep(1)  # Check every second
            
        if not modem_detected:
            logger.warning(f"⚠️ Modem not detected by ModemManager within {detection_timeout}s")
        elif shutdown_flag.is_set():
            logger.info("Shutdown requested during modem detection wait")
    
    if rndis_configured and gps_enabled:
        logger.info(f"✓ {mode_name} mode and GPS configuration completed successfully")
        return "mode_changed" if rndis_changed else True
    elif rndis_configured:
        logger.info(f"✓ {mode_name} mode configured successfully (GPS had issues)")
        return "mode_changed" if rndis_changed else True
    else:
        logger.error(f"✗ {mode_name} mode and GPS configuration failed")
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


def check_internet_connectivity():
    """Check if internet connectivity is available"""
    try:
        # Try to ping Google's DNS and Cloudflare's DNS
        for dns_server in ['8.8.8.8', '1.1.1.1']:
            result = subprocess.run(['ping', '-c', '1', '-W', '3', dns_server], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                logger.debug(f"Internet connectivity confirmed via {dns_server}")
                return True
        
        logger.debug("Internet connectivity test failed - no response from DNS servers")
        return False
    except Exception as e:
        logger.error(f"Error checking internet connectivity: {e}")
        return False


def perform_modem_recovery(timeout=60, poll_interval=3):
    """Reset the modem via AT command (with MM stopped) or fallback to ModemManager restart"""
    try:
        # Import modem reset function from utils
        from utils import reset_modem_at_command
        
        logger.info("Starting modem recovery - stopping ModemManager for AT command access...")
        
        # Stop ModemManager to free up AT ports (MM hogs the ports)
        mm_stop_result = subprocess.run(['sudo', 'systemctl', 'stop', 'ModemManager'], 
                                        capture_output=True, text=True, timeout=10)
        success = False
        message = "Unknown error"
        
        if mm_stop_result.returncode == 0:
            logger.info("✓ ModemManager stopped for AT reset")
            time.sleep(2)  # Give time for ports to be released
            
            # Try AT reset with MM stopped
            success, message = reset_modem_at_command()
            
            # Restart ModemManager regardless of AT result
            mm_start_result = subprocess.run(['sudo', 'systemctl', 'start', 'ModemManager'], 
                                            capture_output=True, text=True, timeout=10)
            if mm_start_result.returncode == 0:
                logger.info("✓ ModemManager restarted after AT reset attempt")
            else:
                logger.error(f"Failed to restart ModemManager: {mm_start_result.stderr}")
            
            if success:
                logger.info(f"✓ AT reset successful: {message}")
            else:
                logger.warning(f"AT reset failed: {message}")
        else:
            logger.error(f"Failed to stop ModemManager: {mm_stop_result.stderr}")
            
        if not success:
            # Fallback to ModemManager restart if AT command fails
            logger.info("AT reset failed - falling back to ModemManager restart...")
            restart_result = subprocess.run(['sudo', 'systemctl', 'restart', 'ModemManager'], 
                                          capture_output=True, text=True, timeout=10)
            if restart_result.returncode != 0:
                logger.error(f"ModemManager restart also failed: {restart_result.stderr}")
                return False
            
            # Wait for modem to reappear after ModemManager restart
            logger.info(f"Waiting for modem to reappear after ModemManager restart (timeout: {timeout}s)...")
            start_time = time.time()
            
            while time.time() - start_time < timeout:
                if shutdown_flag.is_set():
                    return False
                    
                if check_modem_present():
                    logger.info("Modem reappeared in ModemManager after restart")
                    return True
                    
                time.sleep(poll_interval)
            
            logger.warning(f"Modem did not reappear within {timeout} seconds after ModemManager restart")
            return False
        else:
            logger.info("Modem reset via AT command - waiting for udev rule to detect reconnection and trigger daemon termination...")
            
            # Wait for termination signal from udev (modem will be detected by udev and trigger rule)
            # The AT+CRESET command causes modem to restart, so it should now be reconnecting
            start_time = time.time()
            while time.time() - start_time < timeout:
                if shutdown_flag.is_set():
                    logger.info("Received termination signal from udev - daemon shutting down")
                    return True
                time.sleep(poll_interval)
            
            logger.warning(f"No termination signal received within {timeout} seconds after modem reset")
            return False
        
    except Exception as e:
        logger.error(f"Error during modem reset: {e}")
        return False



def main_loop():
    """Main monitoring loop"""
    logger.info("Starting modem recovery daemon")
    
    # Initialize NON-RNDIS mode and GPS on startup (invoked by udev upon USB insertion)
    logger.info("Configuring NON-RNDIS mode and GPS...")
    config_result = configure_modem()
    
    if config_result == "mode_changed":
        logger.info("✓ NON-RNDIS mode was changed - waiting for shutdown signal from udev")
        # Mode was actually changed - modem will reboot/reconnect
        # Wait for udev rule to send shutdown signal, don't continue monitoring
        while not shutdown_flag.is_set():
            shutdown_flag.wait(CHECK_INTERVAL)
        logger.info("Received shutdown signal - daemon terminating")
        return
    elif config_result:  # True - mode was already configured
        logger.info("✓ NON-RNDIS mode and GPS configuration successful - mode was already enabled, continuing monitoring")
    else:  # False - configuration failed
        logger.warning("⚠️ NON-RNDIS mode and GPS configuration failed - continuing with monitoring")
    
    while not shutdown_flag.is_set():
        try:
            # Check if modem is present in ModemManager
            modem_present = check_modem_present()
            
            if modem_present:
                logger.debug("Modem is present in ModemManager")
            else:
                logger.warning("Modem not detected in ModemManager")
                
                # Check internet connectivity first - if internet is available, don't restart modem
                internet_available = check_internet_connectivity()
                
                if internet_available:
                    logger.info("Internet connectivity is available - skipping modem recovery")
                else:
                    logger.info("No internet connectivity detected - checking USB device presence")
                    
                    # Check if USB device is still physically present
                    usb_present = check_usb_device_present()
                    
                    if usb_present:
                        logger.info("USB device still present, attempting recovery")
                        perform_modem_recovery()
                    else:
                        logger.error("USB device not present - hardware issue or device unplugged")
            
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
        
        # Wait for next check
        shutdown_flag.wait(CHECK_INTERVAL)
     
    logger.info("Modem recovery daemon stopped")

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Modem Recovery Daemon for NON-RNDIS mode')
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