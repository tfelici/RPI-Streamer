# SIM7600G-H Modem Manager System

## Overview

The Modem Manager System automatically initializes, monitors, and recovers from cellular connectivity issues with the SIM7600G-H dongle. This system handles both initial modem configuration (RNDIS/NON-RNDIS mode switching and GPS setup) and ongoing monitoring to address common problems where cellular modems fail to recover after signal loss.

### Key Features
- **Mode Switching**: Automatic RNDIS (9011) and NON-RNDIS (9001) mode configuration
- **USB Power Cycling**: Hardware-level modem reset using `uhubctl` for robust recovery
- **Automatic Detection**: Dynamic modem detection across different USB ports
- **GPS Configuration**: Comprehensive GPS and GNSS constellation setup
- **Signal Handling**: Graceful daemon termination with udev integration

## Problem Descript## Problem Description

When using the SIM7600G-H dongle in mobile environments (like cars), the following issues commonly occur:

1. **Signal Loss**: When moving through areas with poor 4G coverage, the modem loses connection
2. **Recovery Failure**: After returning to strong signal areas, the modem fails to automatically reconnect
3. **Manual Reset Required**: Only a manual modem reset restores connectivity

This creates reliability issues for applications that depend on continuous cellular connectivity.

## Solution

The Modem Manager System provides:

- **Automatic Monitoring**: Continuously monitors cellular connection status
- **Smart Detection**: Detects both ModemManager and NetworkManager connection states
- **Graduated Recovery**: Uses escalating recovery methods based on failure severity
- **Logging & Diagnostics**: Comprehensive logging for troubleshooting
- **Integration**: Works with existing RPI Streamer infrastructure

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Modem Manager  │    │  ModemManager   │    │ NetworkManager  │
│     Daemon      │◄──►│    (mmcli)      │◄──►│    (nmcli)      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │                       └───────────────────────┼──────┐
         │                                              │      │
         ▼                                              ▼      ▼
┌─────────────────┐                            ┌─────────────────┐
│   System Logs   │                            │  SIM7600G-H     │
│   & Monitoring  │                            │     Modem       │
└─────────────────┘                            └─────────────────┘
```

## Modem Mode Configuration

### RNDIS vs NON-RNDIS Modes

The daemon supports two operational modes:

#### RNDIS Mode (USB PID 9011)
- **Purpose**: Configures modem for RNDIS networking (direct USB ethernet)
- **Behavior**: Configures modem and GPS, then exits gracefully
- **ModemManager**: Disabled to prevent conflicts with RNDIS networking
- **Usage**: `sudo python3 /home/pi/flask_app/modem_manager_daemon.py --mode rndis`

#### NON-RNDIS Mode (USB PID 9001) - Default
- **Purpose**: Traditional cellular modem operation with ModemManager
- **Behavior**: Configures modem and GPS, then continues monitoring
- **ModemManager**: Enabled and monitored for connection recovery
- **Usage**: `sudo python3 /home/pi/flask_app/modem_manager_daemon.py --mode non-rndis`

### Mode Switching Process

1. **AT Command Configuration**: Uses serial communication to send `AT+CUSBPIDSWITCH` commands
2. **GPS Configuration**: Sets up Galileo constellation, NMEA sentences, and output rates
3. **Service Management**: Enables/disables ModemManager based on selected mode
4. **Automatic Detection**: Finds AT command port dynamically across `/dev/ttyUSB*` and `/dev/ttyACM*`

## Recovery Methods

The system uses multiple recovery approaches:

### 1. USB Power Cycling (Hardware Reset)
- **Primary Method**: Uses `uhubctl` to physically power cycle the USB port
- **Detection**: Automatically detects modem location by Quectel vendor ID (2c7c)
- **Process**: Power off → 3-second wait → Power on → udev re-detection
- **Effectiveness**: Most reliable recovery method for hardware-level issues

### 2. ModemManager Restart (Fallback)
- **Fallback Method**: Used when USB power cycling is unavailable
- **Process**: Restarts ModemManager service and waits for modem reappearance
- **Timing**: 30-60 seconds for full recovery

### 3. Signal-Based Termination
- **Integration**: Daemon receives SIGTERM from udev rules when modem reconnects
- **Graceful Shutdown**: Signal handler sets shutdown flag for clean termination
- **Automation**: Enables automatic daemon lifecycle management

## Configuration

### Detection Parameters
- **Check Interval**: 30 seconds between connection checks
- **Failure Threshold**: 3 consecutive failures trigger recovery
- **Recovery Timeout**: 120 seconds to verify recovery success
- **Max Attempts**: 3 recovery attempts before cooldown
- **Cooldown Period**: 5 minutes after max attempts

### Connection Validation
The system validates connectivity using both:
- ModemManager state (`mmcli -m 0`)
- NetworkManager connection status (`nmcli device status`)

Both must report "connected" for the connection to be considered healthy.

## Installation

### Automatic Installation (Recommended)
The modem manager system is **automatically installed** as part of the main RPI Streamer installation:

```bash
# Download and run the main installer
curl -H "Cache-Control: no-cache" -L -o install_rpi_streamer.sh "https://raw.githubusercontent.com/tfelici/RPI-Streamer/main/install_rpi_streamer.sh"
bash install_rpi_streamer.sh
```

The installer automatically:
1. ✅ Installs ModemManager and NetworkManager dependencies
2. ✅ Creates the modem manager daemon service
3. ✅ Configures log rotation
4. ✅ Enables and starts the service
5. ✅ Tests modem detection
6. ✅ Integrates with existing RPI Streamer services

### Manual Prerequisites (if needed)
If installing on a system without the main installer:
```bash
sudo apt update
sudo apt install modemmanager network-manager
sudo systemctl enable ModemManager NetworkManager
sudo systemctl start ModemManager NetworkManager
```

## Usage

### Command Line Options

```bash
# RNDIS Mode - Configure modem for RNDIS networking and exit
sudo python3 /home/pi/flask_app/modem_manager_daemon.py --mode rndis

