#!/usr/bin/env python3
"""
Modem Recovery Daemon for SIM7600G-H

This daemon monitors cellular connectivity and automatically recovers
from connection failures by resetting the modem when necessary.

Key Features:
- Monitors cellular connection status via ModemManager
- Detects when connection is lost and fails to recover
- Automatically resets modem using ModemManager commands
- Configurable retry intervals and thresholds
- Logs all recovery actions for troubleshooting
- Integrates with existing heartbeat system

The daemon uses a graduated recovery approach:
1. Soft reset via NetworkManager (reconnect)
2. ModemManager bearer reset
3. Full modem reset via ModemManager
4. Hardware reset (if supported)

Usage:
    python3 modem_recovery_daemon.py [--daemon]
"""

import os
import sys
import time
import signal
import logging
import subprocess
import re
import threading
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# Module-level logger
logger = logging.getLogger('modem_recovery')
logger.setLevel(logging.INFO)

# Configuration
CHECK_INTERVAL = 30  # Check connection every 30 seconds
FAILURE_THRESHOLD = 3  # Number of consecutive failures before recovery action
RECOVERY_TIMEOUT = 120  # Wait time after recovery before considering it failed
MAX_RECOVERY_ATTEMPTS = 3  # Maximum recovery attempts before giving up temporarily
RECOVERY_COOLDOWN = 300  # Wait 5 minutes after max attempts before trying again

# Global state
consecutive_failures = 0
recovery_in_progress = False
last_recovery_time = None
recovery_attempts = 0
shutdown_flag = threading.Event()

