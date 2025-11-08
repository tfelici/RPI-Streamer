# GPS Auto-Stop Feature Documentation

## Overview

The GPS Auto-Stop feature automatically monitors aircraft movement during flight tracking and stops GPS tracking when the aircraft remains stationary for a configured duration. This is useful for automatically ending flight sessions when the aircraft has landed and stopped moving.

## How It Works

1. **Reference Position**: Sets initial GPS position as reference point when monitoring starts
2. **Movement Detection**: Continuously measures distance from the reference position (not last position)
3. **Threshold Analysis**: If aircraft moves ≥50 meters from reference position, resets reference to new location and restarts timer
4. **Stationary Timer**: Counts time when aircraft stays within 50-meter radius of reference position
5. **Automatic Stop**: After the configured timeout period with no significant movement, automatically stops GPS tracking via API

## Configuration

### Flight Settings Page

Access the auto-stop configuration through the Flight Settings page (`/flight-settings`):

1. **Auto-Stop GPS Tracking on No Movement**: Enable/disable the auto-stop functionality
2. **Auto-Stop Timeout (minutes)**: Set the duration (1-120 minutes) before stopping tracking

### Settings Storage

The configuration is stored in `../streamerData/settings.json`:

```json
{
  "gps_auto_stop_enabled": true,
  "gps_auto_stop_minutes": 10
}
```

## Technical Implementation

### Components

- **gps_auto_stop_monitor.py**: Standalone monitoring script that runs as a background process
- **Integration**: Automatically started/stopped with GPS tracking through app.py
- **API Integration**: Uses the existing `/gps-control` endpoint to stop tracking

### Movement Detection Algorithm

- **Position Sampling**: Checks GPS position every 30 seconds
- **Accuracy Filtering**: Only considers GPS readings with accuracy better than 20 meters
- **Reference-Based Tracking**: Measures distance from reference position, not previous position
- **Distance Threshold**: Considers movement significant if total displacement exceeds 50 meters
- **Timer Reset**: Resets both reference position and stationary timer when significant movement detected
- **Slow Movement Handling**: Accumulates movement over time - slow continuous movement will eventually exceed threshold

### Process Management

The auto-stop monitor runs as a systemd service:

- **Service Name**: `gps-auto-stop.service`
- **Logging**: Output captured by systemd journal (use `journalctl -u gps-auto-stop.service`)
- **Lifecycle**: Started with GPS tracking, stopped when GPS tracking stops
- **Daemon Mode**: Runs with `--daemon` flag for systemd integration

## Usage Examples

### Manual Control

You can manually control the auto-stop service:

```bash
# Check service status
sudo systemctl status gps-auto-stop.service

# Start the service manually
sudo systemctl start gps-auto-stop.service

# Stop the service
sudo systemctl stop gps-auto-stop.service

# View logs
journalctl -u gps-auto-stop.service -f

# Run interactively for testing
python gps_auto_stop_monitor.py
```

### Integration with Flight Tracking

The auto-stop feature integrates seamlessly with existing flight tracking:

1. **Start Flight**: Enable auto-stop in Flight Settings, then start GPS tracking normally
2. **Fly**: The monitor runs in background, detecting movement throughout the flight
3. **Land**: When aircraft stops moving, the countdown timer begins
4. **Auto-Stop**: After the configured timeout, GPS tracking stops automatically

## Configuration Recommendations

### Timeout Duration

- **Short Flights (10-30 minutes)**: Use 5-10 minute timeout
- **Medium Flights (30-60 minutes)**: Use 10-15 minute timeout  
- **Long Flights (1+ hours)**: Use 15-30 minute timeout
- **Training/Pattern Work**: Use 5 minute timeout for quick stops

### Movement Threshold

The 50-meter movement threshold is designed to:
- Ignore GPS noise and minor position variations
- Detect taxiing and ground movement
- Handle slow continuous movement (accumulates distance over time)
- Reliably identify when aircraft has stopped within a reasonable area

## Troubleshooting

### Auto-Stop Not Working

1. **Check Settings**: Verify auto-stop is enabled in Flight Settings
2. **Check Service**: Use `sudo systemctl status gps-auto-stop.service` to verify service is running
3. **Check Logs**: Review logs with `journalctl -u gps-auto-stop.service -f`
4. **GPS Accuracy**: Ensure GPS has good signal (< 20m accuracy)
5. **Movement Detection**: Verify aircraft actually moved >50m total before stopping

### False Triggering

1. **Increase Timeout**: Use longer timeout for flights with extended ground holds
2. **Check GPS Quality**: Poor GPS can cause false movement detection
3. **Review Logs**: Check if movement detection is working correctly

### Monitor Not Starting

1. **Check Service**: Use `sudo systemctl status gps-auto-stop.service` for service status
2. **Check Dependencies**: Verify all Python modules are available
3. **Check GPS Tracking**: Auto-stop only starts when GPS tracking is active
4. **Check Settings**: Ensure auto-stop is enabled in Flight Settings

## Security Considerations

- The monitor only stops GPS tracking, never starts it
- Uses localhost API calls (no external network access)
- Process isolation prevents interference with main GPS tracking
- Graceful shutdown on system signals (SIGTERM, SIGINT)

## Integration with Other Features

### Video Recording

If video recording is linked to GPS tracking (`gps_stream_link` enabled), auto-stop will also stop video recording automatically.

### Power Management  

Auto-stop works independently of the power loss detection feature. Both can be enabled simultaneously for comprehensive automatic flight ending.

### Startup Modes

Auto-stop works with all GPS startup modes:
- Manual start
- Auto start on boot  
- Auto start on motion

## Future Enhancements

Potential improvements for future versions:

- Configurable movement threshold
- Speed-based detection (vs. position-based)
- Integration with accelerometer/gyroscope data
- Multiple timeout stages (warning before stop)
- Email/SMS notifications when auto-stop triggers