# RPI Streamer Flight Settings v3.00

## Overview

Flight Settings provides comprehensive configuration and control for GPS tracking functionality in the RPI Streamer system. This feature enables seamless integration with flight tracking platforms (Gyropilots.org and Gapilots.org) with flexible startup modes, vehicle registration, and automated streaming coordination using the modern GPS daemon architecture.

## Core Features

### 🛰️ GPS Tracking Configuration
- **Username Management**: Required identification for flight tracking platforms
- **Vehicle Registration**: Aircraft identification (tail numbers, registration codes)
- **Platform Selection**: Support for configurable tracking platforms (Gyropilots.org, Gapilots.org, or custom servers)
- **Real-time Status**: Live GPS tracking status and coordinate monitoring

### ⚙️ Startup Mode Selection
- **Manual Control**: User-initiated GPS tracking via web interface
- **Auto Start on Boot**: Automatic tracking when system powers on
- **Auto Start on Motion**: Motion-triggered tracking for efficient operation
- **Service Integration**: Seamless systemd service management

### 📹 Video Streaming Coordination
- **Integrated Control**: Link GPS tracking with video streaming
- **Synchronized Operation**: Start/stop both services together
- **Independent Operation**: Option to control GPS and streaming separately
- **Recording Metadata**: GPS coordinates embedded in flight recordings

## Configuration Options

### GPS Tracking Setup
1. **Access Flight Settings**: Navigate to `/flight-settings` in the RPI Streamer web interface
2. **Username Configuration**: Enter your tracking platform username (required)
3. **Vehicle Registration**: Optionally enter aircraft registration (e.g., N123AB, G-ABCD)
4. **Platform Selection**: Enter your preferred tracking platform domain (e.g., gyropilots.org, gapilots.org, or custom server)
5. **Save Configuration**: Apply settings to enable GPS tracking functionality

### Startup Mode Configuration

#### Manual Mode (Default)
- **Description**: GPS tracking controlled exclusively through web interface
- **Service State**: `gps-startup.service` disabled
- **Control Method**: "Start/Stop GPS Tracking" buttons in dashboard
- **Use Cases**: Testing, selective flight tracking, manual operation preference

#### Auto Start on Boot
- **Description**: GPS tracking begins automatically when system starts
- **Service State**: `gps-startup.service` enabled and started
- **Behavior**: Immediate tracking initiation after power-on
- **Use Cases**: Always-on tracking, fleet aircraft, continuous monitoring

#### Auto Start on Motion
- **Description**: GPS tracking triggered by aircraft movement detection
- **Service State**: `gps-startup.service` enabled with motion detection
- **Smart Activation**: Reduces power consumption during ground operations
- **Use Cases**: Efficient operation, automatic flight detection

### Video Streaming Integration
- **Linked Operation**: Enable to synchronize GPS tracking with video streaming
- **Independent Control**: Disable for separate GPS and streaming control
- **Recording Enhancement**: GPS coordinates automatically embedded in video metadata
- **Bandwidth Optimization**: Coordinate both services for optimal performance

## Service Management

### GPS Startup Service
The `gps-startup.service` is automatically managed based on Flight Settings configuration:

```bash
# Service status varies by configuration:
# Manual Mode: Service disabled
# Auto Boot/Motion: Service enabled

# Check current service status
sudo systemctl status gps-startup.service

# View service logs
sudo journalctl -u gps-startup.service -f
```

### Service Dependencies
GPS tracking requires these services to be operational:
- **NetworkManager.service**: Internet connectivity for platform synchronization
- **udev**: Automatic GPS device detection and enablement
- **`flask_app.service`**: Web interface for configuration and control

## Web Interface Usage

### Flight Settings Page
Access the configuration interface at `/flight-settings`:

#### Configuration Fields
- **GPS Username**: Required for platform integration and flight identification
- **Vehicle Registration**: Optional aircraft identifier (tail number, registration)
- **Platform Domain**: Configure tracking platform domain (gyropilots.org, gapilots.org, or custom server)
- **Start Mode**: Choose Manual, Auto (Boot), or Auto (Motion)
- **Stream Integration**: Toggle automatic video streaming coordination

#### Real-time Status
- **Current Configuration**: Display active username, vehicle, and mode
- **GPS Status**: Live tracking status (Active/Inactive)
- **Service Status**: GPS startup service state
- **Last Update**: Most recent GPS coordinate transmission

### Main Dashboard Integration
The home page displays current flight configuration:

#### Status Display
- **User Information**: Configured username and vehicle registration
- **Tracking Mode**: Currently selected GPS start mode
- **Active Status**: Real-time GPS tracking state
- **Stream Status**: Video streaming coordination status

#### Control Interface
- **Start GPS Tracking**: Manual initiation button (Manual mode)
- **Stop GPS Tracking**: End current tracking session
- **Stream Control**: Independent video streaming controls
- **Settings Link**: Quick access to Flight Settings configuration

## Hardware Integration

### GPS Hardware Integration
Flight Settings automatically integrates with GPS hardware:

- **Universal GPS Support**: Compatible with USB GPS, GPS HATs, and cellular modem GPS
- **GNSS Support**: GPS, GLONASS, Galileo, BeiDou constellations via direct NMEA parsing
- **High Accuracy**: Professional-grade positioning for aviation
- **SIM7600G-H Integration**: Direct cellular modem GPS communication with automatic enablement

### Motion Detection (Auto Motion Mode)
- **GPS-based Motion**: Detects movement using GPS coordinate changes
- **Threshold Configuration**: Customizable distance sensitivity settings
- **Movement Patterns**: Distinguishes between stationary, taxi, and flight phases
- **Power Efficiency**: Minimizes data recording during ground operations

## Troubleshooting

### Configuration Issues
```bash
# Check Flight Settings file
cat ~/streamerData/settings.json

# Verify username configuration
# Settings file should contain GPS username and mode

# Reset configuration (if needed)
rm ~/streamerData/settings.json
# Reconfigure via web interface
```

### GPS Service Problems
```bash
# Check GPS startup service
sudo systemctl status gps-startup.service

# View service logs
sudo journalctl -u gps-startup.service -f

# Manual service restart
sudo systemctl restart gps-startup.service
```

### Platform Connection Issues
```bash
# Test internet connectivity to your configured platform
ping -c 3 [your-configured-domain]

# Check GPS tracker operation with your platform
cd ~/flask_app
python3 gps_tracker.py [username] --domain [your-configured-domain] --simulate --duration 30

# Verify platform credentials
# Ensure username exists on tracking platform
```

## Advanced Configuration

### Manual Service Control
For advanced users requiring direct service management:

```bash
# Enable GPS startup service manually
sudo systemctl enable gps-startup.service
sudo systemctl start gps-startup.service

# Disable automatic startup
sudo systemctl disable gps-startup.service
sudo systemctl stop gps-startup.service

# Check service dependencies
systemctl list-dependencies gps-startup.service
```

### Configuration File Location
Flight Settings are stored in: `~/streamerData/settings.json`

Example configuration:
```json
{
    "gps_username": "pilot123",
    "vehicle_registration": "N123AB",
    "gps_domain": "your-tracking-platform.com",
    "gps_start_mode": "manual",
    "auto_start_stop_streaming": false
}
```

---

**Note**: Flight Settings provide user-friendly configuration for GPS tracking. Most functionality is accessible through the web interface without requiring command line access.
