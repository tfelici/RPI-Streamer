#!/bin/bash
# RPI Streamer Configuration Script
# Quick access to common development and maintenance tasks

set -e

# Colors for better visibility
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_header() {
    echo -e "${PURPLE}🔧 $1${NC}"
}

# Function to check if we're running as root or with sudo
check_privileges() {
    if [ "$EUID" -eq 0 ]; then
        print_warning "Running as root. Some commands will run without sudo."
        SUDO=""
    else
        print_info "Running as user. Will use sudo for system commands."
        SUDO="sudo"
    fi
}

# Function to restart Flask app service
restart_flask_service() {
    print_header "Restarting Flask App Service"
    echo "Stopping flask_app service..."
    $SUDO systemctl stop flask_app
    echo "Starting flask_app service..."
    $SUDO systemctl start flask_app
    
    # Wait a moment and check status
    sleep 2
    if $SUDO systemctl is-active --quiet flask_app; then
        print_status "Flask app service restarted successfully"
        print_info "Status: $($SUDO systemctl is-active flask_app)"
    else
        print_error "Flask app service failed to start"
        echo "Checking service status..."
        $SUDO systemctl status flask_app --no-pager
    fi
}

# Function to check Flask app service journals
check_flask_journals() {
    print_header "Flask App Service Journals"
    
    # Check if flask_app service exists
    if ! $SUDO systemctl list-unit-files | grep -q "^flask_app.service"; then
        print_error "flask_app.service not found"
        return 1
    fi
    
    echo ""
    print_info "Current Flask App Service Status:"
    $SUDO systemctl status flask_app --no-pager --lines=5
    echo ""
    
    print_info "Recent Flask App Logs (last 50 lines):"
    echo "----------------------------------------"
    $SUDO journalctl -u flask_app --no-pager -n 50 --reverse
    echo ""
    
    print_info "Live log monitoring options:"
    echo "  • Follow live logs: sudo journalctl -u flask_app -f"
    echo "  • Last 100 lines: sudo journalctl -u flask_app -n 100"
    echo "  • Today's logs: sudo journalctl -u flask_app --since today"
    echo "  • Logs since boot: sudo journalctl -u flask_app --since boot"
    echo ""
    
    # Ask if user wants to follow live logs
    read -p "Would you like to follow live Flask app logs? (y/N): " follow_logs
    case $follow_logs in
        [Yy]|[Yy][Ee][Ss])
            print_info "Following live Flask app logs (Ctrl+C to exit)..."
            echo ""
            $SUDO journalctl -u flask_app -f
            ;;
        *)
            print_info "Returning to main menu"
            ;;
    esac
}

# Function to run installer in develop mode
run_develop_install() {
    print_header "Running RPI Streamer Installer"
    print_info "This will update to the latest branch"
    
    if [ -f "install_rpi_streamer.sh" ]; then
        bash install_rpi_streamer.sh
    else
        print_error "install_rpi_streamer.sh not found in current directory"
        print_info "Make sure you're running this from the RPI Streamer directory"
        return 1
    fi
}

# Function to toggle auto-updates on boot
toggle_auto_updates() {
    print_header "Auto-Updates on Boot Management"
    
    # Check if the auto-update service exists
    if ! $SUDO systemctl list-unit-files | grep -q "^install_rpi_streamer.service"; then
        print_error "install_rpi_streamer.service not found"
        print_info "Auto-update service may not be installed"
        return 1
    fi
    
    # Check current status
    if $SUDO systemctl is-enabled install_rpi_streamer.service >/dev/null 2>&1; then
        # Service is enabled, offer to disable
        print_info "Auto-updates on boot are currently ENABLED"
        echo ""
        read -p "Do you want to DISABLE auto-updates on boot? (y/n): " confirm
        case $confirm in
            [Yy]|[Yy][Ee][Ss])
                print_info "Disabling auto-updates on boot..."
                $SUDO systemctl disable install_rpi_streamer.service
                print_status "Auto-updates on boot disabled successfully!"
                print_info "The system will no longer check for updates at startup"
                ;;
            *)
                print_info "Operation cancelled"
                ;;
        esac
    else
        # Service is disabled, offer to enable
        print_info "Auto-updates on boot are currently DISABLED"
        echo ""
        read -p "Do you want to ENABLE auto-updates on boot? (y/n): " confirm
        case $confirm in
            [Yy]|[Yy][Ee][Ss])
                print_info "Enabling auto-updates on boot..."
                $SUDO systemctl enable install_rpi_streamer.service
                print_status "Auto-updates on boot enabled successfully!"
                print_info "The system will check for updates at startup"
                ;;
            *)
                print_info "Operation cancelled"
                ;;
        esac
    fi
}

