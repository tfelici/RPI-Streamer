# SIM7600G-H Modem Manager System

## Overview

The Modem Manager System automatically initializes, monitors, and recovers from cellular connectivity issues with the SIM7600G-H dongle. This system handles both initial modem configuration (NON-RNDIS mode and GPS setup) and intelligent monitoring that only performs recovery when internet connectivity is actually lost.

### Key Features
- **NON-RNDIS Mode**: Automatic NON-RNDIS (9001) mode configuration with ModemManager coexistence
- **AT Command Reset**: Software-based modem reset using AT+CRESET command
- **Internet Connectivity Check**: Smart recovery that only resets modem when internet is actually down
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

### NON-RNDIS Mode Operation

The daemon operates exclusively in NON-RNDIS mode for traditional cellular modem operation:

#### NON-RNDIS Mode (USB PID 9001)
- **Purpose**: Traditional cellular modem operation with ModemManager coexistence
- **Behavior**: Configures modem and GPS, then continues monitoring connectivity with internet check
- **ModemManager**: Works alongside ModemManager without conflicts
- **Recovery**: Uses AT commands for modem reset instead of USB power cycling
- **Usage**: `sudo python3 /home/pi/flask_app/modem_manager_daemon.py`

### Configuration Process

1. **AT Command Configuration**: Uses serial communication to send `AT+CUSBPIDSWITCH` commands
2. **GPS Configuration**: Sets up Galileo constellation, NMEA sentences, and output rates  
3. **ModemManager Coexistence**: Works directly with ModemManager running for optimal performance
4. **Automatic Detection**: Finds AT command port dynamically across `/dev/ttyUSB*` and `/dev/ttyACM*`

### LTE-Only Network Mode

For improved stability and performance, the system configures the modem for LTE-only operation:

#### Benefits of LTE-Only Mode
- **Faster Connections**: LTE provides superior data speeds and lower latency compared to 3G/2G
- **Stable Operation**: Eliminates network switching between different cellular technologies
- **Reduced Recovery Time**: Modem only searches for LTE towers, reducing connection establishment time
- **Power Efficiency**: Less network scanning and mode switching reduces power consumption
- **Consistent Performance**: Avoids degraded performance when falling back to slower 2G/3G networks

#### NetworkManager Configuration
The LTE-only mode is configured through NetworkManager in `/etc/NetworkManager/system-connections/cellular-auto.nmconnection`:

```ini
[gsm]
apn=internet
network-type=lte-only
```

This approach is more reliable than AT commands as NetworkManager handles the configuration automatically during connection establishment.

#### Manual Testing
```bash
# Check current connection
nmcli connection show cellular-auto

# Modify network type if needed
nmcli connection modify cellular-auto gsm.network-type lte-only

# Revert to automatic if needed
nmcli connection modify cellular-auto gsm.network-type auto

# Restart connection to apply changes
nmcli connection down cellular-auto
nmcli connection up cellular-auto
```

#### Compatibility Notes
- **Coverage Requirement**: Ensure adequate LTE coverage in deployment area
- **Carrier Support**: Verify that your cellular carrier supports LTE data plans
- **Fallback Option**: If LTE coverage is insufficient, change `network-type=auto` in the configuration

## Recovery Methods

The system uses intelligent recovery with internet connectivity awareness:

### 1. Internet Connectivity Check (Primary Gate)
- **Smart Recovery**: Only attempts modem reset when internet is actually unavailable
- **Method**: Pings Google DNS (8.8.8.8) and Cloudflare DNS (1.1.1.1)
- **Prevention**: Avoids unnecessary modem resets when connectivity exists via other means
- **Logging**: Clearly indicates when recovery is skipped due to working internet

### 2. AT Command Reset (Primary Method)
- **Software Reset**: Uses `AT+CRESET` command to reset modem via serial interface
- **ModemManager Port Management**: Temporarily stops ModemManager to access AT command ports (MM hogs ports)
- **Process**: Stop MM → Send AT+CRESET → Restart MM → Wait for udev signal → Daemon termination
- **Effectiveness**: Clean software reset without hardware disruption

### 3. ModemManager Restart (Fallback)
- **Fallback Method**: Used when AT command reset fails
- **Process**: Restarts ModemManager service and waits for modem reappearance
- **Timing**: 30-60 seconds for full recovery

### 4. Signal-Based Termination
- **Integration**: Daemon receives SIGTERM from udev rules when modem reconnects
- **Graceful Shutdown**: Signal handler sets shutdown flag for clean termination
- **Automation**: Enables automatic daemon lifecycle management

## Configuration

