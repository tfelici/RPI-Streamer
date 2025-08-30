#!/bin/bash
# RPI Streamer installation script
# Usage: bash install_rpi_streamer.sh [OPTIONS]
#
# Options:
#   --skip-update     Skip updating the codebase from GitHub repository
#                     (use existing local files without checking for updates)
#   --reverse-ssh     Setup reverse SSH tunnel to your server for remote access
#   --tailscale       Setup Tailscale VPN for secure mesh networking
#   --remote          Interactive remote access menu
#
# Examples:
#   bash install_rpi_streamer.sh                     # Basic installation with update check
#   bash install_rpi_streamer.sh --skip-update      # Installation without updating codebase
#   bash install_rpi_streamer.sh --reverse-ssh      # Installation with reverse SSH tunnel
#   bash install_rpi_streamer.sh --tailscale        # Installation with Tailscale VPN

# This script installs the RPI Streamer Flask app and MediaMTX on a Raspberry Pi running Raspberry Pi OS Lite.
# It also sets up a systemd service for the Flask app and MediaMTX, and installs Tailscale for remote access.
# SIM7600G-H 4G dongle support is installed automatically for all deployments.
################################################

set -e

# Wait for internet connectivity (max 20 seconds)
for i in {1..20}; do
    if ping -c 1 github.com &>/dev/null; then
        echo "Internet is up."
        break
    fi
    echo "Waiting for internet connection..."
    sleep 1
done

# If still no connection, exit with error
if ! ping -c 1 github.com &>/dev/null; then
    echo "No internet connection after 20 seconds, aborting installation."
    exit 1
fi

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

# GPS Tracker dependencies
sudo apt-get install python3-serial -y

# RPi.GPIO is only available on Raspberry Pi hardware
if grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null || [ -f /boot/config.txt ]; then
    echo "Raspberry Pi detected - installing RPi.GPIO package..."
    sudo apt-get install python3-rpi.gpio -y
else
    echo "Not running on Raspberry Pi - skipping RPi.GPIO package (GPS hardware features will be limited)"
fi

# SIM7600G-H 4G dongle dependencies (installed always for potential use)
sudo apt-get install minicom screen ppp usb-modeswitch usb-modeswitch-data -y

# WiFi hotspot dependencies (for hotspot mode functionality)
sudo apt-get install hostapd dnsmasq -y

# AutoSSH for reliable reverse tunnel management
sudo apt-get install autossh -y

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


# Setup flask app directory
echo "Setting up Flask app directory... $HOME/flask_app"
mkdir -p "$HOME/flask_app"
mkdir -p "$HOME/streamerData"
cd "$HOME/flask_app"


#     Update the codebase to match the remote GitHub repository if changes are available.
#     This ensures the device has the latest version from the remote repository.
#     If the repository is not already cloned, it will clone it fresh.
#     
#     The --skip-update option allows you to:
#       - Use existing local files without checking for updates
#       - Skip the git fetch and reset operations
#       - Maintain any local modifications you may have made
#       - Avoid network calls to GitHub during installation
#
#     Use --skip-update when:
#       - You want to use a specific local version of the code
#       - You have made local modifications you want to preserve
#       - You're in an environment with limited internet connectivity
#       - You're testing local changes and don't want them overwritten
echo "Updating RPI Streamer codebase..."

# Check if git is installed
if ! command -v git &> /dev/null; then
    echo "Git not found, installing..."
    sudo apt-get install git -y
fi

# If repository exists, update it unless --skip-update is specified
# If not, clone it fresh
if [ -d .git ]; then
    #only skip update if --skip-update is passed
    if [[ "$@" == *"--skip-update"* ]]; then
        echo "Skipping repository update (--skip-update specified)"
    else
        sudo git fetch --all
        sudo git reset --hard origin/main
        sudo git clean -f -d
        echo "Repository updated to latest version"
    fi
else
    rm -rf *
    echo "Repository not found, cloning fresh copy..."
    sudo git clone https://github.com/tfelici/RPI-Streamer.git .
fi
#change ownership of the flask_app directory to the current user
sudo chown -R "$USER":"$USER" "$HOME/flask_app"
#this command is needed to allow any users to run git in the flask_app directory
sudo git config --global --add safe.directory "$HOME/flask_app"
echo "Repository update complete"

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

