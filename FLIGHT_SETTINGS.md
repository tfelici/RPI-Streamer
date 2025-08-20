# Flight Settings Documentation

## Overview

The Flight Settings feature provides comprehensive GPS tracking configuration and control for the RPI Streamer system. This includes username management, aircraft registration, automated streaming control, and flexible GPS tracking startup modes.

## Features

### 1. User Configuration
- **Username**: Set the username for GPS tracking identification
- **Aircraft Registration**: Record your aircraft's registration number (e.g., N123AB, G-ABCD)

### 2. Video Streaming Integration
- **Auto Start/Stop Streaming**: Option to automatically control video streaming when GPS tracking starts/stops
- When enabled: Starting GPS tracking will also start video streaming, and stopping GPS tracking will stop video streaming
- When disabled: GPS tracking and video streaming operate independently

### 3. GPS Tracking Start Modes

#### Manual Mode (Default)
- GPS tracking starts only when you press the "Start GPS Tracking" button in the web interface
- Provides full user control over when tracking begins

#### Auto Start on Boot
- GPS tracking starts automatically when the system boots up
- Useful for aircraft that should always be tracked when powered on
- Managed by systemd service `gps-startup.service`

#### Auto Start on Motion
- GPS tracking starts automatically when aircraft movement is detected
- Uses motion detection algorithms to sense when the aircraft begins moving
- Ideal for reducing unnecessary tracking during ground operations

## Installation

### 1. GPS Startup Service Installation

The GPS startup service is automatically installed when you run the main RPI Streamer installation script:

```bash
sudo bash install_rpi_streamer.sh
```

This installer will:
- Install all RPI Streamer components including the GPS startup manager
- Create the GPS startup systemd service (but not enable it by default)
- Set up proper permissions and paths for GPS functionality
- Display installation status and configuration instructions

### 2. Configure Flight Settings

1. Navigate to the "Flight Settings" page in the web interface
2. Set your GPS username (required for GPS tracking)
3. Optionally set your aircraft registration
4. Choose your preferred GPS start mode
5. Enable/disable automatic streaming control
6. Save your settings

## Usage

### Web Interface

The Flight Settings page (`/flight-settings`) provides a user-friendly interface to configure all options:

- **Username**: Required for GPS tracking functionality
- **Aircraft Registration**: Optional identification field
- **Video Streaming Control**: Toggle automatic streaming integration
- **GPS Start Mode**: Choose from Manual, Auto (Boot), or Auto (Motion)

### Home Dashboard

The home page displays current flight settings and GPS status:
- Username and aircraft registration
- Current start mode
- Stream link status
- Real-time GPS tracking status

### Manual Control

Regardless of the start mode, you can always manually control GPS tracking via:
- Web interface "Start/Stop GPS Tracking" button
- System commands (if needed)

## Service Management

### GPS Startup Service

The `gps-startup.service` is automatically installed with the main RPI Streamer installer and is managed based on your flight settings:

- **Manual Mode**: Service is disabled
- **Boot/Motion Mode**: Service is enabled and runs at startup

The service integrates seamlessly with the flight settings configuration.

### Manual Service Control

If needed, you can manually control the service:

```bash
# Check status
sudo systemctl status gps-startup.service

# View logs
sudo journalctl -u gps-startup.service -f

# Manual control (not recommended - use flight settings instead)
sudo systemctl enable gps-startup.service
sudo systemctl start gps-startup.service
sudo systemctl stop gps-startup.service
sudo systemctl disable gps-startup.service
```

## Configuration Files

### Flight Settings Storage
Settings are stored in `streamerData/settings.json`:

```json
{
  "gps_username": "your_username",
  "aircraft_registration": "N123AB",
  "gps_stream_link": false,
  "gps_start_mode": "manual"
}
```

### Service Files
- `/etc/systemd/system/gps-startup.service`: Systemd service configuration
- `gps_startup_manager.py`: Python script that handles automatic GPS startup
- `gps_tracker.py`: Core GPS tracking functionality

## Motion Detection

The motion detection feature is designed to be extensible. Currently, it includes:

- Placeholder motion detection logic
- Configurable sensitivity thresholds
- Integration with GPS tracking startup

### Future Enhancements
- Accelerometer sensor integration
- GPS coordinate change monitoring
- Vibration pattern detection
- Customizable motion sensitivity settings

## Troubleshooting

### GPS Tracking Won't Start
1. Verify username is configured in Flight Settings
2. Check that `gps_tracker.py` is executable
3. Review system logs: `sudo journalctl -u gps-startup.service`

### Service Not Starting on Boot
1. Ensure the service is installed: `sudo systemctl list-unit-files | grep gps-startup`
2. Check service status: `sudo systemctl status gps-startup.service`
3. Verify GPS start mode is set to "boot" or "motion" in Flight Settings

### Motion Detection Not Working
1. Motion detection is currently a placeholder implementation
2. Check logs for motion detection activity
3. Consider implementing actual sensor integration for your specific hardware

## Security Considerations

- The service runs as the `pi` user for security
- GPS tracking data is handled according to your configured endpoints
- Service logs may contain sensitive location information
- Consider log rotation and retention policies for production use

## Integration with Existing Features

### Stream Control
- When stream linking is enabled, GPS tracking will automatically control video streaming
- Manual stream control remains available regardless of GPS settings
- Stream status is updated in real-time on the dashboard

### Recording Management
- GPS tracking coordinates with existing recording functionality
- Flight settings do not affect existing recording configurations
- USB storage integration continues to work normally

## API Endpoints

The flight settings functionality adds the following endpoints:

- `GET /flight-settings`: Display flight settings page
- `POST /flight-settings`: Save flight settings configuration
- `POST /gps-control`: Start/stop GPS tracking (enhanced with stream integration)

These integrate seamlessly with existing RPI Streamer API endpoints.