### Detection Parameters
- **Check Interval**: 30 seconds between monitoring cycles
- **Internet Check**: Pings DNS servers (8.8.8.8, 1.1.1.1) with 3-second timeout
- **Recovery Trigger**: Only when both ModemManager detection fails AND internet connectivity fails
- **Recovery Method**: AT+CRESET command with fallback to ModemManager restart
- **Termination**: Daemon exits after successful AT reset, waiting for udev signal

### Connection Validation
The system validates connectivity using multiple layers:

1. **ModemManager Detection**: Checks if modem is visible in ModemManager (`mmcli -L`)
2. **Internet Connectivity Check**: Pings DNS servers (8.8.8.8, 1.1.1.1) to verify actual internet access
3. **USB Device Check**: Verifies physical USB device presence (`lsusb`)

Recovery is only triggered when modem is missing from ModemManager AND internet connectivity fails.

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
# NON-RNDIS Mode - Configure modem and continue monitoring
sudo python3 /home/pi/flask_app/modem_manager_daemon.py

# Daemon mode (background process)
sudo python3 /home/pi/flask_app/modem_manager_daemon.py --daemon
```

### Integration with udev Rules

The daemon is typically triggered by udev rules when the modem is detected:

```bash
# Example udev rule (usually in /etc/udev/rules.d/)
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="2c7c", ATTR{idProduct}=="0125|0801", \
  RUN+="/usr/bin/python3 /home/pi/flask_app/modem_manager_daemon.py"
```

### Automatic Modem Detection

The system automatically detects modem status using:

```bash
# Detection process
mmcli -L                          # Check ModemManager detection
ping -c 1 8.8.8.8                # Test internet connectivity
lsusb | grep -E "2c7c|1e0e"      # Find Quectel/SimTech devices by vendor ID
# Only resets modem when both MM detection fails AND internet is down
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

### AT Command Prerequisites

The system uses AT commands for modem reset, which requires:

```bash
# Verify serial device access
ls -la /dev/ttyUSB* /dev/ttyACM*

# Test basic AT communication (replace ttyUSB2 with your AT port)
echo "AT" > /dev/ttyUSB2

# Check for ModemManager coexistence
sudo systemctl status ModemManager
mmcli -L
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

### `send_at_command()`
```python
# Send AT command to modem via serial interface
response, success = send_at_command(serial_port, "AT+COMMAND", timeout=10)
# Returns: (response_string, True) or (error_message, False)
```

### `reset_modem_at_command()`
```python
# Resets modem via AT+CRESET command
success, message = reset_modem_at_command()
# Returns: (True, success_message) or (False, error_message)
```

These functions are used by:
- **Modem Manager Daemon**: For recovery operations
- **Web Interface**: For manual modem reset functionality
- **Other Applications**: Any component needing modem reset

## Web Interface Integration

### Manual Modem Reset Endpoint

The Flask web interface provides a `/system-settings-modem-reset` endpoint for manual modem resets:

```python
@app.route('/system-settings-modem-reset', methods=['POST'])
def system_settings_modem_reset():
    # Stops ModemManager to free AT command ports
    # Executes AT+CRESET command
    # Restarts ModemManager
    # Returns JSON response with success/error status
```

**Key Implementation Details:**
- **ModemManager Management**: Temporarily stops ModemManager to access AT ports (MM hogs the ports)
- **Port Release**: Waits 2 seconds after stopping MM for ports to be released
- **AT Command**: Uses `reset_modem_at_command()` for the actual reset
- **Recovery**: Always attempts to restart ModemManager, even if AT reset fails
- **Error Handling**: Emergency MM restart in exception cases

**Response Format:**
```json
{
  "success": true,
  "message": "Reset command sent successfully. ModemManager restarted."
}
```

**Error Response:**
```json
{
  "success": false, 
  "error": "AT reset failed: Could not access any AT command port. ModemManager was restarted."
}
```

### Testing Web Interface Reset

Use the provided test script to validate the web interface reset procedure:
```bash
sudo python3 test_web_reset.py
```

This test simulates the exact procedure used by the web interface and validates:
- ModemManager stop/start functionality
- AT command port access
- Error handling and recovery

## Log Analysis

### Normal Operation
```
2024-01-15 10:30:15 - modem_manager - INFO - Starting modem recovery daemon
2024-01-15 10:30:16 - modem_manager - INFO - ✓ NON-RNDIS mode and GPS configuration successful - mode was already enabled, continuing monitoring
2024-01-15 10:30:46 - modem_manager - DEBUG - Modem is present in ModemManager
2024-01-15 10:31:16 - modem_manager - DEBUG - Modem is present in ModemManager
```

### Internet Available - Recovery Skipped
```
2024-01-15 10:31:46 - modem_manager - WARNING - Modem not detected in ModemManager
2024-01-15 10:31:46 - modem_manager - DEBUG - Internet connectivity confirmed via 8.8.8.8
2024-01-15 10:31:46 - modem_manager - INFO - Internet connectivity is available - skipping modem recovery
```

### Recovery Process
```
2024-01-15 10:32:15 - modem_manager - WARNING - Modem not detected in ModemManager
2024-01-15 10:32:15 - modem_manager - DEBUG - Internet connectivity test failed - no response from DNS servers
2024-01-15 10:32:15 - modem_manager - INFO - No internet connectivity detected - checking USB device presence
2024-01-15 10:32:15 - modem_manager - INFO - USB device still present, attempting recovery
2024-01-15 10:32:15 - modem_manager - INFO - Resetting modem via AT command...
2024-01-15 10:32:16 - modem_manager - INFO - Modem reset completed successfully: Reset command sent successfully
2024-01-15 10:32:16 - modem_manager - INFO - Modem reset via AT command - waiting for udev rule to detect reconnection and trigger daemon termination...
2024-01-15 10:32:45 - modem_manager - INFO - Received termination signal from udev - daemon shutting down
```

## Troubleshooting

### AT Command Port Issues

**Problem**: `Could not find available AT command port even after stopping ModemManager`

This typically occurs when the modem creates serial ports with different numbering than expected.

**Diagnosis**:
```bash
# Check what serial ports exist
ls -la /dev/ttyUSB* /dev/ttyACM*