# NON-RNDIS Mode - Configure modem and continue monitoring (default)
sudo python3 /home/pi/flask_app/modem_manager_daemon.py --mode non-rndis

# Daemon mode (background process)
sudo python3 /home/pi/flask_app/modem_manager_daemon.py --daemon --mode non-rndis
```

### Integration with udev Rules

The daemon is typically triggered by udev rules when the modem is detected:

```bash
# Example udev rule (usually in /etc/udev/rules.d/)
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="2c7c", ATTR{idProduct}=="0125|0801", \
  RUN+="/usr/bin/python3 /home/pi/flask_app/modem_manager_daemon.py --mode non-rndis"
```

### Automatic USB Detection

The system automatically detects modem location using:

```bash
# Detection process
uhubctl                           # List all USB hubs and ports
grep -E "2c7c:"                  # Find Quectel devices by vendor ID
# Returns hub location and port number for power cycling
```

## Service Management

### Basic Commands
```bash
# Check service status
sudo systemctl status modem-manager

# Start/stop service
sudo systemctl start modem-manager
sudo systemctl stop modem-manager
sudo systemctl restart modem-manager

# Enable/disable autostart
sudo systemctl enable modem-manager
sudo systemctl disable modem-manager
```

### USB Power Cycling Prerequisites

Install `uhubctl` for hardware USB control:

```bash
# Install uhubctl
sudo apt update
sudo apt install uhubctl

# Test USB hub detection
uhubctl

# Test modem detection
uhubctl | grep -E "2c7c:"
```

### Monitoring
```bash
# Real-time logs
sudo journalctl -u modem-manager -f

# Recent logs
sudo journalctl -u modem-manager -n 50

# Daemon log file
tail -f /var/log/modem_manager.log
```

## Shared Utility Functions

The system includes reusable functions in `utils.py`:

### `detect_modem_usb_location()`
```python
# Automatically detects modem USB hub and port
hub_location, port_number = detect_modem_usb_location()
# Returns: (hub_location, port_number) or (None, None) if not found
```

### `power_cycle_modem_usb()`
```python
# Performs complete USB power cycle
success, message = power_cycle_modem_usb()
# Returns: (True, success_message) or (False, error_message)
```

These functions are used by:
- **Modem Manager Daemon**: For recovery operations
- **Web Interface**: For manual modem reset functionality
- **Other Applications**: Any component needing modem power cycling

## Log Analysis

### Normal Operation
```
2024-01-15 10:30:15 - modem_manager - INFO - Connection OK - Signal: 75%, Operator: Vodafone UK, Tech: LTE
2024-01-15 10:30:45 - modem_manager - INFO - Connection OK - Signal: 73%, Operator: Vodafone UK, Tech: LTE
```

### Recovery Process
```
2024-01-15 10:31:15 - modem_manager - WARNING - Connection failed (1/3): {'mm_connected': False, 'nm_connected': False}
2024-01-15 10:31:45 - modem_manager - WARNING - Connection failed (2/3): {'mm_connected': False, 'nm_connected': False}
2024-01-15 10:32:15 - modem_manager - WARNING - Connection failed (3/3): {'mm_connected': False, 'nm_connected': False}
2024-01-15 10:32:15 - modem_manager - ERROR - Connection failure threshold reached, starting recovery attempt 1
2024-01-15 10:32:15 - modem_manager - INFO - Recovery attempt 1: Soft Reset
2024-01-15 10:32:15 - modem_manager - INFO - Disconnecting cellular connection: cellular
2024-01-15 10:32:20 - modem_manager - INFO - Reconnecting cellular connection: cellular
2024-01-15 10:32:35 - modem_manager - INFO - Soft reset completed successfully
2024-01-15 10:32:35 - modem_manager - INFO - Recovery completed successfully
2024-01-15 10:34:35 - modem_manager - INFO - Connection restored after recovery
```

## Troubleshooting

### Service Won't Start
Check dependencies:
```bash
# Verify ModemManager
sudo systemctl status ModemManager
mmcli -L

