# SIM7600 Daemon Communication Manager

## Overview

The `sim7600_manager.py` module provides a daemon-based, system-wide singleton for communicating with the SIM7600G-H modem to prevent serial port conflicts between different parts of the application.

## Problem Solved

Multiple processes were trying to access the SIM7600's serial port (`/dev/ttyUSB2`) simultaneously:
- **GPS Tracker** (`gps_tracker.py`) - for GPS functionality  
- **Web Application** (`app.py`) - for network status monitoring
- **GPS Startup Manager** (`gps_startup_manager.py`) - for motion detection

This caused **multi-process conflicts** where different processes would interfere with each other's GPS connections.

## Solution: System-Wide Daemon

The new architecture uses a **daemon service** with **TCP-based IPC** to provide system-wide singleton access to the SIM7600 hardware.

### Key Features

- **System-wide singleton**: Single daemon process manages hardware
- **Multi-process safe**: Multiple client processes can safely share hardware
- **Thread-safe communication**: All AT commands are properly serialized
- **TCP-based IPC**: Cross-platform communication between daemon and clients
- **Automatic error recovery**: Handles connection failures and timeouts gracefully
- **Automatic GPS management**: GPS automatically starts on daemon connection and runs continuously
- **Enhanced GNSS support**: GPS + GLONASS + Galileo + BeiDou with DOP-based accuracy

## Architecture

```
┌─────────────────┐    TCP     ┌──────────────────┐    Serial    ┌─────────────┐
│   Client Apps   │ ◄──────► │  SIM7600 Daemon  │ ◄─────────► │  SIM7600G-H │
│ (gps_tracker,   │  Socket   │                  │    Port     │   Hardware  │
│  app.py, etc.)  │           │  (Port 7600)     │             │             │
└─────────────────┘           └──────────────────┘             └─────────────┘
```

## GPS Management

The daemon now handles GPS lifecycle automatically:

- **Auto-start**: GPS is automatically started when the daemon establishes connection with the SIM7600 hardware
- **Continuous operation**: GPS runs continuously for all clients to access without conflicts
- **Auto-stop**: GPS is only stopped when the daemon shuts down
- **Multi-client safe**: Multiple clients can safely access GPS data simultaneously
- **No manual control**: Clients no longer need to start/stop GPS - just call `get_gnss_location()`

This prevents race conditions and conflicts between multiple processes trying to control GPS independently.

## Usage

### 1. Start the Daemon

```bash
# Start the SIM7600 daemon service
python sim7600_manager.py --daemon --host localhost --port 7600

# With verbose logging
python sim7600_manager.py --daemon --verbose

# Custom host/port
python sim7600_manager.py --daemon --host 0.0.0.0 --port 8000
```

### 2. Client Applications

#### Basic Network Status

```python
from sim7600_manager import get_sim7600_client

# Create client connection to daemon
client = get_sim7600_client()

# Get network status
status = client.get_network_status()
print(f"Connected: {status.get('connected')}")
print(f"Signal: {status.get('signal_strength')}")
print(f"Operator: {status.get('operator')}")
print(f"Network Type: {status.get('network_type')}")
```

#### GPS/GNSS Location

```python
from sim7600_manager import get_sim7600_client

client = get_sim7600_client()

# GPS is automatically started by the daemon when it connects
# Just get location data directly
success, location = client.get_gnss_location()
if success and location and location.get('fix_status') == 'valid':
    print(f"Location: {location['latitude']}, {location['longitude']}")
    print(f"Altitude: {location['altitude']} meters")
    print(f"Speed: {location['speed']} km/h")
    print(f"Accuracy (HDOP): {location['hdop']}")
    print(f"Satellites: {location['satellites']['total']}")
    print(f"Fix Type: {location['fix_type']}")
else:
    print("No GPS fix available")

# GPS runs continuously and is managed automatically by the daemon
```

#### Remote Connection

```python
from sim7600_manager import get_sim7600_client

# Connect to daemon on different host
client = get_sim7600_client(host='192.168.1.100', port=7600)

# Check if daemon is available
if client.is_available():
    status = client.get_network_status()
    print(f"Remote SIM7600 status: {status}")
```

## Service Integration

### systemd Service (Linux)

Create `/etc/systemd/system/sim7600-daemon.service`:

```ini
[Unit]
Description=SIM7600 Communication Daemon
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/path/to/rpi-streamer
ExecStart=/usr/bin/python3 sim7600_manager.py --daemon --host localhost --port 7600
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable sim7600-daemon
sudo systemctl start sim7600-daemon
sudo systemctl status sim7600-daemon
```

### Docker Container

```dockerfile
FROM python:3.9-slim

WORKDIR /app
COPY sim7600_manager.py .
RUN pip install pyserial

EXPOSE 7600

CMD ["python", "sim7600_manager.py", "--daemon", "--host", "0.0.0.0", "--port", "7600"]
```

## API Reference

### SIM7600Client Methods

