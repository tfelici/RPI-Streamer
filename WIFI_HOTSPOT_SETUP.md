# WiFi Hotspot Mode Setup Guide

## Overview
The RPI Streamer now supports WiFi hotspot mode, allowing the Raspberry Pi to create its own WiFi network that other devices can connect to. This is useful for:

- Remote locations without existing WiFi infrastructure
- Direct connection from mobile devices for configuration
- Isolated network environments
- Backup connectivity when primary WiFi is unavailable

## Features

### WiFi Mode Options
- **Client Mode**: Connect to existing WiFi networks (traditional behavior)
- **Hotspot Mode**: Create a WiFi access point that other devices can join

### Hotspot Configuration
- **Custom SSID**: Set your own network name (default: "RPI-Streamer")
- **Password Protection**: WPA2 security with custom password (minimum 8 characters)
- **Channel Selection**: Choose WiFi channel 1, 6, or 11
- **IP Configuration**: Set custom IP address for the Pi (default: 192.168.4.1)
- **DHCP Server**: Automatically assigns IP addresses to connected devices

## Quick Setup

### Via Web Interface
1. Navigate to **System Settings** in the RPI Streamer web interface
2. In the **WiFi Management** section, select **Hotspot Mode**
3. Configure your hotspot settings:
   - **Hotspot Name (SSID)**: Your network name
   - **Password**: At least 8 characters (WPA2 security)
   - **WiFi Channel**: 1, 6, or 11 (6 is recommended)
   - **IP Address**: The Pi's IP address (default: 192.168.4.1)
4. Click **Switch to Hotspot Mode**
5. Wait for the configuration to complete (30-60 seconds)

### Manual Configuration
If you need to configure the hotspot manually:

```bash
# Install required packages
sudo apt-get install hostapd dnsmasq

# Configure hotspot via RPI Streamer web interface
# Or edit configuration files manually (see Advanced Configuration below)
```

## Network Configuration

### Default Settings
- **SSID**: RPI-Streamer
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
3. Click "Switch to Hotspot Mode"
4. NetworkManager will be stopped
5. hostapd and dnsmasq will be started
6. Static IP will be assigned to wlan0

### Hotspot to Client
1. In System Settings, select "Client Mode"
2. Click "Switch to Client Mode"
3. hostapd and dnsmasq will be stopped
4. NetworkManager will be restarted
5. Pi will attempt to reconnect to saved WiFi networks

## Troubleshooting

### Hotspot Not Starting
```bash
# Check service status
sudo systemctl status hostapd
sudo systemctl status dnsmasq

# Check interface status
ip addr show wlan0

# View logs
sudo journalctl -u hostapd -f
sudo journalctl -u dnsmasq -f
```

### Can't Connect to Hotspot
- Verify password is correct (case-sensitive)
- Check if SSID is visible in WiFi scan
- Try different channel (1, 6, or 11)
- Ensure password is at least 8 characters

### No Internet Access
- Check if Pi has internet via Ethernet/4G
- Verify iptables NAT rules are configured
- Check IP forwarding: `cat /proc/sys/net/ipv4/ip_forward`

### Web Interface Not Accessible
- Verify Pi IP address: `ip addr show wlan0`
- Check if flask_app service is running: `sudo systemctl status flask_app`
- Try connecting directly: `http://192.168.4.1`

## Advanced Configuration

### Manual hostapd Configuration
File: `/etc/hostapd/hostapd.conf`
```
interface=wlan0
driver=nl80211
ssid=RPI-Streamer
hw_mode=g
channel=6
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=rpistreamer123
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
```

### Manual dnsmasq Configuration
File: `/etc/dnsmasq.conf`
```
interface=wlan0
dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,24h
```

### NAT/Forwarding Rules
```bash
# Enable IP forwarding
sudo sysctl net.ipv4.ip_forward=1

# Configure iptables for internet sharing
sudo iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
sudo iptables -A FORWARD -i eth0 -o wlan0 -m state --state RELATED,ESTABLISHED -j ACCEPT
sudo iptables -A FORWARD -i wlan0 -o eth0 -j ACCEPT
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
- hostapd: `/var/log/daemon.log` or `journalctl -u hostapd`
- dnsmasq: `/var/log/daemon.log` or `journalctl -u dnsmasq`
- NetworkManager: `journalctl -u NetworkManager`

### Common Commands
```bash
# Check WiFi interface
iwconfig

# Scan for networks (client mode)
sudo iwlist wlan0 scan

# Check connected devices (hotspot mode)
arp -a

# Restart WiFi services
sudo systemctl restart NetworkManager
sudo systemctl restart hostapd
sudo systemctl restart dnsmasq
```
