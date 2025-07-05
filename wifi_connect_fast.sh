#!/bin/bash
# Enhanced WiFi Fast Connect Script
# This script aggressively scans for and connects to available WiFi networks
# with improved reconnection logic and better error handling

SCRIPT_LOG="/var/log/wifi-fast-connect.log"
MAX_ATTEMPTS=5
SCAN_WAIT=5

# Function to log with timestamp
log_message() {
    echo "$(date): $1" | tee -a "$SCRIPT_LOG"
}

# Function to check internet connectivity
check_internet() {
    if ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1 || ping -c 1 -W 3 1.1.1.1 >/dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

# Function to get WiFi status
get_wifi_status() {
    nmcli -t -f ACTIVE,SSID,SIGNAL device wifi list 2>/dev/null | grep "^yes:" | head -1
}

log_message "Starting enhanced WiFi scanning and connection..."

# Check if WiFi is already connected and working
current_wifi=$(get_wifi_status)
if [ -n "$current_wifi" ]; then
    current_ssid=$(echo "$current_wifi" | cut -d: -f2)
    current_signal=$(echo "$current_wifi" | cut -d: -f3)
    
    log_message "Currently connected to $current_ssid (signal: $current_signal%)"
    
    if check_internet; then
        log_message "Internet connectivity confirmed, connection is working"
        echo "Already connected to $current_ssid with working internet"
        exit 0
    else
        log_message "No internet access, will attempt reconnection"
    fi
fi

# Ensure WiFi radio is enabled
log_message "Ensuring WiFi radio is enabled..."
nmcli radio wifi on 2>/dev/null

# Restart NetworkManager if needed
if ! nmcli general status >/dev/null 2>&1; then
    log_message "NetworkManager appears to be stuck, restarting..."
    sudo systemctl restart NetworkManager
    sleep 10
fi

# Perform multiple connection attempts
for attempt in $(seq 1 $MAX_ATTEMPTS); do
    log_message "Connection attempt $attempt/$MAX_ATTEMPTS"
    
    # Force immediate and thorough WiFi scan
    log_message "Performing WiFi scan..."
    nmcli device wifi rescan 2>/dev/null || true
    sleep $SCAN_WAIT
    
    # Get available networks sorted by signal strength
    log_message "Analyzing available networks..."
    available_networks=$(nmcli -t -f SSID,SIGNAL,SECURITY device wifi list 2>/dev/null | sort -t: -k2 -nr)
    
    if [ -z "$available_networks" ]; then
        log_message "No WiFi networks detected, retrying scan..."
        continue
    fi
    
    # Get configured connections
    configured_connections=$(nmcli -t -f NAME,TYPE connection show | grep ":802-11-wireless" | cut -d: -f1)
    
    if [ -z "$configured_connections" ]; then
        log_message "No configured WiFi connections found"
        echo "No configured WiFi connections found. Please set up WiFi connections first."
        exit 1
    fi
    
    log_message "Found $(echo "$configured_connections" | wc -l) configured connections"
    
    # Try to connect to each configured network in order of signal strength
    connected=false
    
    # Create a prioritized list of networks to try
    for ssid in $configured_connections; do
        # Check if this network is available
        network_info=$(echo "$available_networks" | grep "^$ssid:" | head -1)
        
        if [ -n "$network_info" ]; then
            signal=$(echo "$network_info" | cut -d: -f2)
            security=$(echo "$network_info" | cut -d: -f3)
            
            log_message "Attempting to connect to $ssid (signal: $signal%, security: $security)"
            
            # Use timeout to prevent hanging connections
            if timeout 30 nmcli connection up "$ssid" 2>/dev/null; then
                log_message "Successfully connected to $ssid"
                
                # Wait for IP assignment
                sleep 5
                
                # Verify internet connectivity
                if check_internet; then
                    log_message "Internet connectivity confirmed"
                    connected=true
                    break
                else
                    log_message "Connected but no internet access, trying next network"
                    # Disconnect and try next
                    nmcli connection down "$ssid" 2>/dev/null || true
                fi
            else
                log_message "Failed to connect to $ssid"
            fi
        else
            log_message "Network $ssid not currently available"
        fi
    done
    
    if [ "$connected" = true ]; then
        log_message "WiFi connection successful"
        break
    else
        log_message "All connection attempts failed in this round"
        
        # Power cycle WiFi if we ve tried multiple times
        if [ $attempt -ge 3 ]; then
            log_message "Power cycling WiFi interface..."
            nmcli radio wifi off
            sleep 3
            nmcli radio wifi on
            sleep 5
        fi
    fi
done

# Final status check
echo ""
echo "=== Connection Status ==="
current_wifi=$(get_wifi_status)
if [ -n "$current_wifi" ]; then
    current_ssid=$(echo "$current_wifi" | cut -d: -f2)
    current_signal=$(echo "$current_wifi" | cut -d: -f3)
    
    echo "Connected to: $current_ssid"
    echo "Signal strength: $current_signal%"
    
    if check_internet; then
        echo "Internet connectivity: Working"
        log_message "WiFi connection script completed successfully"
    else
        echo "Internet connectivity: Not working"
        log_message "WiFi connection script completed but internet access issues detected"
    fi
else
    echo "WiFi Status: Not connected"
    log_message "WiFi connection script completed but no connection established"
fi

echo ""
echo "=== Device Status ==="
nmcli device status | grep -E "(DEVICE|wifi)"

echo ""
echo "=== Available Networks ==="
nmcli device wifi list | head -10

log_message "WiFi connection script finished"
