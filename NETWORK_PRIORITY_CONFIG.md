# Network Interface Priority Configuration for RPI Streamer

## Problem Resolution
With the modem in RNDIS mode (9011), all network interfaces are competing for routing priority. This causes the WiFi hotspot to stop accepting connections when the cellular modem connects.

## Solution: Connection Metrics
NetworkManager uses `connection.metric` to determine routing priority. Lower numbers = higher priority.

## Priority Hierarchy (Metrics)

### 1. Ethernet (Wired Connection) - Metric: 100
- **Highest Priority** - Most reliable, unlimited bandwidth
- Usually configured automatically by install_rpi_streamer.sh
- Should be the primary route when connected

### 2. Cellular Modem (RNDIS) - Metric: 200  
- **Backup Internet** - When ethernet unavailable
- SIM7600G-H in mode 9011 (USB Ethernet/RNDIS)
- Provides internet but may have data limits
- **Configuration**: Automatic during installation

### 3. WiFi Client Connection - Metric: 300
- **Tertiary Internet** - When no ethernet or cellular  
- Connects to external WiFi networks
- **Configuration**: Done in app.py system_settings_wifi()

### 4. WiFi Hotspot (AP Mode) - Metric: 400
- **Local Access Only** - Should NOT provide internet routing
- Allows devices to connect to RPI for configuration
- **Configuration**: Done in app.py configure_wifi_hotspot()

## Key Configuration Changes Made

### 1. WiFi Hotspot (app.py - line ~1120)
```bash
'connection.metric', '400'  # Low priority - hotspot should not interfere with internet
```

### 2. WiFi Client (app.py - line ~1008)  
```bash
'connection.metric', '300'  # Lower priority than ethernet (100) and cellular (200)
```

### 3. Cellular RNDIS (install_rpi_streamer.sh - automatic)
```bash
# Configured automatically during installation:
metric=200
autoconnect-priority=10
route-metric=200
```

## Verification Commands

### Check all connections:
```bash
nmcli connection show
```

### Check specific connection details:
```bash  
nmcli connection show "connection-name"
```

### Check active routing:
```bash
ip route show
```

### Check NetworkManager status:
```bash
nmcli device status
```

## Expected Behavior After Configuration

1. **Ethernet connected**: All internet traffic uses ethernet (metric 100)
2. **Ethernet + Cellular**: Ethernet used, cellular standby (failover ready)  
3. **Cellular only**: Internet via cellular modem (metric 200)
4. **WiFi Hotspot active**: Provides local access without interfering with internet routing
5. **Multiple connections**: NetworkManager automatically chooses best route based on metrics

## Troubleshooting

### If hotspot still stops working:
1. Check for IP address conflicts (hotspot uses 192.168.4.x)
2. Verify cellular connection isn't trying to use same IP range
3. Check that cellular connection has `ipv4.method auto` (not shared)

### If internet doesn't failover properly:
1. Verify all connections have correct metrics
2. Check that `connection.autoconnect` is enabled
3. Restart NetworkManager: `sudo systemctl restart NetworkManager`

## Files Modified
- `app.py`: Updated hotspot and WiFi client configurations
- `install_rpi_streamer.sh`: Cellular connection metrics configured automatically during installation