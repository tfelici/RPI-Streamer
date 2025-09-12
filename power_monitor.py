#!/usr/bin/python3
"""
UPS Power Monitor for RPI Streamer

Monitors UPS power status and handles graceful shutdown on power loss.
The grace period before shutdown is configurable through settings.json:
- power_monitor_sleep_time: Grace period in seconds
- Set to 0 or remove to disable power monitoring entirely

When power monitoring is disabled, the script will still log UPS status
but will not perform any shutdown actions on power loss.
"""

import os
import time
import logging
import sys
import argparse
from logging.handlers import RotatingFileHandler
from subprocess import call
from x120x import X120X

import fcntl
from utils import is_streaming, is_gps_tracking, load_settings
from app import stop_flight

# Parse command line arguments
parser = argparse.ArgumentParser(description='UPS Power Monitor for RPI Streamer')
parser.add_argument('--daemon', action='store_true', 
                    help='Run in daemon mode with file logging (default: interactive mode with terminal output only)')
args = parser.parse_args()

# Configure logging based on mode
if args.daemon:
    # Daemon mode: log to both console and file
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler('/var/log/ups-monitor.log', maxBytes=1024*1024, backupCount=3)
        ]
    )
else:
    # Interactive mode: log to console only
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler()
        ]
    )

# Ensure only one instance of the script is running using file locking
lockfile_path = "/var/run/ups-monitor.lock"
try:
    lockfile = open(lockfile_path, 'w')
    fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    lockfile.write(str(os.getpid()))
    lockfile.flush()
except (IOError, OSError):
    logging.error("Another instance of UPS monitor is already running")
    if 'lockfile' in locals() and lockfile:
        lockfile.close()
    sys.exit(1)