# Use the diagnostic script
sudo python3 test_at_ports.py
```

**Common Port Patterns**:
- **Standard**: `/dev/ttyUSB2`, `/dev/ttyUSB3` (AT command ports)
- **Alternative**: `/dev/ttyUSB0`, `/dev/ttyUSB1`, `/dev/ttyUSB4`, `/dev/ttyUSB5`
- **ACM Mode**: `/dev/ttyACM0`, `/dev/ttyACM1`, `/dev/ttyACM2`, `/dev/ttyACM3`

**Solution**: The system now automatically detects and tests all available ports, but if issues persist:

1. **Check ModemManager interference**:
   ```bash
   sudo systemctl stop ModemManager
   sudo python3 test_at_ports.py  # Should show working ports
   sudo systemctl start ModemManager
   ```

2. **Verify port permissions**:
   ```bash
   ls -la /dev/ttyUSB* /dev/ttyACM*
   # Should show: crw-rw---- 1 root dialout
   ```

3. **Check for port conflicts**:
   ```bash
   sudo fuser /dev/ttyUSB* /dev/ttyACM*  # Shows processes using ports
   ```

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

# Test modem reset via AT commands
sudo python3 -c "
import sys
sys.path.insert(0, '/home/pi/flask_app')
from utils import reset_modem_at_command
print(reset_modem_at_command())
"
```

### Internet Connectivity Issues
```bash
# Test internet connectivity manually
ping -c 1 8.8.8.8
ping -c 1 1.1.1.1

# Test connectivity check function
sudo python3 -c "
import sys, subprocess
try:
    for dns in ['8.8.8.8', '1.1.1.1']:
        result = subprocess.run(['ping', '-c', '1', '-W', '3', dns], 
                              capture_output=True, text=True, timeout=10)
        print(f'{dns}: {\"OK\" if result.returncode == 0 else \"FAIL\"}')
except Exception as e:
    print(f'Error: {e}')
"
```

### Modem Reset Issues
```bash
# Check AT command interface
ls -la /dev/ttyUSB* /dev/ttyACM*

# Test AT command communication
sudo python3 -c "
import serial, sys
ports = ['/dev/ttyUSB2', '/dev/ttyUSB3', '/dev/ttyACM2']
for port in ports:
    try:
        with serial.Serial(port, 115200, timeout=5) as ser:
            ser.write(b'AT\r\n')
            response = ser.readline()
            print(f'{port}: {response.decode().strip()}')
    except Exception as e:
        print(f'{port}: Error - {e}')
"

# Check for ModemManager conflicts
sudo systemctl status ModemManager
sudo journalctl -u modem-manager | grep -i "AT command\|reset"
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

### LTE Network Mode Issues

**Problem**: Modem cannot connect after LTE-only mode is configured

This can occur in areas with poor LTE coverage or with carriers that don't support LTE-only connections.

**Diagnosis**:
```bash
# Check current network mode
mmcli -m 0 --command="AT+CNMP?"

# Check signal quality and available networks
mmcli -m 0 --signal-get
mmcli -m 0 --3gpp-scan

