# Service Monitoring & Log Viewer Feature

This document describes the real-time service monitoring and log viewing features added to the RPI Streamer home page.

## Overview

The Service Monitoring section provides real-time status updates and log monitoring for critical system services:
- **GPS Daemon** - Core GPS data processing service
- **GPS Startup** - GPS initialization and management service  
- **MediaMTX** - Media streaming server
- **Heartbeat Daemon** - System health monitoring service

## Features

### 1. Real-time Service Status
- Live status updates every 3 seconds
- Color-coded status indicators
- Tooltip information with service details

### 2. Real-time Log Monitoring
- View live service logs in a dedicated viewer
- Auto-scrolling log display with syntax highlighting
- Pause/resume log streaming
- Clear log buffer
- Last 200 lines shown initially, then real-time updates

## Implementation Details

### Frontend Components

#### HTML Structure
- Service monitoring section with status indicators and log buttons
- Integrated log viewer with controls (clear, pause/resume, close)
- Grid layout showing service status and action buttons

#### CSS Styling
- Dark theme log viewer with syntax highlighting
- Color-coded log entries (error=red, warning=yellow, info=blue, success=green)
- Responsive design with proper scrolling
- Visual status indicators with animations

#### JavaScript Functionality
- **Service Status Updates**: Real-time via Server-Sent Events (SSE)
- **Log Viewer**: Dedicated SSE connection for each service
- **Auto-scrolling**: Keeps latest logs visible
- **Log Management**: Automatic cleanup (keeps last 1000 entries)
- **Error Handling**: Graceful connection management and reconnection

### Backend Components

#### Flask Endpoints

**`/service-status-sse`**
- Real-time service status updates via SSE
- Updates every 3 seconds
- JSON response with detailed service information

**`/service-logs-sse/<service>`**
- Real-time log streaming for specific service
- Uses `journalctl -f` for live log following
- JSON output with timestamps and log levels
- Auto-cleanup after 5 minutes of inactivity

#### Core Functions

**`get_service_status()`**
- Uses `systemctl` commands for service state checking
- Handles multiple service states and error conditions
- Returns detailed status information

**`get_service_logs(service, lines=50)`**
- Uses `journalctl` to retrieve service logs
- JSON parsing for structured log data
- Timestamp conversion and formatting

### Service Status Response Format

```json
{
  "gps-daemon": {
    "status": "active",
    "active_state": "active", 
    "sub_state": "running",
    "load_state": "loaded"
  },
  "mediamtx": {
    "status": "inactive",
    "active_state": "inactive",
    "sub_state": "dead", 
    "load_state": "loaded"
  }
}
```

### Log Entry Response Format

```json
{
  "type": "log",
  "line": "GPS daemon started successfully",
  "timestamp": 1693737600000
}
```

## Usage

### Viewing Service Status
1. Navigate to home page and expand "Service Monitoring" section
2. Status updates automatically every 3 seconds
3. Hover over status for additional details

### Viewing Service Logs
1. Click "📋 View Logs" button next to any service
2. Log viewer opens with last 50 lines
3. New log entries appear in real-time
4. Use controls to pause, clear, or close viewer

### Log Viewer Controls
- **Clear**: Remove all current log entries from display
- **Pause/Resume**: Stop/start real-time log updates
- **Close**: Close log viewer and stop log streaming

## Visual Indicators

### Service Status Colors
- 🟢 **Green**: Service is active and running
- 🔴 **Red**: Service is inactive, failed, or has errors
- 🟡 **Yellow**: Service is in transitional state
- 🔵 **Blue**: Service is in other known states
- ⚪ **Gray**: Service status unavailable or unknown

### Log Entry Colors
- **Red**: Error messages and failures
- **Yellow**: Warning messages
- **Blue**: Informational messages
- **Green**: Success and completion messages
- **White**: Standard log entries

## Technical Benefits

1. **Real-time Monitoring**: Immediate visibility into service health and activity
2. **Integrated Design**: Seamless integration with existing home page
3. **Efficient Streaming**: Uses SSE for minimal bandwidth overhead
4. **Smart Log Management**: Automatic cleanup and scrolling
5. **User-Friendly Controls**: Intuitive log viewer with pause/resume
6. **Robust Error Handling**: Graceful degradation and reconnection
7. **Performance Optimized**: Limited log buffer and connection timeouts

## Dependencies

- **systemctl**: Linux systemd service manager
- **journalctl**: systemd journal log viewer
- **subprocess**: Python module for system commands
- **Server-Sent Events**: Browser support for real-time updates
- **JSON**: Data serialization for structured responses

## Configuration

### Log Display Settings
- **Initial Lines**: 50 (configurable in `get_service_logs()`)
- **Max Buffer**: 1000 entries (configurable in JavaScript)
- **Update Frequency**: Real-time via journalctl follow
- **Connection Timeout**: 5 minutes for log streams

### Service List
Services are defined in both frontend and backend:
```python
services = ['gps-daemon', 'gps-startup', 'mediamtx', 'heartbeat-daemon']
```

## Future Enhancements

1. **Log Filtering**: Search and filter log entries by keyword or level
2. **Log Download**: Export log entries to file
3. **Service Control**: Start/stop/restart buttons for services
4. **Historical Logs**: Browse logs by date/time range
5. **Multiple Services**: View logs from multiple services simultaneously
6. **Log Alerts**: Notifications for error conditions
7. **Performance Metrics**: CPU/memory usage integration
8. **Custom Services**: User-configurable service list
