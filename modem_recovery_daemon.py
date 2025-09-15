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
        self.last_sim_status = None  # Track SIM status changes
        
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
    
    def check_sim_card_status(self):
        """Check if SIM card is present and valid"""
        if not self.modem_id:
            if not self.find_modem():
                return False, "No modem found"
        
        try:
            # First check basic modem status to see if SIM is detected
            result = subprocess.run(['mmcli', '-m', self.modem_id], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                logger.error(f"Failed to get modem status: {result.stderr}")
                return False, "ModemManager error"
            
            modem_output = result.stdout.lower()
            logger.debug(f"DEBUG: Raw modem output (first 500 chars): {result.stdout[:500]}")
            
            # Check for explicit SIM missing indicators first
            if ('sim missing' in modem_output or 
                'no sim' in modem_output or 
                'sim: none' in modem_output or
                'sim slot: empty' in modem_output):
                return False, "No SIM card detected"
            
            # Check for SIM lock status - be more specific about what indicates a lock
            if ('state: locked' in modem_output or 
                'sim-pin required' in modem_output or 
                'sim-puk required' in modem_output or
                'unlock required' in modem_output):
                return False, "SIM card is locked (PIN/PUK required)"
            
            # Quick test: try to query SIM directly to see if it responds
            try:
                quick_sim_test = subprocess.run(['mmcli', '-i', '0'], 
                                              capture_output=True, text=True, timeout=5)
                if quick_sim_test.returncode != 0:
                    # SIM query failed - likely no SIM present
                    logger.debug(f"DEBUG: Direct SIM query failed: {quick_sim_test.stderr}")
                    if 'not found' in quick_sim_test.stderr.lower():
                        return False, "No SIM card detected"
                    # If it's some other error, continue with path-based detection
            except (subprocess.TimeoutExpired, Exception) as e:
                logger.debug(f"DEBUG: SIM quick test failed: {e}")
            
            # If SIM is mentioned, try to get detailed SIM info
            if 'sim path:' in modem_output:
                logger.debug("DEBUG: Found SIM path in modem output")
                # Extract SIM path and check detailed status
                sim_match = re.search(r'sim path:\s*([^\s\n]+)', modem_output)
                if sim_match:
                    sim_path = sim_match.group(1)
                    logger.debug(f"DEBUG: Extracted SIM path: {sim_path}")
                    
                    # Get detailed SIM status - handle errors properly
                    try:
                        sim_result = subprocess.run(['mmcli', '--sim', sim_path.split('/')[-1]], 
                                                  capture_output=True, text=True, timeout=10)
                        if sim_result.returncode == 0:
                            sim_info = sim_result.stdout.lower()
                            logger.debug(f"DEBUG: SIM detailed output: {sim_result.stdout}")
                            
                            # Check SIM state more carefully
                            if 'state: ready' in sim_info:
                                # Get operator info if available
                                operator_match = re.search(r'operator name:\s*[\'"]?([^\'"\r\n]+)', sim_info)
                                operator = operator_match.group(1).strip() if operator_match else "Unknown"
                                return True, f"SIM ready (Operator: {operator})"
                            elif 'state: locked' in sim_info:
                                return False, "SIM card is locked (PIN/PUK required)"
                            elif 'state:' in sim_info:
                                # Extract the actual state
                                state_match = re.search(r'state:\s*(\w+)', sim_info)
                                if state_match:
                                    state = state_match.group(1)
                                    return False, f"SIM card state: {state}"
                                else:
                                    return False, "SIM card not ready"
                            else:
                                return False, "SIM card status unknown"
                        else:
                            # mmcli --sim command failed - this means SIM path exists but SIM is not accessible
                            logger.debug(f"DEBUG: mmcli --sim failed with error: {sim_result.stderr}")
                            # This could be because SIM is not present despite path existing
                            return False, "No SIM card detected"
                    except subprocess.TimeoutExpired:
                        return False, "SIM status check timeout"
                    except Exception as e:
                        logger.debug(f"DEBUG: SIM status check exception: {e}")
                        return False, "SIM status check error"
                else:
                    return False, "SIM card status unclear"
            else:
                # No SIM path found in modem output - definitely no SIM
                return False, "No SIM card detected"
                
        except subprocess.TimeoutExpired:
            logger.error("SIM status check timed out")
            return False, "Timeout"
        except Exception as e:
            logger.error(f"Error checking SIM status: {e}")
            return False, str(e)

    def check_connection_status(self):
        """Check current cellular connection status"""
        if not self.modem_id:
            if not self.find_modem():
                return False, "No modem found"
        
        # First check SIM card status
        sim_ok, sim_status = self.check_sim_card_status()
        
        # Track SIM status changes
        if self.last_sim_status != sim_status:
            if self.last_sim_status is not None:  # Not the first check
                logger.info(f"SIM status changed: '{self.last_sim_status}' -> '{sim_status}'")
            self.last_sim_status = sim_status
        
        if not sim_ok:
            # Only treat as SIM error if it's a real SIM hardware issue
            # Don't skip recovery for timeouts, parsing errors, etc.
            sim_hardware_issues = [
                "No SIM card detected", 
                "SIM card is locked", 
                "No SIM card", 
                "sim missing"
            ]
            
            is_hardware_issue = any(issue.lower() in sim_status.lower() for issue in sim_hardware_issues)
            
            if is_hardware_issue:
                logger.warning(f"SIM card hardware issue: {sim_status}")
                return False, f"SIM error: {sim_status}"
            else:
                # Temporary SIM check failure - continue with connection check
                logger.debug(f"SIM status check issue (continuing): {sim_status}")
                # Don't return here - continue to check connection normally
        
        try:
            # Get modem status from ModemManager
            result = subprocess.run(['mmcli', '-m', self.modem_id], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                logger.error(f"Failed to get modem status: {result.stderr}")
                return False, "ModemManager error"
            
            modem_output = result.stdout.lower()
            
            # Check ModemManager state only (don't interfere with NetworkManager/Ethernet)
            mm_connected = 'state: connected' in modem_output
            
            # For cellular connectivity, ModemManager state is sufficient
            # We don't want to interfere with NetworkManager since Ethernet might be primary
            is_connected = mm_connected
            
            # Additional validation: check if we have an active bearer
            bearer_active = False
            try:
                bearer_result = subprocess.run(['mmcli', '-m', self.modem_id, '--list-bearers'], 
                                             capture_output=True, text=True, timeout=5)
                if bearer_result.returncode == 0 and 'connected' in bearer_result.stdout.lower():
                    bearer_active = True
            except Exception as e:
                logger.debug(f"Bearer check failed: {e}")
            
            # Connection is good if modem is connected and has active bearers
            is_connected = mm_connected and bearer_active
            
            # Get additional status info
            status_info = {
                'mm_connected': mm_connected,
                'bearer_active': bearer_active,
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
        """Attempt soft reset via ModemManager (disconnect/reconnect)"""
        logger.info("Attempting soft reset via ModemManager...")
        
        if not self.modem_id:
            return False
            
        try:
            # Use ModemManager to disconnect and reconnect
            # This only affects the modem, not NetworkManager connections
            logger.info("Disconnecting modem via ModemManager...")
            result = subprocess.run(['mmcli', '-m', self.modem_id, '--simple-disconnect'], 
                                  capture_output=True, text=True, timeout=15)
            
            if result.returncode != 0:
                logger.warning(f"Disconnect command failed (may already be disconnected): {result.stderr}")
                # Continue anyway - modem might already be disconnected
            
            # Wait between disconnect and reconnect, but check for shutdown
            for _ in range(5):
                if shutdown_flag.is_set():
                    logger.info("Shutdown requested during connection reset, aborting")
                    return False
                time.sleep(1)
            
            logger.info("Reconnecting modem via ModemManager...")
            result = subprocess.run(['mmcli', '-m', self.modem_id, '--simple-connect'], 
                                  capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                logger.info("Soft reset completed successfully")
                return True
            else:
                logger.error(f"Failed to reconnect modem: {result.stderr}")
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
            
            # Wait for disable to complete, but check for shutdown
            for _ in range(5):
                if shutdown_flag.is_set():
                    logger.info("Shutdown requested during modem reset, aborting")
                    return False
                time.sleep(1)
            
            # Enable modem
            logger.info("Enabling modem...")
            result = subprocess.run(['mmcli', '-m', self.modem_id, '--enable'], 
                                  capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                logger.info("Full modem reset completed")
                
                # Wait for modem to stabilize, but check for shutdown
                for _ in range(10):
                    if shutdown_flag.is_set():
                        logger.info("Shutdown requested during modem stabilization, aborting")
                        return False
                    time.sleep(1)
                
                # Attempt to reconnect (only if not shutting down)
                if not shutdown_flag.is_set():
                    logger.info("Attempting to reconnect after modem reset...")
                    reconnect_result = subprocess.run(['mmcli', '-m', self.modem_id, '--simple-connect'], 
                                                    capture_output=True, text=True, timeout=30)
                    if reconnect_result.returncode == 0:
                        logger.info("Modem reconnection successful")
                        return True
                    else:
                        logger.warning(f"Modem reconnection failed: {reconnect_result.stderr}")
                        return False
                else:
                    return False
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
                                        
                                        # Wait but check for shutdown
                                        for _ in range(2):
                                            if shutdown_flag.is_set():
                                                logger.info("Shutdown requested during USB reset, aborting")
                                                return False
                                            time.sleep(1)
                                        
                                        # Bind
                                        with open(bind_path, 'w') as f:
                                            f.write(device_name)
                                        
                                        logger.info("USB reset completed")
                                        
                                        # Wait for device to reinitialize, but check for shutdown
                                        for _ in range(10):
                                            if shutdown_flag.is_set():
                                                logger.info("Shutdown requested during device initialization, returning")
                                                return True  # Return success since reset completed
                                            time.sleep(1)
                                        
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
    
    # Initial SIM card check
    sim_ok, sim_status = modem_recovery.check_sim_card_status()
    if sim_ok:
        logger.info(f"Initial SIM check: {sim_status}")
    else:
        logger.warning(f"Initial SIM check failed: {sim_status}")
        logger.info(f"DEBUG: SIM status check returned - OK: {sim_ok}, Status: '{sim_status}'")
    
    while not shutdown_flag.is_set():
        try:
            current_time = datetime.now()
            
            # Check if we're in cooldown period after max recovery attempts
            if (last_recovery_time and recovery_attempts >= MAX_RECOVERY_ATTEMPTS and 
                current_time - last_recovery_time < timedelta(seconds=RECOVERY_COOLDOWN)):
                remaining = RECOVERY_COOLDOWN - (current_time - last_recovery_time).seconds
                logger.info(f"In recovery cooldown, waiting {remaining} more seconds")
                # Make cooldown sleep interruptible
                for _ in range(min(CHECK_INTERVAL, remaining)):
                    if shutdown_flag.is_set():
                        break
                    time.sleep(1)
                continue
            
            # Reset recovery attempts after cooldown
            if (last_recovery_time and recovery_attempts >= MAX_RECOVERY_ATTEMPTS and 
                current_time - last_recovery_time >= timedelta(seconds=RECOVERY_COOLDOWN)):
                logger.info("Recovery cooldown expired, resetting attempt counter")
                recovery_attempts = 0
            
            # Skip check if recovery is in progress
            if recovery_in_progress:
                # Make recovery wait interruptible
                for _ in range(CHECK_INTERVAL):
                    if shutdown_flag.is_set():
                        break
                    time.sleep(1)
                continue
            
            # Check connection status
            is_connected, status_info = modem_recovery.check_connection_status()
            
            # Debug logging
            logger.debug(f"Connection check result: connected={is_connected}, info={status_info}")
            
            if is_connected:
                logger.debug(f"Cellular OK - Signal: {status_info.get('signal_strength', 'N/A')}%, "
                           f"Operator: {status_info.get('operator', 'N/A')}, "
                           f"Tech: {status_info.get('network_type', 'N/A')}, "
                           f"Bearer: {status_info.get('bearer_active', False)}")
                consecutive_failures = 0
                recovery_attempts = 0  # Reset on successful connection
            else:
                # Debug logging
                logger.info(f"DEBUG: Connection failed - status_info type: {type(status_info)}, value: '{status_info}'")
                
                # Check if this is a real SIM hardware issue that recovery can't fix
                is_sim_hardware_error = (isinstance(status_info, str) and 
                                       "SIM error" in status_info and
                                       any(issue in status_info.lower() for issue in [
                                           "no sim card detected",
                                           "sim card is locked", 
                                           "no sim card",
                                           "sim missing"
                                       ]))
                
                logger.info(f"DEBUG: SIM hardware error check - is_sim_error: {is_sim_hardware_error}")
                if isinstance(status_info, str) and "SIM error" in status_info:
                    logger.info(f"DEBUG: Found SIM error, checking specific issues in: '{status_info.lower()}'")
                    for issue in ["no sim card detected", "sim card is locked", "no sim card", "sim missing"]:
                        if issue in status_info.lower():
                            logger.info(f"DEBUG: Matched hardware issue: '{issue}'")
                
                if is_sim_hardware_error:
                    # Don't count SIM hardware errors as connection failures for recovery
                    logger.warning(f"SIM hardware issue detected: {status_info}")
                    
                    # Provide specific guidance based on the error type
                    if "locked" in status_info.lower():
                        logger.error("SIM CARD IS LOCKED - Action required:")
                        logger.error("1. Check if SIM PIN is enabled on this SIM card")
                        logger.error("2. Unlock SIM using: mmcli -i 0 --pin=XXXX (replace XXXX with PIN)")
                        logger.error("3. Or disable SIM PIN using phone/modem management")
                        logger.error("4. Verify SIM works in phone first before using in modem")
                    elif "missing" in status_info.lower() or "no sim" in status_info.lower():
                        logger.error("SIM CARD NOT DETECTED - Action required:")
                        logger.error("1. Check SIM card is properly inserted")
                        logger.error("2. Verify SIM card is not damaged")
                        logger.error("3. Try reseating the SIM card")
                    
                    logger.info("Skipping recovery attempts until SIM issue is resolved")
                    consecutive_failures = 0  # Reset failures to prevent recovery attempts
                else:
                    # This is a connection issue that recovery might fix
                    consecutive_failures += 1
                    logger.warning(f"Connection failed ({consecutive_failures}/{FAILURE_THRESHOLD}): {status_info}")
                
                # Only trigger recovery if it's not a SIM hardware issue and threshold reached
                if (consecutive_failures >= FAILURE_THRESHOLD and recovery_attempts < MAX_RECOVERY_ATTEMPTS and 
                    not is_sim_hardware_error):
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
                                # Wait to verify recovery, but check for shutdown signal
                                for _ in range(RECOVERY_TIMEOUT):
                                    if shutdown_flag.is_set():
                                        logger.info("Shutdown requested, aborting recovery verification")
                                        return
                                    time.sleep(1)
                                
                                # Check if connection is restored (only if not shutting down)
                                if not shutdown_flag.is_set():
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
            
            # Wait for next check, but make it interruptible for faster shutdown
            for _ in range(CHECK_INTERVAL):
                if shutdown_flag.is_set():
                    break
                time.sleep(1)
            
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
            # Even error recovery sleep should be interruptible
            for _ in range(CHECK_INTERVAL):
                if shutdown_flag.is_set():
                    break
                time.sleep(1)

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    global recovery_in_progress
    
    logger.info(f"Received signal {signum}, shutting down...")
    shutdown_flag.set()
    
    # If recovery is in progress, wait a short time then force shutdown
    if recovery_in_progress:
        logger.info("Recovery in progress, allowing 5 seconds for cleanup...")
        import time
        time.sleep(5)
        if recovery_in_progress:
            logger.warning("Forcing shutdown despite active recovery")
            recovery_in_progress = False

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
        # Ensure shutdown flag is set
        shutdown_flag.set()
        
        # Give any active recovery threads a moment to finish
        if recovery_in_progress:
            logger.info("Waiting for recovery thread to finish...")
            import time
            for i in range(10):  # Wait up to 10 seconds
                if not recovery_in_progress:
                    break
                time.sleep(1)
            
            if recovery_in_progress:
                logger.warning("Recovery thread did not finish cleanly")
        
        logger.info("Modem Recovery Daemon stopped")

if __name__ == '__main__':
    main()