# Function to toggle GPS daemon between simulation and real mode
toggle_gps_mode() {
    print_header "GPS Daemon Mode Toggle"
    
    SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
    
    # Check current GPS daemon status
    GPS_ACTIVE=false
    GPS_SIMULATION=false
    
    if $SUDO systemctl is-active --quiet gps-daemon; then
        GPS_ACTIVE=true
        print_info "GPS daemon service is currently ACTIVE"
        
        # Check if running in simulation mode by examining the actual process command line
        GPS_CMDLINE=$(ps aux | grep "python3.*gps_daemon.py" | grep -v grep | head -1)
        if echo "$GPS_CMDLINE" | grep -q -- "--gps-source=simulation"; then
            GPS_SIMULATION=true
            print_info "Current mode: SIMULATION MODE"
        else
            GPS_SIMULATION=false
            print_info "Current mode: REAL MODE"
        fi
    else
        # Check for background simulation process
        GPS_CMDLINE=$(ps aux | grep "python3.*gps_daemon.py" | grep -v grep | head -1)
        if [ -n "$GPS_CMDLINE" ] && echo "$GPS_CMDLINE" | grep -q -- "--gps-source=simulation"; then
            GPS_ACTIVE=true
            GPS_SIMULATION=true
            print_info "GPS daemon is running in SIMULATION MODE (background process)"
        else
            GPS_ACTIVE=false
            GPS_SIMULATION=false
            print_info "GPS daemon is currently INACTIVE"
        fi
    fi
    
    echo ""
    
    if [ "$GPS_ACTIVE" = true ]; then
        if [ "$GPS_SIMULATION" = true ]; then
            # Currently in simulation, offer to switch to real mode
            print_info "🔄 Switch to REAL MODE?"
            echo ""
            read -p "Do you want to switch from simulation to real GPS mode? (y/n): " confirm
            case $confirm in
                [Yy]|[Yy][Ee][Ss])
                    print_info "Switching to real GPS mode..."
                    
                    # Stop any running GPS processes
                    $SUDO systemctl stop gps-daemon 2>/dev/null || true
                    $SUDO pkill -f "gps_daemon.py" 2>/dev/null || true
                    sleep 2
                    
                    # Start GPS daemon service in real mode
                    print_info "Starting GPS daemon service in real mode..."
                    $SUDO systemctl start gps-daemon
                    
                    sleep 3
                    if $SUDO systemctl is-active --quiet gps-daemon; then
                        print_status "GPS switched to REAL MODE successfully!"
                        print_info "Status: $($SUDO systemctl is-active gps-daemon)"
                    else
                        print_error "Failed to start GPS daemon in real mode"
                        $SUDO systemctl status gps-daemon --no-pager
                    fi
                    ;;
                *)
                    print_info "Mode switch cancelled"
                    ;;
            esac
        else
            # Currently in real mode, offer to switch to simulation
            print_info "🔄 Switch to SIMULATION MODE?"
            echo ""
            read -p "Do you want to switch from real to simulation GPS mode? (y/n): " confirm
            case $confirm in
                [Yy]|[Yy][Ee][Ss])
                    print_info "Switching to simulation GPS mode..."
                    
                    # Stop GPS daemon service
                    $SUDO systemctl stop gps-daemon 2>/dev/null || true
                    sleep 2
                    
                    # Start GPS daemon in simulation mode
                    print_info "Starting GPS daemon in simulation mode..."
                    if [ -f "$SCRIPT_DIR/gps_daemon.py" ]; then
                        $SUDO python3 "$SCRIPT_DIR/gps_daemon.py" --daemon --gps-source=simulation &
                        GPS_PID=$!
                        
                        sleep 3
                        if kill -0 $GPS_PID 2>/dev/null; then
                            print_status "GPS switched to SIMULATION MODE successfully!"
                            print_info "Process ID: $GPS_PID"
                        else
                            print_error "Failed to start GPS daemon in simulation mode"
                        fi
                    else
                        print_error "$SCRIPT_DIR/gps_daemon.py not found"
                    fi
                    ;;
                *)
                    print_info "Mode switch cancelled"
                    ;;
            esac
        fi
    else
        # GPS is inactive, ask which mode to start
        print_info "🚀 GPS daemon is not running. Choose startup mode:"
        echo ""
        echo "  1) Start in SIMULATION mode (generates test data)"
        echo "  2) Start in REAL mode (connects to GPS hardware)"
        echo "  0) Cancel"
        echo ""
        read -p "Enter your choice (1-2, or 0): " mode_choice
        
        case $mode_choice in
            1)
                print_info "Starting GPS daemon in simulation mode..."
                if [ -f "$SCRIPT_DIR/gps_daemon.py" ]; then
                    $SUDO python3 "$SCRIPT_DIR/gps_daemon.py" --daemon --gps-source=simulation &
                    GPS_PID=$!
                    
                    sleep 3
                    if kill -0 $GPS_PID 2>/dev/null; then
                        print_status "GPS started in SIMULATION MODE successfully!"
                        print_info "Process ID: $GPS_PID"
                    else
                        print_error "Failed to start GPS daemon in simulation mode"
                    fi
                else
                    print_error "$SCRIPT_DIR/gps_daemon.py not found"
                fi
                ;;
            2)
                print_info "Starting GPS daemon in real mode..."
                $SUDO systemctl start gps-daemon
                
                sleep 3
                if $SUDO systemctl is-active --quiet gps-daemon; then
                    print_status "GPS started in REAL MODE successfully!"
                    print_info "Status: $($SUDO systemctl is-active gps-daemon)"
                else
                    print_error "Failed to start GPS daemon in real mode"
                    $SUDO systemctl status gps-daemon --no-pager
                fi
                ;;
            0)
                print_info "GPS startup cancelled"
                ;;
            *)
                print_error "Invalid choice. Operation cancelled."
                ;;
        esac
    fi
    
    # Show current status and helpful commands
    echo ""
    print_info "Helpful commands:"
    echo "  • Check GPS status: python3 $SCRIPT_DIR/gps_client.py --status"
    echo "  • Get location: python3 $SCRIPT_DIR/gps_client.py --location"
    echo "  • View GPS logs: sudo journalctl -u gps-daemon -f"
}

