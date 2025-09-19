# Network Interface Priority Configuration for RPI Streamer

## Problem Resolution
With the modem in RNDIS mode (9011), all network interfaces are competing for routing priority. This causes the WiFi hotspot to stop accepting connections when the cellular modem connects.

## Solution: NetworkManager Priority System
NetworkManager uses `autoconnect-priority` and `ipv4.route-metric` to determine connection and routing priority. Higher autoconnect-priority numbers = higher priority. Lower route-metric numbers = higher routing priority.

## IPv6 Status: DISABLED
IPv6 is disabled on all connections for simplicity, better cellular compatibility, and easier troubleshooting.

## Priority Hierarchy (IPv4 Only)

### 1. Ethernet (Wired Connection) - Priority: 100, Route Metric: 100
- **Highest Priority** - Most reliable, unlimited bandwidth
- Usually configured automatically by install_rpi_streamer.sh
- Should be the primary route when connected
- **IPv6**: Disabled

### 2. Cellular Modem (RNDIS) - Priority: 10, Route Metric: 200  
- **Backup Internet** - When ethernet unavailable
- SIM7600G-H in mode 9011 (USB Ethernet/RNDIS)
- Provides internet but may have data limits
- **Configuration**: Automatic during installation
- **IPv6**: Disabled

### 3. WiFi Client Connection - Priority: 5, Route Metric: 300
- **Tertiary Internet** - When no ethernet or cellular  
- Connects to external WiFi networks
- **Configuration**: Done in app.py system_settings_wifi()
- **IPv6**: Disabled

### 4. WiFi Hotspot (AP Mode) - Priority: 10, Route Metric: 400
- **Local Access Only** - Should NOT provide internet routing
- Allows devices to connect to RPI for configuration
- **Configuration**: Done in app.py configure_wifi_hotspot()
- **IPv6**: Disabled

## Key Configuration Changes Made

### 1. WiFi Hotspot (app.py - line ~1130)
```bash
'connection.autoconnect-priority', '10'  # Auto-connect priority for reboot
'ipv4.route-metric', '400'              # Low priority - hotspot should not interfere with internet
'ipv6.method', 'disabled'               # Disable IPv6 for simplicity and compatibility
```

### 2. WiFi Client (app.py - line ~1008)  
```bash
'connection.autoconnect-priority', '5'   # Lower than cellular (10) and ethernet (100)
'ipv4.route-metric', '300'              # Lower priority than ethernet (100) and cellular (200)
'ipv6.method', 'disabled'               # Disable IPv6 for simplicity and compatibility
```

### 3. Cellular RNDIS (install_rpi_streamer.sh - automatic)
```bash
# Configured automatically during installation:
autoconnect-priority=10
[ipv4]
route-metric=200
[ipv6]
method=disabled
```

### 4. Ethernet (install_rpi_streamer.sh - automatic)
```bash
# Configured automatically during installation:
autoconnect-priority=100
[ipv4]
route-metric=100
[ipv6]
method=disabled
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