# WiFi Management System Guide v3.00

## Overview
The RPI Streamer v3.00 features a complete WiFi management system with modern network scanning interface and comprehensive hotspot functionality powered by NetworkManager. This system allows the Raspberry Pi to both connect to existing networks and create its own WiFi access point with full routing capabilities.

Key capabilities:
- Modern network scanner with real-time discovery and one-click connections
- Complete hotspot mode with NAT routing via ethernet connection
- Real-time WiFi status with connected network display
- Automatic service management via NetworkManager

## Features

### WiFi Network Management
- **Network Scanner**: Real-time discovery of available WiFi networks with signal strength indicators
- **One-Click Connection**: Connect to networks with password entry and automatic configuration
- **Real-Time Status**: Display of connected network names and IP addresses
- **Password Management**: Secure password handling with toggle visibility

### WiFi Mode Options
- **Client Mode**: Connect to existing WiFi networks with modern scanning interface
- **Hotspot Mode**: Create dynamic hostname-based WiFi access point with full internet routing
- **Seamless Switching**: Easy mode switching through web interface

### Hotspot Configuration
- **Dynamic SSID**: Uses device hostname for unique network identification (e.g., "raspberrypi", "streamer-01")
- **Password Protection**: WPA2 security with configurable password (minimum 8 characters)
- **Channel Selection**: Choose WiFi channel 1, 6, or 11 (6 is default)
- **IP Configuration**: Fixed IP address 192.168.4.1 for the Pi
- **DHCP Server**: Automatic IP assignment to connected devices via NetworkManager
- **Internet Routing**: Full NAT routing via ethernet connection with NetworkManager configuration

## Quick Setup

### WiFi Network Connection
1. Navigate to **System Settings** in the RPI Streamer web interface
2. In the **WiFi Management** section, view the **Available Networks** list
3. Networks are automatically discovered with signal strength indicators
4. Click **Connect** next to your desired network
5. Enter the network password in the provided field
6. Click **Connect** to join the network
7. Status will show "Connected to [Network Name]" when successful

### Hotspot Mode Setup
1. Navigate to **System Settings** in the RPI Streamer web interface
2. In the **WiFi Management** section, select **Hotspot Mode**
3. Configure your hotspot settings:
   - **Password**: Set secure password (minimum 8 characters)
   - **WiFi Channel**: Select 1, 6, or 11 (6 is recommended)
4. Click **Save Hotspot Settings**
5. The system creates a hostname-based network (e.g., "raspberrypi") with internet routing via ethernet
6. Connected devices automatically receive internet access through the Pi

## Network Configuration