# Check and download Windows executable
check_and_download_executable "Windows" "Uploader-windows.exe" "windows/dist/StreamerUploader.exe" "https://github.com/tfelici/Streamer-Uploader/raw/main/windows/dist/StreamerUploader.exe"

# Check and download macOS executable
check_and_download_executable "macOS" "Uploader-macos" "macos/dist/StreamerUploader" "https://github.com/tfelici/Streamer-Uploader/raw/main/macos/dist/StreamerUploader"

# Check and download Linux executable
check_and_download_executable "Linux" "Uploader-linux" "linux/dist/StreamerUploader" "https://github.com/tfelici/Streamer-Uploader/raw/main/linux/dist/StreamerUploader"

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
After=network.target
[Service]
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
printf "Creating systemd service for MediaMTX...\n"
sudo tee /etc/systemd/system/mediamtx.service >/dev/null << EOF
[Unit]
Description=MediaMTX Streaming Server
After=network.target
[Service]
User=root
WorkingDirectory=$HOME/flask_app
ExecStart=$HOME/mediamtx $HOME/flask_app/mediamtx.yml
Restart=always
[Install]
WantedBy=multi-user.target
EOF

# Install GPS Startup Manager Service
printf "?? Installing GPS Startup Manager Service...\n"

# Make the GPS startup script executable
chmod +x "$HOME/flask_app/gps_startup_manager.py"
echo "? Made GPS startup script executable"

# Create the GPS startup service
printf "Creating systemd service for GPS Startup Manager...\n"
sudo tee /etc/systemd/system/gps-startup.service >/dev/null << EOF
[Unit]
Description=GPS Startup Manager for RPI Streamer
After=network.target sim7600-internet.service
Wants=network.target
Requires=sim7600-internet.service

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$HOME/flask_app
ExecStart=/usr/bin/python3 $HOME/flask_app/gps_startup_manager.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "? GPS Startup Manager service installed"
echo "?? GPS Service Info:"
echo "   � Service will be enabled/disabled automatically based on Flight Settings"
echo "   � Configure GPS start mode in the Flight Settings page"
echo "   � Manual control: sudo systemctl status gps-startup.service"

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
ExecStart=/usr/bin/curl -H "Cache-Control: no-cache" -L -o $HOME/flask_app/install_rpi_streamer.sh "https://raw.githubusercontent.com/tfelici/RPI-Streamer/main/install_rpi_streamer.sh?$(date +%s)"
ExecStartPost=/bin/bash -e $HOME/flask_app/install_rpi_streamer.sh
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable install_rpi_streamer
sudo systemctl enable flask_app
sudo systemctl restart flask_app
sudo systemctl enable mediamtx
sudo systemctl restart mediamtx

# Note: GPS startup service is installed but not enabled by default
# It will be automatically enabled/disabled based on Flight Settings configuration
echo "?? GPS Startup Service: Available but not enabled (configure via Flight Settings)"

# Install SIM7600G-H internet setup (always installed)
echo ""
echo "=========================================="
echo "📡 SIM7600G-H 4G DONGLE Internet Setup"
echo "=========================================="
echo ""

# Always install the basic dependencies and drivers
echo "📦 Installing SIM7600G-H dependencies and drivers..."

# Install required packages for SIM7600 support
sudo apt-get update -qq
sudo apt-get install -y usb-modeswitch usb-modeswitch-data minicom screen ppp

echo "✅ SIM7600G-H dependencies installed"

