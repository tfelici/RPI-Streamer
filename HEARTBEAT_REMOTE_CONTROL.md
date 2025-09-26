# Heartbeat Remote Control Feature

The RPI Streamer heartbeat daemon now supports remote device control via heartbeat responses from the server.

## How It Works

1. **Heartbeat Request**: Every 5 seconds, the device sends system stats to the heartbeat server at `https://streamer.lambda-tek.com/heartbeat.php`

2. **Server Response**: The server can include commands in its JSON response

3. **Command Processing**: The daemon processes any commands received and executes them locally

4. **Local API Calls**: Commands are executed by making API calls to the local web server (e.g., `http://localhost:80/gps-control`)

## Command Format

The server should respond with JSON containing command instructions:

```json
{
    "command": "command_type",
    "action": "action_name"
}
```

## Supported Commands

### GPS Control

Control GPS tracking remotely:

#### Start GPS Tracking
```json
{
    "command": "gps-control",
    "action": "start"
}
```

#### Stop GPS Tracking
```json
{
    "command": "gps-control",
    "action": "stop"
}
```

## Implementation Details

### Code Changes

The following functions were added to `heartbeat_daemon.py`:

- `process_server_command(response_data)`: Main command dispatcher
- `handle_gps_control_command(action, command_data)`: GPS control handler

### API Integration

Commands are executed by making POST requests to the local web server endpoints:

```python
response = requests.post(
    'http://localhost:80/gps-control',
    json={'action': 'start'},
    timeout=30
)
```

### Error Handling

- Network errors are logged but don't crash the daemon
- Unknown commands are logged as warnings
- Invalid command formats are handled gracefully
- API call failures are logged with full error details

### Logging

All command processing is logged with appropriate levels:

- `INFO`: Successful command execution
- `WARNING`: Unknown commands or invalid actions  
- `ERROR`: Network failures or processing errors

## Testing

Run the test script to verify functionality:

```bash
python test_heartbeat_commands.py
```

Note: Tests will show network errors if the main app isn't running, which is expected.

## Adding New Commands

To add support for new commands:

1. Add a new command handler function (follow the pattern of `handle_gps_control_command`)
2. Add the command to the dispatcher in `process_server_command`
3. Update this documentation

### Example: Adding Recording Control

```python
def handle_recording_control_command(action, command_data=None):
    """Handle recording control commands"""
    try:
        if action == 'start':
            response = requests.post(
                'http://localhost:80/recording-control',
                json={'action': 'start'},
                timeout=30
            )
            # Handle response...
        elif action == 'stop':
            response = requests.post(
                'http://localhost:80/recording-control', 
                json={'action': 'stop'},
                timeout=30
            )
            # Handle response...
    except Exception as e:
        logger.error(f"Error handling recording control command: {e}")

# Add to process_server_command:
elif command == 'recording-control':
    handle_recording_control_command(action, response_data)
```

## Security Considerations

- Commands are only processed from the configured heartbeat server
- All API calls are made to localhost only
- Network timeouts prevent hanging
- Full error logging for audit trail
- Commands are executed with daemon privileges (same as heartbeat process)

## Server Implementation

The heartbeat server at `https://streamer.lambda-tek.com/heartbeat.php` needs to be updated to:

1. Accept heartbeat POST requests with device stats
2. Determine if commands should be sent to specific devices
3. Include command JSON in the response when needed

Example PHP server response:
```php
<?php
// Process heartbeat data...
$device_id = $_POST['hwid']; // or however device is identified

// Check if device needs commands
if (should_send_gps_start_command($device_id)) {
    echo json_encode([
        'command' => 'gps-control',
        'action' => 'start'
    ]);
} else {
    // Normal response or empty response
    echo json_encode(['status' => 'ok']);
}
?>
```

## Monitoring

Monitor the heartbeat daemon logs to see command processing:

```bash
tail -f /var/log/heartbeat_daemon.log
```

Look for log entries like:
- `Received command from server: gps-control, action: start`
- `GPS start command executed successfully: Started GPS tracking`