# Check connection status  
mmcli -m 0
nmcli connection show cellular
```

**Solutions**:

1. **Revert to auto-mode** (allows 2G/3G fallback):
   ```bash
   # Stop ModemManager temporarily
   sudo systemctl stop ModemManager
   
   # Send AT command to enable auto network mode
   sudo python3 -c "
   import serial, time
   ports = ['/dev/ttyUSB2', '/dev/ttyUSB3', '/dev/ttyACM2']
   for port in ports:
       try:
           with serial.Serial(port, 115200, timeout=5) as ser:
               ser.write(b'AT+CNMP=2\r\n')  # 2 = automatic mode
               time.sleep(1)
               response = ser.readline()
               print(f'Auto-mode set on {port}: {response.decode().strip()}')
               break
       except: continue
   "
   
   # Restart ModemManager
   sudo systemctl start ModemManager
   ```

2. **Check LTE band compatibility**:
   ```bash
   # View supported LTE bands
   mmcli -m 0 --command="AT+QCFG=\"band\""
   
   # Check carrier's LTE bands (consult carrier documentation)
   ```

3. **Verify carrier LTE support**:
   - Ensure your data plan supports LTE
   - Check if carrier requires specific APN for LTE connections
   - Verify SIM card is LTE-capable

**Prevention**: Test LTE-only mode in your deployment area before permanent installation.

**Manual Testing**: You can test network modes using NetworkManager:
```bash
# Check current connection settings
nmcli connection show cellular-auto | grep network-type

# Temporarily change to auto mode for testing
nmcli connection modify cellular-auto gsm.network-type auto
nmcli connection down cellular-auto && nmcli connection up cellular-auto

# Restore LTE-only mode
nmcli connection modify cellular-auto gsm.network-type lte-only
nmcli connection down cellular-auto && nmcli connection up cellular-auto

# Check signal quality
mmcli -m 0 --signal-get
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
CHECK_INTERVAL = 30        # Check every 30 seconds (modem presence and internet connectivity)

# Internet connectivity check parameters (modify check_internet_connectivity function)
DNS_SERVERS = ['8.8.8.8', '1.1.1.1']  # DNS servers to test
PING_TIMEOUT = 3                       # Ping timeout in seconds
CONNECTIVITY_TIMEOUT = 10              # Overall connectivity check timeout
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

### v4.1 (LTE-Only Network Mode for Enhanced Stability)
- **LTE-Only Configuration**: NetworkManager-based LTE-only mode setup (`network-type=lte-only`) for improved stability
- **Reduced Network Switching**: Eliminates 2G/3G fallback to prevent connection instability
- **Enhanced Connection Speed**: Forces modem to use fastest available cellular technology
- **Power Optimization**: Reduces network scanning and mode switching for better efficiency
- **Reliability**: Uses NetworkManager instead of AT commands for more consistent configuration

### v4.0 (Dynamic Port Detection & ModemManager Port Management)
- **Dynamic AT Port Detection**: Automatically detects available AT command ports instead of hardcoded assumptions
- **ModemManager Port Hogging Fix**: Confirmed MM blocks AT ports; implemented stop/start for reliable access
- **Hardware Agnostic**: Works with different SIM7600G-H firmware versions that create different port numbers
- **Enhanced Port Testing**: Tests all available `/dev/ttyUSB*` and `/dev/ttyACM*` ports for AT responsiveness
- **Web Interface Fix**: Updated Flask endpoint to use ModemManager stop/start for reliable web resets
- **Improved Recovery Logic**: Streamlined modem recovery with better fallback handling
- **Diagnostic Tools**: Added comprehensive test scripts for port detection and reset validation

### v3.0 (ModemManager Coexistence & Simplified Operation)
- **ModemManager Coexistence**: Full coexistence with ModemManager for optimal performance
- **SimTech Modem Support**: Added detection for SimTech vendor ID (1e0e) in addition to Quectel (2c7c)  
- **Simplified Operation**: Removed RNDIS mode - focused on NON-RNDIS mode only
- **Enhanced Detection**: Improved modem detection for both Quectel and SimTech variants
- **Faster Configuration**: Eliminates unnecessary ModemManager stops/starts
- **Conflict Detection**: Uses `fuser` to identify processes blocking AT ports

### v2.0 (Enhanced Mode Switching & AT Command Reset)
- **NON-RNDIS Mode Configuration**: Automatic NON-RNDIS (9001) mode setup with GPS
- **AT Command Reset**: Software-based modem reset using AT+CRESET command  
- **Internet Connectivity Check**: Smart recovery only when connectivity is actually lost
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

**Q: What happens if AT command reset fails?**
A: The system automatically falls back to ModemManager restart. If the AT+CRESET command fails or the AT port is unavailable, the daemon will restart ModemManager service as a backup recovery method.

**Q: Does the daemon work with ModemManager running?**
A: Yes, the daemon is designed for full ModemManager coexistence. It works alongside ModemManager without conflicts, providing optimal performance and reliability.

**Q: When does the daemon actually reset the modem?**
A: Only when both conditions are met: (1) modem is not detected in ModemManager AND (2) internet connectivity test fails. This prevents unnecessary resets when connectivity exists via other means.