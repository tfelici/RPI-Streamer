# SIM7600G-H Modem Recovery System

## Overview

The Modem Recovery System automatically monitors and recovers from cellular connectivity issues with the SIM7600G-H dongle. This system addresses the common problem where cellular modems fail to recover after signal loss and require manual reset.

## Problem Descript### Changelog

### v1.1 (Current - Integrated Release)
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
- SIM7600G-H specific optimizationshen using the SIM7600G-H dongle in mobile environments (like cars), the following issues commonly occur:

1. **Signal Loss**: When moving through areas with poor 4G coverage, the modem loses connection
2. **Recovery Failure**: After returning to strong signal areas, the modem fails to automatically reconnect
3. **Manual Reset Required**: Only a manual modem reset restores connectivity

This creates reliability issues for applications that depend on continuous cellular connectivity.

## Solution

The Modem Recovery System provides:

- **Automatic Monitoring**: Continuously monitors cellular connection status
- **Smart Detection**: Detects both ModemManager and NetworkManager connection states
- **Graduated Recovery**: Uses escalating recovery methods based on failure severity
- **Logging & Diagnostics**: Comprehensive logging for troubleshooting
- **Integration**: Works with existing RPI Streamer infrastructure

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Modem Recovery │    │  ModemManager   │    │ NetworkManager  │
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

## Recovery Methods

The system uses a graduated approach with four recovery levels:

### 1. Soft Reset (Level 1)
- Disconnects and reconnects cellular connection via NetworkManager
- Fastest recovery method (5-10 seconds)
- Preserves modem state and configuration

### 2. Bearer Reset (Level 2)
- Deletes and recreates ModemManager bearers
- Reestablishes data connection context
- Takes 15-30 seconds

### 3. Full Modem Reset (Level 3)
- Disables and re-enables the modem via ModemManager
- Complete modem state reset
- Takes 30-60 seconds

### 4. Hardware Reset (Level 4)
- USB bus reset (if supported)
- Physical-level reset of modem hardware
- Last resort method

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
The modem recovery system is **automatically installed** as part of the main RPI Streamer installation:

```bash
# Download and run the main installer
curl -H "Cache-Control: no-cache" -L -o install_rpi_streamer.sh "https://raw.githubusercontent.com/tfelici/RPI-Streamer/main/install_rpi_streamer.sh"
bash install_rpi_streamer.sh
```

The installer automatically:
1. ✅ Installs ModemManager and NetworkManager dependencies
2. ✅ Creates the modem recovery daemon service
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

## Service Management

### Basic Commands
```bash
# Check service status
sudo systemctl status modem-recovery

# Start/stop service
sudo systemctl start modem-recovery
sudo systemctl stop modem-recovery
sudo systemctl restart modem-recovery

# Enable/disable autostart
sudo systemctl enable modem-recovery
sudo systemctl disable modem-recovery
```

### Monitoring
```bash
# Real-time logs
sudo journalctl -u modem-recovery -f

# Recent logs
sudo journalctl -u modem-recovery -n 50

# Daemon log file
tail -f /var/log/modem_recovery.log
```

## Log Analysis

### Normal Operation
```
2024-01-15 10:30:15 - modem_recovery - INFO - Connection OK - Signal: 75%, Operator: Vodafone UK, Tech: LTE
2024-01-15 10:30:45 - modem_recovery - INFO - Connection OK - Signal: 73%, Operator: Vodafone UK, Tech: LTE
```

### Recovery Process
```
2024-01-15 10:31:15 - modem_recovery - WARNING - Connection failed (1/3): {'mm_connected': False, 'nm_connected': False}
2024-01-15 10:31:45 - modem_recovery - WARNING - Connection failed (2/3): {'mm_connected': False, 'nm_connected': False}
2024-01-15 10:32:15 - modem_recovery - WARNING - Connection failed (3/3): {'mm_connected': False, 'nm_connected': False}
2024-01-15 10:32:15 - modem_recovery - ERROR - Connection failure threshold reached, starting recovery attempt 1
2024-01-15 10:32:15 - modem_recovery - INFO - Recovery attempt 1: Soft Reset
2024-01-15 10:32:15 - modem_recovery - INFO - Disconnecting cellular connection: cellular
2024-01-15 10:32:20 - modem_recovery - INFO - Reconnecting cellular connection: cellular
2024-01-15 10:32:35 - modem_recovery - INFO - Soft reset completed successfully
2024-01-15 10:32:35 - modem_recovery - INFO - Recovery completed successfully
2024-01-15 10:34:35 - modem_recovery - INFO - Connection restored after recovery
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
ls -la /home/pi/flask_app/modem_recovery_daemon.py

# Check service file
sudo systemctl cat modem-recovery
```

### Modem Not Detected
```bash
# Check USB devices
lsusb | grep -i quectel

# Check ModemManager detection
mmcli -L
mmcli -m 0  # Replace 0 with your modem number

# Check device files
ls -la /dev/ttyUSB*
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
top -p $(pgrep -f modem_recovery_daemon)

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
Edit `/home/pi/flask_app/modem_recovery_daemon.py`:

```python
# Adjust timing parameters
CHECK_INTERVAL = 30        # Check every 30 seconds
FAILURE_THRESHOLD = 3      # Trigger after 3 failures
RECOVERY_TIMEOUT = 120     # Wait 2 minutes for recovery
MAX_RECOVERY_ATTEMPTS = 3  # Try 3 times before cooldown
RECOVERY_COOLDOWN = 300    # Wait 5 minutes after max attempts
```

### Custom Recovery Methods
Add custom recovery functions to `/home/pi/flask_app/modem_recovery_daemon.py`:

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
A: Yes, edit the configuration parameters in `/home/pi/flask_app/modem_recovery_daemon.py` and restart the service with `sudo systemctl restart modem-recovery`.

**Q: Is this installed automatically?**
A: Yes, when you install RPI Streamer using the main installer (`install_rpi_streamer.sh`), the modem recovery system is automatically included and configured.

**Q: What happens during system updates?**
A: The service automatically restarts after system updates. Recovery state is reset on restart. The main installer will also update the modem recovery daemon if changes are available.

**Q: Is this compatible with VPN connections?**
A: Yes, the system monitors the underlying cellular connection. VPN connections running over cellular will benefit from improved stability.

**Q: Do I need to install this separately?**
A: No, the modem recovery system is now integrated into the main RPI Streamer installation. Simply run `install_rpi_streamer.sh` and it will be automatically configured.