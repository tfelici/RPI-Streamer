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
from logging.handlers import RotatingFileHandler
from subprocess import call
from x120x import X120X

import fcntl
from utils import is_streaming, is_gps_tracking, load_settings
from app import stop_flight

# Configure logging with rotation to keep logs under 1MB
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler('/var/log/ups-monitor.log', maxBytes=1024*1024, backupCount=3)
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
                
                # Load current settings
                settings = load_settings()
                sleep_time = settings.get('power_monitor_sleep_time')
                
                # If sleep_time is 0 or None, disable power monitoring
                if not sleep_time:
                    print("Power monitoring disabled (sleep_time is 0 or unset). Skipping power loss handling.")
                    logging.info("Power monitoring disabled - continuing normal monitoring")
                    continue
                else:
                    print(f"Power monitoring active - will wait {sleep_time} seconds before shutdown if power not restored.")
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
                        
                        timeout_msg = f"GPS tracking active during power loss. Will stop GPS tracking after {timeout_minutes} minutes if power not restored."
                        print(timeout_msg)
                        logging.warning(timeout_msg)
                        
                        # Poll power status during timeout period instead of sleeping
                        print(f"Monitoring power status for {timeout_minutes} minutes before stopping GPS tracking...")
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
                                        recovery_msg = f"Power restored after {elapsed_seconds//60} minutes {elapsed_seconds%60} seconds. GPS tracking continues."
                                        print(recovery_msg)
                                        logging.info(recovery_msg)
                                        break
                                    else:
                                        # Show progress every minute
                                        if elapsed_seconds % 60 == 0:
                                            remaining_minutes = (timeout_seconds - elapsed_seconds) // 60
                                            progress_msg = f"Power still lost. GPS tracking will stop in {remaining_minutes} minutes if power not restored."
                                            print(progress_msg)
                                            logging.info(progress_msg)
                            except Exception as e:
                                print(f"Error checking power during timeout: {e}")
                                logging.error(f"Error checking power during timeout: {e}")
                                # Continue the loop even if we can't check power status
                        else:
                            # Timeout completed without power restoration
                            print("Timeout period completed. Power not restored.")
                            
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
                                                print("GPS tracking stopped due to prolonged power loss")
                                                logging.warning("GPS tracking stopped due to prolonged power loss")
                                            else:
                                                print(f"Failed to stop GPS tracking: {message}")
                                                logging.error(f"Failed to stop GPS tracking: {message}")
                                        except Exception as e:
                                            print(f"Error stopping GPS tracking: {e}")
                                            logging.error(f"Error stopping GPS tracking: {e}")
                                        
                                        # Continue normal monitoring after stopping GPS
                                        continue_msg = "GPS tracking stopped due to prolonged power loss. Continuing normal power monitoring."
                                        print(continue_msg)
                                        logging.info(continue_msg)
                                    else:
                                        recovery_msg = "Power restored just before GPS timeout. GPS tracking continues."
                                        print(recovery_msg)
                                        logging.info(recovery_msg)
                            except Exception as e:
                                print(f"Error during final timeout check: {e}")
                                logging.error(f"Error during final timeout check: {e}")
                    else:
                        activities = []
                        if streaming_active:
                            activities.append("streaming")
                        if gps_active:
                            activities.append("GPS tracking")
                        # Standard behavior - skip shutdown while activities are running
                        activity_msg = f"UPS unplugged but {' and '.join(activities)} {'is' if len(activities)==1 else 'are'} active. Skipping shutdown to prevent interruption."
                        print(activity_msg)
                        logging.warning(activity_msg)
                else:
                    # No activities running, proceed with normal shutdown
                    print(f"Waiting {sleep_time} seconds before shutdown...")
                    
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
                                    grace_recovery_msg = f"Power restored during grace period after {elapsed_seconds} seconds. Continuing monitoring."
                                    print(grace_recovery_msg)
                                    logging.info(grace_recovery_msg)
                                    break
                        except Exception as e:
                            print(f"Error checking power during grace period: {e}")
                            logging.error(f"Error checking power during grace period: {e}")
                    else:
                        # Grace period completed without power restoration
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

            # Load sleep time setting for monitoring interval
            settings = load_settings()
            sleep_time = settings.get('power_monitor_sleep_time')  # No default - should be explicitly set if monitoring is enabled
            
            # If power monitoring is disabled, use a simple sleep
            if not sleep_time:
                print("Power monitoring disabled - using simple 60-second monitoring interval")
                time.sleep(60)
                continue
            
            # Poll for power changes during sleep interval instead of simple sleep
            elapsed_seconds = 0
            check_interval = 10  # Check every 10 seconds
            
            while elapsed_seconds < sleep_time:
                time.sleep(check_interval)
                elapsed_seconds += check_interval
                
                # Check if power status has changed during sleep
                try:
                    with X120X() as ups_sleep_check:
                        ups_sleep_status = ups_sleep_check.get_status()
                        sleep_ac_power = ups_sleep_status.get('ac_power_connected', False)
                        
                        # If power status changed, break out of sleep to handle it immediately
                        if sleep_ac_power != ac_power_connected:
                            status_change_msg = f"Power status changed during monitoring interval. Breaking sleep to handle immediately."
                            print(status_change_msg)
                            logging.info(status_change_msg)
                            break
                except Exception as e:
                    print(f"Error checking power during sleep interval: {e}")
                    logging.error(f"Error checking power during sleep interval: {e}")
                    # Continue the loop even if we can't check power status
                
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

