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
    from utils import load_settings, get_default_hotspot_ssid, get_hardwareid, get_app_version, HEARTBEAT_FILE, add_files_from_path, get_active_recording_info, get_video_duration_mediainfo, is_streaming, is_gps_tracking, find_usb_storage, STREAMER_DATA_DIR
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
              # Try to get WiFi SSID, signal strength, and bitrates if connected
        try:
            if connection_info["wifi"] == "Connected":
                # Check if we're actually in hotspot mode vs client mode
                is_hotspot = False
                hotspot_clients = 0
                
                # First, check if we're connected to an external WiFi network (client mode)
                # by looking for an active WiFi connection with nmcli
                client_mode_detected = False
                connected_ssid = None
                
                try:
                    # Check for active WiFi connections that are NOT hotspot networks
                    nmcli_result = subprocess.run(['nmcli', '-t', '-f', 'NAME,TYPE,STATE', 'connection', 'show', '--active'], 
                                                capture_output=True, text=True, timeout=3)
                    if nmcli_result.returncode == 0:
                        for line in nmcli_result.stdout.strip().split('\n'):
                            if line:
                                parts = line.split(':')
                                if len(parts) >= 3:
                                    conn_name, conn_type, conn_state = parts[0], parts[1], parts[2]
                                    # If we have an active 802-11-wireless connection that's not our hotspot
                                    if (conn_type == '802-11-wireless' and conn_state == 'activated' and 
                                        not conn_name.lower().startswith('hotspot')):
                                        client_mode_detected = True
                                        connected_ssid = conn_name
                                        break
                except Exception:
                    pass
                
                # Additional check: look at IP address ranges to determine mode
                if not client_mode_detected:
                    # Check if wlan0 has an IP in a typical client range vs hotspot range
                    try:
                        for ip_info in connection_info.get("ip_addresses", []):
                            if ip_info.get("interface") == "wlan0":
                                ip = ip_info.get("ip", "")
                                # If IP is in common home/office ranges, likely client mode
                                if (ip.startswith("192.168.1.") or ip.startswith("192.168.0.") or 
                                    ip.startswith("10.") or ip.startswith("172.")):
                                    client_mode_detected = True
                                    break
                                # If IP is in typical hotspot ranges, likely hotspot mode
                                elif (ip.startswith("192.168.4.") or ip.startswith("192.168.43.") or 
                                      ip.startswith("10.42.")):
                                    # This suggests hotspot mode, continue to hostapd check
                                    break
                    except Exception:
                        pass
                
                # Only check for hotspot mode if we didn't detect client mode
                if not client_mode_detected:
                    # Check if hostapd is running (indicates hotspot mode)
                    try:
                        hostapd_result = subprocess.run(['sudo', 'systemctl', 'is-active', 'hostapd'], 
                                                      capture_output=True, text=True, timeout=2)
                        if hostapd_result.stdout.strip() == 'active':
                            is_hotspot = True
                            
                            # Count connected clients using iw command
                            try:
                                clients_result = subprocess.run(['sudo', 'iw', 'dev', 'wlan0', 'station', 'dump'], 
                                                              capture_output=True, text=True, timeout=3)
                                if clients_result.returncode == 0:
                                    # Count the number of "Station" entries
                                    hotspot_clients = clients_result.stdout.count('Station ')
                            except Exception:
                                # Fallback: check DHCP leases file
                                try:
                                    with open('/var/lib/dhcp/dhcpd.leases', 'r') as f:
                                        leases_content = f.read()
                                        # Count active leases (rough estimate)
                                        hotspot_clients = leases_content.count('binding state active')
                                except Exception:
                                    # Fallback: check ARP table for hotspot subnet
                                    try:
                                        settings = load_settings()
                                        hotspot_ip = settings.get('hotspot_ip', '192.168.4.1')
                                        subnet = '.'.join(hotspot_ip.split('.')[:-1]) + '.'
                                        
                                        arp_result = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=2)
                                        if arp_result.returncode == 0:
                                            # Count ARP entries in hotspot subnet (excluding the AP itself)
                                            for line in arp_result.stdout.split('\n'):
                                                if subnet in line and hotspot_ip not in line:
                                                    hotspot_clients += 1
                                    except Exception:
                                        hotspot_clients = 0
                    except Exception:
                        pass
                
                if is_hotspot:
                    # Show number of connected clients instead of regular WiFi info
                    if hotspot_clients == 0:
                        connection_info["wifi"] = "Hotspot (0 clients)"
                    elif hotspot_clients == 1:
                        connection_info["wifi"] = "Hotspot (1 client)"
                    else:
                        connection_info["wifi"] = f"Hotspot ({hotspot_clients} clients)"
                    
                    # Add hotspot details
                    settings = load_settings()
                    hotspot_details = {
                        'ssid': settings.get('hotspot_ssid', get_default_hotspot_ssid()),
                        'clients_count': hotspot_clients,
                        'mode': 'hotspot'
                    }
                    connection_info['wifi_details'] = hotspot_details
                else:
                    # Regular WiFi client mode - get SSID, signal strength, etc.
                    wifi_details = {}
                    
                    # Use the SSID we detected from nmcli active connections if available
                    if connected_ssid:
                        wifi_details['ssid'] = connected_ssid
                    else:
                        # Fallback: Get SSID using nmcli dev wifi
                        try:
                            result = subprocess.run(['nmcli', '-t', '-f', 'active,ssid', 'dev', 'wifi'], capture_output=True, text=True, timeout=2)
                            if result.returncode == 0:
                                lines = result.stdout.strip().split('\n')
                                for line in lines:
                                    if line.startswith('yes:'):
                                        ssid = line.split(':', 1)[1]
                                        if ssid:
                                            wifi_details['ssid'] = ssid
                                            break
                        except Exception:
                            pass
                    
                    # Get detailed WiFi information using iw command (always run for client mode)
                    # Try different interface names, including dynamic detection
                    wifi_interfaces = ['wlan0', 'wlp2s0', 'wlp3s0', 'wlo1']
                    
                    # Try to detect available wireless interfaces dynamically
                    try:
                        iw_dev_result = subprocess.run(['iw', 'dev'], capture_output=True, text=True, timeout=2)
                        if iw_dev_result.returncode == 0:
                            # Parse output to find wireless interfaces
                            for line in iw_dev_result.stdout.split('\n'):
                                if 'Interface' in line:
                                    iface_match = re.search(r'Interface\s+(\w+)', line)
                                    if iface_match:
                                        iface = iface_match.group(1)
                                        if iface not in wifi_interfaces:
                                            wifi_interfaces.append(iface)
                    except Exception:
                        pass  # Fall back to default list
                    
                    for iface in wifi_interfaces:
                        try:
                            iw_result = subprocess.run(['iw', 'dev', iface, 'link'], capture_output=True, text=True, timeout=5)
                            if iw_result.returncode == 0 and ('Connected to' in iw_result.stdout or 'SSID:' in iw_result.stdout):
                                lines = iw_result.stdout.split('\n')
                                interface_found = True
                                for line in lines:
                                    line = line.strip()
                                    # Parse signal strength
                                    if 'signal:' in line:
                                        # Extract signal strength (e.g., "signal: -45 dBm")
                                        signal_match = re.search(r'signal:\s*(-?\d+)\s*dBm', line)
                                        if signal_match:
                                            signal_dbm = int(signal_match.group(1))
                                            wifi_details['signal_dbm'] = signal_dbm
                                            # Convert to percentage (rough estimation)
                                            # -30 dBm = 100%, -90 dBm = 0%
                                            signal_percent = max(0, min(100, (signal_dbm + 90) * 100 / 60))
                                            wifi_details['signal_percent'] = int(signal_percent)
                                    # Parse TX bitrate
                                    elif 'tx bitrate:' in line:
                                        # Extract TX bitrate (e.g., "tx bitrate: 72.2 MBit/s")
                                        tx_match = re.search(r'tx bitrate:\s*([\d.]+)\s*MBit/s', line)
                                        if tx_match:
                                            wifi_details['tx_bitrate'] = float(tx_match.group(1))
                                    # Parse RX bitrate
                                    elif 'rx bitrate:' in line:
                                        # Extract RX bitrate (e.g., "rx bitrate: 65.0 MBit/s")
                                        rx_match = re.search(r'rx bitrate:\s*([\d.]+)\s*MBit/s', line)
                                        if rx_match:
                                            wifi_details['rx_bitrate'] = float(rx_match.group(1))
                                break  # Found working interface, stop trying others
                        except Exception as e:
                            # Log the error for debugging but continue trying other interfaces
                            continue
                    
                    # Update connection_info with detailed WiFi information for client mode
                    if wifi_details:
                        # Set the mode to client for non-hotspot WiFi connections
                        wifi_details['mode'] = 'client'
                        connection_info['wifi_details'] = wifi_details
                        # Update main wifi status with SSID if available
                        if 'ssid' in wifi_details:
                            wifi_status = f"Connected ({wifi_details['ssid']})"
                            # Add signal strength if available
                            if 'signal_percent' in wifi_details:
                                wifi_status += f" - {wifi_details['signal_percent']}%"
                            connection_info["wifi"] = wifi_status
                    else:
                        # Fallback: if we detected client mode but couldn't get details
                        if client_mode_detected:
                            connection_info['wifi_details'] = {
                                'mode': 'client',
                                'ssid': connected_ssid or 'Unknown'
                            }
                            if connected_ssid:
                                connection_info["wifi"] = f"Connected ({connected_ssid})"
        except Exception:
            pass
    
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