# Create the auto-startup service regardless of hardware presence
echo "🔧 Creating SIM7600G-H auto-startup service..."
sudo tee /etc/systemd/system/sim7600-internet.service >/dev/null << 'EOFSERVICE'
[Unit]
Description=SIM7600G-H 4G Internet Connection and Daemon
After=network.target
Wants=network.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c '\
    sleep 30 && \
    if lsusb | grep -q "1e0e:9011"; then \
        # Check if SIM7600 already has a working connection \
        for iface in $(ip link show | grep "usb[0-9]" | cut -d: -f2 | tr -d " "); do \
            if ip addr show $iface | grep -q "inet " && \
               ip link show $iface | grep -q "state UP" && \
               timeout 5 ping -c 1 8.8.8.8 >/dev/null 2>&1; then \
                echo "SIM7600 working connection found on $iface, skipping setup"; \
                break; \
            fi; \
        done; \
        # Try to establish connection on available interfaces if not already connected \
        if ! timeout 5 ping -c 1 8.8.8.8 >/dev/null 2>&1; then \
            for iface in $(ip link show | grep -E "usb[0-9]|eth1" | cut -d: -f2 | tr -d " "); do \
                echo "Setting up $iface..."; \
                # Release any existing DHCP lease and bring interface down/up \
                dhclient -r $iface 2>/dev/null || true; \
                ip link set $iface down 2>/dev/null; \
                sleep 2; \
                if ip link set $iface up 2>/dev/null && \
                   timeout 30 dhclient -v $iface 2>/dev/null && \
                   timeout 5 ping -c 1 8.8.8.8 >/dev/null 2>&1; then \
                    echo "SIM7600 connected on $iface with internet access"; \
                    break; \
                fi; \
            done; \
        fi; \
        # Start SIM7600 daemon after internet is established \
        if timeout 5 ping -c 1 8.8.8.8 >/dev/null 2>&1; then \
            echo "Starting SIM7600 communication daemon..."; \
            if [ -f "'$HOME'/flask_app/sim7600_daemon.py" ]; then \
                cd "'$HOME'/flask_app" && \
                /usr/bin/python3 sim7600_daemon.py --host localhost --port 7600 & \
                echo "SIM7600 daemon started on port 7600"; \
            else \
                echo "SIM7600 daemon not found at '$HOME'/flask_app/sim7600_daemon.py"; \
            fi; \
        else \
            echo "No internet connection available, skipping daemon startup"; \
        fi; \
    fi'
RemainAfterExit=yes
User=root

[Install]
WantedBy=multi-user.target
EOFSERVICE

sudo systemctl daemon-reload
sudo systemctl enable sim7600-internet.service

# Create udev rule for automatic reconnection handling
echo "🔧 Creating udev rule for automatic dongle reconnection..."
sudo tee /etc/udev/rules.d/99-sim7600-internet.rules >/dev/null << 'EOFUDEV'
# SIM7600G-H automatic internet connection on connect/reconnect
# Trigger on SIM7600 device connection
SUBSYSTEM=="usb", ATTR{idVendor}=="1e0e", ATTR{idProduct}=="9011", ACTION=="add", RUN+="/bin/systemctl restart sim7600-internet.service"

# Trigger on USB network interface creation (usb0, usb1, etc.)
SUBSYSTEM=="net", KERNEL=="usb*", ACTION=="add", RUN+="/bin/bash -c 'sleep 5 && /bin/systemctl restart sim7600-internet.service'"
EOFUDEV

# Reload udev rules
sudo udevadm control --reload-rules

echo "✅ SIM7600G-H service and udev rules created"
echo ""
echo "📋 Setup completed! The system will automatically:"
echo "   1. Detect the dongle when connected"
echo "   2. Configure RNDIS mode if needed"
echo "   3. Establish internet connection"
echo "   4. Start SIM7600 communication daemon on port 7600"
echo ""
echo "🔌 To use the dongle:"
echo "   1. Insert SIM card into the dongle"
echo "   2. Connect dongle to USB port"
echo "   3. Wait 30-60 seconds for auto-configuration"
echo "   4. Check connection: ip addr show usb0"
echo "   5. Daemon will be available on localhost:7600"
echo ""
echo "✅ SIM7600G-H setup completed (internet + daemon will auto-start)"

SIM7600_STATUS=0

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
        local new_hostname=$(curl -s "https://streamer.lambda-tek.com/?command=findorcreatehostname&hardwareid=$hardwareid" 2>/dev/null)
        
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
        local check_url="https://streamer.lambda-tek.com?command=check_hardware_device&hardwareid=$hardwareid&public_key=$encoded_public_key&device_hostname=$(hostname)&tunnel_http_port=$http_port&tunnel_ssh_port=$ssh_port"

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
        echo "📋 Your SSH public key:"
        echo "============================================================"
        cat "/home/$USER/.ssh/id_rsa.pub"
        echo "============================================================"
        
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
Environment="AUTOSSH_GATETIME=30"
Environment="AUTOSSH_POLL=30"
Environment="AUTOSSH_FIRST_POLL=30"
Restart=always
RestartSec=10
ExecStart=/usr/bin/autossh -M 0 -N -T \\
    -o ServerAliveInterval=60 \\
    -o ServerAliveCountMax=3 \\
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

