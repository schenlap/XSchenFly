import asyncio

from dataclasses import dataclass
from enum import Enum, IntEnum

from threading import Thread, Event, Lock
from time import sleep

import hid

import XPlaneUdp # should not be used here
import json

import PyCmdMessenger # https://github.com/harmsm/PyCmdMessenger
from requests import Session
import websockets

# it is compatible to mobiflight
# commands see https://github.com/MobiFlight/MobiFlight-FirmwareSource/blob/main/src/CommandMessenger.cpp

XPLANE_WS_URL = "ws://localhost:8086/api/v2"

BUTTONS_CNT = 20

#@unique
class DEVICEMASK(IntEnum):
    NONE = 0
    A107 = 1 # mini overhead


class BUTTON(Enum):
    SWITCH = 0
    TOGGLE = 1
    SEND_0 = 2
    SEND_1 = 3
    SEND_2 = 4
    SEND_3 = 5
    SEND_4 = 6
    SEND_5 = 7
    NONE = 5 # for testing


class Leds(Enum):
    BACKLIGHT = 0 # 0 .. 255
    SCREEN_BACKLIGHT = 1 # 0 .. 255
    LOC_GREEN = 3 # all on/off



class DREF_TYPE(Enum):
    DATA = 0
    CMD = 1
    NONE = 2 # for testing
    ARRAY_0 = 10 # element [0]
    ARRAY_1 = 11 # element [1]
    ARRAY_2 = 12 # element [2]
    ARRAY_3 = 13 # element [3]


class Button:
    def __init__(self, nr, label, mf_button, dataref = None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.NONE, led = None):
        self.id = nr
        self.label = label
        self.mf_button = mf_button # Mobiflight
        self.dataref = dataref
        self.dreftype = dreftype
        #self.data = None
        self.type = button_type
        self.led = led

class Led:
    def __init__(self, nr, label, dataref, dreftype = DREF_TYPE.NONE):
        self.id = nr
        self.label = label
        self.dataref = dataref
        self.dreftype = dreftype

xplane_connected = False
buttonlist = []
ledlist = []
values = []

device_config = DEVICEMASK.NONE


# Konfiguration: Schaltername, DataRef-ID, Kommando
LICHTER = [
    (["Beacon Light", # 0
      "Wing Light", # 1
      "Nav Light", # 2
      "Land Left", # 3
      "Land Right", # 4
      "Nose Light", # 5
      "RWY Turn", # 6
      "Strobe Light", # 7
      "Seatbelts", # 11
      "Smoke"], # 12
      "AirbusFBW/OHPLightSwitches", [0, 1, 2, 3, 4, 5, 6, 7, 11, 12])
    #("Beacon Light",  None, "AirbusFBW/OHPLightSwitches", 0), # 0,1
    #("Taxi Light",    None, "sim/cockpit/electrical/taxi_light_on"),
    #("Strobe Light2",  None, "sim/cockpit/electrical/strobe_lights_on"),
    #("Nav Light",     None, "sim/cockpit/electrical/nav_lights_on"),
    #("Panel Light",   "sim/lights/panel_lights_toggle", "sim/cockpit/electrical/panel_light_on"),
] # dataref_id, switch_object gets added during runtime


def rawsfire_a107_set_leds(device, leds, brightness):
    if isinstance(leds, list):
        for i in range(len(leds)):
            rawsfire_a107_set_led(device, leds[i], brightness)
    else:
        rawsfire_a107_set_led(device, leds, brightness)

def rawsfire_a107_set_led(device, led, brightness):
    if led.value < 100: # FCU
        data = [0x02, 0x10, 0xbb, 0, 0, 3, 0x49, led.value, brightness, 0,0,0,0,0]
    if 'data' in locals():
      cmd = bytes(data)
      device.write(cmd)


def lcd_init(ep):
    data = [0xf0, 0x2, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0] # init packet
    cmd = bytes(data)
    ep.write(cmd)


def rawsfire_107_set_lcd(device, speed, heading, alt, vs):
    global usb_retry
    return



a107_device = None # usb /dev/inputx device

datacache = {}

# List of datarefs without led connection to request.
datarefs = [
    ("AirbusFBW/HDGdashed", 2)
  ]


buttons_press_event = [0] * BUTTONS_CNT
buttons_release_event = [0] * BUTTONS_CNT

usb_retry = False

xp = None

xp_dataref_ids = {}


def create_led_list_a107():
    ledlist.append(Led(0, "APU_MASTER_LED", "AirbusFBW/APUMaster"))
    ledlist.append(Led(1, "APU_STARTER_LED", "AirbusFBW/APUStarter"))
    ledlist.append(Led(2, "ADIRS_ON_BAT_LED", "AirbusFBW/ADIRUOnBat"))
    ledlist.append(Led(3, "GPWS_FLAP3_LED", "AirbusFBW/GPWSSwitchArray", DREF_TYPE.ARRAY_3))
    ledlist.append(Led(4, "GPWS_FLAP_MODE_LED", "AirbusFBW/GPWSSwitchArray", DREF_TYPE.ARRAY_2))