# Function to restart GPS startup manager service
restart_gps_startup_manager() {
    print_header "Restarting GPS Startup Manager Service"
    
    # Check if gps-startup service exists
    if ! $SUDO systemctl list-unit-files | grep -q "^gps-startup.service"; then
        print_error "gps-startup.service not found"
        print_info "The GPS startup manager service may not be installed"
        return 1
    fi
    
    # Stop GPS startup manager if running
    if $SUDO systemctl is-active --quiet gps-startup; then
        print_info "Stopping GPS startup manager..."
        $SUDO systemctl stop gps-startup
        sleep 2
    fi
    
    # Start GPS startup manager service
    print_info "Starting GPS startup manager service..."
    print_info "This manages automatic GPS tracking startup based on flight settings"
    
    $SUDO systemctl start gps-startup
    
    # Wait a moment and check status
    sleep 3
    if $SUDO systemctl is-active --quiet gps-startup; then
        print_status "GPS startup manager service started successfully"
        print_info "Status: $($SUDO systemctl is-active gps-startup)"
        print_info "View logs: sudo journalctl -u gps-startup -f"
    else
        print_error "GPS startup manager service failed to start"
        echo "Checking service status..."
        $SUDO systemctl status gps-startup --no-pager
    fi
}

# Function to restart heartbeat daemon service
restart_heartbeat_daemon() {
    print_header "Restarting Heartbeat Daemon Service"
    
    # Check if heartbeat-daemon service exists
    if ! $SUDO systemctl list-unit-files | grep -q "^heartbeat-daemon.service"; then
        print_error "heartbeat-daemon.service not found"
        print_info "The heartbeat daemon service may not be installed"
        return 1
    fi
    
    # Stop heartbeat daemon if running
    if $SUDO systemctl is-active --quiet heartbeat-daemon; then
        print_info "Stopping heartbeat daemon..."
        $SUDO systemctl stop heartbeat-daemon
        sleep 2
    fi
    
    # Start heartbeat daemon service
    print_info "Starting heartbeat daemon service..."
    print_info "This monitors system stats and sends heartbeats to the server"
    
    $SUDO systemctl start heartbeat-daemon
    
    # Wait a moment and check status
    sleep 3
    if $SUDO systemctl is-active --quiet heartbeat-daemon; then
        print_status "Heartbeat daemon service started successfully"
        print_info "Status: $($SUDO systemctl is-active heartbeat-daemon)"
        print_info "View logs: sudo journalctl -u heartbeat-daemon -f"
        print_info "Heartbeat data is saved to: /tmp/rpi_streamer_heartbeat.json"
    else
        print_error "Heartbeat daemon service failed to start"
        echo "Checking service status..."
        $SUDO systemctl status heartbeat-daemon --no-pager
    fi
}

