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
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('modem_recovery')

# Configuration
CHECK_INTERVAL = 30
FAILURE_THRESHOLD = 3
MAX_RECOVERY_ATTEMPTS = 3
RECOVERY_COOLDOWN = 300

# Global state
consecutive_failures = 0
recovery_in_progress = False
last_recovery_time = None
recovery_attempts = 0
shutdown_flag = threading.Event()

def check_sim_card_status():
    """
    Check SIM card status using mmcli JSON output
    
    Returns:
        dict: Status information with keys:
            - present: bool indicating if SIM is detected  
            - error: str with error details if any
            - status: str with detailed status
    """
    try:
        # Get modem status using JSON output for reliable parsing
        result = subprocess.run(
            ['mmcli', '-m', '0', '--output-json'], 
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode != 0:
            logger.error(f"mmcli command failed: {result.stderr}")
            return {
                'present': False,
                'error': f'mmcli failed: {result.stderr}',
                'status': 'command_failed'
            }
        
        # Parse JSON output
        import json
        try:
            modem_data = json.loads(result.stdout)
            modem_generic = modem_data.get('modem', {}).get('generic', {})
            modem_3gpp = modem_data.get('modem', {}).get('3gpp', {})
            
            # Get modem state and SIM info
            modem_state = modem_generic.get('state', 'unknown')
            sim_path = modem_generic.get('sim', '')
            operator_name = modem_3gpp.get('operator-name', '')
            imei = modem_3gpp.get('imei', '')
            
            logger.debug(f"SIM check - State: {modem_state}, SIM path: {sim_path}, "
                        f"Operator: {operator_name}, IMEI: {imei}")
            
            # Determine SIM status based on JSON data
            if modem_state == 'failed':
                # Check if failure is SIM-related
                state_failed_reason = modem_generic.get('state-failed-reason', '')
                if 'sim' in state_failed_reason.lower() or not sim_path:
                    return {
                        'present': False,
                        'error': f'SIM card not detected (reason: {state_failed_reason})',
                        'status': 'missing'
                    }
                else:
                    return {
                        'present': True,
                        'error': f'Modem failed but SIM present (reason: {state_failed_reason})',
                        'status': 'modem_failed'
                    }
            
            elif not sim_path or sim_path == '/':
                # No SIM path indicates missing SIM
                return {
                    'present': False,
                    'error': 'No SIM card path detected',
                    'status': 'missing'
                }
            
            elif operator_name and imei:
                # Has operator and IMEI - SIM is definitely present and working
                return {
                    'present': True,
                    'error': None,
                    'status': f'present (operator: {operator_name})'
                }
            
            elif sim_path and sim_path != '/':
                # Has SIM path but maybe not fully initialized
                return {
                    'present': True,
                    'error': None,
                    'status': 'present (initializing)'
                }
            
            else:
                # Default: assume present if we got this far
                return {
                    'present': True,
                    'error': None,
                    'status': 'present'
                }
            
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"Failed to parse SIM status JSON: {e}")
            return {
                'present': False,
                'error': f'JSON parse error: {e}',
                'status': 'json_error'
            }
            
    except subprocess.TimeoutExpired:
        logger.error("Timeout checking SIM card status")
        return {
            'present': False,
            'error': 'Command timeout',
            'status': 'timeout'
        }
    except Exception as e:
        logger.error(f"Error checking SIM card status: {e}")
        return {
            'present': False,
            'error': str(e),
            'status': 'error'
        }

