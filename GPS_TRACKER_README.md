# RPI Streamer GPS Tracking System

The RPI Streamer includes a comprehensive GPS tracking system that integrates seamlessly with the Gyropilots and Gapilots flight tracking platforms, providing real-time location tracking for aviation applications.

## Key Features

### 🛰️ Hardware Integration
- **Universal GPS Support**: Compatible with USB GPS receivers, GPS HATs, and other GNSS hardware
- **Enhanced GNSS**: GPS + GLONASS + Galileo + BeiDou satellite constellation support via direct NMEA parsing
- **SIM7600G-H Integration**: Direct cellular modem GPS communication without middleware dependencies
- **Automatic Hardware Detection**: Robust detection and handling of GPS hardware

### 📍 Tracking Capabilities
- **Real-time Tracking**: Continuous GPS coordinate collection and server synchronization
- **Professional Accuracy**: High-precision positioning with satellite count and precision metrics
- **Flight Recording**: Specialized for aviation applications with appropriate update intervals
- **Platform Integration**: Native support for Gyropilots.org and Gapilots.org tracking systems

### ⚙️ Configuration Options
- **Flight Settings Integration**: Web-based configuration through RPI Streamer interface
- **Multiple Start Modes**: Manual, auto-start on boot, or auto-start on motion detection
- **Simulation Mode**: Built-in GPS simulation using Oxford Airport circular flight pattern
- **Service Management**: Systemd integration with automatic startup capabilities

## System Components

### Core Files
- **`gps_tracker.py`**: Main GPS tracking implementation with direct NMEA parsing
- **`utils.py`**: GPS utility functions including `get_gnss_location()` for accessing GPS data
- **`gps_startup_manager.py`**: Service startup manager for automated GPS tracking
- **`gps_daemon.py`**: GPS daemon with integrated initialization for SIM7600G-H cellular modems

### Services
- **`gps-startup.service`**: Systemd service for GPS startup management
- **ModemManager.service**: Standard Linux cellular modem management

## Installation and Setup

### Automatic Installation
GPS tracking is automatically installed with RPI Streamer:

```bash
# Standard installation includes GPS tracking
bash install_rpi_streamer.sh

# Installation automatically:
# ✅ Installs all GPS dependencies
# ✅ Creates GPS startup service (not enabled by default)
# ✅ Sets up automatic GPS enablement for SIM7600G-H modems
# ✅ Configures hardware integration
```

### Flight Settings Configuration
Configure GPS tracking through the web interface:

1. **Navigate to Flight Settings**: Access the `/flight-settings` page
2. **Set Username**: Required for GPS tracking platform integration
3. **Configure Vehicle**: Optional aircraft registration (e.g., N123AB, G-ABCD)
4. **Choose Start Mode**: Manual, Auto (Boot), or Auto (Motion)
5. **Enable Stream Integration**: Optionally link GPS tracking with video streaming

### Service Management
```bash
# Check GPS service status
sudo systemctl status gps-startup.service

# View GPS service logs
sudo journalctl -u gps-startup.service -f

# Check GPS auto-enable status (for SIM7600G-H modems)
sudo journalctl -u gps-daemon.service -f

# Manual service control (when configured for manual mode)
sudo systemctl start gps-startup.service
sudo systemctl stop gps-startup.service
```

## GPS Tracking Modes

### Manual Mode (Default)
- **User Control**: GPS tracking starts only when "Start GPS Tracking" button is pressed
- **Web Interface**: Full control through RPI Streamer web dashboard
- **Service State**: `gps-startup.service` remains disabled
- **Use Case**: Manual flight operations, testing, selective tracking

### Auto Start on Boot
- **Automatic Operation**: GPS tracking starts when system boots
- **Service State**: `gps-startup.service` enabled and started
- **Use Case**: Aircraft that should always be tracked when powered
- **Configuration**: Set via Flight Settings web interface

### Auto Start on Motion
- **Motion Detection**: GPS tracking starts when aircraft movement detected
- **Smart Activation**: Reduces unnecessary tracking during ground operations
- **Service State**: `gps-startup.service` enabled with motion detection
- **Use Case**: Efficient power usage, automatic flight detection

## Hardware Requirements

### Required Hardware
- **Raspberry Pi**: Any model with USB connectivity
- **GPS Hardware**: USB GPS receiver, GPS HAT, or any NMEA-compatible GNSS device
- **GPS Antenna**: Most GPS devices include an antenna; external antennas improve reception

### Supported GPS Hardware Examples
- **USB GPS Receivers**: GlobalSat BU-353-S4, VK-172, etc.
- **GPS HATs**: Adafruit Ultimate GPS HAT, Waveshare GPS HAT, etc.
- **Cellular Modems with GPS**: Any ModemManager-compatible cellular modem with GNSS
- **Serial GPS Devices**: Any NMEA-compatible GPS device

