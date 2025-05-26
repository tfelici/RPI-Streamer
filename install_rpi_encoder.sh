#!/bin/bash
# RPI Encoder installation script
# Usage: sudo bash install_rpi_encoder.sh

# This script installs the RPI Encoder Flask app and MediaMTX on a Raspberry Pi running Raspberry Pi OS Lite.
# It also sets up a systemd service for the Flask app and MediaMTX, and installs Tailscale for remote access.
################################################
set -e

# Update and install dependencies
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip -y
sudo apt-get install ffmpeg -y
pip install flask
pip install requests
sudo apt-get install gunicorn python3-gevent -y

# Setup flask app directory
echo "Setting up Flask app directory..."
mkdir -p /home/admin/flask_app
mkdir -p /home/admin/encoderData
cd /home/admin/flask_app


#     Force update the codebase to match the remote GitHub repository (overwriting local changes, restoring missing files, removing extra tracked files), fix permissions, and restart services.
#     This is useful if the codebase has been modified locally and you want to reset it to the latest version from the remote repository.
#     If the repository is not already cloned, it will clone it.
printf "Updating RPI Encoder codebase..."

# Check if git is installed
if ! command -v git &> /dev/null; then
    echo "Git not found, installing..."
    sudo apt-get install git -y
fi

# If repository exists, force update it
# If not, clone it fresh
echo "Current directory: $(pwd)"
#this command is needed to allow the flask app to run in the /home/admin/flask_app directory
#note: it must run as sudo as the flask app is run as root
git config --global --add safe.directory /home/admin/flask_app
if [ -d .git ]; then
    git fetch --all
    git reset --hard origin/main
    git clean -f -d
else
    echo "Repository not found, cloning fresh copy..."
    git clone https://github.com/tfelici/RPI-Encoder.git .
fi

echo "Repository update complete"

# Check if the app.py file exists
if [ ! -f app.py ]; then
    echo "Error: app.py file not found in the Flask app directory."
    exit 1
fi
#change ownership of the flask_app directory to the admin user
sudo chown -R admin:admin /home/admin/flask_app
# Check if the flask_app directory is writable
if [ ! -w /home/admin/flask_app ]; then
    echo "Error: Flask app directory is not writable."
    exit 1
fi

# search and install latest version of mediamtx
printf "Searching for the latest MediaMTX release...\n"
# Check if jq is installed, if not, install it
# Ensure jq is installed
if ! command -v jq &> /dev/null; then
    sudo apt-get install jq -y
fi
latest_url=$(curl -s https://api.github.com/repos/bluenviron/mediamtx/releases/latest | jq -r '.assets[] | select(.browser_download_url | endswith("linux_arm64.tar.gz")) | .browser_download_url')
printf "Latest MediaMTX URL: %s\n" "$latest_url"
if [ -z "$latest_url" ]; then
    echo "Error: Could not find the latest MediaMTX release URL."
    exit 1
fi
cd /home/admin
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
#mv mediamtx.yml flask_app

# Create systemd service for flask app - this must run after the install_rpi_encoder.service
printf "Creating systemd service for Flask app...\n"
sudo tee /etc/systemd/system/flask_app.service >/dev/null << EOF
[Unit]
Description=RPI Encoder
After=network.target
[Service]
User=root
WorkingDirectory=/home/admin/flask_app
ExecStart=/usr/bin/python3 app.py
#ExecStart=/usr/bin/gunicorn -w 1 -k gevent --threads 4 -b 0.0.0.0:80 app:app
Restart=always
[Install]
WantedBy=multi-user.target
EOF

# Create systemd service for mediamtx - this must run after the install_rpi_encoder.service
printf "Creating systemd service for MediaMTX...\n"
sudo tee /etc/systemd/system/mediamtx.service >/dev/null << EOF
[Unit]
Description=MediaMTX Streaming Server
After=network.target
[Service]
User=root
WorkingDirectory=/home/admin/flask_app
ExecStart=/home/admin/mediamtx /home/admin/flask_app/mediamtx.yml
Restart=always
[Install]
WantedBy=multi-user.target
EOF

#create a systemd service for this script
printf "Creating systemd service for this script...\n"
sudo tee /etc/systemd/system/install_rpi_encoder.service >/dev/null << EOF
[Unit]
Description=RPI Encoder Installation Script
After=network.target
[Service]
User=admin
Type=oneshot
ExecStart=/bin/bash /home/admin/flask_app/install_rpi_encoder.sh
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable install_rpi_encoder
sudo systemctl enable flask_app
sudo systemctl restart flask_app
sudo systemctl enable mediamtx
sudo systemctl restart mediamtx


# Install tailscale
printf "Installing Tailscale...\n"
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up