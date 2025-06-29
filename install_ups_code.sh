#!/bin/bash

set -e  # Exit on any error

echo "?? Updating system packages..."
sudo apt-get update
sudo apt-get upgrade -y

echo "?? Installing required packages..."
sudo apt-get install -y python3-pip i2c-tools git rpi-eeprom

echo "?? Updating EEPROM config..."

# Temp working directory
TMPDIR=$(mktemp -d)
cd "$TMPDIR"

# Dump current EEPROM config (no rpi-eeprom-dump)
sudo rpi-eeprom-config > bootconf.txt

# Modify EEPROM settings
echo "?? Applying EEPROM settings..."
if grep -q "^POWER_OFF_ON_HALT=" bootconf.txt; then
  sed -i 's/^POWER_OFF_ON_HALT=.*/POWER_OFF_ON_HALT=1/' bootconf.txt
else
  echo "POWER_OFF_ON_HALT=1" >> bootconf.txt
fi

if grep -q "^PSU_MAX_CURRENT=" bootconf.txt; then
  sed -i 's/^PSU_MAX_CURRENT=.*/PSU_MAX_CURRENT=5000/' bootconf.txt
else
  echo "PSU_MAX_CURRENT=5000" >> bootconf.txt
fi

# Create and flash EEPROM update
echo "? Finding EEPROM image..."
EEPROM_IMAGE=""

# Try different possible locations for EEPROM images
if ls /lib/firmware/raspberrypi/bootloader/stable/pieeprom-*.bin 1> /dev/null 2>&1; then
    EEPROM_IMAGE="$(ls -1 /lib/firmware/raspberrypi/bootloader/stable/pieeprom-*.bin | sort | tail -n 1)"
    echo "Found stable EEPROM image: $EEPROM_IMAGE"
elif ls /lib/firmware/raspberrypi/bootloader/latest/pieeprom-*.bin 1> /dev/null 2>&1; then
    EEPROM_IMAGE="$(ls -1 /lib/firmware/raspberrypi/bootloader/latest/pieeprom-*.bin | sort | tail -n 1)"
    echo "Found latest EEPROM image: $EEPROM_IMAGE"
elif [ -f /lib/firmware/raspberrypi/bootloader/latest/pieeprom.bin ]; then
    EEPROM_IMAGE="/lib/firmware/raspberrypi/bootloader/latest/pieeprom.bin"
    echo "Found pieeprom.bin: $EEPROM_IMAGE"
else
    echo "No EEPROM image found, using rpi-eeprom-config direct method..."
    # Try direct config update without specifying image
    if sudo rpi-eeprom-config --config bootconf.txt --out pieeprom.upd; then
        if [ -f pieeprom.upd ]; then
            echo "? Flashing EEPROM..."
            # Copy to boot partition where rpi-eeprom-update expects it
            sudo cp pieeprom.upd /boot/firmware/pieeprom.upd 2>/dev/null || sudo cp pieeprom.upd /boot/pieeprom.upd
            sudo rpi-eeprom-update -d -f pieeprom.upd
        else
            echo "Error: pieeprom.upd was not created successfully."
            echo "Trying alternative method..."
        fi
    else
        echo "Error: rpi-eeprom-config failed. Trying alternative method..."
    fi
    
    # If direct method failed, try alternative method
    if [ ! -f pieeprom.upd ]; then
        echo "Creating temporary config script..."
        cat > update_eeprom.sh << 'EOF'
#!/bin/bash
TMPCONF=$(mktemp)
sudo rpi-eeprom-config > "$TMPCONF"
if ! grep -q "^POWER_OFF_ON_HALT=" "$TMPCONF"; then
    echo "POWER_OFF_ON_HALT=1" >> "$TMPCONF"
else
    sed -i 's/^POWER_OFF_ON_HALT=.*/POWER_OFF_ON_HALT=1/' "$TMPCONF"