### Optional Hardware
- **GPIO Connections**: Enhanced hardware integration (optional)
- **External GPS Antenna**: For improved signal in challenging environments
- **UPS HAT**: For continuous operation during power interruptions

## Usage Examples

### Web Interface Control
The primary GPS control is through the RPI Streamer web interface:

1. **Access Dashboard**: Navigate to main RPI Streamer page
2. **Flight Settings**: Configure username and tracking preferences
3. **Start Tracking**: Use "Start GPS Tracking" button for manual control
4. **Monitor Status**: Real-time tracking status and coordinate updates
5. **Stop Tracking**: "Stop GPS Tracking" button ends session

### Command Line Usage (Advanced)

#### GPS Simulation Testing
```bash
# Test with simulated GPS data (Oxford Airport circular pattern)
cd ~/flask_app
python3 gps_tracker.py your_username --domain gyropilots.org --simulate --duration 60
```

#### Real GPS Hardware Testing
```bash
# Test with actual GPS hardware
python3 gps_tracker.py your_username --domain gyropilots.org --interval 2.0
```

#### Manual Coordinate Addition
```bash
# Add coordinates programmatically
python3 -c "
from gps_tracker import GPSTracker
tracker = GPSTracker('your_username', 'gyropilots.org')
tracker.start_tracking()
tracker.add_location(40.7128, -74.0060, 10.0, 5.0, 90.0, 25.0)
"
```

# Stop tracking (coordinates are automatically synced)
## Technical Implementation

### Direct NMEA Integration
- **Direct Hardware Access**: Uses direct NMEA data parsing for GPS communication
- **SIM7600G-H Support**: Specialized integration with SIM7600G-H cellular modems
- **Multi-constellation GNSS**: GPS + GLONASS + Galileo + BeiDou support
- **Enhanced GNSS Data**: Provides satellite counts and precision metrics from multiple constellations

### GPS Data Processing
- **Real-time Coordinates**: Continuous latitude/longitude tracking
- **Altitude Support**: 3D positioning with elevation data
- **Accuracy Metrics**: GPS precision and error measurements
- **Speed/Heading**: Velocity and direction calculations
- **Multi-constellation**: GPS, GLONASS, Galileo, BeiDou satellite support

### Platform Integration
- **Gyropilots.org**: Native integration with flight tracking platform
- **Gapilots.org**: Compatible with alternate tracking platform
- **Non-native Mode**: Uses custom coordinate synchronization protocol
- **Real-time Sync**: Automatic server synchronization with retry logic

## Troubleshooting

### Common Issues

#### GPS Service Not Starting
```bash
# Check service status
sudo systemctl status gps-startup.service

# View service logs for errors
sudo journalctl -u gps-startup.service -f

# Verify Flight Settings configuration
# - Ensure username is set
# - Check GPS start mode selection
```

#### GPS Hardware Issues
```bash
# Check GPS hardware detection
lsusb | grep -i gps
sudo dmesg | grep -i gps

# Check SIM7600G-H GPS status (if using cellular modem)
ls -la /dev/ttyUSB*
sudo minicom -D /dev/ttyUSB3  # AT command interface
# In minicom: AT+CGPS?  # Check GPS status

# Test GPS NMEA data directly
cat /dev/ttyUSB1  # Show raw NMEA sentences from SIM7600G-H
```

#### GPS Signal Problems
```bash
# Check GPS status via AT commands
sudo minicom -D /dev/ttyUSB2 -b 115200

# Enable GPS: AT+CGPS=1
# Check signal: AT+CGPSINFO
# Should return: +CGPSINFO: lat,lon,date,time,alt,speed,course

# Improve signal:
# - Ensure 4G antenna is connected
# - Move to location with clear sky view
# - Wait 2-5 minutes for satellite acquisition
```

#### Platform Synchronization Issues
```bash
# Check internet connectivity
ping -c 3 gyropilots.org

# Test GPS tracker directly
cd ~/flask_app
python3 gps_tracker.py test_user --domain gyropilots.org --simulate --duration 30

# View GPS tracker logs
sudo journalctl -u gps-startup.service | grep -i error
```

### Service Dependencies
The GPS system requires several services to be running:
- **`gps-startup.service`**: For GPS tracking startup (when enabled)
- **NetworkManager.service**: For internet connectivity (if using cellular/WiFi)
- **udev**: For automatic GPS device detection and enablement

