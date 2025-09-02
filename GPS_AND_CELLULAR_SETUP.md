# GPS and Cellular Setup Guide v3.00

The RPI Streamer supports modern Linux GPS and cellular connectivity using the streamlined GPS daemon architecture and industry-standard services.

## GPS Setup

### Overview
GPS functionality uses the modern GPS daemon architecture with integrated initialization, providing real-time location data through a streamlined client-server interface with automatic hardware detection.

### Supported GPS Hardware
- **USB GPS Receivers**: GlobalSat BU-353-S4, VK-172, u-blox receivers, etc.
- **GPS HATs**: Adafruit Ultimate GPS HAT, Waveshare GPS HAT, etc.
- **Cellular Modems with GPS**: Any ModemManager-supported cellular modem with GNSS
- **Serial GPS Devices**: Any NMEA-compatible GPS device

### Installation
```bash
# GPS dependencies are automatically installed with RPI Streamer v3.00
# Modern GPS daemon handles all GPS hardware types automatically

# No manual configuration required - GPS daemon provides automatic:
# ✅ Hardware detection and initialization
# ✅ Multi-client GPS data access
# ✅ Robust error handling and reconnection
```

### Configuration
The modern GPS daemon automatically handles all GPS hardware types with integrated initialization. GPS tracking is configured through the Flight Settings web interface.

### Testing GPS
```bash
# Test GPS daemon communication
python3 ~/flask_app/gps_client.py

# Check GPS daemon status
sudo systemctl status gps-daemon.service

# View GPS daemon logs
sudo journalctl -u gps-daemon.service -f

# Manual GPS data access (for debugging)
# The daemon automatically handles all device types and paths
```

## Cellular Internet Setup

### Overview
Cellular connectivity uses ModemManager, the standard Linux cellular modem management service, along with NetworkManager for connection management.

### Supported Hardware
- **USB Cellular Modems**: Huawei, ZTE, Sierra Wireless, Quectel, etc.
- **Mini PCIe Modems**: With appropriate adapters
- **HAT Modems**: Raspberry Pi cellular HATs

### Installation
ModemManager and NetworkManager are automatically installed and configured by the RPI Streamer installation script:

```bash
# Check if services are running
sudo systemctl status ModemManager
sudo systemctl status NetworkManager

# View available modems
mmcli -L

# Check modem status (replace 0 with your modem number)
mmcli -m 0
```

### Automatic Connection
NetworkManager automatically manages cellular connections:

```bash
# List available connections
nmcli connection show

# Show cellular connection status
nmcli device status | grep gsm

# Check signal strength and connection info
mmcli -m 0 --signal-setup=5  # Enable signal monitoring
mmcli -m 0 --signal-get      # Get current signal info
```

### Manual Connection Setup
If automatic connection fails, you can configure manually:

```bash
# Create a cellular connection (APN will be auto-detected for most carriers)
nmcli connection add type gsm ifname '*' con-name cellular

# For carriers requiring specific APN:
nmcli connection add type gsm ifname '*' con-name cellular gsm.apn=your.carrier.apn

# Connect
nmcli connection up cellular
```

## Integration with RPI Streamer

### GPS Integration
The RPI Streamer automatically uses GPS data via the `get_gnss_location()` function in `utils.py`:

```python
from utils import get_gnss_location

# Get current GPS position
success, location_data = get_gnss_location()

if success and location_data['fix_status'] == 'valid':
    print(f"Location: {location_data['latitude']}, {location_data['longitude']}")
    print(f"Satellites: {location_data['satellites']['total']}")
```

### Cellular Status
Cellular connection status is monitored via ModemManager in the web interface:

- Connection status (connected/disconnected)
- Signal strength
- Operator name
- Network technology (LTE/3G/2G)
- IP address

### GPS Tracking
The GPS tracker (`gps_tracker.py`) automatically:
- Uses direct NMEA parsing for position data
- Detects movement for efficient tracking
- Synchronizes with flight tracking servers
- Handles GPS hardware gracefully

## Troubleshooting

### GPS Issues
```bash
# Check if GPS device is detected
lsusb | grep -i gps
dmesg | grep -i gps

# Test NMEA data directly from GPS device
cat /dev/ttyUSB1  # For SIM7600G-H GPS data
cat /dev/ttyUSB0  # For other USB GPS devices

# Check for conflicting services
sudo fuser /dev/ttyUSB0  # Check if device is in use
```

### Cellular Issues
```bash
# Check if modem is detected
lsusb | grep -E "(Huawei|ZTE|Sierra|Quectel)"
mmcli -L

# Check ModemManager logs
sudo journalctl -u ModemManager -f

# Reset modem connection
nmcli connection down cellular
nmcli connection up cellular

# Check APN settings (may need carrier-specific APN)
nmcli connection show cellular | grep gsm.apn
```

### Common Solutions

#### GPS Not Working
1. **Permission Issues**: Ensure user is in `dialout` group
   ```bash
   sudo usermod -a -G dialout $USER
   ```

2. **Device Path**: GPS device may appear on different path after reboot
   ```bash
   # Check available serial devices
   ls /dev/ttyUSB* /dev/ttyACM*
   ```

3. **Conflicting Software**: Disable other GPS software
   ```bash
   sudo systemctl stop chronyd  # May conflict on some systems
   ```

#### Cellular Not Connecting
1. **SIM Card**: Ensure SIM is activated and has data plan
2. **APN Configuration**: Some carriers require specific APN settings
3. **PIN Lock**: SIM may be PIN locked
   ```bash
   mmcli -m 0 --pin=1234  # Enter PIN if required
   ```

## Performance Monitoring

### GPS Performance
```bash
# Monitor GPS accuracy and satellite count
watch -n 1 'gpspipe -w -n 1 | grep TPV | jq .'

# Check GPS timing and precision
gpsmon  # Interactive monitor with satellite view
```

### Cellular Performance
```bash
# Monitor signal strength
watch -n 5 'mmcli -m 0 --signal-get'

# Check data usage and connection stats
ip -s link show wwan0  # Or appropriate interface
```