# Function for Tailscale setup
setup_tailscale_vpn() {
    echo ""
    echo "=========================================="
    echo "🔐 TAILSCALE VPN SETUP"
    echo "=========================================="
    echo ""
    echo "📦 Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
    echo "✅ Tailscale installed successfully!"
    echo ""
    echo "🚀 Starting Tailscale connection..."
    sudo tailscale up --accept-routes --accept-dns
    echo ""
    echo "📋 Your Tailscale IP address:"
    tailscale ip -4 2>/dev/null || echo "   Run 'tailscale ip' after authentication"
    echo ""
    echo "🌐 Remote Access URLs (after Tailscale authentication):"
    echo "   HTTP: http://$(tailscale ip -4 2>/dev/null || echo '[TAILSCALE-IP]')"
    echo "   SSH:  ssh $(whoami)@$(tailscale ip -4 2>/dev/null || echo '[TAILSCALE-IP]')"
    echo ""
    echo "📱 Complete setup:"
    echo "   1. Install Tailscale on your phone/computer"
    echo "   2. Login with the same account"
    echo "   3. Access your RPI Streamer from anywhere!"
    TAILSCALE_INSTALLED=true
}

# Function for interactive remote access menu
setup_remote_access_menu() {
    echo ""
    echo "🌐 REMOTE ACCESS SETUP"
    echo "======================================"
    echo ""
    echo "Choose your remote access method:"
    echo "1) Reverse SSH Tunnel (to your server) - Recommended"
    echo "2) Tailscale VPN (mesh networking)"
    echo "3) Skip remote access setup"
    echo ""
    
    read -p "Enter your choice [1-3]: " access_choice
    
    case $access_choice in
        1)
            setup_reverse_ssh_tunnel
            ;;
        2)
            setup_tailscale_vpn
            ;;
        3)
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

if [ "$TAILSCALE_INSTALLED" = true ]; then
    echo "🔐 Remote Access (Tailscale VPN):"
    echo "   HTTP: http://$(tailscale ip -4 2>/dev/null || echo '[TAILSCALE-IP-AFTER-AUTH]')"
    echo "   SSH:  ssh $USER@$(tailscale ip -4 2>/dev/null || echo '[TAILSCALE-IP-AFTER-AUTH]')"
    echo "   📱 Install Tailscale app on your devices and login"
    echo ""
fi

echo "⚙️ Flight Settings available for GPS tracking configuration"
echo "🔧 Configure GPS username and tracking modes in Flight Settings"
echo ""
echo "🚀 Services installed and running:"
echo "   ✅ Flask App (HTTP server on port 80)"
echo "   ✅ MediaMTX (Streaming server)" 
echo "   ⚙️ GPS Startup Manager (configure via web interface)"
echo "   📡 SIM7600G-H Internet (auto-configured)"
if [ "$TAILSCALE_INSTALLED" = true ]; then
    echo "   🔐 Tailscale VPN (secure remote access)"
fi
if systemctl is-active --quiet reverse-ssh-tunnel.service; then
    echo "   � Reverse SSH Tunnel (secure remote access)"
fi
echo "   �🔑 SSH Server (remote terminal access)"

echo ""
echo "🛠️ Installation Script Options:"
echo "   --skip-update  : Skip updating codebase from GitHub (use existing local files)"
echo "   --reverse-ssh  : Reverse SSH tunnel to your server"
echo "   --tailscale    : Tailscale VPN for secure mesh networking"
echo "   --remote       : Interactive remote access menu"
echo ""
echo "🌐 Examples:"
echo "   bash install_rpi_streamer.sh --reverse-ssh"
echo "   bash install_rpi_streamer.sh --tailscale"
echo "   bash install_rpi_streamer.sh --skip-update"
echo ""
echo "📚 Documentation:"
echo "   GPS Tracker: GPS_TRACKER_README.md"
echo "   SIM7600 Setup: SIM7600_INTERNET_SETUP.md"

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
if [[ "$@" == *"--reverse-ssh"* ]]; then
    setup_reverse_ssh_tunnel
elif [[ "$@" == *"--tailscale"* ]]; then
    setup_tailscale_vpn
elif [[ "$@" == *"--remote"* ]]; then
    setup_remote_access_menu
else
    echo ""
    echo "🌐 For remote access setup, run with one of these flags:"
    echo "   --reverse-ssh  : Reverse SSH tunnel to your server (recommended)"
    echo "   --tailscale    : Tailscale VPN mesh networking"
    echo "   --remote       : Interactive remote access menu"
fi