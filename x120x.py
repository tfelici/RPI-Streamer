"""
X120X UPS Controller Functions
Handles communication with the X120X UPS controller via I2C and GPIO.
"""

import struct

try:
    import smbus2
    import gpiod
    LIBRARIES_AVAILABLE = True
except ImportError:
    LIBRARIES_AVAILABLE = False


class X120X:
    """
    X120X UPS Controller with context manager support.
    Provides proper resource management for I2C bus connections.
    """
    
    @staticmethod
    def check_device(i2c_bus=1, address=0x36):
        """
        Check if UPS device is present at the specified I2C address.
        Returns True if device is detected, False otherwise.
        """
        if not LIBRARIES_AVAILABLE:
            return False
        
        bus = None
        try:
            bus = smbus2.SMBus(i2c_bus)
            # Try to read from the device at the specified address
            # If device is present, this should succeed
            bus.read_byte(address)
            return True
        except Exception:
            # Device not present or I2C error
            return False
        finally:
            # Always close the bus to prevent file descriptor leaks
            if bus is not None:
                bus.close()
    
    def __init__(self, i2c_bus=1, address=0x36):
        """
        Initialize X120X UPS controller.
        
        Args:
            i2c_bus: I2C bus number (default: 1)
            address: UPS I2C address (default: 0x36)
        """
        if not LIBRARIES_AVAILABLE:
            raise RuntimeError("Required libraries (smbus2, gpiod) not available")
        
        if not self.check_device(i2c_bus, address):
            raise RuntimeError(f"X120X UPS device not found at I2C address 0x{address:02x} on bus {i2c_bus}")
        
        self.i2c_bus = i2c_bus
        self.address = address
        self.bus = None
    
    def __enter__(self):
        """Enter context manager - initialize I2C bus"""
        self.bus = smbus2.SMBus(self.i2c_bus)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager - cleanup resources"""
        self.close()
    
    def close(self):
        """Close the I2C bus connection"""
        if hasattr(self, 'bus') and self.bus:
            self.bus.close()
            self.bus = None
    
    def __del__(self):
        """Ensure bus is closed when object is destroyed"""
        self.close()
    
    def read_voltage(self):
        """
        Read voltage from UPS controller via I2C.
        Returns voltage in volts or None if error.
        """
        if not self.bus:
            raise RuntimeError("I2C bus is not initialized or has been closed")
        try:
            read = self.bus.read_word_data(self.address, 2)  # reads word data (16 bit)
            swapped = struct.unpack("<H", struct.pack(">H", read))[0]  # big endian to little endian
            voltage = swapped * 1.25 / 1000 / 16  # convert to understandable voltage
            return voltage
        except Exception:
            return None
    
    def read_capacity(self):
        """
        Read battery capacity from UPS controller via I2C.
        Returns capacity percentage (0-100) or None if error.
        """
        if not self.bus:
            raise RuntimeError("I2C bus is not initialized or has been closed")
        try:
            read = self.bus.read_word_data(self.address, 4)  # reads word data (16 bit)
            swapped = struct.unpack("<H", struct.pack(">H", read))[0]  # big endian to little endian
            capacity = swapped / 256  # convert to 1-100% scale
            return capacity
        except Exception:
            return None
    
    def get_battery_status(self, voltage=None):
        """
        Convert voltage to human-readable battery status.
        If voltage is None, reads voltage from device.
        Returns status string.
        """
        if voltage is None:
            voltage = self.read_voltage()
        
        if voltage is None:
            return "Unknown"
        if voltage >= 3.87:
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
    
    def get_ac_power_state(self, pld_pin=6):
        """
        Check AC power state via GPIO.
        Compatible with both Bookworm (gpiod v1.x) and Trixie (gpiod v2.x).
        Returns True if AC power is present, False if unplugged, None if error.
        """
        if not LIBRARIES_AVAILABLE:
            return None
        
        try:
            # Try to determine gpiod API version and use appropriate method
            try:
                # Test for gpiod v1.x (Bookworm)
                chip = gpiod.Chip('/dev/gpiochip0')
                pld_line = chip.get_line(pld_pin)
                pld_line.request(consumer="PLD", type=gpiod.LINE_REQ_DIR_IN)
                
                ac_power_state = pld_line.get_value()
                pld_line.release()
                chip.close()
                
                return ac_power_state == 1
                
            except AttributeError:
                # This is gpiod v2.x (Trixie)
                direction = gpiod.line.Direction.INPUT
                line_settings = gpiod.LineSettings(direction=direction)
                
                request = gpiod.request_lines(
                    "/dev/gpiochip0",
                    consumer="PLD", 
                    config={pld_pin: line_settings}
                )
                
                try:
                    values = request.get_values([pld_pin])
                    gpio_value = values[0]
                    
                    # Handle GPIO value (can be enum or int)
                    if hasattr(gpio_value, 'value'):
                        numeric_value = gpio_value.value
                    elif str(gpio_value) == 'Value.ACTIVE':
                        numeric_value = 1
                    elif str(gpio_value) == 'Value.INACTIVE':
                        numeric_value = 0
                    else:
                        numeric_value = int(gpio_value)
                    
                    return numeric_value == 1
                    
                finally:
                    request.release()
                    
        except Exception:
            return None
    
    def get_status(self):
        """
        Get complete UPS status information.
        Returns dict with voltage, capacity, battery_status, and ac_power_connected.
        """
        try:
            voltage = self.read_voltage()
            capacity = self.read_capacity()
            battery_status = self.get_battery_status(voltage)
            ac_power_connected = self.get_ac_power_state()
            
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
