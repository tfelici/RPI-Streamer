#!/usr/bin/env python3
"""
RPI Streamer Heartbeat Daemon

This daemon runs independently of the web server and provides centralized
system statistics collection. It:

1. Collects comprehensive system metrics every 5 seconds
2. Saves stats to local file (/tmp/rpi_streamer_stats.json) for web interface consumption
3. Sends heartbeat data to remote server for monitoring
4. Runs as a systemd service and starts automatically on boot

The web application's event_stream function simply reads from the stats file,
ensuring consistency and eliminating code duplication.
"""

import os
import sys
import time
import json
import signal
import psutil
import requests
import threading
import subprocess
import re
import glob
import socket
import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Tuple, Optional, Dict, Any

# Module-level named logger; configuration happens in main().
# We create the named logger now; handlers and format are configured in main().
logger = logging.getLogger('heartbeat_daemon')
logger.setLevel(logging.INFO)
logger.propagate = True

# Import functions from the main app to avoid duplication
try:
    from utils import load_settings, get_default_hotspot_ssid, get_hardwareid, get_app_version, HEARTBEAT_FILE, add_files_from_path, get_active_recording_info, get_video_duration_mediainfo, is_streaming, is_gps_tracking, find_usb_storage, STREAMER_DATA_DIR, get_wifi_mode_status
    logger.info("Successfully imported from utils.py")
except ImportError as e:
    logger.error(f"Error importing functions from utils.py: {e}")
    logger.error("Make sure this script is run from the same directory as utils.py")
    sys.exit(1)

try:
    from gps_client import get_gnss_location
