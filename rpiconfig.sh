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

# Function to run installer in develop mode
run_develop_install() {
    print_header "Running RPI Streamer Installer (Develop Branch)"
    print_info "This will update to the latest develop branch and install"
    
    if [ -f "install_rpi_streamer.sh" ]; then
        bash install_rpi_streamer.sh --develop
    else
        print_error "install_rpi_streamer.sh not found in current directory"
        print_info "Make sure you're running this from the RPI Streamer directory"
        return 1
    fi
}

# Function to run installer in develop mode without updates
run_develop_install_no_update() {
    print_header "Running RPI Streamer Installer (Develop Branch, No Update)"
    print_info "This will install using existing local files without GitHub updates"
    
    if [ -f "install_rpi_streamer.sh" ]; then
        bash install_rpi_streamer.sh --develop --skip-update
    else
        print_error "install_rpi_streamer.sh not found in current directory"
        print_info "Make sure you're running this from the RPI Streamer directory"
        return 1
    fi
}

# Function to restart GPS daemon in simulation mode
restart_gps_simulation() {
    print_header "Restarting GPS Daemon in Simulation Mode"
    
    # Stop GPS daemon if running
    if $SUDO systemctl is-active --quiet gps-daemon; then
        print_info "Stopping GPS daemon..."
        $SUDO systemctl stop gps-daemon
        sleep 2
    fi
    
    # Start GPS daemon in simulation mode
    print_info "Starting GPS daemon in simulation mode..."
    print_warning "GPS will generate simulated location data for testing"
    
    # Run GPS daemon in simulation mode (background process)
    if [ -f "gps_daemon.py" ]; then
        $SUDO python3 gps_daemon.py --daemon --simulate &
        GPS_PID=$!
        
        # Wait a moment and check if process is running
        sleep 3
        if kill -0 $GPS_PID 2>/dev/null; then
            print_status "GPS daemon started in simulation mode (PID: $GPS_PID)"
            print_info "Check status with: python3 gps_client.py --status"
            print_info "Get simulated location: python3 gps_client.py --location"
        else
            print_error "Failed to start GPS daemon in simulation mode"
        fi
    else
        print_error "gps_daemon.py not found in current directory"
        return 1
    fi
}

# Function to restart GPS daemon in real mode
restart_gps_real() {
    print_header "Restarting GPS Daemon in Real Mode"
    
    # Stop any running GPS daemon first
    if $SUDO systemctl is-active --quiet gps-daemon; then
        print_info "Stopping GPS daemon service..."
        $SUDO systemctl stop gps-daemon
        sleep 2
    fi
    
    # Kill any background GPS processes
    $SUDO pkill -f "gps_daemon.py" 2>/dev/null || true
    sleep 1
    
    # Start GPS daemon service in real mode
    print_info "Starting GPS daemon service in real mode..."
    print_info "GPS will attempt to connect to real GPS hardware"
    
    $SUDO systemctl start gps-daemon
    
    # Wait a moment and check status
    sleep 3
    if $SUDO systemctl is-active --quiet gps-daemon; then
        print_status "GPS daemon service started in real mode"
        print_info "Status: $($SUDO systemctl is-active gps-daemon)"
        print_info "Check detailed status: python3 gps_client.py --status"
        print_info "View logs: sudo journalctl -u gps-daemon -f"
    else
        print_error "GPS daemon service failed to start"
        echo "Checking service status..."
        $SUDO systemctl status gps-daemon --no-pager
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
    
    echo "🛰️ GPS Status:"
    echo "   GPS Daemon: $($SUDO systemctl is-active gps-daemon 2>/dev/null || echo 'inactive')"
    if command -v python3 >/dev/null && [ -f "gps_client.py" ]; then
        echo "   GPS Client Status:"
        python3 gps_client.py --status 2>/dev/null | sed 's/^/      /' || echo "      Unable to get GPS status"
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

# Main menu function
show_menu() {
    clear
    echo -e "${CYAN}=========================================="
    echo -e "🔧 RPI STREAMER CONFIGURATION MENU"
    echo -e "==========================================${NC}"
    echo ""
    show_system_status
    echo ""
    echo "Available Options:"
    echo "  1) Restart Flask App Service"
    echo "  2) Install/Update (Develop Branch)"
    echo "  3) Install Local Code (Develop, No Update)"
    echo "  4) Restart GPS Daemon (Simulation Mode)"
    echo "  5) Restart GPS Daemon (Real Mode)"
    echo "  6) Show System Status"
    echo "  7) Reboot Now"
    echo "  8) Exit"
    echo ""
}

# Main script execution
main() {
    check_privileges
    
    while true; do
        show_menu
        read -p "Enter your choice (1-8): " choice
        echo ""
        
        case $choice in
            1)
                restart_flask_service
                ;;
            2)
                run_develop_install
                ;;
            3)
                run_develop_install_no_update
                ;;
            4)
                restart_gps_simulation
                ;;
            5)
                restart_gps_real
                ;;
            6)
                show_system_status
                ;;
            7)
                reboot_system
                ;;
            8)
                print_info "Exiting RPI Streamer Configuration Menu"
                exit 0
                ;;
            *)
                print_error "Invalid choice. Please enter 1-8."
                ;;
        esac
        
        echo ""
        read -p "Press Enter to continue..."
    done
}

# Run main function
main "$@"