### Configuration Files
- **Flight Settings**: Stored in `~/streamerData/settings.json`
- **Service Configuration**: `/etc/systemd/system/gps-startup.service`
- **GPS Daemon**: GPS daemon with integrated initialization and NMEA parsing
- **Python Dependencies**: See `requirements.txt` for all GPS-related packages

## Integration with Flight Recording

### Video Streaming Integration
When enabled in Flight Settings:
- **Synchronized Start**: GPS tracking automatically starts video streaming
- **Synchronized Stop**: Stopping GPS also stops video streaming
- **Recording Coordination**: GPS coordinates embedded in flight recording metadata

### Flight Data
The GPS tracker provides comprehensive flight data:
- **Position**: Real-time latitude/longitude coordinates
- **Altitude**: Barometric and GPS altitude readings
- **Speed**: Ground speed and vertical speed calculations
- **Track**: Heading and course over ground
- **Precision**: GPS accuracy and satellite count information

---

**Note**: GPS tracking functionality is fully integrated with the RPI Streamer web interface. Most users will configure and control GPS tracking through the Flight Settings page rather than command line tools.

# Check status
status = tracker.get_status()
print(f"Tracking active: {status['tracking_active']}")
print(f"Pending coordinates: {status['pending_coordinates']}")
```

## How It Works

The GPS tracker replicates the mobile app's "non-native" tracking mode:

1. **Session Management**: Each tracking session gets a unique track ID based on current timestamp
2. **Coordinate Collection**: GPS points are collected locally with timestamps
3. **Background Sync**: A worker thread automatically syncs coordinates to the server every 2 seconds
4. **Unified GPS Interface**: Single method handles both simulation and real GPS modes
5. **Error Handling**: Network failures are handled gracefully with retry logic
6. **Clean Shutdown**: Remaining coordinates are synced before stopping

### Simulation Details

The simulation mode creates a realistic circular flight path:
- **Starting Point**: Oxford Airport (Kidlington), UK
- **Flight Pattern**: 5km diameter circle completed in 60 seconds  
- **Realistic Parameters**: Altitude variations, GPS accuracy simulation, speed changes

### Server Communication

The tracker communicates with the configured flight server domain (gyropilots.org or gapilots.org) at `/trackflight.php` using:

- `command: 'addpoints'` - Sync GPS coordinates
- `command: 'trackingended'` - Signal end of tracking session

Data format matches exactly what the mobile app sends.

## Configuration

Key configuration parameters (can be modified in `GPSTracker` class):

- `sync_interval = 2.0` - How often to sync coordinates (seconds)
- `sync_timeout = 10.0` - Network request timeout (seconds)
- `sync_threshold = 100` - Max coordinates before forced sync
- `max_retry_attempts = 3` - Retry attempts for failed syncs

## Logging

The tracker logs all activities to:
- Console output
- `gps_tracker.log` file

Log levels: INFO, WARNING, ERROR

## GPS Hardware Setup (Raspberry Pi)

For real GPS tracking, you'll need:

1. **GPS Hardware**: Any NMEA-compatible GPS/GNSS device or SIM7600G-H cellular modem
2. **Connection**: USB, Serial, or HAT connection to Raspberry Pi
3. **Python Dependencies**: python3-serial for direct NMEA communication

Common hardware setup examples:

### USB GPS Receiver
```bash
# Connect USB GPS device and check detection
lsusb | grep -i gps
dmesg | tail

# GPS device usually appears as /dev/ttyUSB0 or /dev/ttyACM0
# For SIM7600G-H: /dev/ttyUSB1 for NMEA data, /dev/ttyUSB3 for AT commands
```

### GPS HAT (Serial)
```bash
# Enable UART in raspi-config for GPS HATs using GPIO serial
sudo raspi-config
# Navigate to: Interfacing Options -> Serial -> Enable

# GPS HAT usually connects to /dev/ttyS0 or /dev/serial0
```

### Installation and Configuration
```bash
# Install python serial library for direct NMEA communication
sudo apt-get update
sudo apt-get install python3-serial

# For SIM7600G-H modems, GPS auto-enable is configured automatically during RPI Streamer installation

# Test GPS functionality
# Test GPS NMEA data directly
cat /dev/ttyUSB1  # Show raw NMEA sentences from SIM7600G-H
```

**Note**: Real GPS hardware support requires proper GPS device setup and direct NMEA communication. The system will gracefully handle missing hardware and provide helpful error messages.

## Integration with RPI Streamer

This GPS tracker can be integrated into the RPI Streamer workflow to provide location tracking alongside video streaming. The tracking runs independently and can be started/stopped via the main application.

## Error Handling

The tracker includes comprehensive error handling for:
- Network connectivity issues
- GPS hardware problems
- Server communication errors
- Invalid GPS data
- System interruptions

All errors are logged with appropriate detail levels.