# Verify NetworkManager
sudo systemctl status NetworkManager
nmcli device status
```

Check permissions:
```bash
# Ensure script is executable
ls -la /home/pi/flask_app/modem_manager_daemon.py

# Check service file
sudo systemctl cat modem-manager
```

### Modem Not Detected
```bash
# Check USB devices (look for Quectel/SimTech)
lsusb | grep -E -i "quectel|simtech|2c7c"

# Check ModemManager detection
mmcli -L
mmcli -m 0  # Replace 0 with your modem number

# Check device files
ls -la /dev/ttyUSB*
ls -la /dev/ttyACM*

# Test USB power cycling detection
sudo python3 -c "
import sys
sys.path.insert(0, '/home/pi/flask_app')
from utils import detect_modem_usb_location
print(detect_modem_usb_location())
"
```

### USB Power Cycling Issues
```bash
# Check uhubctl installation
which uhubctl
uhubctl --version

# Check USB hub support for power control
sudo uhubctl

# Test manual power cycling
sudo uhubctl -a off -p PORT_NUMBER -l HUB_LOCATION
sleep 3
sudo uhubctl -a on -p PORT_NUMBER -l HUB_LOCATION

# Check for permission issues
sudo dmesg | grep -i usb
sudo journalctl -u modem-manager | grep -i "power cycle"
```

### Mode Switching Issues
```bash
# Check current USB PID mode
mmcli -m 0 --command="AT+CUSBPIDSWITCH?"

# Test AT command port access
sudo python3 -c "
import serial
ports = ['/dev/ttyUSB2', '/dev/ttyUSB3', '/dev/ttyACM2']
for port in ports:
    try:
        with serial.Serial(port, 115200, timeout=5) as ser:
            ser.write(b'AT\\r\\n')
            response = ser.readline()
            print(f'{port}: {response}')
    except Exception as e:
        print(f'{port}: {e}')
"
```

### Recovery Not Working
Check connection manually:
```bash
# Test NetworkManager
nmcli connection show
nmcli connection down cellular
nmcli connection up cellular

# Test ModemManager
mmcli -m 0 --simple-disconnect
mmcli -m 0 --simple-connect
```

### High CPU Usage
The daemon uses minimal resources, but if issues occur:
```bash
# Check process resources
top -p $(pgrep -f modem_manager_daemon)

# Adjust check interval (edit daemon script)
CHECK_INTERVAL = 60  # Increase from 30 seconds
```

## Performance Impact

The recovery system has minimal performance impact:

- **CPU Usage**: < 1% (periodic checks only)
- **Memory Usage**: ~10MB Python process
- **Network Impact**: Minimal (status checks only)
- **Recovery Time**: 10-60 seconds depending on method

## Integration with Existing Systems

### Heartbeat Daemon
The recovery system complements the existing `heartbeat_daemon.py`:
- Heartbeat monitors and reports connection status
- Recovery daemon actively fixes connection issues

### Web Interface
Connection status from recovery daemon appears in:
- System diagnostics page
- Connection status indicators
- Real-time monitoring displays

### GPS Tracking
Modem recovery ensures continuous connectivity for:
- GPS track uploading
- Remote monitoring
- Emergency communications

## Advanced Configuration

### Custom Recovery Logic
Edit `/home/pi/flask_app/modem_manager_daemon.py`:

```python
# Adjust timing parameters
CHECK_INTERVAL = 30        # Check every 30 seconds
FAILURE_THRESHOLD = 3      # Trigger after 3 failures
RECOVERY_TIMEOUT = 120     # Wait 2 minutes for recovery
MAX_RECOVERY_ATTEMPTS = 3  # Try 3 times before cooldown
RECOVERY_COOLDOWN = 300    # Wait 5 minutes after max attempts
```

### Custom Recovery Methods
Add custom recovery functions to `/home/pi/flask_app/modem_manager_daemon.py`:

```python
def custom_recovery_method(self):
    """Custom recovery implementation"""
    logger.info("Performing custom recovery...")
    # Your custom recovery logic here
    return True  # Return True if successful
