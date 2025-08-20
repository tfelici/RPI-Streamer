# SIM7600G-H 4G Dongle Internet Setup Guide

## Overview
This guide helps you set up the Waveshare SIM7600G-H 4G DONGLE for internet access on Raspberry Pi using the RNDIS (Remote Network Driver Interface Specification) method.

## Hardware Requirements
- Raspberry Pi (any model with USB port)
- Waveshare SIM7600G-H 4G DONGLE
- 4G Nano SIM card (activated and with data plan)
- USB cable (included with dongle)
- 4G antenna (included with dongle)

## Quick Setup

### Automated Installation
The RPI Streamer installation script now includes SIM7600G-H support:

```bash
# Install with SIM7600 support (recommended)
bash install_rpi_streamer.sh --sim7600

# The setup will:
# ✅ Install all required drivers and dependencies
# ✅ Create auto-startup service for the dongle
# ✅ Configure the dongle if currently connected
# ✅ Work automatically when dongle is plugged in later
```

**Important**: The installation will proceed even if the dongle is not currently connected. All drivers and services will be installed so the dongle will work immediately when plugged in later.

### Hardware Connection
1. Insert your activated nano SIM card into the SIM7600G-H dongle
2. Connect the 4G antenna to the MAIN antenna connector
3. Connect the dongle to Raspberry Pi via USB
4. Power on - the PWR LED should be solid, NET LED should blink
5. Wait 30-60 seconds for automatic configuration

### 2. Check Device Recognition
```bash
# Check if dongle is detected
lsusb | grep 1e0e

# Check USB serial ports
ls /dev/ttyUSB*
```
You should see multiple ttyUSB ports (typically ttyUSB0, ttyUSB1, ttyUSB2)

### 3. Configure RNDIS Mode
```bash
# Open minicom on AT command port (usually ttyUSB2)
sudo minicom -D /dev/ttyUSB2 -b 115200

# In minicom, send this command:
AT+CUSBPIDSWITCH=9011,1,1
```
The module will restart automatically after this command.

### 4. Network Interface Setup
```bash
# Check for USB network interface (after restart)
ifconfig

# You should see a usb0 interface. If so, bring it up:
sudo ip link set usb0 up

# Get IP address via DHCP
sudo dhclient -v usb0
```

### 5. Test Internet Connection
```bash
# Check IP address
ip addr show usb0

# Test connectivity
ping -c 5 8.8.8.8
```

## Troubleshooting

### No ttyUSB Ports
- Disconnect and reconnect the dongle
- Check `dmesg | tail` for USB errors
- Try a different USB port
- Ensure SIM card is properly inserted

### No usb0 Interface After RNDIS Switch
- Wait 30-60 seconds after the AT command
- Check `dmesg | tail` for interface creation
- Try the command again: `AT+CUSBPIDSWITCH=9011,1,1`

### Can't Get IP Address
```bash
# Check if interface is up
sudo ip link set usb0 up

# Try DHCP again
sudo dhclient -v usb0

# Or set static IP (if your carrier provides one)
sudo ip addr add 192.168.1.100/24 dev usb0
```

### No Internet Despite Having IP
Check network registration and signal:
```bash
sudo minicom -D /dev/ttyUSB2 -b 115200
```
Then send these AT commands:
- `AT+CPIN?` - Check SIM card status (should return "READY")
- `AT+CSQ` - Check signal quality (should return values like "+CSQ: 17,99")
- `AT+CREG?` - Check network registration
- `AT+COPS?` - Check current operator

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

| Command | Description | Expected Response |
|---------|-------------|-------------------|
| `AT` | Test communication | `OK` |
| `AT+CPIN?` | Check SIM status | `+CPIN: READY` |
| `AT+CSQ` | Check signal quality | `+CSQ: XX,99` |
| `AT+CREG?` | Network registration | `+CREG: 0,1` or `+CREG: 0,5` |
| `AT+COPS?` | Current operator | Operator name |
| `AT+CGDCONT?` | List PDP contexts | Context list |
| `AT+CUSBPIDSWITCH=9011,1,1` | Switch to RNDIS | `OK` (then restart) |
| `AT+CUSBPIDSWITCH=9001,1,1` | Switch to Windows mode | `OK` (then restart) |

## Auto-Startup Configuration

The setup script creates a systemd service for automatic connection on boot:

```bash
# Check service status
sudo systemctl status sim7600-internet.service

# Enable/disable auto-startup
sudo systemctl enable sim7600-internet.service
sudo systemctl disable sim7600-internet.service

# Manual start/stop
sudo systemctl start sim7600-internet.service
sudo systemctl stop sim7600-internet.service
```

## Network Modes Comparison

| Mode | Interface | Speed | Use Case |
|------|-----------|-------|----------|
| RNDIS (9011) | usb0 | Fast | **Recommended** - Linux/Pi |
| NDIS (9001) | wwan0 | Fast | Windows, requires drivers |
| PPP | ppp0 | Medium | Legacy systems |
| ECM (9018) | usb0 | Fast | Linux, no drivers needed |

## GPS Integration

The SIM7600G-H also provides GPS functionality:
```bash
# Enable GPS (in minicom)
AT+CGPS=1

# Get GPS info
AT+CGPSINFO

# Disable GPS
AT+CGPS=0
```

## Performance Optimization

### Force 4G Only Mode
```bash
# In minicom
AT+CNMP=38
```

### Check Current Network Mode
```bash
AT+CNMP?
# Returns: 2=Auto, 13=GSM only, 38=LTE only
```

## Files Created by Setup
- `/etc/systemd/system/sim7600-internet.service` - Auto-startup service
- `/tmp/minicom_output` - Temporary AT command responses

## Support Resources
- [Waveshare SIM7600G-H Wiki](https://www.waveshare.com/wiki/SIM7600G-H_4G_DONGLE)
- [RNDIS Setup Guide](https://www.waveshare.com/wiki/Raspberry_Pi_networked_via_RNDIS)
- [AT Command Manual](https://www.waveshare.com/w/index.php?title=File:SIM7500_SIM7600_Series_AT_Command_Manual_V3.00.pdf)

## Integration with GPS Tracker

Once internet is working, you can use the SIM7600G-H for both:
1. **Internet connectivity** (via RNDIS/usb0)
2. **GPS tracking** (via the existing gps_tracker.py script)

The GPS tracker script communicates directly with the GPS hardware via AT commands on the serial interface, while the internet connection runs independently on the USB network interface.