# Function to show current system status
show_system_status() {
    print_header "System Status Overview"
    echo ""
    
    echo "🌐 Network Status:"
    echo "   Flask App: $($SUDO systemctl is-active flask_app 2>/dev/null || echo 'inactive')"
    echo "   MediaMTX:  $($SUDO systemctl is-active mediamtx 2>/dev/null || echo 'inactive')"
    echo "   Heartbeat: $($SUDO systemctl is-active heartbeat-daemon 2>/dev/null || echo 'inactive')"
    echo ""
    
    echo "🔋 Power Management:"
    if systemctl list-unit-files | grep -q ups-monitor.service; then
        echo "   UPS Monitor: $($SUDO systemctl is-active ups-monitor 2>/dev/null || echo 'inactive')"
        echo "   UPS Monitor Enabled: $($SUDO systemctl is-enabled ups-monitor 2>/dev/null || echo 'disabled')"
    else
        echo "   UPS Monitor: not installed"
    fi
    echo ""
    
    echo "🛰️ GPS Status:"
    GPS_STATUS="$($SUDO systemctl is-active gps-daemon 2>/dev/null || echo 'inactive')"
    echo "   GPS Daemon: $GPS_STATUS"
    
    # Show GPS mode if active
    if [ "$GPS_STATUS" = "active" ]; then
        GPS_CMDLINE=$(ps aux | grep "python3.*gps_daemon.py" | grep -v grep | head -1)
        if echo "$GPS_CMDLINE" | grep -q -- "--gps-source=simulation"; then
            echo "   GPS Mode: SIMULATION"
        elif echo "$GPS_CMDLINE" | grep -q -- "--gps-source=xplane"; then
            echo "   GPS Mode: X-PLANE"
        else
            echo "   GPS Mode: REAL"
        fi
    elif ps aux | grep "python3.*gps_daemon.py" | grep -v grep | grep -q -- "--gps-source=simulation"; then
        echo "   GPS Daemon: active (background)"
        echo "   GPS Mode: SIMULATION"
    elif ps aux | grep "python3.*gps_daemon.py" | grep -v grep | grep -q -- "--gps-source=xplane"; then
        echo "   GPS Daemon: active (background)"
        echo "   GPS Mode: X-PLANE"
    fi
    
    echo "   GPS Startup Manager: $($SUDO systemctl is-active gps-startup 2>/dev/null || echo 'inactive')"
    SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
    if command -v python3 >/dev/null && [ -f "$SCRIPT_DIR/gps_client.py" ]; then
        echo "   GPS Client Status:"
        python3 "$SCRIPT_DIR/gps_client.py" --status 2>/dev/null | sed 's/^/      /' || echo "      Unable to get GPS status"
    fi
    echo ""
    
    echo "🔧 System Info:"
    echo "   Hostname: $(hostname)"
    echo "   IP Address: $(hostname -I | awk '{print $1}' || echo 'Unknown')"
    echo "   Uptime: $(uptime -p 2>/dev/null || echo 'Unknown')"
    echo ""
}