```

### APN-Specific Configuration
The main RPI Streamer installer automatically configures cellular connections with auto-detected APNs. For carriers requiring specific APN settings, modify the NetworkManager configuration created by the installer:

```bash
# Edit the auto-created cellular connection
sudo nmcli connection modify cellular-auto gsm.apn your.carrier.apn
sudo nmcli connection up cellular-auto
```

Or create a custom connection:
```bash
sudo nmcli connection add type gsm ifname '*' con-name cellular-custom gsm.apn=your.carrier.apn
```

## Security Considerations

The recovery daemon runs as root to access:
- ModemManager D-Bus interface
- NetworkManager configuration
- USB device reset capabilities

Security measures implemented:
- Systemd security restrictions
- Limited filesystem access
- No network listening ports
- Minimal privilege requirements

## Support and Maintenance

### Regular Maintenance
- Monitor logs weekly for patterns
- Update recovery thresholds based on experience
- Check for ModemManager/NetworkManager updates

### Log Rotation
Automatic log rotation is configured:
- Daily rotation
- Keep 7 days of logs
- Compress old logs
- Automatic cleanup

### Monitoring Integration
The system can be integrated with monitoring tools:
- Prometheus metrics export
- SNMP monitoring
- Email alerts on repeated failures
- Integration with existing RPI Streamer alerts

## Changelog

### v2.0 (Enhanced Mode Switching & USB Power Cycling)
- **RNDIS/NON-RNDIS Mode Switching**: Added `--mode` parameter for USB PID configuration
- **USB Power Cycling**: Hardware-level reset using `uhubctl` with automatic modem detection
- **Shared Utilities**: Created reusable functions in `utils.py` for cross-application use
- **AT Command Integration**: Direct serial communication for modem configuration
- **GPS Configuration**: Comprehensive GNSS setup with Galileo constellation support
- **Signal Handling**: Graceful daemon termination with udev integration
- **Automatic Detection**: Dynamic modem location detection by vendor ID (2c7c)
- **Service Management**: Mode-specific ModemManager enable/disable functionality

### v1.1 (Integrated Release)
- **Integrated into main RPI Streamer installer**
- Automatic installation with `install_rpi_streamer.sh`
- Enhanced NetworkManager integration
- Improved cellular connection configuration
- Log rotation automatically configured

### v1.0 (Initial Release)
- Basic connection monitoring
- Four-level recovery system
- SystemD integration
- Comprehensive logging
- SIM7600G-H specific optimizations

## FAQ

**Q: Will this work with other cellular modems?**
A: Yes, the system works with any ModemManager-supported modem. The SIM7600G-H specific features (like hardware reset) may not work with other models.

**Q: Does this interfere with normal modem operation?**
A: No, the system only intervenes when connection failures are detected. Normal operation is unaffected.

**Q: Can I adjust the recovery timing?**
A: Yes, edit the configuration parameters in `/home/pi/flask_app/modem_manager_daemon.py` and restart the service with `sudo systemctl restart modem-manager`.

**Q: Is this installed automatically?**
A: Yes, when you install RPI Streamer using the main installer (`install_rpi_streamer.sh`), the modem recovery system is automatically included and configured.

**Q: What happens during system updates?**
A: The service automatically restarts after system updates. Recovery state is reset on restart. The main installer will also update the modem recovery daemon if changes are available.

**Q: Is this compatible with VPN connections?**
A: Yes, the system monitors the underlying cellular connection. VPN connections running over cellular will benefit from improved stability.

**Q: Do I need to install this separately?**
A: No, the modem recovery system is now integrated into the main RPI Streamer installation. Simply run `install_rpi_streamer.sh` and it will be automatically configured.

**Q: When should I use RNDIS mode vs NON-RNDIS mode?**
A: Use **RNDIS mode** for direct USB ethernet networking (modem appears as network interface). Use **NON-RNDIS mode** for traditional cellular operation with ModemManager and NetworkManager. NON-RNDIS is recommended for most users.

**Q: What happens if USB power cycling fails?**
A: The system automatically falls back to ModemManager restart. If `uhubctl` is not installed or the USB hub doesn't support power control, the daemon will use software-based recovery methods.

**Q: Can the daemon automatically switch between modes?**
A: No, the mode must be specified when starting the daemon with `--mode rndis` or `--mode non-rndis`. The daemon will configure the modem for the specified mode and operate accordingly.

**Q: Does USB power cycling affect other USB devices?**
A: No, the power cycling is port-specific. Only the USB port where the modem is connected is power cycled, leaving other devices unaffected.