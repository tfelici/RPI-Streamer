# SIM7600G-H 4G Dongle Internet Setup Guide

## Overview
The RPI Streamer includes built-in support for the Waveshare SIM7600G-H 4G DONGLE, providing plug-and-play cellular internet connectivity using RNDIS (Remote Network Driver Interface Specification).

## Hardware Requirements
- Raspberry Pi (any model with USB port)
- Waveshare SIM7600G-H 4G DONGLE
- 4G Nano SIM card (activated with data plan)
- USB cable (included with dongle)
- 4G antenna (included with dongle)

## Automatic Setup

### Installation
The RPI Streamer installation script includes complete SIM7600G-H support:

```bash
# Standard installation (includes SIM7600 support automatically)
bash install_rpi_streamer.sh

# The setup automatically:
# ✅ Installs all required drivers and dependencies
# ✅ Creates sim7600-internet.service for connection management
# ✅ Creates sim7600-daemon.service for communication (port 7600)
# ✅ Sets up udev rules for automatic reconnection
# ✅ Configures the dongle when connected (current or future)
```

**Key Features:**
- **Plug-and-play operation**: Works immediately when dongle is connected
- **Automatic reconnection**: Service handles dongle disconnection/reconnection
- **Enhanced connection logic**: Checks existing connections before attempting setup
- **Thread-safe communication**: Centralized SIM7600Manager prevents conflicts
- **GPS integration**: Provides both internet and GPS functionality

### Hardware Connection
1. **Prepare SIM card**: Insert activated nano SIM card into SIM7600G-H dongle
2. **Connect antenna**: Attach 4G antenna to the MAIN antenna connector
3. **USB connection**: Connect dongle to Raspberry Pi USB port
4. **Power on**: PWR LED should be solid, NET LED should blink during connection
5. **Auto-configuration**: Wait 30-60 seconds for automatic setup

### Service Management
```bash
# Check dongle detection
lsusb | grep 1e0e

# Check internet service status
sudo systemctl status sim7600-internet.service

# Check communication daemon status
sudo systemctl status sim7600-daemon.service

# View service logs
sudo journalctl -u sim7600-internet.service -f
sudo journalctl -u sim7600-daemon.service -f

# Manual service restart (if needed)
sudo systemctl restart sim7600-internet.service
```

## Connection Process

### Automatic RNDIS Configuration
The service automatically:

1. **Detects dongle**: Checks for USB device ID `1e0e:9011`
2. **Waits for initialization**: 30-second delay for dongle startup
3. **Checks existing connections**: Verifies if connection already active
4. **Configures RNDIS mode**: Sends `AT+CUSBPIDSWITCH=9011,1,1` if needed
5. **Establishes network**: Brings up USB interface and requests DHCP
6. **Validates connectivity**: Tests internet access with ping

### Network Interface Management
```bash
# Check network interfaces
ip addr show

# Look for USB interfaces (usb0, usb1, etc.)
ip link show | grep usb

# Check interface status
ip addr show usb0

# Test connectivity
ping -c 3 8.8.8.8
```

## Advanced Features

### Communication Daemon
The SIM7600 daemon (`sim7600-daemon.service`) provides API access:

- **Port**: localhost:7600
- **Thread-safe communication**: Prevents AT command conflicts
- **GPS integration**: Provides both cellular and GPS functionality
- **Status monitoring**: Real-time dongle status and signal strength

### GPS Functionality
The SIM7600G-H includes integrated GPS capabilities:

- **GNSS support**: GPS, GLONASS, Galileo, BeiDou satellites
- **High accuracy**: Professional-grade positioning for flight tracking
- **Dual purpose**: Single device provides internet + GPS
- **Centralized management**: Thread-safe communication prevents conflicts

### Automatic Reconnection
The system includes robust reconnection logic:

- **udev rules**: Automatically restart service when dongle connected
- **Connection validation**: Checks existing connections before setup
- **Interface detection**: Monitors multiple USB network interfaces
- **Graceful recovery**: Handles temporary disconnections smoothly

## Troubleshooting

### Common Issues

#### No dongle detection
```bash
# Check USB recognition
lsusb | grep 1e0e

# If not found:
# - Disconnect and reconnect dongle
# - Try different USB port
# - Check dmesg for USB errors
dmesg | tail -20
```

#### No network interface after connection
```bash
# Check interface creation
ip link show | grep usb

# If no usb0 interface:
# - Verify RNDIS mode configuration
# - Check service logs for errors
sudo journalctl -u sim7600-internet.service -f

# Manual RNDIS configuration (if needed)
sudo minicom -D /dev/ttyUSB2 -b 115200
# Send: AT+CUSBPIDSWITCH=9011,1,1
```

