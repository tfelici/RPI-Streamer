#!/usr/bin/python3

import os
import time
import logging
import sys
from logging.handlers import RotatingFileHandler
from subprocess import call
from x120x import X120X

import fcntl
from utils import is_streaming

# Configure logging with rotation to prevent unlimited growth
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler('/var/log/ups-monitor.log', maxBytes=50000, backupCount=1)
    ]
)

# User-configurable variables
SLEEP_TIME = 60  # Time in seconds to wait between failure checks
LOOP = True  # Set to True for continuous monitoring (required for systemd service)

# Ensure only one instance of the script is running using file locking
lockfile_path = "/var/run/ups-monitor.lock"
try:
    lockfile = open(lockfile_path, 'w')
    fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    lockfile.write(str(os.getpid()))
    lockfile.flush()
except (IOError, OSError):
    print("Another instance of UPS monitor is already running")
    if 'lockfile' in locals() and lockfile:
        lockfile.close()
    sys.exit(1)

try:
    logging.info("Starting UPS monitoring")
    
    # Main monitoring loop - runs until error occurs
    while True:
        try:
            # Create fresh UPS connection for each monitoring cycle
            with X120X() as ups:
                logging.debug("UPS connection established for monitoring cycle")
                
                # Get complete UPS status using fresh connection
                ups_status = ups.get_status()
                
                voltage = ups_status['voltage']
                capacity = ups_status['capacity']
                battery_status = ups_status['battery_status']
                ac_power_connected = ups_status['ac_power_connected']
                
                # Handle case where UPS communication fails
                if voltage is None or capacity is None or ac_power_connected is None:
                    error_msg = "UPS communication error - device may be disconnected."
                    print(error_msg)
                    logging.error(error_msg)
                    # Exit and let service restart us
                    exit(1)
                
                status_msg = f"Capacity: {capacity:.2f}% ({battery_status}), AC Power: {'Plugged in' if ac_power_connected else 'Unplugged'}, Voltage: {voltage:.2f}V"
                print(status_msg)
                logging.info(status_msg)
            # First connection closed here
            
            # Handle power state outside of connection context
            if not ac_power_connected:
                warning_msg = "UPS is unplugged or AC power loss detected."
                print(warning_msg)
                logging.warning(warning_msg)
                
                # Check if streaming is active - if so, skip shutdown process entirely
                if is_streaming():
                    streaming_msg = "UPS unplugged but streaming is active. Skipping shutdown to prevent stream interruption."
                    print(streaming_msg)
                    logging.warning(streaming_msg)
                else:
                    print(f"Waiting {SLEEP_TIME} seconds before shutdown...")
                    time.sleep(SLEEP_TIME)
                    
                    # Create fresh connection for recheck after sleep
                    with X120X() as ups_recheck:
                        ups_status_recheck = ups_recheck.get_status()
                        recheck_ac_power = ups_status_recheck.get('ac_power_connected', False)
                        
                        if not recheck_ac_power:
                            shutdown_message = "UPS still unplugged after grace period. Initiating shutdown."
                            print(shutdown_message)
                            logging.critical(shutdown_message)
                            call("sudo nohup shutdown -h now", shell=True)
                        else:
                            recovery_msg = "Power restored during grace period. Continuing monitoring."
                            print(recovery_msg)
                            logging.info(recovery_msg)
            else:
                print("UPS plugged in. No action required.")
                logging.debug("UPS plugged in.")

            if LOOP:
                time.sleep(SLEEP_TIME)
            else:
                # Single run mode - exit after one successful check
                exit(0)
                
        except Exception as e:
            error_msg = f"Error during monitoring cycle: {e}"
            print(error_msg)
            logging.error(error_msg)
            # Exit and let service restart us
            exit(1)

except KeyboardInterrupt:
    print("Monitoring stopped by user")
    logging.info("Monitoring stopped by user (KeyboardInterrupt)")
except Exception as e:
    error_msg = f"Fatal error: {e}"
    print(error_msg)
    logging.critical(error_msg)
    exit(1)

finally:
    # File lock is automatically released when the process exits
    # Close the lockfile handle if it was opened
    if 'lockfile' in locals() and lockfile:
        try:
            lockfile.close()
        except:
            pass
    logging.info("UPS monitoring stopped")
    exit(0)

