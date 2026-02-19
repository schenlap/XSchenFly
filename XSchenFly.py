#!/usr/bin/env python3
VERSION = "v1.4+"

# IP Address of machine running X-Plane. 
UDP_IP = "127.0.0.1"
UDP_PORT = 49000

from dataclasses import dataclass
from enum import Enum, IntEnum
import os
import requests

from threading import Thread, Event, Lock
from time import sleep

import hid

import devices.winwing_fcu
import devices.winwing_mcdu
import devices.winwing_throttle
import devices.rowsfire_a107
import XPlaneUdp

class DrefType(Enum):
    DATA = 0
    CMD = 1
    NONE = 2 # for testing


values_processed = Event()
xplane_connected = False


datacache = {}


xp = None

def kb_wait_quit_event():
    print(f"*** Press ENTER to quit this script ***\n")
    while True:
        c = input() # wait for ENTER (not worth to implement kbhit for differnt plattforms, so make it very simple)
        print(f"Exit")
        os._exit(0)


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
            print("using hidapi mac version")
            self.device = hid.Device(vid=vid, pid=pid)

        if self.device is None:
            raise RuntimeError("Device not found")

        print("Device connected.")


def get_latest_release_github():
    url = "https://api.github.com/repos/schenlap/XSchenFly/releases/latest"
    response = requests.get(url, timeout=2)
    if response.status_code == 200:
        data = response.json()
        return data['name']
    else:
        print(f"Error fetching latest release: {response.status_code}")
        return None


def main():
    global xp
    global values, xplane_connected
    global device_config

    new_version = None

    # Check for new version on github. If you dont't want this, remove the following lines until 'version check end'
    print(f"Current version: {VERSION}")
    if "+" in VERSION:
        print(f"*** WARNING: this is a development version {VERSION}, disable online version check ***\n")
    else:
        latest_release = get_latest_release_github()
        if latest_release != None and latest_release != VERSION:
            print(f"New version {latest_release} available, please update winwing_mcdu.py")
            print(f"from http://github/com/schenlap/winwing_mcdu/releases/latest\n")
            new_version = latest_release
    # version check end

    kb_quit_event_thread = Thread(target=kb_wait_quit_event)
    kb_quit_event_thread.start()

    xp = XPlaneUdp.XPlaneUdp()
    xp.BeaconData["IP"] = UDP_IP # workaround to set IP and port
    xp.BeaconData["Port"] = UDP_PORT
    xp.UDP_PORT = xp.BeaconData["Port"]
    print(f'waiting for X-Plane to connect on port {xp.BeaconData["Port"]}')

    dev_winwing_mcdu = devices.winwing_mcdu.device(UDP_IP, UDP_PORT)
    dev_winwing_mcdu.init_device(VERSION, new_version)

    dev_winwing_fcu = devices.winwing_fcu.device(UDP_IP, UDP_PORT)
    dev_winwing_fcu.init_device(VERSION, new_version)

    dev_winwing_throttle = devices.winwing_throttle.device()
    dev_winwing_throttle.init_device()

    dev_rowsfire_a107 = devices.rowsfire_a107.device(UDP_IP, UDP_PORT)
    dev_rowsfire_a107.init_device(VERSION, new_version)

    while True:
        if not xplane_connected:
            try:
                xp.AddDataRef("sim/aircraft/view/acf_tailnum")
                values = xp.GetValues()

                print(f"X-Plane connected")
                xplane_connected = True
                dev_winwing_mcdu.connected()
                dev_winwing_fcu.connected()
                dev_rowsfire_a107.connected()
                dev_winwing_throttle.connected()
            except XPlaneUdp.XPlaneTimeout:
                xplane_connected = False
                sleep(1)
            continue

        try:
            dev_winwing_mcdu.cyclic.set()
            dev_winwing_fcu.cyclic.set()
            dev_rowsfire_a107.cyclic.set()
            dev_winwing_throttle.cyclic.set()
            values = xp.GetValues()
        except XPlaneUdp.XPlaneTimeout:
            print(f'X-Plane timeout, could not connect on port {xp.BeaconData["Port"]}, waiting for X-Plane')
            xplane_connected = False
            dev_winwing_mcdu.disconnected()
            dev_winwing_fcu.disconnected()
            dev_rowsfire_a107.disconnected()
            dev_winwing_throttle.disconnected()
            sleep(2)

if __name__ == '__main__':
  main() 