#### Connection established but no internet
```bash
# Check IP assignment
ip addr show usb0

# Test DHCP renewal
sudo dhclient -r usb0  # Release current lease
sudo dhclient -v usb0  # Request new lease

# Check routing
ip route show

# Test DNS resolution
nslookup google.com
```
### Signal and carrier issues
```bash
# Check signal strength and carrier info
sudo minicom -D /dev/ttyUSB2 -b 115200

# AT commands for diagnostics:
AT+CSQ          # Signal quality
AT+COPS?        # Current operator
AT+CGMI         # Manufacturer info
AT+CGMM         # Model info
AT+CGMR         # Firmware revision
AT+CPIN?        # SIM card status
AT+CREG?        # Network registration status
```

#### SIM card issues
```bash
# Check SIM status via AT commands
AT+CPIN?        # PIN status
AT+CCID         # SIM card ID
AT+CNUM         # SIM phone number (if supported)

# Common solutions:
# - Ensure SIM is activated and has data plan
# - Check SIM card orientation in dongle
# - Try SIM in mobile phone to verify activation
# - Contact carrier for data plan status
```

### Service Management
```bash
# Full service restart
sudo systemctl restart sim7600-internet.service
sudo systemctl restart sim7600-daemon.service

# Check service dependencies
sudo systemctl list-dependencies sim7600-daemon.service

# View detailed service status
sudo systemctl status sim7600-internet.service -l
sudo systemctl status sim7600-daemon.service -l

# Reset service configuration (if needed)
sudo systemctl daemon-reload
sudo systemctl enable sim7600-internet.service
sudo systemctl enable sim7600-daemon.service
```

## Manual Configuration (Advanced)

### Manual RNDIS Mode Switch
```bash
# Connect to AT command interface
sudo minicom -D /dev/ttyUSB2 -b 115200

# Send RNDIS configuration command
AT+CUSBPIDSWITCH=9011,1,1

# Wait for dongle restart (30-60 seconds)
# Exit minicom: Ctrl+A, then X
```

### Manual Network Setup
```bash
# After RNDIS configuration, set up network manually
sudo ip link set usb0 up
sudo dhclient -v usb0

# Or configure static IP (if provided by carrier)
sudo ip addr add 192.168.1.100/24 dev usb0
sudo ip route add default via 192.168.1.1 dev usb0
```

### APN Configuration (if needed)
Some carriers require specific APN settings:
```bash
# In minicom, configure APN (replace YOUR_APN with your carrier's APN)
AT+CGDCONT=1,"IP","YOUR_APN"

# Common APNs:
# T-Mobile: fast.t-mobile.com
# AT&T: phone
# Verizon: vzwinternet
```

## AT Commands Reference

Essential AT commands for troubleshooting:

| Command | Description | Expected Response |
|---------|-------------|-------------------|
| `AT` | Test communication | `OK` |
| `AT+CPIN?` | Check SIM status | `+CPIN: READY` |
| `AT+CSQ` | Signal quality (0-31) | `+CSQ: XX,99` |
| `AT+CREG?` | Network registration | `+CREG: 0,1` or `+CREG: 0,5` |
| `AT+COPS?` | Current operator | Operator name |
| `AT+CGDCONT?` | List PDP contexts | Context configurations |
| `AT+CUSBPIDSWITCH=9011,1,1` | Enable RNDIS mode | `OK` (then restart) |
| `AT+CGACT?` | Check PDP context | Context status |

## Integration with RPI Streamer

The SIM7600G-H provides comprehensive functionality:

### Internet Connectivity
- **Primary or backup**: Can serve as main internet or failover
- **Streaming support**: Sufficient bandwidth for video streaming  
- **Remote access**: Enables device access from anywhere with cellular coverage
- **Always-on connectivity**: Independent of WiFi/Ethernet availability

### GPS Tracking Integration
- **Dual functionality**: Single device provides internet + GPS
- **Flight recording**: Real-time location tracking for aviation applications
- **Platform integration**: Direct connection to Gyropilots/Gapilots tracking
- **Professional accuracy**: GNSS support for precise positioning

### System Integration
- **Web interface**: Real-time status in RPI Streamer diagnostics panel
- **Service coordination**: Automatic startup and dependency management
- **Error recovery**: Robust reconnection and fault tolerance
- **Multi-device support**: Consistent behavior across fleet deployments

---

**Note**: This guide covers the automatic SIM7600G-H configuration included with RPI Streamer installation. All setup is handled automatically - this documentation serves as reference for troubleshooting and advanced configuration.