class ModemRecovery:
    """Handles modem recovery operations"""
    
    def __init__(self):
        self.modem_id = None
        self.last_check_time = datetime.now()
        self.connection_history = []  # Track connection status history
        
    def find_modem(self):
        """Find the cellular modem using ModemManager"""
        try:
            result = subprocess.run(['mmcli', '-L'], capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                # Parse modem list output to find modem index
                modem_match = re.search(r'/org/freedesktop/ModemManager1/Modem/(\d+)', result.stdout)
                if modem_match:
                    self.modem_id = modem_match.group(1)
                    logger.info(f"Found modem at index {self.modem_id}")
                    return True
                else:
                    logger.warning("No modems found in ModemManager")
                    return False
            else:
                logger.error(f"Failed to list modems: {result.stderr}")
                return False
        except FileNotFoundError:
            logger.error("ModemManager (mmcli) not available")
            return False
        except subprocess.TimeoutExpired:
            logger.error("ModemManager command timed out")
            return False
        except Exception as e:
            logger.error(f"Error finding modem: {e}")
            return False
    
    def check_connection_status(self):
        """Check current cellular connection status"""
        if not self.modem_id:
            if not self.find_modem():
                return False, "No modem found"
        
        try:
            # Get modem status from ModemManager
            result = subprocess.run(['mmcli', '-m', self.modem_id], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                logger.error(f"Failed to get modem status: {result.stderr}")
                return False, "ModemManager error"
            
            modem_output = result.stdout.lower()
            
            # Check ModemManager state
            mm_connected = 'state: connected' in modem_output
            
            # Also check NetworkManager for additional validation
            nm_connected = False
            try:
                nm_result = subprocess.run(['nmcli', 'device', 'status'], 
                                         capture_output=True, text=True, timeout=5)
                if nm_result.returncode == 0:
                    # Look for connected GSM/cellular devices
                    for line in nm_result.stdout.split('\n'):
                        if 'gsm' in line.lower() and 'connected' in line.lower():
                            nm_connected = True
                            break
            except Exception as e:
                logger.warning(f"NetworkManager check failed: {e}")
            
            # Connection is good if both ModemManager and NetworkManager agree it's connected
            is_connected = mm_connected and nm_connected
            
            # Get additional status info
            status_info = {
                'mm_connected': mm_connected,
                'nm_connected': nm_connected,
                'signal_strength': None,
                'operator': None,
                'network_type': None
            }
            
            # Parse signal strength
            signal_match = re.search(r'signal quality:\s*(\d+)%', result.stdout)
            if signal_match:
                status_info['signal_strength'] = int(signal_match.group(1))
            
            # Parse operator
            operator_match = re.search(r'operator name:\s*[\'"]?([^\'"\r\n]+)', result.stdout)
            if operator_match:
                status_info['operator'] = operator_match.group(1).strip()
            
            # Parse network type
            if 'access tech: lte' in modem_output:
                status_info['network_type'] = "LTE"
            elif 'access tech: umts' in modem_output:
                status_info['network_type'] = "3G"
            elif 'access tech: gsm' in modem_output:
                status_info['network_type'] = "2G"
            
            return is_connected, status_info
            
        except subprocess.TimeoutExpired:
            logger.error("Modem status check timed out")
            return False, "Timeout"
        except Exception as e:
            logger.error(f"Error checking connection status: {e}")
            return False, str(e)
    
    def soft_reset_connection(self):
        """Attempt soft reset via NetworkManager"""
        logger.info("Attempting soft reset via NetworkManager...")
        try:
            # Find cellular connection
            result = subprocess.run(['nmcli', 'connection', 'show'], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                logger.error("Failed to list NetworkManager connections")
                return False
            
            # Look for GSM/cellular connection
            cellular_connection = None
            for line in result.stdout.split('\n'):
                if 'gsm' in line.lower() or 'cellular' in line.lower():
                    # Extract connection name (first field)
                    parts = line.split()
                    if parts:
                        cellular_connection = parts[0]
                        break
            
            if not cellular_connection:
                logger.error("No cellular connection found in NetworkManager")
                return False
            
            # Disconnect and reconnect
            logger.info(f"Disconnecting cellular connection: {cellular_connection}")
            subprocess.run(['nmcli', 'connection', 'down', cellular_connection], 
                         capture_output=True, timeout=15)
            
            time.sleep(5)  # Wait between disconnect and reconnect
            
            logger.info(f"Reconnecting cellular connection: {cellular_connection}")
            result = subprocess.run(['nmcli', 'connection', 'up', cellular_connection], 
                                  capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                logger.info("Soft reset completed successfully")
                return True
            else:
                logger.error(f"Failed to reconnect: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error("Soft reset timed out")
            return False
        except Exception as e:
            logger.error(f"Error during soft reset: {e}")
            return False
    
    def reset_modem_bearers(self):
        """Reset modem bearers via ModemManager"""
        if not self.modem_id:
            return False
        
        logger.info("Resetting modem bearers...")
        try:
            # List and delete all bearers
            result = subprocess.run(['mmcli', '-m', self.modem_id, '--list-bearers'], 
                                  capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                # Find bearer IDs
                bearer_matches = re.findall(r'/org/freedesktop/ModemManager1/Bearer/(\d+)', result.stdout)
                for bearer_id in bearer_matches:
                    logger.info(f"Deleting bearer {bearer_id}")
                    subprocess.run(['mmcli', '-m', self.modem_id, '--delete-bearer', bearer_id], 
                                 capture_output=True, timeout=10)
            
            # Create new bearer
            logger.info("Creating new bearer...")
            result = subprocess.run(['mmcli', '-m', self.modem_id, '--simple-connect'], 
                                  capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                logger.info("Bearer reset completed successfully")
                return True
            else:
                logger.error(f"Failed to create new bearer: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error("Bearer reset timed out")
            return False
        except Exception as e:
            logger.error(f"Error during bearer reset: {e}")
            return False
    
    def full_modem_reset(self):
        """Perform full modem reset via ModemManager"""
        if not self.modem_id:
            return False
        
        logger.info("Performing full modem reset...")
        try:
            # Disable modem
            logger.info("Disabling modem...")
            subprocess.run(['mmcli', '-m', self.modem_id, '--disable'], 
                         capture_output=True, timeout=20)
            
            time.sleep(5)  # Wait for disable to complete
            
            # Enable modem
            logger.info("Enabling modem...")
            result = subprocess.run(['mmcli', '-m', self.modem_id, '--enable'], 
                                  capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                logger.info("Full modem reset completed")
                time.sleep(10)  # Wait for modem to stabilize
                
                # Attempt to reconnect
                return self.soft_reset_connection()
            else:
                logger.error(f"Failed to enable modem: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error("Full modem reset timed out")
            return False
        except Exception as e:
            logger.error(f"Error during full modem reset: {e}")
            return False
    
    def hardware_reset(self):
        """Attempt hardware reset if possible"""
        logger.info("Attempting hardware reset...")
        
        # For SIM7600G-H, try to reset via USB bus reset
        try:
            # Find USB device
            result = subprocess.run(['lsusb'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                # Look for Quectel devices (SIM7600G-H manufacturer)
                quectel_match = re.search(r'Bus (\d+) Device (\d+): ID [0-9a-fA-F:]+.*Quectel', result.stdout, re.IGNORECASE)
                if quectel_match:
                    bus = quectel_match.group(1)
                    device = quectel_match.group(2)
                    
                    logger.info(f"Found Quectel device at Bus {bus} Device {device}")
                    
                    # Try to reset USB device (requires root)
                    usb_path = f"/dev/bus/usb/{bus.zfill(3)}/{device.zfill(3)}"
                    if os.path.exists(usb_path):
                        logger.info("Attempting USB reset...")
                        # This is a simple approach - unbind and rebind the device
                        try:
                            # Find the USB device in sysfs
                            result = subprocess.run(['find', '/sys/bus/usb/devices/', '-name', f"{bus}-*"], 
                                                  capture_output=True, text=True, timeout=5)
                            for device_path in result.stdout.strip().split('\n'):
                                if device_path and os.path.exists(device_path):
                                    # Try to unbind and rebind
                                    driver_path = os.path.join(device_path, 'driver')
                                    if os.path.exists(driver_path):
                                        device_name = os.path.basename(device_path)
                                        unbind_path = os.path.join(driver_path, 'unbind')
                                        bind_path = os.path.join(driver_path, 'bind')
                                        
                                        # Unbind
                                        with open(unbind_path, 'w') as f:
                                            f.write(device_name)
                                        time.sleep(2)
                                        
                                        # Bind
                                        with open(bind_path, 'w') as f:
                                            f.write(device_name)
                                        
                                        logger.info("USB reset completed")
                                        time.sleep(10)  # Wait for device to reinitialize
                                        return True
                        except Exception as e:
                            logger.warning(f"USB reset failed: {e}")
            
            logger.warning("Could not perform hardware reset")
            return False
            
        except Exception as e:
            logger.error(f"Error during hardware reset: {e}")
            return False
    
    def perform_recovery(self, attempt_number):
        """Perform recovery based on attempt number"""
        recovery_methods = [
            ("Soft Reset", self.soft_reset_connection),
            ("Bearer Reset", self.reset_modem_bearers),
            ("Full Modem Reset", self.full_modem_reset),
            ("Hardware Reset", self.hardware_reset)
        ]
        
        if attempt_number <= len(recovery_methods):
            method_name, method_func = recovery_methods[attempt_number - 1]
            logger.info(f"Recovery attempt {attempt_number}: {method_name}")
            return method_func()
        else:
            logger.error("All recovery methods exhausted")
            return False

def monitor_connection():
    """Main connection monitoring loop"""
    global consecutive_failures, recovery_in_progress, last_recovery_time, recovery_attempts
    
    modem_recovery = ModemRecovery()
    
    logger.info("Starting modem recovery monitoring...")
    
    while not shutdown_flag.is_set():
        try:
            current_time = datetime.now()
            
            # Check if we're in cooldown period after max recovery attempts
            if (last_recovery_time and recovery_attempts >= MAX_RECOVERY_ATTEMPTS and 
                current_time - last_recovery_time < timedelta(seconds=RECOVERY_COOLDOWN)):
                logger.info(f"In recovery cooldown, waiting {RECOVERY_COOLDOWN - (current_time - last_recovery_time).seconds} more seconds")
                time.sleep(CHECK_INTERVAL)
                continue
            
            # Reset recovery attempts after cooldown
            if (last_recovery_time and recovery_attempts >= MAX_RECOVERY_ATTEMPTS and 
                current_time - last_recovery_time >= timedelta(seconds=RECOVERY_COOLDOWN)):
                logger.info("Recovery cooldown expired, resetting attempt counter")
                recovery_attempts = 0
            
            # Skip check if recovery is in progress
            if recovery_in_progress:
                time.sleep(CHECK_INTERVAL)
                continue
            
            # Check connection status
            is_connected, status_info = modem_recovery.check_connection_status()
            
            if is_connected:
                logger.debug(f"Connection OK - Signal: {status_info.get('signal_strength', 'N/A')}%, "
                           f"Operator: {status_info.get('operator', 'N/A')}, "
                           f"Tech: {status_info.get('network_type', 'N/A')}")
                consecutive_failures = 0
                recovery_attempts = 0  # Reset on successful connection
            else:
                consecutive_failures += 1
                logger.warning(f"Connection failed ({consecutive_failures}/{FAILURE_THRESHOLD}): {status_info}")
                
                # Trigger recovery if threshold reached
                if consecutive_failures >= FAILURE_THRESHOLD and recovery_attempts < MAX_RECOVERY_ATTEMPTS:
                    recovery_in_progress = True
                    recovery_attempts += 1
                    last_recovery_time = current_time
                    
                    logger.error(f"Connection failure threshold reached, starting recovery attempt {recovery_attempts}")
                    
                    # Perform recovery in separate thread to avoid blocking
                    def recovery_thread():
                        global recovery_in_progress
                        try:
                            success = modem_recovery.perform_recovery(recovery_attempts)
                            if success:
                                logger.info("Recovery completed successfully")
                                time.sleep(RECOVERY_TIMEOUT)  # Wait to verify recovery
                                # Check if connection is restored
                                is_recovered, _ = modem_recovery.check_connection_status()
                                if is_recovered:
                                    logger.info("Connection restored after recovery")
                                    global consecutive_failures
                                    consecutive_failures = 0
                                else:
                                    logger.warning("Recovery completed but connection not restored")
                            else:
                                logger.error("Recovery attempt failed")
                        except Exception as e:
                            logger.error(f"Error during recovery: {e}")
                        finally:
                            recovery_in_progress = False
                    
                    thread = threading.Thread(target=recovery_thread, daemon=True)
                    thread.start()
                elif recovery_attempts >= MAX_RECOVERY_ATTEMPTS:
                    logger.error(f"Maximum recovery attempts ({MAX_RECOVERY_ATTEMPTS}) reached, entering cooldown")
            
            # Wait for next check
            time.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
            time.sleep(CHECK_INTERVAL)

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received signal {signum}, shutting down...")
    shutdown_flag.set()

def main():
    """Main entry point"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Modem Recovery Daemon for SIM7600G-H')
    parser.add_argument('--daemon', action='store_true', default=False,
                        help='Run in daemon mode (no console output)')
    args = parser.parse_args()
    
    # Import RotatingFileHandler for log rotation
    from logging.handlers import RotatingFileHandler

    # Configure logging with rotation to keep logs under 1MB (same as heartbeat daemon)
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    log_file = '/var/log/modem_recovery.log' if args.daemon else 'modem_recovery.log'
    
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

    # If running interactively (not daemon) and stdout is a TTY, ensure logs print to console
    try:
        if not args.daemon and sys.stdout.isatty():
            root_logger = logging.getLogger()
            has_stream = any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers)
            if not has_stream:
                sh = logging.StreamHandler(sys.stdout)
                sh.setFormatter(logging.Formatter(log_format))
                root_logger.addHandler(sh)
    except Exception:
        pass
    
    logger.info("Modem Recovery Daemon starting...")
    logger.info(f"Configuration: Check interval={CHECK_INTERVAL}s, "
               f"Failure threshold={FAILURE_THRESHOLD}, "
               f"Recovery timeout={RECOVERY_TIMEOUT}s")
    
    # Set up signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        monitor_connection()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        logger.info("Modem Recovery Daemon stopped")

if __name__ == '__main__':
    main()