# RPI Streamer GPS Tracker

This Python script replicates the background geolocation tracking functionality from the Gyropilots mobile app, using the "non-native" mode for coordinate synchronization.

## Features

- **Background GPS Tracking**: Continuously tracks GPS coordinates and syncs them to the Gyropilots server
- **Automatic Sync**: Coordinates are automatically synchronized with retry logic and error handling
- **Non-Native Mode**: Uses custom coordinate synchronization (matches the iOS/Apple mode from the mobile app)
- **Real GPS Support**: Can work with actual GPS hardware via gpsd
- **Simulation Mode**: Includes GPS simulation for testing without hardware

## Files

- `gps_tracker.py` - Main GPS tracker implementation with unified simulation and real GPS support
- `gps_requirements.txt` - Python dependencies

## Installation

1. Install Python dependencies:
```bash
pip install -r gps_requirements.txt
```

2. For real GPS usage on Raspberry Pi, install and configure gpsd:
```bash
sudo apt-get install gpsd gpsd-clients
sudo systemctl enable gpsd
sudo systemctl start gpsd
```

## Usage

### Simulation Mode

Run with simulated GPS data (Oxford Airport circular flight):
```bash
python gps_tracker.py your_username --simulate --duration 60
```

### Real GPS Hardware Mode

Use with actual GPS hardware (placeholder - currently uses simulation):
```bash
python gps_tracker.py your_username --real-gps
```

### Manual Mode

Run without automatic GPS collection for programmatic control:
```bash
python gps_tracker.py your_username
```

### Command Line Options

**gps_tracker.py:**
- `username` - Required: Your Gyropilots username
- `--host` - Server hostname (default: gyropilots.org)
- `--interval` - GPS update interval in seconds (default: 5.0)
- `--simulate` - Use simulated GPS data (Oxford Airport circular flight)
- `--real-gps` - Use real GPS hardware (placeholder - not yet implemented)
- `--duration` - Duration to run in seconds (for simulation mode)

### Programmatic Usage

```python
from gps_tracker import GPSTracker

# Create tracker instance
tracker = GPSTracker('your_username')

# Start tracking session
tracker.start_tracking()

# Option 1: Start GPS simulation mode
tracker.start_gps_tracking(update_interval=5.0, simulate=True)

# Option 2: Start real GPS mode (placeholder)
tracker.start_gps_tracking(update_interval=5.0, simulate=False)

# Option 3: Add GPS coordinates manually
tracker.add_location(
    latitude=40.7128,
    longitude=-74.0060,
    altitude=10.0,
    accuracy=5.0,
    heading=90.0,
    speed=25.0
)

# Stop tracking (coordinates are automatically synced)
tracker.stop_tracking()

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

The tracker communicates with `https://gyropilots.org/trackflight.php` using:

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

1. **GPS Hardware**: USB GPS dongle or HAT with GPS module
2. **gpsd**: GPS daemon to interface with hardware (when implemented)
3. **Proper Permissions**: User must be in dialout group

Example setup for future implementation:
```bash
# Add user to dialout group
sudo usermod -a -G dialout $USER

# Configure gpsd (adjust device path as needed)
sudo nano /etc/default/gpsd
# Set: DEVICES="/dev/ttyUSB0"
# Set: GPSD_OPTIONS="-n"

# Restart gpsd
sudo systemctl restart gpsd

# Test GPS connection
cgps -s
```

**Note**: Real GPS hardware support is currently a placeholder. The `--real-gps` option uses simulation data until actual GPS integration is implemented.

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