poll_time = 60  # Default poll time if settings load fails
try:
    logging.info("Starting UPS monitoring")
    if args.daemon:
        logging.info("Running in daemon mode - logging to console and file")
    else:
        logging.info("Running in interactive mode - logging to console only")
    
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
                    logging.error("UPS communication error - device may be disconnected.")
                    # Exit and let service restart us
                    exit(1)
                
                logging.info(f"Capacity: {capacity:.2f}% ({battery_status}), AC Power: {'Plugged in' if ac_power_connected else 'Unplugged'}, Voltage: {voltage:.2f}V")
            # First connection closed here
            
            # Load current settings
            settings = load_settings()
            sleep_time = settings.get('power_monitor_sleep_time')
            
            # Handle power state outside of connection context
            if not ac_power_connected:
                logging.warning("UPS is unplugged or AC power loss detected.")
                
                # If sleep_time is 0 or None, disable power monitoring
                if not sleep_time:
                    logging.info("Power monitoring disabled (sleep_time is 0 or unset) - continuing normal monitoring for 60 seconds")
                    time.sleep(poll_time)
                    continue
                else:
                    logging.info(f"Power monitoring active - grace period set to {sleep_time} seconds")
                
                # Check if streaming or GPS tracking is active
                streaming_active = is_streaming()
                gps_active = is_gps_tracking()
                
                if streaming_active or gps_active:                    
                    # Check if GPS tracking should be stopped after timeout
                    if (gps_active and 
                        settings['gps_stop_on_power_loss']):
                        
                        timeout_minutes = settings['gps_stop_power_loss_minutes']
                        timeout_seconds = timeout_minutes * 60
                        
                        logging.warning(f"GPS tracking active during power loss. Will stop GPS tracking after {timeout_minutes} minutes if power not restored.")
                        
                        # Poll power status during timeout period instead of sleeping
                        logging.info(f"Monitoring power status for {timeout_minutes} minutes before stopping GPS tracking...")
                        elapsed_seconds = 0
                        check_interval = 10  # Check every 10 seconds
                        
                        while elapsed_seconds < timeout_seconds:
                            time.sleep(check_interval)
                            elapsed_seconds += check_interval
                            
                            # Check if power has been restored
                            try:
                                with X120X() as ups_timeout_check:
                                    ups_timeout_status = ups_timeout_check.get_status()
                                    timeout_ac_power = ups_timeout_status.get('ac_power_connected', False)
                                    
                                    if timeout_ac_power:
                                        # Power restored! Exit the timeout loop
                                        logging.info(f"Power restored after {elapsed_seconds//60} minutes {elapsed_seconds%60} seconds. GPS tracking continues.")
                                        break
                                    else:
                                        # Show progress every minute
                                        if elapsed_seconds % 60 == 0:
                                            remaining_minutes = (timeout_seconds - elapsed_seconds) // 60
                                            logging.info(f"Power still lost. GPS tracking will stop in {remaining_minutes} minutes if power not restored.")
                            except Exception as e:
                                logging.error(f"Error checking power during timeout: {e}")
                                # Continue the loop even if we can't check power status
                        else:
                            # Timeout completed without power restoration
                            logging.warning("Timeout period completed. Power not restored.")
                            
                            # Final power check before stopping GPS
                            try:
                                with X120X() as ups_final_timeout_check:
                                    ups_final_timeout_status = ups_final_timeout_check.get_status()
                                    final_timeout_ac_power = ups_final_timeout_status.get('ac_power_connected', False)
                                    
                                    if not final_timeout_ac_power:
                                        # Power still lost, stop GPS tracking
                                        try:
                                            success, message, status_code = stop_flight()
                                            if success:
                                                logging.warning("GPS tracking stopped due to prolonged power loss")
                                            else:
                                                logging.error(f"Failed to stop GPS tracking: {message}")
                                        except Exception as e:
                                            logging.error(f"Error stopping GPS tracking: {e}")
                                        
                                        # Continue normal monitoring after stopping GPS
                                        logging.info("GPS tracking stopped due to prolonged power loss. Continuing normal power monitoring.")
                                    else:
                                        logging.info("Power restored just before GPS timeout. GPS tracking continues.")
                            except Exception as e:
                                logging.error(f"Error during final timeout check: {e}")
                    else:
                        activities = []
                        if streaming_active:
                            activities.append("streaming")
                        if gps_active:
                            activities.append("GPS tracking")
                        # Standard behavior - skip shutdown while activities are running
                        logging.warning(f"UPS unplugged but {' and '.join(activities)} {'is' if len(activities)==1 else 'are'} active. Skipping shutdown to prevent interruption.")
                else:
                    # No activities running, proceed with normal shutdown
                    logging.info(f"Waiting {sleep_time} seconds before shutdown...")
                    
                    # Poll for power restoration during grace period
                    elapsed_seconds = 0
                    check_interval = 10  # Check every 10 seconds
                    
                    while elapsed_seconds < sleep_time:
                        time.sleep(check_interval)
                        elapsed_seconds += check_interval
                        
                        # Check if power has been restored during grace period
                        try:
                            with X120X() as ups_grace_check:
                                ups_grace_status = ups_grace_check.get_status()
                                grace_ac_power = ups_grace_status.get('ac_power_connected', False)
                                
                                if grace_ac_power:
                                    # Power restored during grace period
                                    logging.info(f"Power restored during grace period after {elapsed_seconds} seconds. Continuing monitoring.")
                                    break
                        except Exception as e:
                            logging.error(f"Error checking power during grace period: {e}")
                    else:
                        # Grace period completed without power restoration
                        # Create fresh connection for recheck after sleep
                        with X120X() as ups_recheck:
                            ups_status_recheck = ups_recheck.get_status()
                            recheck_ac_power = ups_status_recheck.get('ac_power_connected', False)
                            
                            if not recheck_ac_power:
                                logging.critical("UPS still unplugged after grace period. Initiating shutdown.")
                                call("sudo nohup shutdown -h now", shell=True)
                            else:
                                logging.info("Power restored during grace period. Continuing monitoring.")
            else:
                logging.debug("UPS plugged in.")

            # Simple monitoring interval between cycles
            time.sleep(poll_time)
                
        except Exception as e:
            logging.error(f"Error during monitoring cycle: {e}")
            # Exit and let service restart us
            exit(1)

except KeyboardInterrupt:
    logging.info("Monitoring stopped by user (KeyboardInterrupt)")
except Exception as e:
    logging.critical(f"Fatal error: {e}")
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

