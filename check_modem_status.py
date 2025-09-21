#!/usr/bin/env python3
"""
Quick diagnostic script to check modem status before testing
"""

import sys
import os
import subprocess
import json

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def check_system_status():
    """Check current system status"""
    print("=== Current System Status ===")
    
    # Check ModemManager status
    print("\n1. ModemManager Service Status:")
    try:
        result = subprocess.run(['systemctl', 'status', 'ModemManager'], 
                               capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print("   ✓ ModemManager service is running")
        else:
            print("   ✗ ModemManager service is not running")
    except Exception as e:
        print(f"   ✗ Error checking ModemManager: {e}")
    
    # Check modem detection
    print("\n2. Modem Detection:")
    try:
        result = subprocess.run(['mmcli', '-L', '--output-json'], 
                               capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            modem_data = json.loads(result.stdout)
            modem_list = modem_data.get('modem-list', [])
            if modem_list:
                print(f"   ✓ Modem detected: {len(modem_list)} modem(s)")
                for i, modem_path in enumerate(modem_list):
                    print(f"     Modem {i}: {modem_path}")
            else:
                print("   ✗ No modems detected by ModemManager")
        else:
            print("   ✗ ModemManager not responding or no output")
    except Exception as e:
        print(f"   ✗ Error checking modem detection: {e}")
    
    # Check USB devices
    print("\n3. USB Device Detection:")
    try:
        result = subprocess.run(['lsusb'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lines = result.stdout.split('\n')
            modem_devices = [line for line in lines if any(term in line.lower() for term in ['simtech', 'qualcomm', '2c7c', '1e0e'])]
            if modem_devices:
                print("   ✓ USB modem device(s) found:")
                for device in modem_devices:
                    print(f"     {device}")
            else:
                print("   ✗ No USB modem devices found")
        else:
            print("   ✗ lsusb command failed")
    except Exception as e:
        print(f"   ✗ Error checking USB devices: {e}")
    
    # Check serial ports
    print("\n4. Serial Ports:")
    ports_to_check = ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyUSB2', '/dev/ttyUSB3', '/dev/ttyACM0', '/dev/ttyACM1']
    existing_ports = [port for port in ports_to_check if os.path.exists(port)]
    if existing_ports:
        print(f"   ✓ Serial ports available: {existing_ports}")
    else:
        print("   ✗ No expected serial ports found")
    
    # Check internet connectivity
    print("\n5. Internet Connectivity:")
    try:
        result = subprocess.run(['ping', '-c', '1', '-W', '3', '8.8.8.8'], 
                               capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print("   ✓ Internet connectivity confirmed (8.8.8.8)")
        else:
            print("   ✗ No internet connectivity")
    except Exception as e:
        print(f"   ✗ Error checking internet: {e}")
    
    print("\n=== Status Check Complete ===")

if __name__ == "__main__":
    check_system_status()