def check_network_connectivity():
    """
    Test actual internet connectivity by pinging Google
    
    Returns:
        dict: Network connectivity status with keys:
            - connected: bool
            - error: str if any
            - ping_time: float (ms) if successful
    """
    try:
        # Ping Google DNS with 1 packet, 5 second timeout
        result = subprocess.run(
            ['ping', '-c', '1', '-W', '5', 'google.com'],
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode == 0:
            # Try to extract ping time from output
            ping_time = None
            for line in result.stdout.split('\n'):
                if 'time=' in line:
                    try:
                        time_part = line.split('time=')[1].split()[0]
                        ping_time = float(time_part)
                        break
                    except (IndexError, ValueError):
                        pass
            
            return {
                'connected': True,
                'error': None,
                'ping_time': ping_time
            }
        else:
            return {
                'connected': False,
                'error': f'Ping failed: {result.stderr.strip() or "Network unreachable"}',
                'ping_time': None
            }
            
    except subprocess.TimeoutExpired:
        return {
            'connected': False,
            'error': 'Ping timeout',
            'ping_time': None
        }
    except Exception as e:
        return {
            'connected': False,
            'error': f'Ping error: {str(e)}',
            'ping_time': None
        }

def check_connection_status():
    """
    Check if cellular connection is active and internet is reachable
    
    Returns:
        dict: Connection status with keys:
            - connected: bool
            - error: str if any
            - modem_state: str
            - ping_time: float (ms) if successful
    """
    try:
        # Get modem status in JSON format for reliable parsing
        result = subprocess.run(
            ['mmcli', '-m', '0', '--output-json'], 
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode != 0:
            return {
                'connected': False,
                'error': f'Cannot query modem: {result.stderr}',
                'modem_state': 'unknown',
                'ping_time': None
            }
        
        # Parse JSON output
        import json
        try:
            modem_data = json.loads(result.stdout)
            modem_generic = modem_data.get('modem', {}).get('generic', {})
            modem_3gpp = modem_data.get('modem', {}).get('3gpp', {})
            
            # Get the actual state and signal from JSON
            modem_state = modem_generic.get('state', 'unknown')
            signal_quality_info = modem_generic.get('signal-quality', {})
            signal_quality = int(signal_quality_info.get('value', 0)) if signal_quality_info.get('value', '0').isdigit() else 0
            signal_recent = signal_quality_info.get('recent', 'no') == 'yes'
            
            # Get additional useful info
            operator_name = modem_3gpp.get('operator-name', 'Unknown')
            access_tech = modem_generic.get('access-technologies', ['unknown'])
            access_tech_str = access_tech[0] if access_tech else 'unknown'
            
            logger.debug(f"Modem state: {modem_state}, Signal: {signal_quality}% ({'recent' if signal_recent else 'cached'}), "
                        f"Operator: {operator_name}, Tech: {access_tech_str}")
            
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"Failed to parse modem JSON: {e}")
            return {
                'connected': False,
                'error': f'JSON parse error: {e}',
                'modem_state': 'json_error',
                'ping_time': None
            }
        
        # Determine if modem state is healthy
        modem_healthy = modem_state in ['connected', 'registered']
        
        # If modem appears healthy, test actual internet connectivity
        if modem_healthy:
            network_status = check_network_connectivity()
            
            if network_status['connected']:
                return {
                    'connected': True,
                    'error': None,
                    'modem_state': modem_state,
                    'ping_time': network_status['ping_time']
                }
            else:
                # Modem says it's connected but no internet - this is a problem
                return {
                    'connected': False,
                    'error': f'Modem {modem_state} but no internet: {network_status["error"]}',
                    'modem_state': modem_state,
                    'ping_time': None
                }
        else:
            # Modem is not in a healthy state
            return {
                'connected': False,
                'error': f'Modem in {modem_state} state',
                'modem_state': modem_state,
                'ping_time': None
            }
            
    except subprocess.TimeoutExpired:
        return {
            'connected': False,
            'error': 'Timeout checking modem status',
            'modem_state': 'timeout',
            'ping_time': None
        }
    except Exception as e:
        return {
            'connected': False,
            'error': str(e),
            'modem_state': 'error',
            'ping_time': None
        }

def reset_modem():
    """Reset the modem using ModemManager (soft reset via disable/enable)"""
    global recovery_in_progress, last_recovery_time, recovery_attempts
    
    recovery_in_progress = True
    last_recovery_time = datetime.now()
    recovery_attempts += 1
    
    logger.info(f"Attempting modem soft reset (attempt {recovery_attempts})")
    
    try:
        # First try soft reset (disable/enable)
        logger.info("Disabling modem...")
        result = subprocess.run(
            ['mmcli', '-m', '0', '--disable'], 
            capture_output=True, text=True, timeout=20
        )
        
        if result.returncode != 0:
            logger.warning(f"Disable command failed: {result.stderr}")
        
        # Wait between disable and enable
        time.sleep(5)
        
        logger.info("Re-enabling modem...")
        result = subprocess.run(
            ['mmcli', '-m', '0', '--enable'], 
            capture_output=True, text=True, timeout=30
        )
        
        if result.returncode == 0:
            logger.info("Modem soft reset completed successfully")
            # Wait for modem to stabilize
            time.sleep(10)
            return True
        else:
            logger.error(f"Enable command failed: {result.stderr}")
            # If soft reset fails, try hardware reset as fallback
            logger.info("Soft reset failed, attempting hardware reset...")
            result = subprocess.run(
                ['mmcli', '-m', '0', '--reset'], 
                capture_output=True, text=True, timeout=30
            )
            
            if result.returncode == 0:
                logger.info("Hardware reset completed")
                time.sleep(15)  # Hardware reset needs more time
                return True
            else:
                logger.error(f"Hardware reset also failed: {result.stderr}")
                return False
            
    except subprocess.TimeoutExpired:
        logger.error("Timeout during modem reset")
        return False
    except Exception as e:
        logger.error(f"Error resetting modem: {e}")
        return False
    finally:
        recovery_in_progress = False

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, shutting down...")
    shutdown_flag.set()

def main_loop():
    """Main monitoring loop"""
    global consecutive_failures, recovery_attempts
    
    logger.info("Starting modem recovery daemon")
    
    while not shutdown_flag.is_set():
        try:
            # Check SIM card first
            sim_status = check_sim_card_status()
            
            if not sim_status['present']:
                logger.warning(f"SIM card issue detected: {sim_status['error']}")
                # Don't attempt recovery if SIM is missing - that's a hardware issue
                consecutive_failures = 0
                time.sleep(CHECK_INTERVAL)
                continue
            
            # Check connection status
            conn_status = check_connection_status()
            
            if conn_status['connected']:
                ping_info = f" (ping: {conn_status['ping_time']:.1f}ms)" if conn_status['ping_time'] else ""
                logger.info(f"Connection healthy - modem: {conn_status['modem_state']}{ping_info}")
                consecutive_failures = 0
                recovery_attempts = 0
            else:
                consecutive_failures += 1
                logger.warning(f"Connection failure {consecutive_failures}/{FAILURE_THRESHOLD}: {conn_status['error']} (modem: {conn_status['modem_state']})")
                
                # Attempt recovery if threshold reached
                if consecutive_failures >= FAILURE_THRESHOLD and not recovery_in_progress:
                    if recovery_attempts < MAX_RECOVERY_ATTEMPTS:
                        # Check if we're in cooldown period
                        if last_recovery_time and (datetime.now() - last_recovery_time).seconds < RECOVERY_COOLDOWN:
                            logger.info("In recovery cooldown period, waiting...")
                        else:
                            logger.info("Starting modem recovery")
                            if reset_modem():
                                logger.info("Modem reset completed")
                                consecutive_failures = 0
                            else:
                                logger.error("Modem reset failed")
                    else:
                        logger.warning(f"Max recovery attempts ({MAX_RECOVERY_ATTEMPTS}) reached, waiting for cooldown")
                        if last_recovery_time and (datetime.now() - last_recovery_time).seconds >= RECOVERY_COOLDOWN:
                            recovery_attempts = 0
                            logger.info("Cooldown period ended, resetting recovery attempts")
            
            # Wait for next check
            shutdown_flag.wait(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(CHECK_INTERVAL)
    
    logger.info("Modem recovery daemon stopped")

def main():
    return # disable for now
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