#!/usr/bin/python3

import os
import time
import logging
from subprocess import call
from utils import get_ups_status

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/var/log/ups-monitor.log')
    ]
)

# User-configurable variables
SLEEP_TIME = 60  # Time in seconds to wait between failure checks
LOOP = True  # Set to True for continuous monitoring (required for systemd service)

# Ensure only one instance of the script is running
pid = str(os.getpid())
pidfile = "/var/run/X1200.pid" # move to /var/run because of conventions
if os.path.isfile(pidfile):
    print("Script already running")
    exit(1)
else:
    with open(pidfile, 'w') as f:
        f.write(pid)

try:
    logging.info("Starting UPS monitoring")
    
    while True:
        try:
            # Get complete UPS status using utility function
            ups_status = get_ups_status()
            
            voltage = ups_status['voltage']
            capacity = ups_status['capacity']
            battery_status = ups_status['battery_status']
            ac_power_connected = ups_status['ac_power_connected']
            
            # Handle case where UPS is not available
            if voltage is None or capacity is None or ac_power_connected is None:
                error_msg = "UPS not available or communication error."
                print(error_msg)
                logging.error(error_msg)
                if LOOP:
                    time.sleep(SLEEP_TIME)
                    continue
                else:
                    exit(1)
            
            status_msg = f"Capacity: {capacity:.2f}% ({battery_status}), AC Power: {'Plugged in' if ac_power_connected else 'Unplugged'}, Voltage: {voltage:.2f}V"
            print(status_msg)
            logging.info(status_msg)
            
            if not ac_power_connected:
                warning_msg = "UPS is unplugged or AC power loss detected."
                print(warning_msg)
                logging.warning(warning_msg)
                print(f"Waiting {SLEEP_TIME} seconds before shutdown...")
                time.sleep(SLEEP_TIME)
                
                # Check power state again after sleep time
                ups_status_recheck = get_ups_status()
                if ups_status_recheck['ac_power_connected'] is False:
                    shutdown_message = "UPS still unplugged after grace period. Initiating shutdown."
                    print(shutdown_message)
                    logging.critical(shutdown_message)
                    call("sudo nohup shutdown -h now", shell=True)
                else:
                    recovery_msg = "Power restored during grace period. Continuing monitoring."
                    print(recovery_msg)
                    logging.info(recovery_msg)
            else:
                # Check for other critical conditions only when AC power is present
                critical_condition = False
                shutdown_reason = ""
                
                if capacity < 20:
                    critical_msg = "Battery level critical."
                    print(critical_msg)
                    logging.warning(critical_msg)
                    critical_condition = True
                    shutdown_reason = "due to critical battery level."
                elif voltage < 3.20:
                    critical_msg = "Battery voltage critical."
                    print(critical_msg)
                    logging.warning(critical_msg)
                    critical_condition = True
                    shutdown_reason = "due to critical battery voltage."
                
                if critical_condition:
                    shutdown_message = f"Critical condition met {shutdown_reason} Initiating shutdown."
                    print(shutdown_message)
                    logging.critical(shutdown_message)
                    call("sudo nohup shutdown -h now", shell=True)
                else:
                    print("System operating within normal parameters. No action required.")
                    logging.debug("System operating within normal parameters.")
            
            if LOOP:
                time.sleep(SLEEP_TIME)
            else:
                break
                
        except Exception as e:
            error_msg = f"Error during monitoring cycle: {e}"
            print(error_msg)
            logging.error(error_msg)
            if LOOP:
                time.sleep(SLEEP_TIME)
                continue
            else:
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
    if os.path.isfile(pidfile):
        os.unlink(pidfile)
    logging.info("UPS monitoring stopped")
    exit(0)