fi
if ! grep -q "^PSU_MAX_CURRENT=" "$TMPCONF"; then
    echo "PSU_MAX_CURRENT=5000" >> "$TMPCONF"
else
    sed -i 's/^PSU_MAX_CURRENT=.*/PSU_MAX_CURRENT=5000/' "$TMPCONF"
fi
sudo rpi-eeprom-config --apply "$TMPCONF"
rm -f "$TMPCONF"
EOF
        chmod +x update_eeprom.sh
        if ./update_eeprom.sh; then
            echo "EEPROM updated using alternative method."
        else
            echo "Warning: Could not update EEPROM. You may need to manually run: sudo rpi-eeprom-config --edit"
        fi
        rm -f update_eeprom.sh
    fi
    # Skip the rest of EEPROM update
    EEPROM_IMAGE="SKIP"
fi

if [ "$EEPROM_IMAGE" != "SKIP" ] && [ -n "$EEPROM_IMAGE" ]; then
    sudo rpi-eeprom-config --out pieeprom.upd --config bootconf.txt "$EEPROM_IMAGE"
    echo "? Flashing EEPROM..."
    sudo rpi-eeprom-update -d -f pieeprom.upd
fi

# Enable I2C in /boot/firmware/config.txt
CONFIG_FILE="/boot/firmware/config.txt"
echo "?? Enabling I2C in config.txt..."
if ! grep -q "^dtparam=i2c_arm=on" "$CONFIG_FILE"; then
  echo "dtparam=i2c_arm=on" | sudo tee -a "$CONFIG_FILE" > /dev/null
fi

# Ensure i2c-dev module loads at boot
MODULES_FILE="/etc/modules"
echo "?? Ensuring i2c-dev is in /etc/modules..."
if ! grep -q "^i2c-dev" "$MODULES_FILE"; then
  echo "i2c-dev" | sudo tee -a "$MODULES_FILE" > /dev/null
fi

# Clone x120x repo
REPO_DIR="$HOME/x120x"
echo "?? Cloning x120x GitHub repo..."
if [ -d "$REPO_DIR" ]; then
  echo "?? Removing existing x120x directory..."
  rm -rf "$REPO_DIR"
fi
git clone https://github.com/suptronics/x120x.git "$REPO_DIR"

# Install and configure power monitor script
echo "?? Setting up power monitoring service..."
POWER_MONITOR_SCRIPT="/usr/local/bin/power_monitor.py"
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

# Copy power monitor script to system location
if [ -f "$SCRIPT_DIR/power_monitor.py" ]; then
    echo "Installing power monitor script..."
    sudo cp "$SCRIPT_DIR/power_monitor.py" "$POWER_MONITOR_SCRIPT"
    sudo chmod +x "$POWER_MONITOR_SCRIPT"
    
    # Ensure Loop is set to True for continuous monitoring
    sudo sed -i 's/^Loop\s*=\s*False/Loop = True/' "$POWER_MONITOR_SCRIPT"
    echo "Power monitor script installed to $POWER_MONITOR_SCRIPT"
else
    echo "Warning: power_monitor.py not found in script directory. Skipping power monitor setup."
fi

# Create systemd service file
echo "?? Creating systemd service for UPS monitoring..."
sudo tee /etc/systemd/system/ups-monitor.service > /dev/null << EOF
[Unit]
Description=UPS Power Monitor
Documentation=man:power_monitor.py
After=network.target
Wants=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 $HOME/flask_app/power_monitor.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
KillMode=process
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

# Enable and start the service
echo "?? Enabling UPS monitor service..."
sudo systemctl daemon-reload
sudo systemctl enable ups-monitor.service

echo "? UPS monitor service created and enabled."
echo "? The service will start automatically after reboot."
echo "? To start it now (after reboot), run: sudo systemctl start ups-monitor.service"
echo "? To check status, run: sudo systemctl status ups-monitor.service"
echo "? To view logs, run: sudo journalctl -u ups-monitor.service -f"

echo "? Setup complete. Please reboot to apply all changes."
