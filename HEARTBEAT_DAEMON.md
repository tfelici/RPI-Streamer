# RPI Streamer Heartbeat Daemon

This document describes the standalone heartbeat daemon that runs independently of the web server to monitor device health and send status updates to the remote server.

## Overview

The heartbeat daemon (`heartbeat_daemon.py`) is a standalone Python service that:

- Runs independently of the Flask web application
- Sends periodic heartbeat data to the remote server every 5 seconds
- Starts automatically on boot as a systemd service
- Provides robust error handling and graceful shutdown
- Monitors CPU usage, memory usage, temperature, power consumption, UPS status, and comprehensive hardware diagnostics via vcgencmd

## Features

- **Independent Operation**: No dependency on the web server running
- **Automatic Startup**: Starts on boot via systemd
- **Graceful Shutdown**: Handles SIGTERM and SIGINT signals properly
- **Error Resilience**: Continues running even if heartbeat requests fail
- **Resource Efficient**: Minimal CPU and memory usage
- **Comprehensive Monitoring**: Collects all available system metrics including UPS power status and vcgencmd hardware diagnostics

## Installation

1. **Install the systemd service:**
   ```bash
   sudo cp heartbeat-daemon.service /etc/systemd/system/
   sudo systemctl daemon-reload
   ```

2. **Enable and start the service:**
   ```bash
   sudo systemctl enable heartbeat-daemon
   sudo systemctl start heartbeat-daemon
   ```

## Service Management

### Check Service Status
```bash
sudo systemctl status heartbeat-daemon
```

### View Live Logs
```bash
sudo journalctl -u heartbeat-daemon -f
```

### Stop the Service
```bash
sudo systemctl stop heartbeat-daemon
```

### Start the Service
```bash
sudo systemctl start heartbeat-daemon
```

### Restart the Service
```bash
sudo systemctl restart heartbeat-daemon
```

### Disable Automatic Startup
```bash
sudo systemctl disable heartbeat-daemon
```

### Enable Automatic Startup
```bash
sudo systemctl enable heartbeat-daemon
```

## Centralized Diagnostics Architecture

Starting with v3.00, the heartbeat daemon serves as the **single source of truth** for all hardware diagnostics and monitoring data. This centralized approach eliminates code duplication and ensures consistent data across all system components.

### Key Components

- **Heartbeat Daemon**: Collects all hardware data and saves to `/tmp/rpi_streamer_heartbeat.json`
- **Flask Application**: Reads from heartbeat JSON file via `/diagnostics` endpoint
- **Admin Console**: Displays real-time hardware status including UPS power monitoring
- **WordPress Integration**: Matches admin console functionality for WordPress environments

### Hardware Data Sources

1. **vcgencmd Integration**: 13 different vcgencmd commands for comprehensive Raspberry Pi diagnostics
2. **UPS Monitoring**: X120X UPS status including battery level, power state, and voltage/current readings
3. **INA219 Power Monitoring**: Precision power consumption measurements
4. **System Metrics**: CPU, memory, temperature, disk usage via standard Linux interfaces

### Data Structure

The heartbeat daemon organizes all diagnostics data in a structured JSON format:
- `diagnostics.vcgencmd.*`: All vcgencmd command outputs
- `diagnostics.ups_*`: UPS status, battery, power metrics
- `diagnostics.power_*`: INA219 power monitoring data
- `system.*`: Standard system metrics (CPU, memory, disk, etc.)

## Configuration

The daemon uses these configuration constants (modify in `heartbeat_daemon.py` if needed):

- `HEARTBEAT_INTERVAL = 5`: Send heartbeat every 5 seconds
- `HEARTBEAT_URL = 'https://streamer.lambda-tek.com/heartbeat.php'`: Remote server endpoint
- `REQUEST_TIMEOUT = 2.0`: HTTP request timeout in seconds
- `PIDFILE = '/tmp/heartbeat_daemon.pid'`: Process ID file location

## Data Collected

The daemon collects and sends these metrics:

### System Metrics
- **hardwareid**: Unique device identifier (CPU serial or MAC address)
- **cpu**: CPU usage percentage
- **mem**: Memory usage percentage  
- **temp**: CPU temperature (from vcgencmd or thermal zone)
- **power**: Power consumption (if available)
- **fan_rpm**: Fan speed (if available)
- **disk**: Disk usage statistics (percent, used/total GB)
- **connection**: Network connection details (WiFi, Ethernet, 4G, GPS)
- **timestamp**: Unix timestamp of measurement
- **app_version**: RPI Streamer application version

### Hardware Diagnostics (NEW)
- **diagnostics.vcgencmd_***: Complete vcgencmd output including GPU memory, temperature, voltage, throttling status
- **diagnostics.ups_connected**: Boolean - UPS connection status
- **diagnostics.ups_battery_level**: Number - Battery percentage (0-100)
- **diagnostics.ups_power_status**: String - Power state (battery/charging/charged)
- **diagnostics.ups_voltage**: Number - Current voltage reading
- **diagnostics.ups_current**: Number - Current amperage reading
- **diagnostics.power_***: INA219 power monitoring data (voltage, current, power consumption)

### Activity Status (NEW)
- **streaming_active**: Boolean - Is live streaming currently active?
- **tracking_active**: Boolean - Is GPS tracking currently running?
- **recording_active**: Boolean - Is video recording currently in progress?
- **recording_file_size_mb**: Number - Current recording file size in MB (0 if not recording)
- **recording_file_path**: String - Path to current recording file (null if not recording)

## Communication Protocol

The daemon sends heartbeat data to the remote server using HTTP requests:

### Current Method (POST)
- **URL**: `https://streamer.lambda-tek.com/heartbeat.php`
- **Method**: POST
- **Content-Type**: `application/json`
- **Body**: JSON-encoded system metrics
- **Timeout**: 2 seconds (fire-and-forget)

### Legacy Method (GET) - Deprecated
- **URL**: `https://streamer.lambda-tek.com/heartbeat.php?params=<json>`
- **Method**: GET with URL-encoded JSON parameters
- **Still supported** for backward compatibility

**Why POST is preferred:**
- No URL length limits for comprehensive system data
- Better security (data not logged in web server access logs)
- Proper HTTP semantics (POST for sending data)
- No proxy/CDN caching issues

## Error Handling

The daemon is designed to be resilient:

- **Network Errors**: Heartbeat failures are logged but don't stop the daemon
- **System Errors**: Metric collection failures use fallback values
- **Service Restarts**: Systemd automatically restarts the daemon if it crashes
- **Graceful Shutdown**: Properly handles stop signals and cleans up resources

## Security

The service runs with restricted permissions:

- Runs as the `pi` user (not root)
- Limited filesystem access via systemd restrictions
- No network privileges beyond HTTPS requests
- Private temporary directory

## Troubleshooting

### Service Won't Start
1. Check service status: `sudo systemctl status heartbeat-daemon`
2. View logs: `sudo journalctl -u heartbeat-daemon -n 20`
3. Verify file permissions: `ls -la heartbeat_daemon.py`
4. Check Python dependencies: `python3 -c "import psutil, requests"`

### No Heartbeat Data Received
1. Check network connectivity: `ping streamer.lambda-tek.com`
2. Verify daemon is running: `sudo systemctl is-active heartbeat-daemon`
3. Monitor logs for errors: `sudo journalctl -u heartbeat-daemon -f`
4. Test manual execution: `python3 heartbeat_daemon.py`

### High Resource Usage
The daemon is designed to be lightweight. If you notice high resource usage:
1. Check for Python dependency issues
2. Verify no other heartbeat processes are running
3. Review system metrics collection frequency

## Migration from App.py

If you previously had heartbeat functionality in the Flask app:

1. The old heartbeat code has been removed from `app.py`
2. The `/stats` route no longer sends heartbeat data
3. Install and start the daemon service as described above
4. Heartbeat functionality will continue automatically via the daemon

## Files

- `heartbeat_daemon.py`: Main daemon script
- `heartbeat-daemon.service`: Systemd service configuration
- `/tmp/heartbeat_daemon.pid`: Runtime process ID file
- `/var/log/journal/`: Service logs (view with journalctl)

## Dependencies

- Python 3.6+
- `psutil` library for system metrics
- `requests` library for HTTP requests
- systemd for service management
