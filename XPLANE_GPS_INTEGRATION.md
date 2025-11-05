# X-Plane GPS Integration

## Overview

The GPS daemon has been extended to support multiple GPS data sources, including integration with X-Plane flight simulator. This allows you to use your RPI Streamer system with X-Plane for flight simulation recording and tracking.

## Supported GPS Sources

1. **Hardware GPS** (Default) - Uses the SIM7600G-H modem's GPS receiver
2. **X-Plane Simulator** - Receives GPS data from X-Plane via UDP broadcast
3. **Built-in Simulation** - Generates simulated circular flight pattern for testing

## Configuration

### Flight Settings Page

GPS source selection is available in the Flight Settings page (`/flight-settings`):

1. **GPS Data Source** dropdown with three options:
   - SIM7600G-H Hardware GPS (Default)
   - X-Plane Simulator UDP Broadcast  
   - Built-in GPS Simulation (Testing)

2. **X-Plane UDP Configuration** (shown when X-Plane is selected):
   - **UDP Port**: Port to listen for X-Plane data (default: 49003)
   - **Bind Address**: IP address to bind listener (default: 0.0.0.0 for all interfaces)

### Settings File

The GPS source is stored in `../streamerData/settings.json`:

```json
{
  "gps_source": "xplane",
  "xplane_udp_port": 49003, 
  "xplane_bind_address": "0.0.0.0"
}
```

### Command Line Override

You can override the GPS source when running the daemon manually:

```bash
# Use X-Plane GPS with custom port
python3 gps_daemon.py --gps-source=xplane --xplane-port=49004

# Use simulation mode
python3 gps_daemon.py --gps-source=simulation --delay=30

# Use hardware GPS (default)
python3 gps_daemon.py --gps-source=hardware
```

## X-Plane Setup

### 1. Enable Data Output in X-Plane

1. Open X-Plane
2. Go to **Settings** → **Data Output**
3. Find **"GPS position"** in the list (usually around index 20)
4. Check the **"UDP"** checkbox for GPS position data
5. Set the IP address to your RPI Streamer's IP address
6. Set the port to 49003 (or your custom port)

### 2. Network Configuration

- Ensure X-Plane and RPI Streamer are on the same local network
- The RPI Streamer will listen on all network interfaces by default (0.0.0.0)
- Default UDP port is 49003 (standard X-Plane data output port)

### 3. Verify Connection

Check the GPS daemon status to confirm X-Plane data reception:

```bash
sudo systemctl status gps-daemon.service
sudo journalctl -u gps-daemon.service -f
```

Look for messages like:
- "X-Plane GPS mode starting - listening on 0.0.0.0:49003"
- "Successfully bound to UDP port 49003, waiting for X-Plane data..."
- "GPS daemon status: xplane_data_valid"

## Data Format

The X-Plane integration supports:

- **Position**: Latitude, longitude, altitude (MSL)
- **Velocity**: Ground speed (converted from knots to km/h)  
- **Heading**: True heading in degrees
- **Simulated GPS metadata**: Accuracy values, satellite counts, fix status

The system processes X-Plane's UDP "DATA" packets and extracts:
- **Index 20**: GPS position data (lat/lon/altitude)
- **Index 18**: Velocities data (ground speed)
- **Index 17**: Attitude data (heading)

## Service Management

The GPS daemon automatically uses the configured GPS source from settings. When you change the GPS source in Flight Settings, the system will:

1. Update the settings.json file
2. Restart the gps-startup.service to pick up new settings
3. The GPS daemon will switch to the new source automatically

No manual service reconfiguration is needed.

## Troubleshooting

### X-Plane Not Connecting

1. **Check network connectivity**: Ensure X-Plane and RPI Streamer are on same LAN
2. **Verify X-Plane data output**: Confirm GPS position UDP output is enabled  
3. **Check port conflicts**: Ensure port 49003 isn't used by other applications
4. **Firewall**: Ensure UDP port 49003 is open on RPI Streamer

### GPS Daemon Status

Monitor daemon status via the web interface or command line:

```bash
# Real-time log monitoring
sudo journalctl -u gps-daemon.service -f

# Check current status
sudo systemctl status gps-daemon.service
```

### Status Messages

- `xplane_connecting`: Starting X-Plane UDP listener
- `xplane_listening`: Waiting for X-Plane data packets  
- `xplane_data_valid`: Successfully receiving X-Plane GPS data
- `xplane_timeout`: No data received from X-Plane (>30 seconds)
- `xplane_no_connection`: Cannot bind to UDP port
- `xplane_error`: General X-Plane integration error

## Benefits

- **Flight Simulation Recording**: Record simulated flights with video overlay
- **Route Testing**: Test GPS tracking and recording without actual flight
- **Training**: Practice with GPS systems using realistic flight data  
- **Development**: Develop and test GPS-based features safely on the ground

## Limitations

- X-Plane must be running and configured to send UDP data
- Only works on local network (no internet-based connection)
- Dependent on X-Plane's data output accuracy and timing
- May have different precision characteristics compared to real GPS hardware