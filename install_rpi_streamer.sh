#!/bin/bash
# RPI Streamer installation script
# Usage: bash install_rpi_streamer.sh [OPTIONS]
#
# Options:
#   --main            Install stable main branch (default)
#   --develop         Install latest develop branch (may be unstable)
#   --daemon          Run in daemon mode (no interactive prompts)
#
# Examples:
#   bash install_rpi_streamer.sh                     # Basic installation with main branch
#   bash install_rpi_streamer.sh --develop          # Installation with develop branch
#   bash install_rpi_streamer.sh --daemon           # Silent installation without prompts

# This script installs the RPI Streamer Flask app and MediaMTX on a Raspberry Pi running Raspberry Pi OS Lite.
# It also sets up a systemd service for the Flask app and MediaMTX, with optional remote access configuration.
#
################################################

set -e

# Check for internet connectivity before proceeding
echo "🌐 Checking for internet connectivity..."
INTERNET_FOUND=false
for i in {1..30}; do
    if ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1; then
        echo "✅ Internet connection confirmed - proceeding with installation"
        INTERNET_FOUND=true
        break
    fi
    echo "Waiting for internet connection... ($i/30)"
    sleep 1
done

if [ "$INTERNET_FOUND" = "false" ]; then
    echo "❌ No internet connection detected after 30 seconds!"
    echo ""
    echo "📋 This installation script requires internet access to:"
    echo "   • Download and install packages (ModemManager, NetworkManager, etc.)"
    echo "   • Update system packages"
    echo "   • Clone the RPI Streamer repository from GitHub"
    echo "   • Download MediaMTX and other dependencies"
    echo ""
    echo "🔌 Please ensure internet connectivity via:"
    echo "   • Ethernet cable connection, or"
    echo "   • WiFi connection (configure with: sudo raspi-config), or"
    echo "   • Wait for cellular dongle to establish connection (if already connected)"
    echo ""
    echo "💡 After establishing internet connection, re-run this script:"
    echo "   bash install_rpi_streamer.sh"
    echo ""
    exit 1
fi
echo ""

# Check for GitHub updates immediately after confirming internet connectivity
echo "🔄 Checking for GitHub updates..."

# Determine which branch to use based on flags (support both old and new naming)
# Make this available globally for the entire script
if [[ "$@" == *"--develop"* ]] || [[ "$@" == *"--development"* ]]; then
    TARGET_BRANCH="origin/develop"
    BRANCH_NAME="develop"
    BRANCH_FLAG="--develop"
    echo "Using develop branch for installation"
else
    # Default to main branch for stability
    TARGET_BRANCH="origin/main"
    BRANCH_NAME="main"
    BRANCH_FLAG="--main"
    echo "Using main branch for installation"
fi

# Setup flask app directory
echo "Setting up Flask app directory... $HOME/flask_app"
mkdir -p "$HOME/flask_app"
mkdir -p "$HOME/streamerData"
cd "$HOME/flask_app"

#     Update the codebase to match the remote GitHub repository if changes are available.
#     This ensures the device has the latest version from the remote repository.
#     If the repository is not already cloned, it will clone it fresh.
echo "Updating RPI Streamer codebase..."

# Check if git is installed
if ! command -v git &> /dev/null; then
    echo "Git not found, installing..."
    sudo apt-get install git -y
fi

# Variable to track if we need to continue with installation
UPDATES_AVAILABLE=false
IS_NEW_INSTALLATION=false

# If repository exists, check for updates
# If not, it's a new installation and we should proceed
if [ -d .git ]; then
    # Get current commit hash before fetch
    CURRENT_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "")
    
    sudo git fetch --all
    
    # Get the latest commit hash from remote (TARGET_BRANCH and BRANCH_NAME already set globally)
    LATEST_COMMIT=$(git rev-parse $TARGET_BRANCH 2>/dev/null || echo "")
    
    # Check if there are updates available
    if [ "$CURRENT_COMMIT" != "$LATEST_COMMIT" ] && [ -n "$LATEST_COMMIT" ]; then
        echo "📦 Updates available on $BRANCH_NAME branch"
        echo "   Current: ${CURRENT_COMMIT:0:8}"
        echo "   Latest:  ${LATEST_COMMIT:0:8}"
        UPDATES_AVAILABLE=true
        
        sudo git reset --hard $TARGET_BRANCH
        sudo git clean -f -d
        echo "Repository updated to latest $BRANCH_NAME branch"
    else
        echo "✅ Already up to date on $BRANCH_NAME branch"
        echo "💡 No installation needed - system is current"
        exit 0
    fi
else
    echo "Repository not found, this is a new installation..."
    IS_NEW_INSTALLATION=true
    cd ..
    rm -rf flask_app
    mkdir flask_app
    cd flask_app
    sudo git clone https://github.com/tfelici/RPI-Streamer.git .

    # Checkout the branch determined globally (BRANCH_NAME already set)
    sudo git checkout $BRANCH_NAME
    echo "Using $BRANCH_NAME branch"
fi

# Change ownership of the flask_app directory to the current user
sudo chown -R "$USER":"$USER" "$HOME/flask_app"
# This command is needed to allow any users to run git in the flask_app directory
sudo git config --global --add safe.directory "$HOME/flask_app"

# Check if the app.py file exists
if [ ! -f app.py ]; then
    echo "Error: app.py file not found in the Flask app directory."
    exit 1
fi

# Check if the flask_app directory is writable
if [ ! -w "$HOME/flask_app" ]; then
    echo "Error: Flask app directory is not writable."
    exit 1
fi

# Continue with installation only if updates were found or it's a new installation
# Also continue if we're in development mode (--develop flag)
if [ "$UPDATES_AVAILABLE" = "true" ] || [ "$IS_NEW_INSTALLATION" = "true" ] || [[ "$@" == *"--develop"* ]]; then
    if [[ "$@" == *"--develop"* ]] && [ "$UPDATES_AVAILABLE" = "false" ] && [ "$IS_NEW_INSTALLATION" = "false" ]; then
        echo "🚀 Proceeding with installation in development mode..."
    else
        echo "🚀 Proceeding with installation..."
    fi
else
    echo "✅ No updates needed, exiting."
    exit 0
fi

# Install and configure ModemManager for cellular connectivity
echo "📡 Setting up ModemManager for cellular modem support..."

# Update package list and install ModemManager
sudo apt-get update -qq
sudo apt-get install -y modemmanager network-manager

# Enable and start ModemManager service
echo "🔧 Enabling ModemManager service..."
sudo systemctl enable ModemManager
#sudo systemctl start ModemManager

# Enable and ensure NetworkManager is running
echo "🌐 Ensuring NetworkManager is enabled..."
sudo systemctl enable NetworkManager
sudo systemctl start NetworkManager

# Configure network interface priorities - Ethernet prioritized over cellular
echo "📝 Configuring network interface priorities..."

# Create Ethernet connection with high priority (higher number = higher priority)
sudo tee /etc/NetworkManager/system-connections/ethernet-priority.nmconnection >/dev/null << 'EOFETHERNET'
[connection]
id=ethernet-priority
type=ethernet
autoconnect=true
autoconnect-priority=100
interface-name=eth0

