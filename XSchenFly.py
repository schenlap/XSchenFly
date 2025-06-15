#!/usr/bin/env python3
VERSION = "v1.4+"

# IP Address of machine running X-Plane. 
UDP_IP = "127.0.0.1"
UDP_PORT = 49000

import binascii
from dataclasses import dataclass
from enum import Enum, IntEnum
import os
import requests
import socket
import struct

#for raw usb
import re
import subprocess

from threading import Thread, Event, Lock
from time import sleep

import hid
import usb.core
import usb.backend.libusb1
import usb.util


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

    def find_device(self):
        device_config = 0
        devlist = [
            {'vid': 0x4098, 'pid': 0xbb36, 'name': 'MCDU - Captain', 'mask': DEVICEMASK.MCDU | DEVICEMASK.CAP},
            {'vid': 0x4098, 'pid': 0xbb3e, 'name': 'MCDU - First Offizer', 'mask': DEVICEMASK.MCDU | DEVICEMASK.FO},
            {'vid': 0x4098, 'pid': 0xbb3a, 'name': 'MCDU - Observer', 'mask': DEVICEMASK.MCDU | DEVICEMASK.OBS},
            {'vid': 0x4098, 'pid': 0xbb35, 'name': 'PFP 3N - Captain (not tested)', 'mask': DEVICEMASK.PFP3N | DEVICEMASK.CAP},
            {'vid': 0x4098, 'pid': 0xbb39, 'name': 'PFP 3N - First Officer (not tested)', 'mask': DEVICEMASK.PFP3N | DEVICEMASK.FO},
            {'vid': 0x4098, 'pid': 0xbb3d, 'name': 'PFP 3N - Observer (not tested)', 'mask': DEVICEMASK.PFP3N | DEVICEMASK.OBS},
            {'vid': 0x4098, 'pid': 0xbc1d, 'name': 'PFP 4 (not tested)', 'mask': DEVICEMASK.PFP4},
            {'vid': 0x4098, 'pid': 0xba01, 'name': 'PFP 7 (not tested)', 'mask': DEVICEMASK.PFP7}
        ]
        for d in devlist:
            print(f"now searching for winwing {d['name']} ... ", end='')
            found = False
            for dev in hid.enumerate():
                if dev['vendor_id'] == d['vid'] and dev['product_id'] == d['pid']:
                    print("found")
                    self.device_config |= d['mask']
                    # to force F/O or CAP, uncomment the following line
                    #self.device_config = DEVICEMASK.MCDU | DEVICEMASK.CAP
                    #self.device_config = DEVICEMASK.MCDU | DEVICEMASK.FO
                    return d['vid'], d['pid'], self.device_config
            print("not found")
        return None, None, 0


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

    while True:
        if not xplane_connected:
            try:
                xp.AddDataRef("sim/aircraft/view/acf_tailnum")
                values = xp.GetValues()

                print(f"X-Plane connected")
                xplane_connected = True
            except XPlaneUdp.XPlaneTimeout:
                xplane_connected = False
                sleep(1)
            continue

        try:
            values = xp.GetValues()
            #values_processed.wait()
        except XPlaneUdp.XPlaneTimeout:
            print(f'X-Plane timeout, could not connect on port {xp.BeaconData["Port"]}, waiting for X-Plane')
            xplane_connected = False
            sleep(2)

if __name__ == '__main__':
  main() 