### Default Hotspot Settings
- **SSID**: Dynamic hostname-based (e.g., "raspberrypi", "streamer-01") for unique device identification
- **Security**: WPA2 with configurable password
- **IP Address**: 192.168.4.1 (Pi's address in hotspot mode)
- **DHCP Range**: 192.168.4.2 to 192.168.4.20
- **Subnet Mask**: 255.255.255.0
- **Default Gateway**: 192.168.4.1 (the Pi)
- **DNS Servers**: Forwarded through Pi to internet via ethernet

### Automatic Internet Routing
The hotspot mode automatically configures:
- **iptables NAT**: Routes traffic from WiFi clients to ethernet internet connection
- **IP Forwarding**: Enables packet forwarding between WiFi and ethernet interfaces
- **DHCP Server**: Provides automatic IP configuration to connected devices
- **DNS Forwarding**: Routes DNS queries through the Pi to upstream servers
- **SSID**: Dynamic hostname-based (unique per device)
- **Password**: rpistreamer123
- **IP Range**: 192.168.4.1 - 192.168.4.50
- **Channel**: 6
- **Security**: WPA2-PSK

### Device IP Assignments
- **Raspberry Pi**: 192.168.4.1 (configurable)
- **Connected Devices**: 192.168.4.10 - 192.168.4.50 (via DHCP)
- **Web Interface**: Access at http://192.168.4.1

## Usage Scenarios

### Remote Configuration
1. Enable hotspot mode on the Pi
2. Connect your mobile device/laptop to the Pi's WiFi
3. Navigate to http://192.168.4.1 to access the web interface
4. Configure streaming settings, GPS tracking, etc.

### Internet Sharing
If the Pi has internet access via Ethernet or 4G dongle:
1. Connected devices can access the internet through the Pi
2. The Pi acts as a gateway/router
3. NAT (Network Address Translation) is automatically configured

### Isolated Network
- Create a private network for testing
- No internet access required
- Direct communication between devices

## Switching Between Modes

### Client to Hotspot
1. In System Settings, select "Hotspot Mode"
2. Configure hotspot settings
3. Click "Update Hotspot" or "Activate Hotspot"
4. NetworkManager will create and activate the hotspot connection
5. Static IP will be assigned to wlan0
6. DHCP server will be automatically configured

### Hotspot to Client
1. In System Settings, scan for available networks
2. Click "Connect" next to your desired network
3. Enter the network password
4. NetworkManager will automatically switch from hotspot to client mode
5. Pi will connect to the selected WiFi network

## Troubleshooting

### Hotspot Not Starting
```bash
# Check NetworkManager status
sudo systemctl status NetworkManager

# Check interface status
ip addr show wlan0

# View NetworkManager logs
sudo journalctl -u NetworkManager -f

# Check active connections
nmcli connection show --active
```

### Can't Connect to Hotspot
- Verify password is correct (case-sensitive)
- Check if SSID is visible in WiFi scan
- Try different channel (1, 6, or 11)
- Ensure password is at least 8 characters
- Check NetworkManager connection status

### No Internet Access
- Check if Pi has internet via Ethernet/4G
- Verify NetworkManager routing configuration
- Check IP forwarding: `cat /proc/sys/net/ipv4/ip_forward`
- Check connection sharing: `nmcli connection show <hotspot-name>`

### Web Interface Not Accessible
- Verify Pi IP address: `ip addr show wlan0`
- Check if flask_app service is running: `sudo systemctl status flask_app`
- Try connecting directly: `http://192.168.4.1`

## Advanced Configuration

### Manual NetworkManager Hotspot Configuration
You can manually create a hotspot connection using nmcli:
```bash
# Create hotspot connection
sudo nmcli connection add type wifi ifname wlan0 con-name Hotspot autoconnect no \
  wifi.mode ap wifi.ssid "RPI-Streamer" wifi.channel 6 \
  ipv4.method shared ipv6.method ignore \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "your-password"

# Activate the hotspot
sudo nmcli connection up Hotspot
```

### NetworkManager WiFi Configuration
You can view and modify WiFi connections using nmcli:
```bash
# List all connections
nmcli connection show

# Show WiFi networks
nmcli device wifi list

# Connect to a network
nmcli device wifi connect "NetworkName" password "password"

# Show connection details
nmcli connection show "ConnectionName"
```

### NAT/Forwarding Rules
NetworkManager with ipv4.method shared automatically handles:
- IP forwarding configuration
- NAT/masquerading rules via iptables
- DHCP server functionality
- Internet connection sharing

Manual verification:
```bash
# Check IP forwarding (should be 1)
cat /proc/sys/net/ipv4/ip_forward

# View iptables rules
sudo iptables -t nat -L
sudo iptables -L FORWARD
```

## Integration with Other Features

### GPS Tracking
- GPS tracking works in both WiFi modes
- Hotspot mode useful for remote GPS configuration
- Mobile devices can connect to view live tracking data

### 4G Internet Sharing
- Combine with cellular modems for internet backhaul
- Pi provides WiFi + 4G internet access
- Ideal for remote streaming locations

### Streaming Setup
- Configure streaming settings via hotspot connection
- Test streams locally before going live
- Mobile devices can monitor stream status

## Performance Considerations

### Range and Speed
- **Range**: ~30-100 meters depending on environment
- **Speed**: 802.11g speeds (up to 54 Mbps theoretical)
- **Concurrent Connections**: Up to ~10 devices typically

### Power Usage
- Hotspot mode uses more power than client mode
- Consider power requirements for battery operation
- Monitor temperature in extended hotspot operation

## Security Notes

### Default Password
- **Change the default password** for security
- Use strong passwords (8+ characters, mixed case, numbers)
- Consider MAC address filtering for high-security environments

### Network Isolation
- Hotspot creates isolated network by default
- Connected devices can communicate with each other
- Firewall rules can be added for additional security

## Support

### Log Files
- NetworkManager: `journalctl -u NetworkManager`
- WiFi interface: `dmesg | grep wlan0`
- System logs: `/var/log/syslog`

### Common Commands
```bash
# Check NetworkManager status
sudo systemctl status NetworkManager

# View WiFi interface status
nmcli device status

# Show active connections
nmcli connection show --active

# Restart NetworkManager
sudo systemctl restart NetworkManager

# Check WiFi interface
iwconfig

# Scan for networks (client mode)
nmcli device wifi list

# Check connected devices (hotspot mode)
nmcli device wifi show-password

# View connection details
nmcli connection show "connection-name"
```