def create_button_list_a107():
    create_led_list_a107()
    buttonlist.append(Button(0, "APU_MASTER", "MF_Name_APU_Master", "AirbusFBW/APUMaster", DREF_TYPE.DATA, BUTTON.TOGGLE, ledlist[0]))
    buttonlist.append(Button(1, "APU_START", "MF_Name_APU_Start", "AirbusFBW/APUStarter", DREF_TYPE.DATA, BUTTON.TOGGLE, ledlist[1]))


def a107_button_event(xp):
    #print(f'events: press: {buttons_press_event}, release: {buttons_release_event}')
    for b in buttonlist:
        if not any(buttons_press_event) and not any(buttons_release_event):
            break
        if b.id == None:
            continue
        if buttons_press_event[b.id]:
            buttons_press_event[b.id] = 0
            #print(f'button {b.label} pressed')
            if b.type == BUTTON.TOGGLE:
                val = datacache[b.dataref]
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} from {bool(val)} to {not bool(val)}')
                    xp.WriteDataRef(b.dataref, not bool(val))
                elif b.dreftype== DREF_TYPE.CMD:
                    print(f'send command {b.dataref}')
                    xp.SendCommand(b.dataref)
            elif b.type == BUTTON.SWITCH:
                val = datacache[b.dataref]
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 1')
                    xp.WriteDataRef(b.dataref, 1)
                elif b.dreftype== DREF_TYPE.CMD:
                    print(f'send command {b.dataref}')
                    xp.SendCommand(b.dataref)
            elif b.type == BUTTON.SEND_0:
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 0')
                    xp.WriteDataRef(b.dataref, 0)
            elif b.type == BUTTON.SEND_1:
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 1')
                    xp.WriteDataRef(b.dataref, 1)
            elif b.type == BUTTON.SEND_2:
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 2')
                    xp.WriteDataRef(b.dataref, 2)
            elif b.type == BUTTON.SEND_3:
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 3')
                    xp.WriteDataRef(b.dataref, 3)
            elif b.type == BUTTON.SEND_4:
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 4')
                    xp.WriteDataRef(b.dataref, 4)
            elif b.type == BUTTON.SEND_5:
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 5')
                    xp.WriteDataRef(b.dataref, 5)
            else:
                print(f'no known button type for button {b.label}')
        if buttons_release_event[b.id]:
            buttons_release_event[b.id] = 0
            print(f'button {b.label} released')
            if b.type == BUTTON.SWITCH:
                xp.WriteDataRef(b.dataref, 0)


def fcu_create_events(usb_mgr):
    return


def set_button_led_lcd(device, dataref, v):
    global led_brightness
    for b in buttonlist:
        if b.dataref == dataref:
            if b.led == None:
                break
            if v >= 255:
                v = 255
            print(f'led: {b.led}, value: {v}')

            rawsfire_a107_set_leds(device, b.led, int(v))
            if b.led == Leds.BACKLIGHT:
                rawsfire_a107_set_led(device, Leds.EXPED_YELLOW, int(v))
                print(f'set led brigthness: {b.led}, value: {v}')
                led_brightness = v
            break


def set_datacache(usb_mgr, values):
    global datacache
    global exped_led_state

    new = False
 

def startupscreen(device, device_config, version, new_version):
    leds = [Leds.SCREEN_BACKLIGHT, Leds.BACKLIGHT]

    rawsfire_a107_set_leds(device, leds, 80)
    #rawsfire_a107_set_lcd(device, version[1:], "   ", "Schen", " lap")


def get_dataref_id():
    global LICHTER
    global xp_dataref_ids
    global cmdrefs_ids

    xp = Session()
    xp.headers["Accept"] = "application/json"
    xp.headers["Content-Type"] = "application/json"
    print(f"[A107] reading led dataref ids ...")
    for l in ledlist:
        xpdr_code_response = xp.get("http://localhost:8086/api/v2/datarefs", params={"filter[name]": l.dataref})
        if xpdr_code_response.status_code != 200:
            print(xpdr_code_response)
            return
        print(f'name: {l.label}, id: {xpdr_code_response.json()["data"][0]["id"]}')
        if xpdr_code_response.json()["data"][0]["id"] in xp_dataref_ids:
            print(f"[A107] INFO: Object dataref alread registered")
            continue
        if l.dreftype.value >= DREF_TYPE.ARRAY_0.value:
            larray = []
            for l2 in ledlist:
                if l2.dataref == l.dataref:
                    larray.append(l2)
            xp_dataref_ids[xpdr_code_response.json()["data"][0]["id"]] = larray.copy()
            print(f"[A107] ARRAY in ids {larray}")
        else:
            xp_dataref_ids[xpdr_code_response.json()["data"][0]["id"]] = l