# Function to reboot the system
reboot_system() {
    print_header "System Reboot"
    print_warning "This will immediately reboot the Raspberry Pi"
    echo ""
    read -p "Are you sure you want to reboot now? (y/N): " confirm
    
    case $confirm in
        [Yy]|[Yy][Ee][Ss])
            print_info "Rebooting system in 3 seconds..."
            echo "3..."
            sleep 1
            echo "2..."
            sleep 1
            echo "1..."
            sleep 1
            print_status "Rebooting now!"
            $SUDO reboot
            ;;
        *)
            print_info "Reboot cancelled"
            ;;
    esac
}

# Function to toggle power monitor service
toggle_power_monitor() {
    print_header "Power Monitor Service Management"
    
    # Check if service exists
    if ! systemctl list-unit-files | grep -q ups-monitor.service; then
        print_error "UPS Monitor service not found!"
        print_info "Install UPS management first using install_ups_management.sh"
        return 1
    fi
    
    # Check current status
    if systemctl is-enabled ups-monitor.service >/dev/null 2>&1; then
        # Service is enabled, offer to disable
        print_info "Power Monitor service is currently ENABLED"
        echo ""
        read -p "Do you want to DISABLE the Power Monitor service? (y/n): " confirm
        case $confirm in
            [Yy]|[Yy][Ee][Ss])
                print_info "Stopping and disabling Power Monitor service..."
                $SUDO systemctl stop ups-monitor.service 2>/dev/null || true
                $SUDO systemctl disable ups-monitor.service
                print_status "Power Monitor service disabled successfully!"
                ;;
            *)
                print_info "Operation cancelled"
                ;;
        esac
    else
        # Service is disabled, offer to enable
        print_info "Power Monitor service is currently DISABLED"
        echo ""
        read -p "Do you want to ENABLE the Power Monitor service? (y/n): " confirm
        case $confirm in
            [Yy]|[Yy][Ee][Ss])
                print_info "Enabling and starting Power Monitor service..."
                $SUDO systemctl enable ups-monitor.service
                $SUDO systemctl start ups-monitor.service 2>/dev/null || print_warning "Service enabled but may need reboot to start"
                print_status "Power Monitor service enabled successfully!"
                ;;
            *)
                print_info "Operation cancelled"
                ;;
        esac
    fi
}

# Main menu function
show_menu() {
    clear
    echo -e "${CYAN}=========================================="
    echo -e "🔧 RPI STREAMER CONFIGURATION MENU"
    echo -e "==========================================${NC}"
    echo ""
    echo "Available Options:"
    echo "  1) Restart Flask App Service"
    echo "  2) Check Flask App Service Logs"
    echo "  3) Install/Update RPI Streamer (Latest Branch)"
    echo "  4) Toggle Auto-Updates on Boot"
    echo "  5) Show System Status"
    echo "  r) Reboot Now"
    echo "  0) Exit"
    echo ""
}

# Main script execution
main() {
    check_privileges
    
    while true; do
        show_menu
        read -p "Enter your choice (1-5, r, or 0): " choice
        echo ""
        
        case $choice in
            1)
                restart_flask_service
                ;;
            2)
                check_flask_journals
                ;;
            3)
                run_develop_install
                ;;
            4)
                toggle_auto_updates
                ;;
            5)
                show_system_status
                ;;
            r|R)
                reboot_system
                ;;
            0)
                print_info "Exiting RPI Streamer Configuration Menu"
                exit 0
                ;;
            *)
                print_error "Invalid choice. Please enter 1-5, r, or 0."
                ;;
        esac
        
        echo ""
        read -p "Press Enter to continue..."
    done
}

# Run main function
main "$@"