[ethernet]

[ipv4]
method=auto
route-metric=100

[ipv6]
method=auto
route-metric=100
EOFETHERNET

# Create NetworkManager configuration for automatic cellular connection with DNS servers
# Note: Cellular providers often don't provide DNS servers or provide unreliable ones
# Using public DNS servers (8.8.8.8, 8.8.4.4, 1.1.1.1) ensures reliable domain resolution
echo "📝 Configuring automatic cellular connection with DNS servers..."
sudo tee /etc/NetworkManager/system-connections/cellular-auto.nmconnection >/dev/null << 'EOFCELLULAR'
[connection]
id=cellular-auto
type=gsm
autoconnect=true
autoconnect-priority=10

[gsm]
# APN will be auto-detected by ModemManager for most carriers
# You can specify a custom APN here if needed:
# apn=your.carrier.apn

[ipv4]
method=auto
route-metric=200
dns=8.8.8.8;8.8.4.4;1.1.1.1;
ignore-auto-dns=true

[ipv6]
method=auto
route-metric=200
EOFCELLULAR

# Set proper permissions for NetworkManager connection files
sudo chmod 600 /etc/NetworkManager/system-connections/ethernet-priority.nmconnection
sudo chmod 600 /etc/NetworkManager/system-connections/cellular-auto.nmconnection
sudo chown root:root /etc/NetworkManager/system-connections/ethernet-priority.nmconnection
sudo chown root:root /etc/NetworkManager/system-connections/cellular-auto.nmconnection

# Create NetworkManager dispatcher script for dynamic priority management
echo "🔧 Creating network dispatcher script for dynamic routing..."
sudo tee /etc/NetworkManager/dispatcher.d/99-interface-priority >/dev/null << 'EOFDISPATCHER'
#!/bin/bash
# NetworkManager dispatcher script to ensure Ethernet takes priority over cellular

INTERFACE="$1"
ACTION="$2"

case "$ACTION" in
    up)
        if [[ "$INTERFACE" == "eth0" ]]; then
            # Ethernet is up - ensure it has the default route
            echo "$(date): Ethernet interface $INTERFACE is up - setting as primary route" >> /var/log/network-priority.log
            # Remove any default route from cellular interfaces
            ip route del default dev $(ip route | grep 'default.*wwan' | awk '{print $5}') 2>/dev/null || true
            # Ensure ethernet default route exists with lower metric
            ip route add default via $(ip route | grep "^default.*$INTERFACE" | awk '{print $3}') dev $INTERFACE metric 100 2>/dev/null || true
        elif [[ "$INTERFACE" =~ wwan[0-9]+ ]]; then
            # Cellular interface is up
            echo "$(date): Cellular interface $INTERFACE is up" >> /var/log/network-priority.log
            # Only add cellular default route if no ethernet route exists
            if ! ip route | grep -q "default.*eth0"; then
                echo "$(date): No ethernet route found - allowing cellular default route" >> /var/log/network-priority.log
            else
                echo "$(date): Ethernet route exists - cellular will be backup only" >> /var/log/network-priority.log
            fi
        fi
        ;;
    down)
        if [[ "$INTERFACE" == "eth0" ]]; then
            # Ethernet is down - allow cellular to take over
            echo "$(date): Ethernet interface $INTERFACE is down - allowing cellular takeover" >> /var/log/network-priority.log
        fi
        ;;
esac
EOFDISPATCHER

# Make dispatcher script executable
sudo chmod +x /etc/NetworkManager/dispatcher.d/99-interface-priority

# Create log file for network priority events
sudo touch /var/log/network-priority.log
sudo chmod 644 /var/log/network-priority.log

# Note: NetworkManager will automatically pick up new connection files
# We avoid reloading NetworkManager during installation to prevent network disruption
echo "📝 NetworkManager configuration files created (will be loaded on next boot/restart)"

echo "✅ ModemManager and NetworkManager configured for automatic connectivity with interface priority"
echo "🌐 Network interface priority configuration:"
echo "   1. � Ethernet (eth0): Priority 100, Route metric 100 (HIGHEST PRIORITY)"
echo "   2. 📡 Cellular (wwan*): Priority 10, Route metric 200 (BACKUP)"
echo "📡 When network interfaces are available:"
echo "   • Ethernet cable connected: All traffic routes via Ethernet"
echo "   • Only cellular connected: Traffic routes via cellular"
echo "   • Both connected: Ethernet takes priority, cellular as backup"
echo "   • Ethernet disconnected: Automatic failover to cellular"
echo "💾 Network priority events logged to: /var/log/network-priority.log"
echo ""

# Update and install dependencies
sudo apt-get update -y
sudo apt-get upgrade -y
sudo apt-get install ffmpeg -y
sudo apt-get install v4l-utils alsa-utils -y
sudo apt-get install python3 python3-pip -y
sudo apt-get install python3-flask -y
sudo apt-get install python3-psutil -y
sudo apt-get install python3-requests -y
sudo apt-get install python3-werkzeug -y
sudo apt-get install gunicorn python3-gevent python3-requests-toolbelt -y
sudo apt-get install python3-pymediainfo -y
sudo apt-get install mediainfo -y

# GPS Tracker dependencies (using direct NMEA parsing)

# Only prepare WiFi interface when NOT running in daemon mode
# In daemon mode (updates), preserve existing WiFi/hotspot state
if [[ "$@" != *"--daemon"* ]]; then
    # Ensure WiFi interface is ready for NetworkManager management
    echo "📡 Preparing WiFi interface for NetworkManager management..."

    # Unblock WiFi radio if needed (defensive measure - app.py will also do this when creating hotspot)
    sudo rfkill unblock wifi 2>/dev/null || echo "Note: WiFi radio unblock not needed or already active"

    # Ensure WiFi radio is enabled for NetworkManager (modern Pi OS default)
    sudo nmcli radio wifi on 2>/dev/null || echo "Note: WiFi radio already enabled or not available"

    # Disconnect any existing WiFi connections to ensure clean state
    sudo nmcli device disconnect wlan0 2>/dev/null || true

    echo "✅ WiFi interface prepared for hotspot mode via NetworkManager"
    echo "� WiFi hotspot configuration available via web interface:"
    echo "   • Access the RPI Streamer web interface after installation"
    echo "   • Navigate to Network Settings or WiFi Hotspot section"
    echo "   • Configure hotspot name, password, and IP settings"
    echo "   • Enable/disable hotspot as needed through the web interface"
else
    echo "🤖 Running in daemon mode - preserving existing WiFi/hotspot configuration"
fi

# AutoSSH for reliable reverse tunnel management
sudo apt-get install autossh -y

# Serial communication tools for GPS AT command management
sudo apt-get install python3-serial -y

#also add gstreamer dependencies
sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-alsa gstreamer1.0-pulseaudio -y
sudo apt-get install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-gst-plugins-base-1.0 gir1.2-gstreamer-1.0 -y