async def xplane_ws_listener():
    print(f"[A107] register datarefs ...")
    xpws_req_id = 0
    async with websockets.connect(XPLANE_WS_URL) as ws:
        # Abonnement senden
        subscribe_msg = {
            "req_id": xpws_req_id,
            "type": "dataref_subscribe_values",
            "params": {
                "datarefs": [{"id": ref_id} for ref_id in xp_dataref_ids]
                #"commands": [{"id": ref_id_cmd} for ref_id_cmd in cmdref_ids]
            }
        }
        #print(subscribe_msg)
        await ws.send(json.dumps(subscribe_msg))
        xpws_req_id = xpws_req_id + 1

        # Warte auf Bestätigung
        ack = await ws.recv()
        ack_data = json.loads(ack)
        if not ack_data.get("success", False):
            print(f"Abonnement fehlgeschlagen: {ack_data}")
            return

        print("[A107] dataref subscription done")

        # Haupt-Loop: Updates empfangen
        while True:
            try:
                msg = await ws.recv()
                data = json.loads(msg)

                print(f"[A107] recevice: {data}")
                if data.get("type") == "dataref_update_values":
                    for ref_id_str, value in data["data"].items():
                        ref_id = int(ref_id_str)
                        print(f"[A107] searching for {ref_id}...", end='')
                        if ref_id in xp_dataref_ids:
                            ledobj = xp_dataref_ids[ref_id]

                            if type(value) is list:
                                if type(ledobj) != list:
                                    print("")
                                    print(f"[A107] ERROR: led array dataref not registered as list!")
                                    exit()
                                print(f"") # end line
                                idx = 0
                                for v in value:
                                    for l2 in ledobj: # we received an array, send update to all objects
                                        if idx == l2.dreftype.value - DREF_TYPE.ARRAY_0.value:
                                            print(f"[A107]                       array value[{idx}] of {l2.label} = {value[idx]}")
                                    idx += 1
                            #    for s in switch:
                            #        value2 = value[LICHTER[0][2][idx]]
                            #        print(f"value: {value2}")
                            #        s.value = bool(value2)
                            #        s.update()
                            #        idx =idx + 1
                            else:
                                print(f" found: {ledobj.label} = {value}")
                            #switch.value = bool(value)
                            #switch.update()
                        else:
                            print(f" not found")
                else:
                    print(f"[A107] not defined {data}")

            except Exception as e:
                print(f"[A107] Fehler im Listener: {e}")
                break


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
            print("[A107] using hidapi mac version")
            self.device = hid.Device(vid=vid, pid=pid)

        if self.device is None:
            raise RuntimeError("Device not found")

        print("[A107] Device connected.")

    def find_device(self):
        device_config = 0

        devlist = [{'vid':0x4098, 'pid':0xba01, 'name':'A107', 'mask':DEVICEMASK.A107},
        ]

        for d in devlist:
            print(f"[A107] now searching for rawsfire {d['name']} ... ", end='')
            found = False
            for dev in hid.enumerate():
                if dev['vendor_id'] == d['vid'] and dev['product_id'] == d['pid']:
                    print("found")
                    self.device_config |= d['mask']
                    return d['vid'], d['pid'], self.device_config
            print("not found")
        return None, None, 0
    

async def start_xp_ws():
    t = asyncio.create_task(xplane_ws_listener())
    await asyncio.wait({t})


class device:
    def __init__(self, UDP_IP, UDP_PORT):
        self.usb_mgr = None
        self.cyclic = Event()

    def connected(self):
        global xplane_connected
        print(f"[A107] X-Plane connected")
        get_dataref_id()
        #RequestDataRefs(self.xp)
        #loop = asyncio.new_event_loop()
        #asyncio.set_event_loop(loop)
        asyncio.run(start_xp_ws())
        #asyncio.run(create_ws())
        #xplane_connected = True


    def disconnected(self):
        global xplane_connected
        xplane_connected = False
        print(f"[A107] X-Plane disconnected")
        startupscreen(self.usb_mgr.device, device_config, self.version, self.new_version)


    def cyclic_worker(self):
        global value
        global device_config
        global values

        self.cyclic.wait()
        #while True:
            #try:
            #    values = self.xp.GetValues()
            #    values_processed.wait()
            #except XPlaneUdp.XPlaneTimeout:
            #    sleep(1)
            #    continue


    def init_device(self, version: str = None, new_version: str = None):
        global values, xplane_connected
        global device_config
        global datacache

        self.version = version
        self.new_version = new_version

        self.usb_mgr = UsbManager()
        vid, pid, device_config = self.usb_mgr.find_device()

        if pid is None:
            exit(f" [A107] No compatible rawsfire device found, quit")
        else:
            self.usb_mgr.connect_device(vid=vid, pid=pid)

        create_button_list_a107()
    
        startupscreen(self.usb_mgr.device, device_config, version, new_version)

        #usb_event_thread = Thread(target=fcu_create_events, args=[self.usb_mgr])
        #usb_event_thread.start()

        #cyclic_thread = Thread(target=self.cyclic_worker)
        #cyclic_thread.start()
