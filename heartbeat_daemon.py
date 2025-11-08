#!/usr/bin/env python3
"""
RPI Streamer Heartbeat Daemon

This daemon runs independently of the web server and provides centralized
system statistics collection and remote device control. It:

1. Collects comprehensive system metrics every 5 seconds including:
   - CPU, memory, temperature, power consumption
   - Disk usage, network connection info
   - Complete system diagnostics (vcgencmd, UPS, INA219)
2. Saves stats to local file (/tmp/rpi_streamer_heartbeat.json) for web interface consumption
3. Sends heartbeat data to remote server for monitoring
4. Processes commands from server responses for remote device control:
   - GPS control commands (start/stop GPS tracking)
   - Future commands can be added easily
5. Runs as a systemd service and starts automatically on boot

The web application's /stats and /diagnostics endpoints read from the stats file,
ensuring consistency and eliminating code duplication. All hardware monitoring
is centralized here to reduce vcgencmd overhead and provide consistent data.

Remote Control Feature:
The heartbeat server can now send commands in JSON responses with this format:
{
    "command": "command_type",
    "action": "action_name"
}

Currently supported commands:
- "gps-control" with actions "start" or "stop" to control GPS tracking
- "stream-control" with actions "start" or "stop" to control video streaming
- "system-control" with actions "shutdown" or "reboot" to control system power state
- "settings-update" with action "update" to update device settings, or "reset" to reset to defaults
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
    from utils import get_hardwareid, get_app_version, HEARTBEAT_FILE, add_files_from_path, is_streaming, is_recording, is_gps_tracking, find_usb_storage, STREAMER_DATA_DIR, get_wifi_mode_status
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
            "device_present": False,
            "sim_present": True
        }
        
        try:
            # Use mmcli to get modem information with JSON output
            result = subprocess.run(['mmcli', '-L', '--output-json'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                try:
                    modem_list_json = json.loads(result.stdout)
                    modem_paths = modem_list_json.get('modem-list', [])
                except Exception as e:
                    dongle_status["error"] = f"Failed to parse mmcli -L JSON: {e}"
                    modem_paths = []

                if modem_paths:
                    # Use first modem found
                    modem_path = modem_paths[0]
                    # Extract modem id from path
                    modem_id_match = re.search(r'/Modem/(\d+)', modem_path)
                    if modem_id_match:
                        modem_id = modem_id_match.group(1)
                        dongle_status["device_present"] = True

                        # Get detailed modem status in JSON
                        modem_result = subprocess.run(['mmcli', '-m', modem_id, '--output-json'], capture_output=True, text=True, timeout=5)
                        if modem_result.returncode == 0:
                            try:
                                modem_json = json.loads(modem_result.stdout)
                                # Check state
                                state = modem_json.get('modem', {}).get('generic', {}).get('state', None)
                                if state == 'connected':
                                    dongle_status["connected"] = True
                                elif state == 'registered':
                                    dongle_status["connected"] = True
                                elif state == 'enabled':
                                    dongle_status["connected"] = False
                                elif state == 'failed':
                                    dongle_status["connected"] = False
                                    # Check for SIM missing
                                    reason = modem_json.get('modem', {}).get('generic', {}).get('state-failed-reason', '')
                                    if reason == 'sim-missing':
                                        dongle_status["sim_present"] = False
                                    else:
                                        dongle_status["sim_present"] = True
                                else:
                                    dongle_status["connected"] = False

                                # Signal strength
                                signal_quality = modem_json.get('modem', {}).get('generic', {}).get('signal-quality', {})
                                if signal_quality:
                                    value = signal_quality.get('value', None)
                                    if value is not None:
                                        dongle_status["signal_strength"] = f"{value}%"

                                # Operator
                                operator = modem_json.get('modem', {}).get('3gpp', {}).get('operator-name', None)
                                if operator:
                                    dongle_status["operator"] = operator

                                # Network type
                                if (dongle_status["connected"]):
                                    access_techs = modem_json.get('modem', {}).get('generic', {}).get('access-technologies', [])
                                    if access_techs:
                                        dongle_status["network_type"] = ','.join(access_techs)

                                # IP address (try bearers)
                                bearers = modem_json.get('modem', {}).get('generic', {}).get('bearers', [])
                                for bearer_path in bearers:
                                    bearer_id_match = re.search(r'/Bearer/(\d+)', bearer_path)
                                    if bearer_id_match:
                                        bearer_id = bearer_id_match.group(1)
                                        bearer_info = subprocess.run(['mmcli', '-b', bearer_id, '--output-json'], capture_output=True, text=True, timeout=5)
                                        if bearer_info.returncode == 0:
                                            try:
                                                bearer_json = json.loads(bearer_info.stdout)
                                                ip_addr = bearer_json.get('bearer', {}).get('properties', {}).get('address', None)
                                                if ip_addr:
                                                    dongle_status["ip_address"] = ip_addr
                                                    break
                                            except Exception:
                                                pass
                                # If no IP from ModemManager, check common cellular interfaces
                                if not dongle_status.get("ip_address"):
                                    try:
                                        ip_result = subprocess.run(['ip', 'addr', 'show', 'ppp0'], capture_output=True, text=True, timeout=3)
                                        if ip_result.returncode == 0:
                                            ip_match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', ip_result.stdout)
                                            if ip_match:
                                                dongle_status["ip_address"] = ip_match.group(1)
                                    except:
                                        pass
                            except Exception as e:
                                dongle_status["error"] = f"Failed to parse mmcli -m JSON: {e}"
                    else:
                        dongle_status["device_present"] = False
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
            if (dongle_status['sim_present'] is False):
                connection_info["4g"] = "Device present, No SIM card"
            else:
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

def get_system_diagnostics():
    """
    Get comprehensive system diagnostics using vcgencmd.
    Returns a dictionary with all available diagnostic information.
    """
    diagnostics = {}
    
    # Check if vcgencmd is available first
    try:
        subprocess.run(['vcgencmd', 'version'], capture_output=True, text=True, timeout=2)
        vcgencmd_available = True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        vcgencmd_available = False
    
    # List of vcgencmd commands to run
    commands = {
        'temperature': ['measure_temp'],
        'pmic_vdd_core_v': ['pmic_read_adc', 'VDD_CORE_V'],
        'pmic_vdd_core_a': ['pmic_read_adc', 'VDD_CORE_A'],
        'pmic_ext5v_v': ['pmic_read_adc', 'EXT5V_V'],
        'throttled': ['get_throttled'],
        'mem_arm': ['get_mem', 'arm'],
        'mem_gpu': ['get_mem', 'gpu'],
        'codec_h264': ['codec_enabled', 'H264'],
        'codec_mpg2': ['codec_enabled', 'MPG2'],
        'codec_wvc1': ['codec_enabled', 'WVC1'],
        'codec_mpg4': ['codec_enabled', 'MPG4'],
        'codec_mjpg': ['codec_enabled', 'MJPG'],
        'config_int': ['get_config', 'int'],
        'config_str': ['get_config', 'str'],
    }
    
    # If vcgencmd is not available, set all values to "--"
    if not vcgencmd_available:
        for key in commands.keys():
            diagnostics[key] = "--"
    else:
        for key, cmd_args in commands.items():
            try:
                result = subprocess.run(['vcgencmd'] + cmd_args, capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    output = result.stdout.strip()
                    
                    # Parse and clean up specific outputs
                    if key == 'temperature':
                        # Extract temperature from "temp=48.8'C"
                        temp_match = re.search(r'temp=([\d.]+)', output)
                        if temp_match:
                            diagnostics[key] = f"{temp_match.group(1)}°C"
                        else:
                            diagnostics[key] = output
                    elif key.startswith('pmic_'):
                        # Parse PMIC values
                        if 'VDD_CORE_V' in output:
                            # Extract voltage from "VDD_CORE_V volt(15)=0.84104930V"
                            volt_match = re.search(r'=([\d.]+)V', output)
                            if volt_match:
                                diagnostics[key] = f"{float(volt_match.group(1)):.3f}V"
                            else:
                                diagnostics[key] = output
                        elif 'VDD_CORE_A' in output:
                            # Extract current from "VDD_CORE_A current(7)=2.35752000A"
                            current_match = re.search(r'=([\d.]+)A', output)
                            if current_match:
                                diagnostics[key] = f"{float(current_match.group(1)):.3f}A"
                            else:
                                diagnostics[key] = output
                        elif 'EXT5V_V' in output:
                            # Extract voltage from "EXT5V_V volt(24)=5.15096000V"
                            volt_match = re.search(r'=([\d.]+)V', output)
                            if volt_match:
                                diagnostics[key] = f"{float(volt_match.group(1)):.3f}V"
                            else:
                                diagnostics[key] = output
                        else:
                            diagnostics[key] = output
                    else:
                        diagnostics[key] = output
                else:
                    diagnostics[key] = f"Error: {result.stderr.strip()}" if result.stderr.strip() else "N/A"
            except subprocess.TimeoutExpired:
                diagnostics[key] = "Timeout"
            except FileNotFoundError:
                diagnostics[key] = "--"
            except Exception as e:
                diagnostics[key] = f"Error: {str(e)}"
    
    # Add UPS status information
    try:
        from x120x import X120X
        with X120X() as ups:
            ups_status = ups.get_status()
            diagnostics['ups_voltage'] = ups_status['voltage']
            diagnostics['ups_capacity'] = ups_status['capacity'] 
            diagnostics['ups_battery_status'] = ups_status['battery_status']
            diagnostics['ups_ac_power'] = ups_status['ac_power_connected']
    except RuntimeError as e:
        # UPS device not found or libraries not available
        diagnostics['ups_voltage'] = None
        diagnostics['ups_capacity'] = None
        diagnostics['ups_battery_status'] = "UPS Not Available"
        diagnostics['ups_ac_power'] = None
    except Exception as e:
        diagnostics['ups_voltage'] = None
        diagnostics['ups_capacity'] = None
        diagnostics['ups_battery_status'] = f"Error: {str(e)}"
        diagnostics['ups_ac_power'] = None
    
    # Add INA219 power monitoring information
    try:
        from INA219 import INA219
        with INA219(addr=0x41) as ina219:
            diagnostics['ina219_bus_voltage'] = f"{ina219.getBusVoltage_V():.3f} V"
            diagnostics['ina219_shunt_voltage'] = f"{ina219.getShuntVoltage_mV():.3f} mV"
            diagnostics['ina219_current'] = f"{ina219.getCurrent_mA():.1f} mA"
            diagnostics['ina219_power'] = f"{ina219.getPower_W():.3f} W"
            
            # Get power status
            power_status = ina219.getPowerStatus()
            if power_status is True:
                diagnostics['ina219_power_source'] = "Plugged In"
            elif power_status is False:
                diagnostics['ina219_power_source'] = "Unplugged"
            else:
                diagnostics['ina219_power_source'] = "Unknown"
                
            # Calculate PSU voltage (bus + shunt)
            psu_voltage = ina219.getBusVoltage_V() + (ina219.getShuntVoltage_mV() / 1000)
            diagnostics['ina219_psu_voltage'] = f"{psu_voltage:.3f} V"
            
            # Calculate battery percentage (based on 3S: 9V empty, 12.6V full)
            bus_voltage = ina219.getBusVoltage_V()
            battery_percent = (bus_voltage - 9) / 3.6 * 100
            battery_percent = max(0, min(100, battery_percent))
            diagnostics['ina219_battery_percent'] = f"{battery_percent:.1f}%"
        
    except RuntimeError as e:
        # INA219 device not found
        diagnostics['ina219_bus_voltage'] = "INA219 Not Available"
        diagnostics['ina219_shunt_voltage'] = "--"
        diagnostics['ina219_current'] = "--"
        diagnostics['ina219_power'] = "--"
        diagnostics['ina219_power_source'] = "--"
        diagnostics['ina219_psu_voltage'] = "--"
        diagnostics['ina219_battery_percent'] = "--"
    except Exception as e:
        diagnostics['ina219_bus_voltage'] = f"Error: {str(e)}"
        diagnostics['ina219_shunt_voltage'] = "N/A"
        diagnostics['ina219_current'] = "N/A"
        diagnostics['ina219_power'] = "N/A"
        diagnostics['ina219_power_source'] = "N/A"
        diagnostics['ina219_psu_voltage'] = "N/A"
        diagnostics['ina219_battery_percent'] = "N/A"
    
    # Parse throttled status for special highlighting
    throttled_raw = diagnostics.get('throttled', '')
    throttled_info = parse_throttled_status(throttled_raw)
    diagnostics['throttled_parsed'] = throttled_info
    
    return diagnostics

def parse_throttled_status(throttled_output):
    """
    Parse the throttled status from vcgencmd get_throttled output.
    Returns a dict with parsed information about undervoltage and throttling.
    """
    info = {
        'raw': throttled_output,
        'has_issues': False,
        'current_issues': [],
        'past_issues': [],
        'hex_value': None
    }
    
    try:
        # Extract hex value from output like "throttled=0x50000"
        match = re.search(r'throttled=0x([0-9a-fA-F]+)', throttled_output)
        if match:
            hex_value = int(match.group(1), 16)
            info['hex_value'] = hex_value
            
            # Bit meanings for throttled status
            bit_meanings = {
                0: 'Under-voltage detected',
                1: 'Arm frequency capped',
                2: 'Currently throttled',
                3: 'Soft temperature limit active',
                16: 'Under-voltage has occurred',
                17: 'Arm frequency capping has occurred', 
                18: 'Throttling has occurred',
                19: 'Soft temperature limit has occurred'
            }
            
            for bit, meaning in bit_meanings.items():
                if hex_value & (1 << bit):
                    info['has_issues'] = True
                    if bit < 16:
                        info['current_issues'].append(meaning)
                    else:
                        info['past_issues'].append(meaning)
                        
    except Exception as e:
        info['parse_error'] = str(e)
    
    return info

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
        
        # Get comprehensive diagnostics including UPS and INA219 data
        diagnostics = get_system_diagnostics()
        
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
            'app_version': app_version,
            'diagnostics': diagnostics
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

def process_server_command(response_data):
    """
    Process commands received from the heartbeat server response.
    
    The server can send commands in the JSON response with the following format:
    {
        "command": "command_type",
        "action": "action_name",
        ... additional parameters ...
    }
    
    Supported commands:
    - gps-control: Control GPS tracking (actions: start, stop)
    - stream-control: Control video streaming (actions: start, stop)
    - Future commands can be added here (e.g., recording-control, system-control, etc.)
    """
    try:
        command = response_data.get('command')
        action = response_data.get('action')
        
        if not command:
            return
            
        logger.info(f"Received command from server: {command}, action: {action}")
        
        if command == 'gps-control':
            handle_gps_control_command(action, response_data)
        elif command == 'stream-control':
            handle_stream_control_command(action, response_data)
        elif command == 'system-control':
            handle_system_control_command(action, response_data)
        elif command == 'settings-update':
            handle_settings_update_command(action, response_data)
        # Future command handlers can be added here:
        # elif command == 'recording-control':
        #     handle_recording_control_command(action, response_data, settings)
        else:
            logger.warning(f"Unknown command received: {command}")
            
    except Exception as e:
        logger.error(f"Error processing server command: {e}")

def handle_gps_control_command(action, command_data=None):
    """
    Handle GPS control commands received from the server.
    
    Args:
        action (str): The GPS control action ('start' or 'stop')
        command_data (dict): Full command data (for future use with additional parameters)
    """
    try:
        if action == 'start':
            logger.info("Executing GPS start command from server")
            # Make POST request to the gps-control endpoint
            response = requests.post(
                'http://localhost:80/gps-control',
                json={'action': 'start'},
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                status_msg = result.get('status', 'GPS started')
                logger.info(f"GPS start command executed successfully: {status_msg}")
            else:
                error_msg = f"GPS start command failed with status {response.status_code}"
                try:
                    error_detail = response.json().get('error', response.text)
                    error_msg += f": {error_detail}"
                except:
                    error_msg += f": {response.text}"
                logger.error(error_msg)
                
        elif action == 'stop':
            logger.info("Executing GPS stop command from server")
            # Make POST request to the gps-control endpoint
            response = requests.post(
                'http://localhost:80/gps-control',
                json={'action': 'stop'},
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                status_msg = result.get('status', 'GPS stopped')
                logger.info(f"GPS stop command executed successfully: {status_msg}")
            else:
                error_msg = f"GPS stop command failed with status {response.status_code}"
                try:
                    error_detail = response.json().get('error', response.text)
                    error_msg += f": {error_detail}"
                except:
                    error_msg += f": {response.text}"
                logger.error(error_msg)
                
        else:
            logger.warning(f"Unknown GPS control action: {action}")
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error executing GPS control command: {e}")
    except Exception as e:
        logger.error(f"Error handling GPS control command: {e}")

def handle_stream_control_command(action, command_data=None):
    """
    Handle stream control commands received from the server.
    
    Args:
        action (str): The stream control action ('start' or 'stop')
        command_data (dict): Full command data (for future use with additional parameters)
    """
    try:
        if action == 'start':
            logger.info("Executing stream start command from server")
            # Make POST request to the stream-control endpoint
            response = requests.post(
                'http://localhost:80/stream-control',
                json={'action': 'start'},
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                status_msg = result.get('status', 'Stream started')
                logger.info(f"Stream start command executed successfully: {status_msg}")
            else:
                error_msg = f"Stream start command failed with status {response.status_code}"
                try:
                    error_detail = response.json().get('error', response.text)
                    error_msg += f": {error_detail}"
                except:
                    error_msg += f": {response.text}"
                logger.error(error_msg)
                
        elif action == 'stop':
            logger.info("Executing stream stop command from server")
            # Make POST request to the stream-control endpoint
            response = requests.post(
                'http://localhost:80/stream-control',
                json={'action': 'stop'},
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                status_msg = result.get('status', 'Stream stopped')
                logger.info(f"Stream stop command executed successfully: {status_msg}")
            else:
                error_msg = f"Stream stop command failed with status {response.status_code}"
                try:
                    error_detail = response.json().get('error', response.text)
                    error_msg += f": {error_detail}"
                except:
                    error_msg += f": {response.text}"
                logger.error(error_msg)
                
        else:
            logger.warning(f"Unknown stream control action: {action}")
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error executing stream control command: {e}")
    except Exception as e:
        logger.error(f"Error handling stream control command: {e}")

def handle_system_control_command(action, command_data=None):
    """
    Handle system control commands received from the server.
    
    Args:
        action (str): The system control action ('shutdown', 'reboot', etc.)
        command_data (dict): Full command data (for future use with additional parameters)
    """
    try:
        if action == 'shutdown':
            logger.info("Executing system shutdown command from server")
            
            # Schedule shutdown with a small delay to allow heartbeat response to be sent
            def delayed_shutdown():
                time.sleep(2)  # Allow time for any response to be sent
                try:
                    # Use systemctl to perform graceful shutdown
                    subprocess.run(['sudo', 'shutdown', '-h', 'now'], timeout=10)
                except Exception as e:
                    logger.error(f"Error executing shutdown command: {e}")
                    # Fallback to halt command
                    try:
                        subprocess.run(['sudo', 'halt'], timeout=10)
                    except Exception as e2:
                        logger.error(f"Error executing halt fallback: {e2}")
            
            # Run shutdown in separate thread to avoid blocking heartbeat response
            shutdown_thread = threading.Thread(target=delayed_shutdown, daemon=True)
            shutdown_thread.start()
            logger.info("System shutdown initiated")
            
        elif action == 'reboot':
            logger.info("Executing system reboot command from server")
            
            # Schedule reboot with a small delay to allow heartbeat response to be sent
            def delayed_reboot():
                time.sleep(2)  # Allow time for any response to be sent
                try:
                    # Use systemctl to perform graceful reboot
                    subprocess.run(['sudo', 'reboot'], timeout=10)
                except Exception as e:
                    logger.error(f"Error executing reboot command: {e}")
            
            # Run reboot in separate thread to avoid blocking heartbeat response
            reboot_thread = threading.Thread(target=delayed_reboot, daemon=True)
            reboot_thread.start()
            logger.info("System reboot initiated")
            
        else:
            logger.warning(f"Unknown system control action: {action}")
            
    except Exception as e:
        logger.error(f"Error handling system control command: {e}")

def handle_settings_update_command(action, command_data=None):
    """
    Handle settings update commands received from the server.
    
    Args:
        action (str): The settings update action ('update', 'reset', etc.)
        command_data (dict): Full command data containing settings to update
    """
    try:
        from utils import load_settings, save_settings
        
        if action == 'update':
            logger.info("Executing settings update command from server")
            
            # Get the settings data from the command
            new_settings = command_data.get('settings', {})
            if not new_settings:
                logger.warning("No settings provided in update command")
                return
            
            # Load current settings
            current_settings = load_settings()
            
            # Update settings with new values
            settings_updated = False
            updated_keys = []
            
            for key, value in new_settings.items():
                if key in current_settings:
                    if current_settings[key] != value:
                        old_value = current_settings[key]
                        current_settings[key] = value
                        updated_keys.append(f"{key}: {old_value} -> {value}")
                        settings_updated = True
                        logger.info(f"Updated setting {key}: {old_value} -> {value}")
                else:
                    # Add new setting
                    current_settings[key] = value
                    updated_keys.append(f"{key}: (new) -> {value}")
                    settings_updated = True
                    logger.info(f"Added new setting {key}: {value}")
            
            if settings_updated:
                # Save updated settings
                try:
                    save_settings(current_settings)
                    logger.info(f"Settings updated successfully. Changed: {', '.join(updated_keys)}")
                except Exception as save_error:
                    logger.error(f"Failed to save updated settings: {save_error}")
            else:
                logger.info("No setting changes needed - all values already match")
                
        elif action == 'reset':
            logger.info("Executing settings reset command from server")
            
            try:
                # Reset settings to defaults directly
                from utils import DEFAULT_SETTINGS
                save_settings(DEFAULT_SETTINGS.copy())
                logger.info("Settings reset to defaults successfully")
            except Exception as reset_error:
                logger.error(f"Failed to reset settings to defaults: {reset_error}")
                
        else:
            logger.warning(f"Unknown settings update action: {action}")
            
    except ImportError as e:
        logger.error(f"Error importing settings functions: {e}")
    except Exception as e:
        logger.error(f"Error handling settings update command: {e}")

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
            
            # Add streaming and recording status
            server_payload['is_streaming'] = is_streaming()
            server_payload['is_recording'] = is_recording()
            
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
            
            # Send POST request to heartbeat server with enhanced payload and process response
            # Send the enhanced JSON in request body (more secure and no size limits)
            response = requests.post(
                HEARTBEAT_URL,
                json=server_payload,
                timeout=REQUEST_TIMEOUT,
                headers={'Content-Type': 'application/json'}
            )
            
            logger.info(f"Heartbeat sent: CPU={stats_data['cpu']:.1f}%, MEM={stats_data['mem']:.1f}%, TEMP={stats_data['temp']}")
            
            # Process commands from server response
            try:
                if response.status_code == 200:
                    response_data = response.json()
                    if isinstance(response_data, dict) and 'command' in response_data:
                        process_server_command(response_data)
            except (ValueError, KeyError) as e:
                logger.debug(f"No valid command in server response: {e}")
            except Exception as e:
                logger.error(f"Error processing server response: {e}")
            
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
    import argparse
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='RPI Streamer Heartbeat Daemon')
    parser.add_argument('--daemon', action='store_true', default=False,
                        help='Run in daemon mode (systemd service)')
    args = parser.parse_args()
    
    # Configure logging based on daemon mode
    if args.daemon:
        # Daemon mode: output to systemd journal (stdout/stderr)
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
    else:
        # Interactive mode: output to console with cleaner format
        logging.basicConfig(
            level=logging.INFO,
            format='%(levelname)s: %(message)s',
            stream=sys.stdout
        )

    main()