#create symbolic link for python3 to python if it doesn't exist
if [ ! -L /usr/bin/python ]; then
    echo "Creating symbolic link for python3 to python..."
    sudo ln -s /usr/bin/python3 /usr/bin/python
else
    echo "Symbolic link for python3 to python already exists."
fi


#download the executables directory from the Streamer-Uploader repository
#only download if the executables have changed or don't exist
if [ ! -d "$HOME/executables" ]; then
    mkdir -p "$HOME/executables"
fi

# Function to check and download executable if needed
# This function handles both SHA checking and downloading in one place
# Returns 0 on success (up to date or downloaded), 1 on failure (but doesn't exit)

# Check if jq is installed, if not, install it (needed for version comparison)
if ! command -v jq &> /dev/null; then
    sudo apt-get install jq -y
fi

check_and_download_executable() {
    local platform=$1
    local filename=$2
    local api_path=$3
    local download_url=$4
    local local_file="$HOME/executables/$filename"
    local sha_file="$HOME/executables/${filename}.sha"
    
    printf "Checking %s executable...\n" "$platform"
    
    # Get remote file SHA from GitHub API
    remote_sha=$(curl -s "https://api.github.com/repos/tfelici/Streamer-Uploader/contents/$api_path" | jq -r ".sha")
    
    if [ -z "$remote_sha" ] || [ "$remote_sha" = "null" ]; then
        echo "Warning: Could not fetch remote SHA for $platform executable. Remote file may not exist or API is unavailable."
        echo "Skipping $platform executable update check."
        return 0  # Return success to continue gracefully
    fi
    
    # Check if local file exists and has a stored SHA
    local need_download=false
    if [ -f "$local_file" ] && [ -f "$sha_file" ]; then
        # Read the stored SHA
        stored_sha=$(cat "$sha_file" 2>/dev/null || echo "")
        
        if [ -z "$stored_sha" ]; then
            echo "No stored SHA found for $platform executable, will download."
            need_download=true
        elif [ "$stored_sha" = "$remote_sha" ]; then
            echo "$platform executable is up to date."
            return 0  # Already up to date
        else
            echo "$platform executable needs update (stored: ${stored_sha:0:7}, remote: ${remote_sha:0:7})."
            need_download=true
        fi
    else
        echo "$platform executable not found, will download."
        need_download=true
    fi
    
    # Download if needed
    if [ "$need_download" = "true" ]; then
        printf "Downloading %s executable...\n" "$platform"
        if curl -H "Cache-Control: no-cache" -L "$download_url?$(date +%s)" -o "$local_file"; then
            echo "$platform executable downloaded successfully."
            # Store the remote SHA for future comparisons
            echo "$remote_sha" > "$sha_file"
            return 0  # Success
        else
            echo "Warning: Failed to download $platform executable. This may be due to network issues or the file not existing."
            echo "Continuing with installation..."
            return 0  # Return success to continue gracefully
        fi
    fi
}

# Download the StreamerUploader executables for different platforms
printf "Checking StreamerUploader executables...\n"

# Use the same branch as the main RPI Streamer installation
STREAMER_UPLOADER_BRANCH=$BRANCH_NAME
printf "Using Streamer-Uploader $STREAMER_UPLOADER_BRANCH branch for executables...\n"

# Check and download Windows executable
check_and_download_executable "Windows" "Uploader-windows.exe" "windows/dist/StreamerUploader.exe" "https://github.com/tfelici/Streamer-Uploader/raw/$STREAMER_UPLOADER_BRANCH/windows/dist/StreamerUploader.exe"

# Check and download macOS executable
check_and_download_executable "macOS" "Uploader-macos" "macos/dist/StreamerUploader" "https://github.com/tfelici/Streamer-Uploader/raw/$STREAMER_UPLOADER_BRANCH/macos/dist/StreamerUploader"

# Check and download Linux executable
check_and_download_executable "Linux" "Uploader-linux" "linux/dist/StreamerUploader" "https://github.com/tfelici/Streamer-Uploader/raw/$STREAMER_UPLOADER_BRANCH/linux/dist/StreamerUploader"

printf "StreamerUploader executable check completed.\n"

# Make the downloaded linux and macos executables executable
[ -f "$HOME/executables/Uploader-linux" ] && chmod +x "$HOME/executables/Uploader-linux"
[ -f "$HOME/executables/Uploader-macos" ] && chmod +x "$HOME/executables/Uploader-macos"
# search and install latest version of mediamtx
#only install MediaMTX if it does not exist or is not the latest version
printf "Checking for existing MediaMTX installation...\n"

