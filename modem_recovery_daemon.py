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