# GPS Control Command Structure - Consistency Verification

## Overview
The GPS control system maintains consistent JSON command structures throughout the entire flow from web UI to device execution.

## Command Flow Structure

### 1. Web UI → Server (heartbeat.php)
**Request Format:**
```javascript
// FormData sent via POST to heartbeat.php
{
    "command": "queue_device_command",     // Fixed command type for all device commands
    "hardwareid": "device-id",             // Target device identifier  
    "device_command": "gps-control",       // The actual command type
    "device_action": "start" | "stop"      // The action to perform
}
```

### 2. Server Storage (heartbeat.php queue)
**Queue File Format (`data/commands_{hardwareid}.json`):**
```json
[
    {
        "command": "gps-control",           // Converted from device_command
        "action": "start",                  // Converted from device_action
        "timestamp": 1727344800,            // Unix timestamp
        "queued_at": "2025-09-26 12:00:00" // Human-readable timestamp
    }
]
```

### 3. Server → Device (heartbeat response)
**JSON Response Format:**
```json
{
    "command": "gps-control",               // Command type
    "action": "start",                      // Action to perform
    "timestamp": 1727344800,                // Original timestamp
    "queued_at": "2025-09-26 12:00:00"     // When command was queued
}
```

### 4. Device Processing (heartbeat_daemon.py)
**Processing Logic:**
```python
def process_server_command(response_data):
    command = response_data.get('command')    # 'gps-control'
    action = response_data.get('action')      # 'start' or 'stop'
    
    if command == 'gps-control':
        handle_gps_control_command(action, response_data)
```

### 5. Local API Call (Flask app)
**Final API Request:**
```python
# POST to http://localhost:80/gps-control
{
    "action": "start"  # Only the action is needed for the local API
}
```

## Consistency Verification ✅

| Component | Command Field | Action Field | Status |
|-----------|---------------|--------------|---------|
| Web UI → Server | `device_command: "gps-control"` | `device_action: "start"` | ✅ Consistent |
| Server Queue | `command: "gps-control"` | `action: "start"` | ✅ Consistent |
| Server → Device | `command: "gps-control"` | `action: "start"` | ✅ Consistent |
| Device Processing | `command: "gps-control"` | `action: "start"` | ✅ Consistent |
| Local API | N/A | `action: "start"` | ✅ Consistent |

## Key Design Decisions

1. **Parameter Naming Convention:**
   - Web UI uses `device_command` and `device_action` to be explicit about queuing device commands
   - Internal system uses `command` and `action` for cleaner JSON structure
   - heartbeat.php handles the translation between these naming conventions

2. **Command Types:**
   - `gps-control` is the standardized command type for all GPS operations
   - Actions are `start` and `stop` for GPS tracking control
   - Structure allows easy addition of new command types (e.g., `recording-control`, `system-control`)

3. **Timestamp Handling:**
   - Unix timestamp for programmatic processing
   - Human-readable timestamp for debugging and logging
   - Commands include both original queue time and processing time

4. **Error Handling:**
   - Consistent JSON error responses throughout the chain
   - Proper HTTP status codes (400 for bad requests, 200 for success)
   - Comprehensive logging at each step

## Future Command Extensions

The structure is designed to easily support additional commands:

```json
{
    "command": "recording-control",
    "action": "start",
    "quality": "1080p",
    "duration": 3600
}
```

```json
{
    "command": "system-control", 
    "action": "reboot",
    "delay_seconds": 30
}
```

All new commands follow the same consistent structure and flow through the same processing pipeline.