from dataclasses import dataclass
from enum import Enum, IntEnum
import hid

from threading import Thread, Event, Lock
from time import sleep

import time

import xp_websocket

# sim/cockpit2/controls/speedbrake_ratio	float	y	ratio	This is how much the speebrake HANDLE is deflected, in ratio, where 0.0 is fully retracted, 0.5 is halfway down, and 1.0 is fully down, and -0.5 is speedbrakes ARMED.

#@unique
class DEVICEMASK(IntEnum):
    NONE =  0
    THROTTLE =  0x01


def throttle_create_events(xp, usb_mgr, display_mgr):
    pass

def xplane_ws_listener(data, led_dataref_ids): # receive ids and find led
    pass


class UsbManager:
    def __init__(self):
        self.device = None
        self.device_config = 0

    def connect_device(self, vid: int, pid: int):

        # Connect to device. Linux uses device whreas mac uses Device
        try:
            self.device = hid.device()
            self.device.open(vid, pid)
        except AttributeError as e:
            print("[UM32] using hidapi mac version")
            self.device = hid.Device(vid=vid, pid=pid)

        if self.device is None:
            raise RuntimeError("[UM32] Device not found")

        print("[UM32] Device connected.")

    def find_device(self):
        device_config = 0
        devlist = [
            {'vid': 0x4098, 'pid': 0xb920, 'name': 'Ursa Minor 32 Throttle', 'mask': DEVICEMASK.THROTTLE}

        ]
        for d in devlist:
            print(f"[UM32] now searching for winwing {d['name']} ... ", end='')
            found = False
            for dev in hid.enumerate():
                if dev['vendor_id'] == d['vid'] and dev['product_id'] == d['pid']:
                    print("found")
                    self.device_config |= d['mask']
                    return d['vid'], d['pid'], self.device_config
            print("not found")
        return None, None, 0


class DisplayManager:
    # 7-segment encoding table: digit -> (a, b, c, d, e, f, g)
    SEVEN_SEG = {
        0: (1,1,1,1,1,1,0), 1: (0,1,1,0,0,0,0), 2: (1,1,0,1,1,0,1),
        3: (1,1,1,1,0,0,1), 4: (0,1,1,0,0,1,1), 5: (1,0,1,1,0,1,1),
        6: (1,0,1,1,1,1,1), 7: (1,1,1,0,0,0,0), 8: (1,1,1,1,1,1,1),
        9: (1,1,1,1,0,1,1),
    }

    SLOT_SEG_INDEX = [5, 4, 3, 2, 1, 0]  # slot -> segment: f, e, d, c, b, a
    SIDE_BITS = {'L': [1, 1, 1, 0, 0, 0], 'R': [1, 1, 0, 1, 1, 1]}

    class Leds(Enum):
        THROTTLE_BACKLIGHT = 0 # cmd = 0x10, 0 .. 255
        MARKER_BACKLIGHT = 2 # 0 .. 255
        ENG1_FAULT = 3
        ENG1_FIRE = 4
        ENG2_FAULT = 5
        ENG2_FIRE = 6
        MOTOR1 = 0x0e
        MOTOR2 = 0x0f
        PACK_32_BACKLIGHT = 100 # cmd = 0x01
        LCD_BACKLIGHT = 102



    def __init__(self, device):
        self.device = device

    def startupscreen(self, version: str = None, new_version: str = None):
        self.set_backlights(120)
        self.clear()
        self.send_lcd('L', 0.5)


    def set_backlights(self, value : int):
        self.set_leds([self.Leds.LCD_BACKLIGHT,
                       self.Leds.PACK_32_BACKLIGHT,
                       self.Leds.THROTTLE_BACKLIGHT,
                       self.Leds.MARKER_BACKLIGHT], value)


    def clear(self):
        self.set_leds([self.Leds.ENG1_FAULT,
                       self.Leds.ENG1_FIRE,
                       self.Leds.ENG2_FAULT,
                       self.Leds.ENG2_FIRE,
                       self.Leds.MOTOR1,
                       self.Leds.MOTOR2], 0)


    def set_leds(self, leds : Leds, brightness : int):
        if isinstance(leds, list):
            for i in range(len(leds)):
                self.set_led(leds[i], brightness)
        else:
            self.set_led(leds, brightness)


    def set_led(self, led : Leds, brightness : int):
        cmd = 0x10
        value = led.value
        if value >= self.Leds.PACK_32_BACKLIGHT.value:
            cmd = 0x01
            value -= self.Leds.PACK_32_BACKLIGHT.value

        data = [0x02, cmd, 0xb9, 0, 0, 3, 0x49, value, brightness, 0,0,0,0,0]
        print(f"set led {led} in {data}")
        #if 'data' in locals():
        cmd = bytes(data)
        self.device.write(cmd)


    def _calc_lcd_params(self, side, integer, fractional):
        """Calculate b29 and slots for any digit combination (0-9)."""
        frac_segs = self.SEVEN_SEG[fractional]  # (a, b, c, d, e, f, g)
        ones_segs = self.SEVEN_SEG[integer]
        side_bit = 1 if side == 'R' else 0
        b29 = (frac_segs[6] << 3) | (ones_segs[6] << 2) | side_bit

        bit0 = self.SIDE_BITS[side]
        slots = []
        for i in range(6):
            seg = self.SLOT_SEG_INDEX[i]
            slots.append((frac_segs[seg] << 3) | (ones_segs[seg] << 2) | bit0[i])

        return b29, slots


    def send_lcd(self, side, value, counter=0):
        # Usage (integer and fractional both support 0-9):
        # send_lcd('L', 0.5)  # Display "L 0.5"
        # send_lcd('R', 1.0)  # Display "R 1.0"
        # send_lcd('L', 2.3)  # Display "L 2.3"
        integer = int(value)
        fractional = int((value - integer) * 10)
        b29, slots = self._calc_lcd_params(side, integer, fractional)

        # DATA packet
        data = [0] * 64
        data[0] = 0xF0
        data[2] = counter & 0xFF
        data[3] = 0x38
        data[4], data[5] = 0x01, 0xB9
        data[8], data[9] = 0x02, 0x01
        data[17] = 0x24
        data[25] = 0x04
        data[29] = b29
        for i, pos in enumerate([33, 37, 41, 45, 49, 53]):
            data[pos] = slots[i]
        data[57], data[58] = 0x01, 0xB9
        self.device.write(data)

        # COMMIT packet
        commit = [0] * 64
        commit[0] = 0xF0
        commit[2] = (counter + 1) & 0xFF
        commit[3] = 0x0E
        commit[5], commit[6] = 0x03, 0x01
        self.device.write(commit)





