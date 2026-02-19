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
    col_map = {
            'L' : 0x0000, # black with grey background
            'A' : 0x0021, # amber
            'W' : 0x0042, # white
            'B' : 0x0063, # cyan
            'G' : 0x0084, # green
            'M' : 0x00A5, # magenta
            'R' : 0x00C6, # red
            'Y' : 0x00E7, # yellow
            'E' : 0x0108, # grey
            ' ' : 0x0042  # use white
    }

    def __init__(self, device):
        self.device = device

    def startupscreen(self, version: str = None, new_version: str = None):
        pass

    def clear(self):
        pass


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
        #self.display_mgr.startupscreen(self.version, self.new_version)

        #create_button_list_mcdu()

        usb_event_thread = Thread(target=throttle_create_events, args=[self.xp, self.usb_mgr, self.display_mgr])
        usb_event_thread.start()

        cyclic_thread = Thread(target=self.cyclic_worker)
        cyclic_thread.start()