except ImportError as e:
    logger.warning(f"Could not import from gps_client.py: {e}")
    logger.warning("GPS functionality will be limited")
    # Create a dummy function to prevent crashes
    def get_gnss_location(*args, **kwargs) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Fallback GPS client when gps_client is unavailable."""
        return False, {"error": "GPS client not available"}

# Configuration
HEARTBEAT_INTERVAL = 5  # Send heartbeat every 5 seconds
HEARTBEAT_URL = 'https://streamer.lambda-tek.com/heartbeat.php'
REQUEST_TIMEOUT = 2.0  # Short timeout for fire-and-forget requests

# Global flag for graceful shutdown
shutdown_flag = threading.Event()

def get_temperature():
    # Use vcgencmd like in get_system_diagnostics for consistency
    try:
        # Check if vcgencmd is available first
        subprocess.run(['vcgencmd', 'version'], capture_output=True, text=True, timeout=2)
        
        # Get temperature using vcgencmd
        result = subprocess.run(['vcgencmd', 'measure_temp'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            output = result.stdout.strip()
            # Extract temperature from "temp=48.8'C"
            temp_match = re.search(r'temp=([\d.]+)', output)
            if temp_match:
                return f"{temp_match.group(1)}°C"
            else:
                return output
        else:
            return "N/A"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # vcgencmd not available, fallback to "--" like in diagnostics
        return "--"
    except Exception:
        return "N/A"

def get_fan_rpm():
    try:
        sys_devices_path = Path('/sys/devices/platform/cooling_fan')
        fan_input_files = list(sys_devices_path.rglob('fan1_input'))
        if not fan_input_files:
            return "No fan?"
        with open(fan_input_files[0], 'r') as file:
            rpm = file.read().strip()
        return f"{rpm} RPM"
    except FileNotFoundError:
        return "Fan RPM file not found"
    except PermissionError:
        return "Permission denied accessing the fan RPM file"
    except Exception as e:
        return f"Unexpected error: {e}"

def get_disk_usage():
    """Get disk usage for the root filesystem"""
    try:
        disk_usage = psutil.disk_usage('/')
        total_gb = disk_usage.total / (1024**3)  # Convert to GB
        used_gb = disk_usage.used / (1024**3)
        free_gb = disk_usage.free / (1024**3)
        percent_used = (disk_usage.used / disk_usage.total) * 100
        
        return {
            'percent': round(percent_used, 1),
            'used_gb': round(used_gb, 1),
            'total_gb': round(total_gb, 1),
            'free_gb': round(free_gb, 1),
            'formatted': f"{percent_used:.1f}% ({used_gb:.1f}GB / {total_gb:.1f}GB)"
        }
    except Exception as e:
        logger.error(f"Error getting disk usage: {e}")
        return {
            'percent': 0,
            'used_gb': 0,
            'total_gb': 0,
            'free_gb': 0,
            'formatted': "N/A"
        }

def power_consumption_watts():
    """
    Calculate total power consumption in watts by parsing all PMIC voltage and current readings.
    Returns formatted power string like "2.45 W", or fallback values if not available.
    """
    # Candidate sysfs paths to check for direct power/current readings
    power_paths = []
    power_paths.extend(glob.glob('/sys/class/power_supply/*/current_now'))
    power_paths.extend(glob.glob('/sys/bus/i2c/devices/*/in*_input'))
    power_paths.extend(glob.glob('/sys/devices/platform/*/power*'))

    for path in power_paths:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    val = int(f.read().strip())
                    if val > 10000:
                        return f"{val/1_000_000:.2f} W"
                    else:
                        return f"{val} uW"
            except Exception:
                continue
    
    try:
        # Check if vcgencmd is available first
        subprocess.run(['vcgencmd', 'version'], capture_output=True, text=True, timeout=2)
        
        # Get all PMIC ADC readings
        output = subprocess.check_output(['vcgencmd', 'pmic_read_adc'], timeout=5).decode("utf-8")
        lines = output.split('\n')
        amperages = {}
        voltages = {}
        
        for line in lines:
            cleaned_line = line.strip()
            if cleaned_line and '=' in cleaned_line:
                try:
                    parts = cleaned_line.split(' ')
                    label, value = parts[0], parts[-1]
                    val = float(value.split('=')[1][:-1])  # Remove unit (V or A)
                    short_label = label[:-2]  # Remove _V or _A suffix
                    if label.endswith('A'):
                        amperages[short_label] = val
                    elif label.endswith('V'):
                        voltages[short_label] = val
                except (ValueError, IndexError):
                    continue
        
        # Calculate total wattage (V * A = W) for matching voltage/current pairs
        wattage = sum(amperages[key] * voltages[key] for key in amperages if key in voltages)
        if wattage > 0:
            return f"{wattage:.2f} W"
        
        # Fallback: Try individual VDD_CORE readings if comprehensive method failed
        voltage = None
        current = None
        
        # Get VDD_CORE voltage
        result = subprocess.run(['vcgencmd', 'pmic_read_adc', 'VDD_CORE_V'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and 'VDD_CORE_V' in result.stdout:
            volt_match = re.search(r'=([\d.]+)V', result.stdout)
            if volt_match:
                voltage = float(volt_match.group(1))
        
        # Get VDD_CORE current
        result = subprocess.run(['vcgencmd', 'pmic_read_adc', 'VDD_CORE_A'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and 'VDD_CORE_A' in result.stdout:
            current_match = re.search(r'=([\d.]+)A', result.stdout)
            if current_match:
                current = float(current_match.group(1))
        
        # Calculate power if both voltage and current are available
        if voltage is not None and current is not None:
            watts = voltage * current
            return f"{watts:.2f} W"
        elif voltage is not None:
            return f"{voltage:.3f} V (core)"
        
        return "0.00 W"
        
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # vcgencmd not available, fallback to "--" like in diagnostics
        return "--"
    except Exception as e:
        logger.error(f"Error calculating power consumption: {e}")
        return "N/A"

def get_connection_info():
    """
    Get current network connection information including IP addresses and connection types.
    Returns a dictionary with connection details.
    """
    connection_info = {
        "ethernet": "Disconnected",
        "wifi": "Disconnected", 
        "ip_addresses": [],
        "active_connections": []
    }
    
    try:
        # Get network interfaces and their addresses
        
        # Get all network interfaces with IP addresses
        interfaces = psutil.net_if_addrs()
        for interface_name, addresses in interfaces.items():
            for addr in addresses:
                if addr.family == socket.AF_INET and not addr.address.startswith('127.'):
                    # Skip APIPA addresses (169.254.x.x) unless no other connections exist
                    is_apipa = addr.address.startswith('169.254.')
                    
                    # Determine connection type based on interface name
                    interface_lower = interface_name.lower()
                    if any(keyword in interface_lower for keyword in ['ethernet', 'eth', 'en0', 'eno', 'enp']):
                        conn_type = "ethernet"
                        if not is_apipa:
                            connection_info["ethernet"] = "Connected"
                    elif any(keyword in interface_lower for keyword in ['wi-fi', 'wifi', 'wlan', 'wlp', 'wireless']):
                        conn_type = "wifi"
                        if not is_apipa:
                            connection_info["wifi"] = "Connected"
                    else:
                        conn_type = "other"
                    
                    connection_info["ip_addresses"].append({
                        "interface": interface_name,
                        "ip": addr.address,
                        "type": conn_type
                    })
        
        # Use ip command to detect active connections
        try:
            result = subprocess.run(['ip', 'route', 'show', 'default'], capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if 'dev' in line:
                        parts = line.split()
                        if 'dev' in parts:
                            dev_index = parts.index('dev')
                            if dev_index + 1 < len(parts):
                                interface = parts[dev_index + 1]
                                if interface not in connection_info["active_connections"]:
                                    connection_info["active_connections"].append(interface)
                                    
                                    # Determine connection type
                                    if any(keyword in interface.lower() for keyword in ["eth", "en", "eno", "enp"]):
                                        connection_info["ethernet"] = "Connected"
                                    elif any(keyword in interface.lower() for keyword in ["wlan", "wi", "wlp"]):
                                        connection_info["wifi"] = "Connected"
        except Exception:
            pass
        
        # Get WiFi status using the enhanced function from utils.py
        try:
            wifi_status = get_wifi_mode_status()
            connection_info["wifi_status"] = wifi_status
            
            # Set simplified display status for backward compatibility
            if wifi_status['wifi_connected']:
                if wifi_status['hotspot_active']:
                    # Show number of connected clients
                    client_count = wifi_status.get('client_count', 0)
                    if client_count == 0:
                        connection_info["wifi"] = "Hotspot (0 clients)"
                    elif client_count == 1:
                        connection_info["wifi"] = "Hotspot (1 client)"
                    else:
                        connection_info["wifi"] = f"Hotspot ({client_count} clients)"
                else:
                    # Client mode - show SSID and signal strength
                    wifi_status_text = f"Connected ({wifi_status['current_ssid']})"
                    if wifi_status.get('signal_percent') is not None:
                        wifi_status_text += f" - {wifi_status['signal_percent']}%"
                    connection_info["wifi"] = wifi_status_text
            else:
                connection_info["wifi"] = "Disconnected"
        except Exception:
            connection_info["wifi"] = "Error getting WiFi status"
    
        # Get 4G dongle status using ModemManager
        dongle_status = {
            "connected": False,
            "signal_strength": None,
            "operator": None,
            "network_type": None,
            "ip_address": None,
            "device_present": False
        }
        
        try:
            # Use mmcli to get modem information
            # List available modems
            result = subprocess.run(['mmcli', '-L'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                # Parse modem list output to find modem index
                modem_match = re.search(r'/org/freedesktop/ModemManager1/Modem/(\d+)', result.stdout)
                if modem_match:
                    modem_id = modem_match.group(1)
                    dongle_status["device_present"] = True
                    
                    # Get detailed modem status
                    modem_result = subprocess.run(['mmcli', '-m', modem_id], capture_output=True, text=True, timeout=5)
                    if modem_result.returncode == 0:
                        modem_output = modem_result.stdout
                        
                        # Parse connection state from ModemManager
                        if 'state: connected' in modem_output.lower():
                            dongle_status["connected"] = True
                        
                        # Also check NetworkManager for cellular connections
                        if not dongle_status["connected"]:
                            try:
                                nm_result = subprocess.run(['nmcli', 'device', 'status'], capture_output=True, text=True, timeout=5)
                                if nm_result.returncode == 0:
                                    # Check for connected GSM/cellular devices
                                    for line in nm_result.stdout.split('\n'):
                                        if 'gsm' in line.lower() and 'connected' in line.lower():
                                            dongle_status["connected"] = True
                                            break
                            except:
                                pass  # NetworkManager check failed, continue with ModemManager only
                        
                        # Parse signal strength
                        signal_match = re.search(r'signal quality:\s*(\d+)%', modem_output)
                        if signal_match:
                            signal_percent = int(signal_match.group(1))
                            dongle_status["signal_strength"] = f"{signal_percent}%"
                        
                        # Parse operator
                        operator_match = re.search(r'operator name:\s*\'([^\']+)\'', modem_output)
                        if not operator_match:
                            # Try without quotes (format: "operator name: vodafone UK")
                            operator_match = re.search(r'operator name:\s*([^\r\n]+)', modem_output)
                        if operator_match:
                            dongle_status["operator"] = operator_match.group(1).strip()
                        
                        # Parse network type
                        if 'access tech: lte' in modem_output.lower():
                            dongle_status["network_type"] = "LTE"
                        elif 'access tech: umts' in modem_output.lower():
                            dongle_status["network_type"] = "3G"
                        elif 'access tech: gsm' in modem_output.lower():
                            dongle_status["network_type"] = "2G"
                    
                    # Get IP address if connected
                    if dongle_status["connected"]:
                        # Try ModemManager bearer info
                        bearer_result = subprocess.run(['mmcli', '-m', modem_id, '--list-bearers'], capture_output=True, text=True, timeout=5)
                        if bearer_result.returncode == 0:
                            bearer_match = re.search(r'/org/freedesktop/ModemManager1/Bearer/(\d+)', bearer_result.stdout)
                            if bearer_match:
                                bearer_id = bearer_match.group(1)
                                bearer_info = subprocess.run(['mmcli', '-b', bearer_id], capture_output=True, text=True, timeout=5)
                                if bearer_info.returncode == 0:
                                    ip_match = re.search(r'address:\s*(\d+\.\d+\.\d+\.\d+)', bearer_info.stdout)
                                    if ip_match:
                                        dongle_status["ip_address"] = ip_match.group(1)
                        
                        # If no IP from ModemManager, check common cellular interfaces
                        if not dongle_status.get("ip_address"):
                            try:
                                # Check ppp0 interface (common for cellular connections)
                                ip_result = subprocess.run(['ip', 'addr', 'show', 'ppp0'], capture_output=True, text=True, timeout=3)
                                if ip_result.returncode == 0:
                                    ip_match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', ip_result.stdout)
                                    if ip_match:
                                        dongle_status["ip_address"] = ip_match.group(1)
                            except:
                                pass  # ppp0 interface check failed
                else:
                    dongle_status["device_present"] = False
            else:
                dongle_status["device_present"] = False
                
        except FileNotFoundError:
            dongle_status["error"] = "ModemManager (mmcli) not available"
        except subprocess.TimeoutExpired:
            dongle_status["error"] = "ModemManager timeout"
        except Exception as e:
            dongle_status["error"] = f"ModemManager error: {str(e)}"
        
        connection_info["4g_dongle"] = dongle_status
        
        # Add simplified 4G status for main display
        if dongle_status.get("connected"):
            status_text = "Connected"
            if dongle_status.get("operator"):
                status_text += f" ({dongle_status['operator']})"
            if dongle_status.get("signal_strength"):
                status_text += f" - {dongle_status['signal_strength']}"
            connection_info["4g"] = status_text
        elif dongle_status.get("device_present"):
            connection_info["4g"] = "Device present, not connected"
        elif dongle_status.get("error"):
            connection_info["4g"] = "Error: " + dongle_status["error"]
        else:
            connection_info["4g"] = "No device"
        
        # Get GPS status from GPS daemon via client
        try:
            # Use the GPS daemon client and return the raw structure
            success, gps_status = get_gnss_location()
            
            if not success or not gps_status:
                # Function call failed, provide fallback structure
                gps_status = {
                    "available": False,
                    "fix_status": "no_fix",
                    "error": gps_status.get('error', 'GPS function failed') if gps_status else 'GPS function failed'
                }
            else:
                # Add availability flag for backward compatibility
                gps_status["available"] = gps_status.get('fix_status') == 'valid'
                
        except Exception as e:
            gps_status = {
                "available": False,
                "fix_status": "no_fix",
                "error": f"GPS error: {e}"
            }
        
        connection_info["gps"] = gps_status
            
    except Exception as e:
        connection_info["error"] = str(e)
    
    return connection_info

def collect_system_stats():
    """Collect comprehensive system statistics for both heartbeat and web interface"""
    try:
        hardwareid = get_hardwareid()
        
        # Get system metrics using functions from app.py
        cpu = psutil.cpu_percent(interval=0.1)  # Short interval for daemon
        mem = psutil.virtual_memory().percent
        temp = get_temperature()
        power = power_consumption_watts()
        fan_rpm = get_fan_rpm()
        disk = get_disk_usage()
        connection = get_connection_info()
        app_version = get_app_version()
        
        # Prepare comprehensive stats data
        stats_data = {
            'hardwareid': hardwareid,
            'cpu': cpu,
            'mem': mem,
            'temp': temp,
            'power': power,
            'fan_rpm': fan_rpm,
            'disk': disk,
            'connection': connection,
            'timestamp': time.time(),
            'app_version': app_version
        }
        
        return stats_data
        
    except Exception as e:
        logger.error(f"Error collecting system stats: {e}")
        return None

def save_stats_to_file(stats_data):
    """
    Save stats data to local file for web interface consumption.
    
    Uses atomic write to prevent race conditions between the heartbeat daemon
    (writer) and the web application (reader). Writes to temp file first,
    then atomically renames to final file.
    """
    try:
        # Use atomic write to prevent race conditions
        temp_file = HEARTBEAT_FILE + f'.tmp.{os.getpid()}'

        # Write to temp file first
        with open(temp_file, 'w') as f:
            json.dump(stats_data, f)
            f.flush()  # Ensure data is written before rename
            os.fsync(f.fileno())  # Force write to disk
        
        # Atomic move to final location
        os.rename(temp_file, HEARTBEAT_FILE)

        logger.info(f"Stats file updated: {HEARTBEAT_FILE}")

    except Exception as e:
        logger.error(f"Error saving stats to file: {e}")
        # Clean up temp file if it exists
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except:
            pass

def cleanup_stale_stats():
    """Remove stats file if it becomes too old (daemon shutdown)"""
    try:
        if os.path.exists(HEARTBEAT_FILE):
            stat = os.stat(HEARTBEAT_FILE)
            file_age = time.time() - stat.st_mtime
            if file_age > 60:  # Remove if older than 1 minute
                os.remove(HEARTBEAT_FILE)
                logger.info("Removed stale stats file")
    except Exception as e:
        logger.error(f"Error cleaning up stats file: {e}")

def send_heartbeat():
    """Send heartbeat data to remote server and save stats locally (fire and forget)"""
    def _send_async():
        try:
            # Collect comprehensive system stats
            stats_data = collect_system_stats()
            if not stats_data:
                return
            
            # Save basic stats to local file for web interface (without additional data)
            save_stats_to_file(stats_data)
            
            # Create enhanced payload for server with additional information
            server_payload = stats_data.copy()
            
            # Add streaming status
            server_payload['is_streaming'] = is_streaming()
            
            # Add GPS tracking status
            server_payload['is_gps_tracking'] = is_gps_tracking()
            
            # Add active files info (copied from event_stream in app.py)
            active_files = []
            usb_mount_point = find_usb_storage()
            local_recording_path = os.path.join(STREAMER_DATA_DIR, 'recordings', 'webcam')
            add_files_from_path(active_files, local_recording_path, "", "Local", active_only=True)
            if usb_mount_point:
                usb_recording_path = os.path.join(usb_mount_point, 'streamerData', 'recordings', 'webcam')
                add_files_from_path(active_files, usb_recording_path, "[USB] ", "USB", active_only=True)
            server_payload['active_files'] = active_files
            
            # Fire and forget POST request to heartbeat server with enhanced payload
            # Send the enhanced JSON in request body (more secure and no size limits)
            requests.post(
                HEARTBEAT_URL,
                json=server_payload,
                timeout=REQUEST_TIMEOUT,
                headers={'Content-Type': 'application/json'}
            )
            
            logger.info(f"Heartbeat sent: CPU={stats_data['cpu']:.1f}%, MEM={stats_data['mem']:.1f}%, TEMP={stats_data['temp']}")
            
        except Exception as e:
            # Log errors but don't fail
            logger.error(f"Heartbeat error: {e}")
    
    # Run in daemon thread - never blocks main thread
    thread = threading.Thread(target=_send_async, daemon=True)
    thread.start()

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received signal {signum}, shutting down...")
    shutdown_flag.set()

def cleanup_on_shutdown():
    """Clean up files on daemon shutdown"""
    cleanup_stale_stats()

def main():
    """Main daemon loop"""
    # Ensure we declare globals before any use in this function
    global logger

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='RPI Streamer Heartbeat Daemon')    
    parser.add_argument('--daemon', action='store_true', default=False,
                        help='Run in daemon mode (no console output, log to runtime dir/syslog)')
    args = parser.parse_args()

    # Logging removed - logger is a no-op to keep the rest of the code working.
    logger.info("RPI Streamer Heartbeat Daemon starting...")
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
        
    try:
        # Main loop
        logger.info("Starting heartbeat daemon main loop...")
        while not shutdown_flag.is_set():
            logger.debug(f"Sending heartbeat at {datetime.now()}")
            send_heartbeat()
            
            # Wait for next interval or shutdown signal
            if shutdown_flag.wait(timeout=HEARTBEAT_INTERVAL):
                break  # Shutdown requested
        
    except Exception as e:
        logger.error(f"Daemon error: {e}")
    finally:
        cleanup_on_shutdown()
        logger.info("Heartbeat daemon stopped")

if __name__ == '__main__':
    # Avoid parsing arguments here because systemd may pass other flags
    # whether we're running in daemon mode by inspecting sys.argv so
    # we can configure interactive logging appropriately.
    daemon_mode = '--daemon' in sys.argv

    # Basic logging configuration (interactive runs)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # If running interactively (not daemon) and stdout is a TTY, ensure logs print to console
    try:
        if not daemon_mode and sys.stdout.isatty():
            root_logger = logging.getLogger()
            has_stream = any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers)
            if not has_stream:
                sh = logging.StreamHandler(sys.stdout)
                sh.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
                root_logger.addHandler(sh)
    except Exception:
        pass

    main()