class device:
    def __init__(self, UDP_IP = None, UDP_PORT = None):
        self.usb_mgr = None
        self.cyclic = Event()
        self.xp = xp_websocket.XP_Websocket()


    def connected(self):
        global xplane_connected
        global xp

        #if not mf_dev:
        #    return
        print(f"[UM32] X-Plane connected")
        #xplane_get_dataref_ids(self.xp)
        print(f"[UM32] subsrcibe datarefs... ", end="")
        t = Thread(target=self.xp.datarefs_subscribe, args=(self.xp.led_dataref_ids, xplane_ws_listener))
        t.start()
        print(f"done")
        xplane_connected = True
        xp = self.xp
        #create_combined_button_list_a107()
        #mf_dev.force_sync(2)


    def disconnected(self):
        global xplane_connected
        xplane_connected = False
        print(f"[UM32] X-Plane disconnected")
        #startupscreen(mf_dev, device_config, self.version, self.new_version)


    def cyclic_worker(self):
        global device_config

        self.cyclic.wait()
        apu_master = self.xp.dataref_id_fetch("AirbusFBW/APUMaster")
        strobe = self.xp.dataref_id_fetch("AirbusFBW/OHPLightSwitches")
        antiice = self.xp.command_id_fetch("toliss_airbus/antiicecommands/WingToggle")
        while True:
            #self.xp.dataref_set_value(apu_master, 1)
            #self.xp.dataref_set_value(strobe, 1, 7)
            #self.xp.command_activate_duration(antiice, 1)
            time.sleep(2)
            #self.xp.dataref_set_value(apu_master, 0)
            #self.xp.dataref_set_value(strobe, 0, 7)
            time.sleep(2)


    def init_device(self, version: str = None, new_version: str = None):
        global values, xplane_connected
        global device_config

        self.version = version
        self.new_version = new_version

        self.usb_mgr = UsbManager()
        vid, pid, device_config = self.usb_mgr.find_device()

        if pid is None:
            print(f" [UM32] No compatible winwing device found, quit")
            return
        else:
            self.usb_mgr.connect_device(vid=vid, pid=pid)

        self.display_mgr = DisplayManager(self.usb_mgr.device)
        self.display_mgr.startupscreen(self.version, self.new_version)

        #create_button_list_mcdu()

        usb_event_thread = Thread(target=throttle_create_events, args=[self.xp, self.usb_mgr, self.display_mgr])
        usb_event_thread.start()

        cyclic_thread = Thread(target=self.cyclic_worker)
        cyclic_thread.start()
