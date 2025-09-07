# RPI Streamer Heartbeat Daemon

This document describes the standalone heartbeat daemon that runs independently of the web server to monitor device health and send status updates to the remote server.

## Overview

The heartbeat daemon (`heartbeat_daemon.py`) is a standalone Python service that:

- Runs independently of the Flask web application
- Sends periodic heartbeat data to the remote server every 5 seconds
- Starts automatically on boot as a systemd service
- Provides robust error handling and graceful shutdown
- Monitors CPU usage, memory usage, temperature, power consumption, and fan RPM

## Features

- **Independent Operation**: No dependency on the web server running
- **Automatic Startup**: Starts on boot via systemd
- **Graceful Shutdown**: Handles SIGTERM and SIGINT signals properly
- **Error Resilience**: Continues running even if heartbeat requests fail
- **Resource Efficient**: Minimal CPU and memory usage
- **Comprehensive Monitoring**: Collects all available system metrics

## Installation

### Automatic Installation

Run the installation script to set up the daemon service:

```bash
cd /path/to/rpi-streamer
chmod +x install_heartbeat_daemon.sh
./install_heartbeat_daemon.sh
```

The installation script will:
1. Make the daemon script executable
2. Install the systemd service file
3. Enable the service for automatic startup
4. Start the service immediately
5. Display status and log information

### Manual Installation

If you prefer to install manually:

1. **Make the daemon executable:**
   ```bash
   chmod +x heartbeat_daemon.py
   ```

2. **Install the systemd service:**
   ```bash
   sudo cp heartbeat-daemon.service /etc/systemd/system/
   sudo systemctl daemon-reload
   ```

3. **Enable and start the service:**
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
- `install_heartbeat_daemon.sh`: Automatic installation script
- `/tmp/heartbeat_daemon.pid`: Runtime process ID file
- `/var/log/journal/`: Service logs (view with journalctl)

## Dependencies

- Python 3.6+
- `psutil` library for system metrics
- `requests` library for HTTP requests
- systemd for service management