# Get the latest version from GitHub API
printf "Checking latest MediaMTX version...\n"
latest_version=$(curl -s https://api.github.com/repos/bluenviron/mediamtx/releases/latest | jq -r ".tag_name")
if [ -z "$latest_version" ]; then
    echo "Error: Could not fetch latest MediaMTX version from GitHub."
    exit 1
fi
printf "Latest MediaMTX version: %s\n" "$latest_version"

# Check if MediaMTX exists and get its version
NEED_INSTALL=false
if [ -f "$HOME/mediamtx" ]; then
    echo "MediaMTX binary found, checking version..."
    # Check if the mediamtx binary is executable
    if [ ! -x "$HOME/mediamtx" ]; then
        echo "MediaMTX binary is not executable, will reinstall."
        NEED_INSTALL=true
    else
        # Get current version
        current_version=$("$HOME/mediamtx" --version 2>/dev/null | head -n1 | grep -o 'v[0-9][0-9.]*' || echo "unknown")
        printf "Current MediaMTX version: %s\n" "$current_version"
        
        if [ "$current_version" = "$latest_version" ]; then
            echo "MediaMTX is already up to date ($current_version)."
            NEED_INSTALL=false
        else
            echo "MediaMTX needs update from $current_version to $latest_version."
            NEED_INSTALL=true
        fi
    fi
else
    echo "MediaMTX is not installed, proceeding with installation."
    NEED_INSTALL=true
fi

# Only proceed with download if we need to install/update
if [ "$NEED_INSTALL" = "true" ]; then
    # Determine the latest MediaMTX release URL based on system architecture
    printf "Detecting system architecture...\n"
    ARCH=$(uname -m)
    if [[ "$ARCH" == "aarch64"* || "$ARCH" == arm* ]]; then
        MTX_SUFFIX="linux_arm64.tar.gz"
    else
        MTX_SUFFIX="linux_amd64.tar.gz"
    fi
    printf "Searching for the latest MediaMTX release for $MTX_SUFFIX...\n"
    
    latest_url=$(curl -s https://api.github.com/repos/bluenviron/mediamtx/releases/latest | jq -r ".assets[] | select(.browser_download_url | endswith(\"$MTX_SUFFIX\")) | .browser_download_url")
    printf "Latest MediaMTX URL: %s\n" "$latest_url"
    if [ -z "$latest_url" ]; then
        echo "Error: Could not find the latest MediaMTX release URL."
        exit 1
    fi
    cd "$HOME"
    wget "$latest_url"
    exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo "Error: Failed to download MediaMTX."
        exit $exit_code
    fi
    printf "Extracting MediaMTX...\n"
    # Extract the downloaded file
    if [ ! -f "$(basename "$latest_url")" ]; then
        echo "Error: Downloaded file not found."
        exit 1
    fi
    # Ensure the file is a tar.gz file
    if [[ "$(basename "$latest_url")" != *.tar.gz ]]; then
        echo "Error: Downloaded file is not a tar.gz file."
        exit 1
    fi
    tar xvf $(basename "$latest_url")
    chmod +x mediamtx
    #delete the downloaded file
    rm $(basename "$latest_url")
    # Check if the mediamtx binary exists
    if [ ! -f mediamtx ]; then
        echo "Error: MediaMTX binary not found after extraction."
        exit 1
    fi
    echo "MediaMTX installation completed successfully."
else
    echo "Skipping MediaMTX download - already up to date."
fi
#mv mediamtx.yml flask_app

# Create systemd service for flask app - this must run after the install_rpi_streamer.service
printf "Creating systemd service for Flask app...\n"
sudo tee /etc/systemd/system/flask_app.service >/dev/null << EOF
[Unit]
Description=RPI Streamer
After=network-online.target NetworkManager-wait-online.service
Wants=network-online.target
Requires=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$HOME/flask_app
Environment="OWNER=$USER:$USER"
#ExecStart=/usr/bin/python3 app.py
ExecStart=/usr/bin/gunicorn -w 4 -k gevent --graceful-timeout 1 -b 0.0.0.0:80 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# Create systemd service for mediamtx - this must run after the install_rpi_streamer.service
# Note: Using network-online.target ensures MediaMTX starts after network is fully configured
# This prevents SRT/RTSP port binding issues during boot when network isn't ready yet
printf "Creating systemd service for MediaMTX...\n"
sudo tee /etc/systemd/system/mediamtx.service >/dev/null << EOF
[Unit]
Description=MediaMTX Streaming Server
After=network-online.target NetworkManager-wait-online.service
Wants=network-online.target
Requires=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$HOME/flask_app
ExecStart=$HOME/mediamtx $HOME/flask_app/mediamtx.yml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Create systemd service for heartbeat daemon
printf "Creating systemd service for Heartbeat Daemon...\n"
sudo tee /etc/systemd/system/heartbeat-daemon.service >/dev/null << EOF
[Unit]
Description=RPI Streamer Heartbeat Daemon
After=network-online.target NetworkManager-wait-online.service
Wants=network-online.target
Requires=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$HOME/flask_app
ExecStart=/usr/bin/python3 $HOME/flask_app/heartbeat_daemon.py --daemon
ExecStop=/bin/kill -TERM \$MAINPID
Restart=always
RestartSec=10

# Environment
Environment=PYTHONPATH=$HOME/flask_app
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Security settings
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

#make rpiconfig executable
chmod +x "$HOME/flask_app/rpiconfig.sh"
echo "✅ Made rpiconfig script executable"
#create a symbolic link in /usr/local/bin so it's in PATH
sudo ln -sf "$HOME/flask_app/rpiconfig.sh" "/usr/local/bin/rpiconfig"

# Install udev rule for automatic GPS daemon startup when SIM7600G-H is inserted
printf "🔌 Installing udev rule for SIM7600G-H auto-detection...\n"

sudo tee /etc/udev/rules.d/99-sim7600-gps.rules >/dev/null << 'EOFUDEV'
# udev rule for SIM7600G-H GPS Daemon Management
# This rule triggers when a SIM7600G-H modem is inserted or removed
# Environment variables captured from actual device monitoring: ID_VENDOR_ID=1e0e, ID_MODEL_ID=9011, SUBSYSTEM=usb, DEVTYPE=usb_device

# When SIM7600G-H is added, restart GPS daemon (ensures clean state)
# Using PRODUCT environment variable for consistency with removal rule
# PRODUCT format is "vendor/product/version" = "1e0e/9011/318"
ACTION=="add", SUBSYSTEM=="usb", ENV{PRODUCT}=="1e0e/9011/318", RUN+="/bin/systemctl restart gps-daemon.service"
ACTION=="add", SUBSYSTEM=="usb", ENV{PRODUCT}=="1e0e/9001/318", RUN+="/bin/systemctl restart gps-daemon.service"

# When SIM7600G-H is removed, stop GPS daemon immediately
# Using PRODUCT environment variable for removal events since ATTR{} attributes are not available
# PRODUCT format is "vendor/product/version" = "1e0e/9011/318"
ACTION=="remove", SUBSYSTEM=="usb", ENV{PRODUCT}=="1e0e/9011/318", RUN+="/bin/systemctl stop gps-daemon.service"
ACTION=="remove", SUBSYSTEM=="usb", ENV{PRODUCT}=="1e0e/9001/318", RUN+="/bin/systemctl stop gps-daemon.service"
EOFUDEV

# Set proper permissions for udev rule
sudo chown root:root /etc/udev/rules.d/99-sim7600-gps.rules
sudo chmod 644 /etc/udev/rules.d/99-sim7600-gps.rules

# Create boot-time GPS detection service for dongles present at boot
printf "🚀 Creating boot-time GPS detection service...\n"
sudo tee /etc/systemd/system/gps-boot-detect.service >/dev/null << EOF
[Unit]
Description=GPS Boot Detection for RPI Streamer
After=multi-user.target ModemManager.service
Wants=multi-user.target ModemManager.service

[Service]
Type=oneshot
User=root
Group=root
# Poll for dongle readiness before starting GPS daemon
ExecStart=/bin/bash -c '
    if lsusb | grep -q -E "(1e0e:900[19]|2c7c:0[13][02][56])"; then
        echo "SIM7600G-H detected, polling for dongle readiness..."
        
        # Poll for serial ports and AT command responsiveness
        for i in {1..60}; do
            # Check if serial ports exist
            if [ -c /dev/ttyUSB1 ] && [ -c /dev/ttyUSB3 ]; then
                echo "Serial ports available, testing AT command responsiveness..."
                
                # Test AT command responsiveness (quick test)
                if timeout 3 bash -c "echo \"AT\" > /dev/ttyUSB3 2>/dev/null && sleep 1 && grep -q \"OK\" < /dev/ttyUSB3 2>/dev/null"; then
                    echo "✓ Dongle is ready and responding to AT commands"
                    echo "Starting GPS daemon after successful dongle readiness check"
                    systemctl start gps-daemon.service
                    exit 0
                else
                    echo "Dongle not yet responding to AT commands, waiting... ($i/60)"
                fi
            else
                echo "Waiting for serial ports to appear... ($i/60)"
            fi
            sleep 2
        done
        
        echo "⚠️ Timeout waiting for dongle readiness, starting GPS daemon anyway"
        echo "GPS daemon will handle initialization internally"
        systemctl start gps-daemon.service
    else
        echo "No SIM7600G-H detected at boot"
    fi
'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

# Enable boot detection service
sudo systemctl enable gps-boot-detect.service

# Reload udev rules
sudo udevadm control --reload-rules
sudo udevadm trigger

echo "✅ udev rule installed - GPS daemon will only run when SIM7600G-H is present"

# Create the GPS startup service
printf "Creating systemd service for GPS Startup Manager...\n"
sudo tee /etc/systemd/system/gps-startup.service >/dev/null << EOF
[Unit]
Description=GPS Startup Manager for RPI Streamer
After=network-online.target NetworkManager-wait-online.service
Wants=network-online.target
Requires=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$HOME/flask_app
ExecStart=/usr/bin/python3 $HOME/flask_app/gps_startup_manager.py --daemon
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "✅ GPS Auto-Enable System installed"

# Install GPS Daemon
printf "🛰️ Installing GPS Daemon...\n"

# Install Python dependencies for GPS daemon
echo "Installing GPS daemon dependencies..."
pip3 install --user pyserial 2>/dev/null || echo "pyserial already installed"

# Install GPS daemon as systemd service (but don't enable or start it - will be controlled by udev rules)
echo "Installing GPS daemon service..."
sudo tee /etc/systemd/system/gps-daemon.service >/dev/null << EOF
[Unit]
Description=GPS Daemon for RPI Streamer
After=network-online.target NetworkManager-wait-online.service
Wants=network-online.target
Requires=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/home/$USER/flask_app
# GPS daemon handles initialization internally
ExecStart=/usr/bin/python3 /home/$USER/flask_app/gps_daemon.py --daemon
ExecStop=/bin/kill -TERM \$MAINPID
Restart=no
RestartSec=10

# Environment
Environment=PYTHONPATH=/home/$USER/flask_app
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Security settings
NoNewPrivileges=false
PrivateTmp=false
PrivateDevices=false
ProtectHome=false
ProtectSystem=false

# Allow access to serial devices
SupplementaryGroups=dialout

[Install]
# GPS daemon is started/stopped by udev rules when hardware is detected
# WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload

# Do NOT enable GPS daemon at boot - it will be controlled by udev rules
# sudo systemctl enable gps-daemon.service

# Add user to dialout group for serial access (if not already added)
sudo usermod -a -G dialout $USER

echo "✅ GPS Daemon installed (will only run when GPS hardware is detected)"

echo "🛰️ GPS System Info:"
echo "   🛰️ GPS Daemon: Runs only when GPS hardware is detected"
echo "   🔌 Hardware Integration: GPS daemon handles enablement automatically"
echo "   � Boot Detection: GPS daemon checks for dongles present at boot"
echo "   ⚙️ Startup Manager: Configure GPS start mode in Flight Settings"
echo "   📡 Multi-GNSS: GPS + GLONASS + Galileo + BeiDou constellation support"
echo "   📋 Status: sudo systemctl status gps-daemon.service"
echo "   🔧 Manual control: sudo systemctl start/stop gps-daemon.service"

#create a systemd service for this script
printf "Creating systemd service for this script...\n"

sudo tee /etc/systemd/system/install_rpi_streamer.service >/dev/null << EOF
[Unit]
Description=RPI Streamer Installation Script
After=network-online.target
Wants=network-online.target
[Service]
User=$USER
Type=oneshot
ExecStart=/usr/bin/curl -H "Cache-Control: no-cache" -L -o $HOME/flask_app/install_rpi_streamer.sh "https://raw.githubusercontent.com/tfelici/RPI-Streamer/$BRANCH_NAME/install_rpi_streamer.sh?$(date +%s)"
ExecStartPost=/bin/bash -e $HOME/flask_app/install_rpi_streamer.sh $BRANCH_FLAG --daemon
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable install_rpi_streamer
echo "✅ install_rpi_streamer.service created and enabled"
sudo systemctl enable flask_app
sudo systemctl restart flask_app
sudo systemctl enable mediamtx
sudo systemctl restart mediamtx
sudo systemctl enable heartbeat-daemon
sudo systemctl restart heartbeat-daemon
sudo systemctl enable gps-startup
sudo systemctl restart gps-startup

# Note: GPS startup service is now enabled by default and will start at boot
# It will check Flight Settings configuration and act accordingly
echo "🛰️ GPS Startup Service: Enabled and will start at boot (configure behavior via Flight Settings)"

# Function to register device with hardware console and setup SSH key
register_device_with_console() {
    local hardwareid=$1
    local http_port=$2
    local ssh_port=$3
    local server_host=$4
    local server_user=$5
    local server_port=$6
    
    echo "🔗 Registering device and SSH key with hardware console..."
    
    # Check if curl is available for hostname fetching
    if command -v curl >/dev/null 2>&1; then
        # Fetch new hostname from server and update device hostname
        echo "🏷️  Fetching new hostname from server..."
        local new_hostname=$(curl -s "https://streamer.lambda-tek.com/public_api.php?command=findorcreatehostname&hardwareid=$hardwareid" 2>/dev/null)
        
        if [ -n "$new_hostname" ] && [ "$new_hostname" != "null" ] && [ "$new_hostname" != "false" ]; then
            # Clean the hostname (remove any quotes or extra characters)
            new_hostname=$(echo "$new_hostname" | tr -d '"' | tr -d '\n' | tr -d '\r')
            echo "📝 New hostname received: $new_hostname"
            
            # Update the device hostname
            echo "🔧 Updating device hostname to: $new_hostname"
            if command -v hostnamectl >/dev/null 2>&1; then
                sudo hostnamectl set-hostname "$new_hostname"
                # Also update /etc/hosts for local resolution
                sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$new_hostname/" /etc/hosts
            else
                # Fallback for older systems
                echo "$new_hostname" | sudo tee /etc/hostname > /dev/null
                sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$new_hostname/" /etc/hosts
            fi
            
            # Verify hostname change
            local current_hostname=$(hostname)
            if [ "$current_hostname" = "$new_hostname" ]; then
                echo "✅ Hostname successfully updated to: $current_hostname"
            else
                echo "⚠️  Hostname update may require reboot to take full effect"
                echo "   Current: $current_hostname, Expected: $new_hostname"
            fi
        else
            echo "⚠️  Failed to fetch new hostname from server, continuing with current hostname: $(hostname)"
        fi
        echo ""
    else
        echo "⚠️  curl not available for hostname fetching, continuing with current hostname: $(hostname)"
        echo ""
    fi
    
    # Generate SSH key if it doesn't exist (needed for registration)
    if [ ! -f "/home/$USER/.ssh/id_rsa" ]; then
        echo "🔑 Generating SSH key pair..."
        sudo -u $USER ssh-keygen -t rsa -b 4096 -f "/home/$USER/.ssh/id_rsa" -N "" -C "rpi-streamer@$(hostname)"
        echo "✅ SSH key pair generated"
    fi
    
    # Try to register device with gyropilots.org hardware console
    if command -v curl >/dev/null 2>&1; then
        # Read the public key (should exist now)
        local public_key=""
        if [ -f "/home/$USER/.ssh/id_rsa.pub" ]; then
            public_key=$(cat "/home/$USER/.ssh/id_rsa.pub")
        else
            echo "❌ SSH public key not found after generation, setup failed"
            return 1
        fi
        
        echo "📤 Sending device registration and SSH key to server..."
        
        # Invite the user to click on this link to register the hardware
        local encoded_public_key=$(echo "$public_key" | sed 's/ /%20/g' | sed 's/+/%2B/g' | sed 's/=/%3D/g' | sed 's/\//%2F/g')
        echo "🌐 To complete device registration, please visit this link:"
        echo "   https://streamer.lambda-tek.com/admin?command=setup_hardware_device&hardwareid=$hardwareid&public_key=$encoded_public_key&device_hostname=$(hostname)&tunnel_http_port=$http_port&tunnel_ssh_port=$ssh_port"
        echo ""
        echo "📋 Or copy and paste the above URL into your web browser"
        echo ""
        #read -p "Press Enter after completing registration on the website..."
        
        # Verify registration by checking the hardware device
        echo "🔍 Awaiting device registration..."
        local check_url="https://streamer.lambda-tek.com/public_api.php?command=check_hardware_device&hardwareid=$hardwareid&public_key=$encoded_public_key&device_hostname=$(hostname)&tunnel_http_port=$http_port&tunnel_ssh_port=$ssh_port"

        local response=$(curl -s "$check_url" 2>/dev/null || echo "false")
        
        # Check if response indicates successful registration (not "false")
        if [ "$response" != "false" ] && [ -n "$response" ] && [ "$response" != "null" ]; then
            echo "✅ Device registration verified successfully!"
            response='{"status":"success","message":"Device registration verified via check_hardware_device"}'
        else
            echo "❌ Device registration verification failed"
            echo "   Response: $response"
            echo "   This may indicate the registration was not completed successfully"
            response='{"status":"error","message":"Device registration verification failed"}'
        fi
        
        if [ $? -eq 0 ] && [ -n "$response" ]; then
            # Parse JSON response
            local error=$(echo "$response" | grep -o '"error":"[^"]*"' | cut -d'"' -f4)
            local status=$(echo "$response" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
            local message=$(echo "$response" | grep -o '"message":"[^"]*"' | cut -d'"' -f4)
            
            if [ -z "$error" ]; then
                echo "✅ Device registration and SSH key setup successful!"
                echo "📋 Status: $status"
                echo "💬 Message: $message"                
            else
                echo "❌ Server setup failed: $error"
                echo "🔄 Falling back to manual setup instructions..."
                return 1
            fi
        else
            echo "⚠️  Failed to communicate with server, skipping automated setup"
            return 1
        fi
        
        # Create device info file for server management
        cat > "/home/$USER/device-info.json" << EOF
{
    "hardwareid": "$hardwareid",
    "hostname": "$(hostname)",
    "http_port": $http_port,
    "ssh_port": $ssh_port,
    "server_host": "$server_host",
    "local_ip": "$(hostname -I | awk '{print $1}')",
    "registration_date": "$(date -Iseconds)",
    "ssh_command": "ssh $USER@$server_host -p $ssh_port",
    "ssh_forward_command": "ssh -L 8080:localhost:$http_port $server_user@$server_host -p $server_port",
    "device_url": "http://localhost:8080",
    "setup_status": "automated"
}
EOF
        echo "📄 Device info saved to: /home/$USER/device-info.json"
    else
        echo "⚠️  curl not available, skipping automatic registration"
        return 1
    fi
}

# Function to setup reverse SSH tunnel
setup_reverse_ssh_tunnel() {
    echo ""
    echo "=========================================="
    echo "🔒 REVERSE SSH TUNNEL SETUP"
    echo "=========================================="
    echo ""
    echo "This creates a secure tunnel from your RPI to your server"
    echo "allowing SSH port forwarding access without any server configuration."
    echo ""
    
    # Hardcoded server configuration
    server_host="streamer.lambda-tek.com"
    server_port="2024"
    server_user="streamer"
    echo "Using server: $server_user@$server_host:$server_port"
    echo ""
    
    # Auto-generate unique ports based on device hardware ID
    hardwareid=$(cat /proc/cpuinfo | grep Serial | cut -d ' ' -f 2 2>/dev/null || echo "unknown")
    if [ "$hardwareid" = "unknown" ] || [ -z "$hardwareid" ]; then
        hardwareid=$(cat /sys/class/net/*/address 2>/dev/null | head -1 | tr -d ':' || echo "fallback-$(date +%s)")
    fi
    
    # Generate unique ports based on hardware ID hash
    PORT_BASE=$(echo "$hardwareid" | sha256sum | cut -c1-4)
    PORT_BASE=$((16#$PORT_BASE % 25000 + 15000))  # Port range 15000-40000 (avoids Webmin/Usermin)
    
    # Internal tunnel ports (server-side, not exposed) - safe range
    tunnel_http_port=$((PORT_BASE + 30000))  # Internal: 45000-70000 range (safe)
    tunnel_ssh_port=$((PORT_BASE + 30001))
    
    echo ""
    echo "🎯 AUTO-GENERATED UNIQUE PORTS FOR THIS DEVICE:"
    echo "   Hardware ID: $hardwareid"
    echo "   Internal HTTP Tunnel: $tunnel_http_port (server-side only, safe range)"
    echo "   Internal SSH Tunnel: $tunnel_ssh_port (server-side only, safe range)"
    echo "   Access via SSH port forwarding: ssh -L 8080:localhost:80 user@server -p $server_port"
    echo ""
    
    read -p "Use these auto-generated ports? [Y/n]: " use_auto_ports
    if [[ $use_auto_ports =~ ^[Nn] ]]; then
        read -p "Enter internal tunnel port for HTTP (server-side): " tunnel_http_port
        read -p "Enter internal tunnel port for SSH (server-side): " tunnel_ssh_port
    fi

    # Register device with hardware console (includes SSH key generation)
    echo ""
    echo "📝 REGISTERING DEVICE WITH HARDWARE CONSOLE..."
    if register_device_with_console "$hardwareid" "$tunnel_http_port" "$tunnel_ssh_port" "$server_host" "$server_user" "$server_port"; then
        echo ""
        echo "🎉 AUTOMATED SETUP COMPLETE!"
        echo "✅ SSH key automatically registered on your server"

        echo ""
        echo "🔧 Creating reverse SSH tunnel service..."
        # Create the tunnel service after successful registration
        sudo tee /etc/systemd/system/reverse-ssh-tunnel.service >/dev/null << EOF
[Unit]
Description=AutoSSH Reverse Tunnel to $server_host
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
Environment="AUTOSSH_GATETIME=0"
Environment="AUTOSSH_POLL=10"
Environment="AUTOSSH_FIRST_POLL=10"
Restart=always
RestartSec=5
ExecStart=/usr/bin/autossh -M 0 -N -T \\
    -o ServerAliveInterval=15 \\
    -o ServerAliveCountMax=2 \\
    -o ConnectTimeout=10 \\
    -o ExitOnForwardFailure=yes \\
    -o StrictHostKeyChecking=no \\
    -o UserKnownHostsFile=/dev/null \\
    -R 127.0.0.1:$tunnel_http_port:localhost:80 \\
    -R 127.0.0.1:$tunnel_ssh_port:localhost:22 \\
    $server_user@$server_host -p $server_port

[Install]
WantedBy=multi-user.target
EOF
        
        # Enable and start the service
        sudo systemctl daemon-reload
        sudo systemctl enable reverse-ssh-tunnel.service
        sudo systemctl start reverse-ssh-tunnel.service
        
        echo "✅ Reverse SSH tunnel service created and started"
        echo "✅ AutoSSH will maintain persistent tunnel with automatic reconnection"
        echo "⚡ Your RPI Streamer tunnel should connect automatically!"
    else
        echo ""
        echo "❌ AUTOMATED SETUP FAILED"
        echo ""
        echo "The device registration with the hardware console failed."
        echo "Please check your internet connection and try again."
        echo ""
        echo "If the problem persists, contact support with your hardware ID: $hardwareid"
        return 1
    fi
    
    echo ""
    echo "✅ REVERSE SSH TUNNEL SETUP COMPLETE!"
    echo ""
    echo "📋 ACCESS YOUR DEVICE WEB INTERFACE:"
    echo "======================================"
    echo ""
    echo "💻 Command: ssh -L 8080:localhost:$tunnel_http_port $server_user@$server_host -p $server_port"
    echo "🌐 Then visit: http://localhost:8080"
    echo "   Direct SSH: ssh $USER@localhost -p $tunnel_ssh_port (run this on the server)"
    echo ""
    echo "📋 For multiple devices, use different local ports:"
    echo "   Device 1: ssh -L 8081:localhost:$tunnel_http_port $server_user@$server_host -p $server_port"
    echo "   Device 2: ssh -L 8082:localhost:[other_device_tunnel_http_port] $server_user@$server_host -p $server_port"
    echo ""
    echo "📊 AutoSSH Tunnel Status:"
    echo "   Service: systemctl status reverse-ssh-tunnel.service"
    echo "   Logs:    journalctl -u reverse-ssh-tunnel.service -f"
    echo "   AutoSSH will automatically reconnect if connection drops"
    echo ""
    echo "🔧 On your server:"
    echo "   Internal HTTP Tunnel: localhost:$tunnel_http_port"
    echo "   Internal SSH Tunnel:  localhost:$tunnel_ssh_port"
    
    # Store configuration for later reference
    cat > "/home/$USER/tunnel-config.txt" << EOF
# RPI Streamer Reverse SSH Tunnel Configuration
Server: $server_host:$server_port
User: $server_user
Internal HTTP Tunnel: localhost:$tunnel_http_port
Internal SSH Tunnel: localhost:$tunnel_ssh_port
Service: reverse-ssh-tunnel.service (AutoSSH)

# Test tunnel locally on server:
curl http://localhost:$tunnel_http_port

# Connect to RPI via SSH through tunnel:
ssh $USER@localhost -p $tunnel_ssh_port

# Access web interface via port forwarding:
ssh -L 8080:localhost:$tunnel_http_port $server_user@$server_host -p $server_port
# Then visit: http://localhost:8080

# AutoSSH will automatically maintain the connection and reconnect if needed
EOF
    
    echo "📄 Configuration saved to: /home/$USER/tunnel-config.txt"
}

# Function for interactive remote access menu
setup_remote_access_menu() {
    echo ""
    echo "🌐 REMOTE ACCESS SETUP"
    echo "======================================"
    echo ""
    echo "Would you like to set up reverse SSH tunnel for remote access?"
    echo "This allows secure access to your device from anywhere via your server."
    echo ""
    echo "Options:"
    echo "  1) Yes - Set up reverse SSH tunnel (recommended)"
    echo "  2) No - Skip remote access setup"
    echo ""
    
    read -p "Enter your choice (1-2) [1]: " access_choice
    access_choice=${access_choice:-1}
    
    case $access_choice in
        1)
            setup_reverse_ssh_tunnel
            ;;
        2)
            echo "Skipping remote access setup"
            return
            ;;
        *)
            echo "Invalid choice, skipping remote access setup"
            return
            ;;
    esac
}

# Print completion message
echo ""
echo "🎉 RPI STREAMER INSTALLATION COMPLETED!"
echo "=========================================="
echo ""
echo "🏠 Local Access:"
echo "   HTTP: http://$(hostname -I | awk '{print $1}')"
echo "   SSH:  ssh $USER@$(hostname -I | awk '{print $1}')"
echo ""

echo ""

echo "⚙️ Flight Settings available for GPS tracking configuration"
echo "🔧 Configure GPS username and tracking modes in Flight Settings"
echo ""
echo "🌐 Network Priority Configuration:"
echo "   🥇 Ethernet (eth0): Primary connection with highest priority"
echo "   🥈 Cellular (wwan*): Backup connection with automatic failover"
echo "   📊 Priority logs: tail -f /var/log/network-priority.log"
echo "   🔧 Network status: nmcli connection show"
echo ""
echo "🚀 Services installed and running:"
echo "   ✅ Flask App (HTTP server on port 80)"
echo "   ✅ MediaMTX (Streaming server)" 
echo "   💓 Heartbeat Daemon (independent device monitoring)"
echo "   ⚙️ GPS Daemon (auto-starts and enables GPS with hardware)"
echo "   ⚙️ GPS Startup Manager (configure via web interface)"
if systemctl is-active --quiet reverse-ssh-tunnel.service; then
    echo "   🔒 Reverse SSH Tunnel (secure remote access)"
fi
echo "   🔑 SSH Server (remote terminal access)"

echo ""
echo "🛠️ Installation Script Options:"
echo "   --daemon       : Run in daemon mode (no interactive prompts)"
echo ""
echo "🌐 Examples:"
echo "   bash install_rpi_streamer.sh                      # Interactive installation"
echo "   bash install_rpi_streamer.sh --daemon             # Silent installation"
echo ""
echo "📚 Documentation:"
echo "   GPS Tracker: GPS_TRACKER_README.md"
echo "   Flight Settings: FLIGHT_SETTINGS.md"
echo "   Multi-Device Setup: MULTI_DEVICE_SETUP.md"
echo ""
echo "🌐 Network Testing Commands:"
echo "   nmcli connection show                              # Show all connections"
echo "   ip route show                                      # Show routing table"
echo "   ping -I eth0 8.8.8.8                             # Test ethernet connectivity"
echo "   ping -I wwan0 8.8.8.8                            # Test cellular connectivity"
echo "   tail -f /var/log/network-priority.log             # Monitor network priority events"
echo ""
echo "�️ GPS Testing Commands:"
echo "   python3 $HOME/flask_app/gps_client.py --status    # Check daemon status"
echo "   python3 $HOME/flask_app/gps_client.py --location  # Get current location"
echo "   sudo journalctl -u gps-daemon -f                  # View daemon logs"
echo ""
echo "�🔋 Optional UPS Management:"
echo "   Install UPS monitoring for battery backup systems:"
echo "   curl -H \"Cache-Control: no-cache\" -O https://raw.githubusercontent.com/tfelici/RPI-Streamer/$BRANCH_NAME/install_ups_management.sh"
echo "   bash install_ups_management.sh"

# Generate unique hardware identifier and register hardware
echo ""
echo "=========================================="
echo "🔧 HARDWARE REGISTRATION"
echo "=========================================="

# Generate unique hardware ID using multiple system identifiers
hardwareid=$(cat /proc/cpuinfo | grep Serial | cut -d ' ' -f 2 2>/dev/null || echo "unknown")
if [ "$hardwareid" = "unknown" ] || [ -z "$hardwareid" ]; then
    # Fallback to MAC address if no serial number
    hardwareid=$(cat /sys/class/net/*/address 2>/dev/null | head -1 | tr -d ':' || echo "fallback-$(date +%s)")
fi

echo "📋 Hardware ID: $hardwareid"
echo "🔗 Registering device with hardware console..."
# Remote Access Setup
if [[ "$@" == *"--daemon"* ]]; then
    echo ""
    echo "🤖 Running in daemon mode - skipping remote access setup"
else
    setup_remote_access_menu
fi

# WiFi Hotspot Setup Option
echo ""
echo "=========================================="
echo "📶 WIFI HOTSPOT CONFIGURATION"
echo "=========================================="
echo ""

if [[ "$@" == *"--daemon"* ]]; then
    echo "🤖 Running in daemon mode - skipping hotspot configuration"
    echo "   WiFi hotspot can be configured later via the web interface"
else
    echo "Would you like to configure this device as a WiFi hotspot?"
    echo "This allows you to connect directly to the device when no internet is available."
    echo ""
    echo "Options:"
    echo "  1) Yes - Create WiFi hotspot now (default)"
    echo "  2) No - Keep current network configuration"
    echo ""
    read -p "Enter your choice (1-2) [1]: " hotspot_choice
    hotspot_choice=${hotspot_choice:-1}

    case $hotspot_choice in
        1)
            echo ""
            echo "🔧 Setting up WiFi hotspot..."
            
            # Ensure WiFi interface is ready before configuration
        echo "📡 Preparing WiFi interface for hotspot mode..."
        sudo rfkill unblock wifi 2>/dev/null || true
        sudo ip link set wlan0 up 2>/dev/null || echo "Warning: Could not bring up wlan0 interface"
        
        # Verify WiFi interface is available
        if ! ip link show wlan0 >/dev/null 2>&1; then
            echo "❌ WiFi interface wlan0 not found!"
            echo "   This device may not have WiFi capability or the driver is not loaded."
            echo "   Skipping hotspot configuration."
            echo ""
            break
        fi
        
        echo "✅ WiFi interface ready for hotspot configuration"
        
        # Wait for Flask app to be ready first
        echo "⏳ Waiting for Flask app to start..."
        flask_ready=false
        for i in {1..30}; do
            if curl -s http://localhost/system-settings-data >/dev/null 2>&1; then
                echo "✅ Flask app is ready"
                flask_ready=true
                break
            fi
            if [ $i -eq 30 ]; then
                echo "❌ Flask app did not start in time, skipping hotspot setup"
                echo "   You can configure the hotspot later via the web interface"
                echo "   Exiting hotspot configuration..."
                break
            fi
            sleep 2
        done
        
        # Exit early if Flask app is not reachable
        if [ "$flask_ready" = "false" ]; then
            echo ""
            echo "⚠️  Cannot configure hotspot without Flask app running"
            echo "   Please ensure the RPI Streamer service is running and try again"
            echo "   Check status with: sudo systemctl status flask_app"
            break
        fi
        
        # Get current settings from the application
        echo "📡 Getting current WiFi settings from application..."
        
        # Fetch current settings via API
        settings_response=$(curl -s http://localhost/system-settings-data)
        
        # Extract current WiFi settings using jq (guaranteed to be available)
        current_hotspot_ssid=$(echo "$settings_response" | jq -r '.wifi.hotspot_ssid // ""')
        current_hotspot_password=$(echo "$settings_response" | jq -r '.wifi.hotspot_password // ""')
        current_hotspot_channel=$(echo "$settings_response" | jq -r '.wifi.hotspot_channel // 6')
        current_hotspot_ip=$(echo "$settings_response" | jq -r '.wifi.hotspot_ip // "192.168.4.1"')
        
        # Use current settings as defaults, with fallbacks
        default_ssid="${current_hotspot_ssid:-RPI-Streamer-${hardwareid: -6}}"
        default_password="${current_hotspot_password:-rpistreamer123}"
        default_channel="${current_hotspot_channel:-6}"
        default_ip="${current_hotspot_ip:-192.168.4.1}"
        
        echo "📋 Current hotspot settings loaded from application"
        
        # Get hotspot configuration from user with current settings as defaults
        read -p "Enter hotspot SSID [$default_ssid]: " hotspot_ssid
        hotspot_ssid=${hotspot_ssid:-$default_ssid}
        
        while true; do
            read -s -p "Enter hotspot password (minimum 8 characters) [$default_password]: " hotspot_password
            echo ""
            # Use default if no input provided
            hotspot_password=${hotspot_password:-$default_password}
            if [ ${#hotspot_password} -ge 8 ]; then
                break
            else
                echo "❌ Password must be at least 8 characters long. Please try again."
            fi
        done
        
        read -p "Enter hotspot channel [$default_channel]: " hotspot_channel
        hotspot_channel=${hotspot_channel:-$default_channel}
        
        read -p "Enter hotspot IP address [$default_ip]: " hotspot_ip
        hotspot_ip=${hotspot_ip:-$default_ip}
        
        # Configure hotspot via API (Flask app already verified as running)
        echo "🔧 Configuring WiFi hotspot..."
        
        response=$(curl -s -X POST \
            -H "Content-Type: application/json" \
            -d "{
                \"mode\": \"hotspot\",
                \"hotspot_ssid\": \"$hotspot_ssid\",
                \"hotspot_password\": \"$hotspot_password\",
                \"hotspot_channel\": $hotspot_channel,
                \"hotspot_ip\": \"$hotspot_ip\"
            }" \
            http://localhost/system-settings-wifi-mode)
        
        if echo "$response" | grep -q '"success": *true'; then
            echo "✅ WiFi hotspot configured successfully!"
            echo "💾 Hotspot persistence is handled by the Flask app's NetworkManager integration"
            echo ""
            echo "📶 Hotspot Details:"
            echo "   SSID: $hotspot_ssid"
            echo "   IP Address: $hotspot_ip"
            echo "   Channel: $hotspot_channel"
            echo ""
            echo "🔌 Connect to the hotspot and access:"
            echo "   HTTP: http://$hotspot_ip"
            echo ""
            echo "ℹ️  You can switch back to client mode anytime via the web interface"
        else
            echo "❌ Failed to configure WiFi hotspot"
            echo "   Response: $response"
            echo "   You can configure the hotspot later via the web interface"
        fi
        ;;
    2)
        echo "ℹ️  Keeping current network configuration"
        echo "   You can set up a WiFi hotspot later via the web interface at:"
        echo "   System Settings > WiFi Settings > Hotspot Mode"
        ;;
    *)
        echo "❌ Invalid choice, keeping current network configuration"
        ;;
    esac
fi