#### `get_network_status() -> Dict[str, Any]`
Returns comprehensive network status information.

**Returns:**
```python
{
    "connected": bool,
    "signal_strength": str,  # e.g., "-67 dBm"
    "operator": str,         # e.g., "Verizon"
    "network_type": str,     # "LTE", "3G", "2G"
    "registration_status": int
}
```

#### `get_gnss_location() -> Tuple[bool, Optional[Dict[str, Any]]]`
Get enhanced GNSS location data. GPS is automatically started by the daemon.

**Returns:** `(success: bool, location_data: dict)`

**Location Data Structure:**
```python
{
    'mode': int,                    # 2=2D fix, 3=3D fix
    'fix_type': str,               # "2D", "3D", "Unknown"
    'satellites': {
        'gps': int,                # GPS satellites
        'glonass': int,            # GLONASS satellites  
        'beidou': int,             # BeiDou satellites
        'total': int               # Total satellites
    },
    'latitude': float,             # Decimal degrees
    'longitude': float,            # Decimal degrees
    'altitude': float,             # Meters above sea level
    'speed': float,                # km/h
    'course': float,               # Degrees from north
    'pdop': float,                 # Position dilution of precision
    'hdop': float,                 # Horizontal dilution of precision
    'vdop': float,                 # Vertical dilution of precision
    'fix_status': str,             # "valid", "no_fix", "invalid"
    'source': str                  # "GNSS"
}
```

#### `is_available() -> bool`
Check if SIM7600 daemon is available and responsive.

#### `close()`
Close connection to daemon (automatically called on destruction).

### Factory Functions

#### `get_sim7600_client(host='localhost', port=7600) -> SIM7600Client`
Create a client connection to the SIM7600 daemon.

#### `get_sim7600_daemon(**kwargs) -> SIM7600Daemon`
Get the daemon singleton instance (for daemon process only).

## Configuration

### Daemon Configuration

The daemon can be configured via constructor parameters:

```python
# Custom serial port and network settings
daemon = SIM7600Daemon(
    port='/dev/ttyUSB3',           # Custom serial port
    baud_rate=115200,              # Baud rate
    timeout=2,                     # Command timeout
    host='0.0.0.0',               # Bind to all interfaces
    daemon_port=8000               # Custom TCP port
)
```

### Client Configuration

```python
# Connect to specific daemon
client = SIM7600Client(host='192.168.1.100', port=8000)
```

## Troubleshooting

### Daemon Not Starting

**Problem**: Daemon fails to start with "Address already in use"
**Solution**: 
```bash
# Check if port is in use
sudo netstat -tulpn | grep 7600

# Kill existing process
sudo pkill -f sim7600_manager.py

# Or use different port
python sim7600_manager.py --daemon --port 7601
```

### Client Cannot Connect

**Problem**: `Failed to connect to SIM7600 daemon`
**Solution**:
1. Ensure daemon is running: `ps aux | grep sim7600_manager`
2. Check network connectivity: `telnet localhost 7600`
3. Verify firewall settings
4. Check daemon logs

### Serial Port Access

**Problem**: `Permission denied: '/dev/ttyUSB2'`
**Solution**:
```bash
# Add user to dialout group
sudo usermod -a -G dialout $USER
logout  # Re-login required

# Or run daemon with sudo (not recommended for production)
sudo python sim7600_manager.py --daemon
```

### GPS Not Working

**Problem**: `get_gnss_location()` returns `fix_status: "no_fix"`
**Solution**:
1. GPS is automatically started by daemon - no manual start needed
2. Wait for satellite acquisition (can take 30-60 seconds outdoors)
3. Check antenna connection
4. Verify SIM7600 module GPS capability
5. Ensure daemon is running and connected to hardware

## Migration from Old Manager

### Old Code (Direct Manager)
```python
from sim7600_manager import get_sim7600_manager
manager = get_sim7600_manager()
status = manager.get_network_status()
```

### New Code (Daemon Client)
```python
from sim7600_manager import get_sim7600_client
client = get_sim7600_client()
status = client.get_network_status()
```

The API is identical - just replace `get_sim7600_manager()` with `get_sim7600_client()` and ensure the daemon is running.

## Logging

The daemon produces detailed logs for debugging:

```bash
# Start daemon with verbose logging
python sim7600_manager.py --daemon --verbose

# View logs in systemd
journalctl -u sim7600-daemon -f

# Logs are also written to file in daemon mode
tail -f /var/log/sim7600_daemon.log
```

## Security Considerations

- **Network Access**: Daemon binds to localhost by default for security
- **No Authentication**: Currently no authentication between client/daemon
- **Process Isolation**: Daemon runs as separate process with minimal privileges
- **Firewall**: Ensure port 7600 is not exposed to untrusted networks

For production deployments, consider:
- Running daemon as dedicated user
- Using Unix domain sockets instead of TCP (Linux only)
- Implementing authentication tokens
- Network firewall rules
