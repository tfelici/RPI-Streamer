"""
X120X UPS Controller Functions
Handles communication with the X120X UPS controller via I2C and GPIO.
"""

import struct

def check_ups_device():
    """
    Check if UPS device is present at I2C address 0x36 using smbus2.
    Returns True if device is detected, False otherwise.
    """
    try:
        import smbus2
        bus = smbus2.SMBus(1)
        # Try to read from the device at address 0x36
        # If device is present, this should succeed
        bus.read_byte(0x36)
        bus.close()
        return True
    except Exception:
        # Device not present or I2C error
        return False

try:
    import smbus2
    import gpiod
    # Check if both libraries are available AND the UPS device is present
    UPS_AVAILABLE = check_ups_device()
except ImportError:
    UPS_AVAILABLE = False


def read_voltage(bus, address=0x36):
    """
    Read voltage from UPS controller via I2C.
    Returns voltage in volts or None if error.
    """
    if not UPS_AVAILABLE:
        return None
    try:
        read = bus.read_word_data(address, 2)  # reads word data (16 bit)
        swapped = struct.unpack("<H", struct.pack(">H", read))[0]  # big endian to little endian
        voltage = swapped * 1.25 / 1000 / 16  # convert to understandable voltage
        return voltage
    except Exception:
        return None


def read_capacity(bus, address=0x36):
    """
    Read battery capacity from UPS controller via I2C.
    Returns capacity percentage (0-100) or None if error.
    """
    if not UPS_AVAILABLE:
        return None
    try:
        read = bus.read_word_data(address, 4)  # reads word data (16 bit)
        swapped = struct.unpack("<H", struct.pack(">H", read))[0]  # big endian to little endian
        capacity = swapped / 256  # convert to 1-100% scale
        return capacity
    except Exception:
        return None


def get_battery_status(voltage):
    """
    Convert voltage to human-readable battery status.
    Returns status string.
    """
    if voltage is None:
        return "Unknown"
    if 3.87 <= voltage <= 4.2:
        return "Full"
    elif 3.7 <= voltage < 3.87:
        return "High"
    elif 3.55 <= voltage < 3.7:
        return "Medium"
    elif 3.4 <= voltage < 3.55:
        return "Low"
    elif voltage < 3.4:
        return "Critical"
    else:
        return "Unknown"


def get_ac_power_state(pld_pin=6):
    """
    Check AC power state via GPIO.
    Returns True if AC power is present, False if unplugged, None if error.
    """
    if not UPS_AVAILABLE:
        return None
    try:
        # Try different GPIO chip names for compatibility
        chip = None
        for chip_name in ['gpiochip0', 'gpiochip4']:
            try:
                chip = gpiod.Chip(chip_name)
                break
            except:
                continue
        
        if chip is None:
            return None
            
        pld_line = chip.get_line(pld_pin)
        pld_line.request(consumer="PLD", type=gpiod.LINE_REQ_DIR_IN)
        ac_power_state = pld_line.get_value()
        pld_line.release()
        return ac_power_state == 1
    except Exception:
        return None


def get_ups_status():
    """
    Get complete UPS status information.
    Returns dict with voltage, capacity, battery_status, and ac_power_connected.
    Returns None values if UPS is not available or error occurs.
    """
    if not UPS_AVAILABLE:
        return {
            'voltage': None,
            'capacity': None,
            'battery_status': 'UPS Not Available',
            'ac_power_connected': None
        }
    
    try:
        bus = smbus2.SMBus(1)
        address = 0x36
        voltage = read_voltage(bus, address)
        capacity = read_capacity(bus, address)
        battery_status = get_battery_status(voltage)
        ac_power_connected = get_ac_power_state()
        
        return {
            'voltage': voltage,
            'capacity': capacity,
            'battery_status': battery_status,
            'ac_power_connected': ac_power_connected
        }
    except Exception as e:
        return {
            'voltage': None,
            'capacity': None,
            'battery_status': f'Error: {str(e)}',
            'ac_power_connected': None
        }
