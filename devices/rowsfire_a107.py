import asyncio

from dataclasses import dataclass
from enum import Enum, IntEnum

from threading import Thread, Event, Lock
from time import sleep

import hid

import json
import time

import PyCmdMessenger # https://github.com/harmsm/PyCmdMessenger
from requests import Session
import websockets

# it is compatible to mobiflight
# commands see https://github.com/MobiFlight/MobiFlight-FirmwareSource/blob/main/src/CommandMessenger.cpp

XPLANE_WS_URL = "ws://localhost:8086/api/v2"
XPLANE_REST_URL = "http://localhost:8086/api/v2"

class XP_Websocket:
    def __init__(self, rest_url, ws_url):
        self.xp_dataref_ids = {}
        self.rest_url = rest_url
        self.ws_url = ws_url
        self.xp = Session()
        self.xp.headers["Accept"] = "application/json"
        self.xp.headers["Content-Type"] = "application/json"
        self.iddict = {}
        self.req_id = 0
        self.ws = None


    def dataref_id_fetch(self, dataref):
        xpdr_code_response = self.xp.get(self.rest_url + "/datarefs", params={"filter[name]": dataref})
        if xpdr_code_response.status_code != 200:
            print(f"could not get id for {dataref}, Errorcode: {xpdr_code_response.status_code}:{xpdr_code_response.text}")
            return None
        return xpdr_code_response.json()["data"][0]["id"]
    

    def command_id_fetch(self, command):
        xpdr_code_response = self.xp.get(self.rest_url + "/commands", params={"filter[name]": command})
        if xpdr_code_response.status_code != 200:
            print(f"could not get id for {command}, Errorcode: {xpdr_code_response.status_code}:{xpdr_code_response.text}")
            return None
        return xpdr_code_response.json()["data"][0]["id"]


    def dataref_set_value(self, id, value, index = None):
        if type(id) is not int:
            id = self.dataref_id_fetch(id)

        set_msg = {
            "data": value
        }
        if index:
            xpdr_code_response = self.xp.patch(self.rest_url + "/datarefs/" + str(id) + "/value", data=json.dumps(set_msg), params={"index":index})
        else:
            xpdr_code_response = self.xp.patch(self.rest_url + "/datarefs/" + str(id) + "/value", data=json.dumps(set_msg))   

        if xpdr_code_response.status_code != 200:
            print(f"could not set data for id {id}. Errorcode: {xpdr_code_response.status_code}:{xpdr_code_response.text}")
            return None


    def command_activate_duration(self, id, duration = 0.2):
        if type(id) is not int:
            id = self.command_id_fetch(id)

        set_msg = {
            "duration": duration
        }

        xpdr_code_response = self.xp.post(self.rest_url + "/command/" + str(id) + "/activate", data=json.dumps(set_msg))

        if xpdr_code_response.status_code != 200:
            print(f"could not send command for id {id}. Errorcode: {xpdr_code_response.status_code}:{xpdr_code_response.text}")
            return None


    def datarefs_subscribe(self,dataref_list, update_callback = None):
        asyncio.run(self.async_dataref_subscribe(dataref_list, update_callback))


    async def async_dataref_subscribe(self, dataref_list, update_callback):
        await (self.async_dataref_subsrcibe2_listener(dataref_list, update_callback))


    async def async_dataref_subsrcibe2_listener(self, dataref_list, update_callback):
        async with websockets.connect(self.ws_url, open_timeout=100) as ws:
            self.ws = ws
            # Abonnement senden
            subscribe_msg = {
                "req_id": self.req_id,
                "type": "dataref_subscribe_values",
                "params": {
                    "datarefs": [{"id": ref_id} for ref_id in dataref_list]
                    #"commands": [{"id": ref_id_cmd} for ref_id_cmd in cmdref_ids]
                }
            }
            await ws.send(json.dumps(subscribe_msg))
            self.req_id += 1

            # wait for ack
            ack = await ws.recv()
            ack_data = json.loads(ack)
            if not ack_data.get("success", False):
                print(f"Abonnement fehlgeschlagen: {ack_data}")
                return

            # main-rx-Loop: get updates
            while True:
                try:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    if update_callback:
                        update_callback(data)
                except Exception as e:
                    print(f"[A107] Fehler im Listener: {e}")
                    break


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


class DREF_TYPE(Enum):
    DATA = 0
    CMD = 1
    NONE = 2 # for testing
    ARRAY_0 = 10 # element [0]
    ARRAY_1 = 11 # element [1]
    ARRAY_2 = 12 # element [2]
    ARRAY_3 = 13
    ARRAY_4 = 14
    ARRAY_5 = 15
    ARRAY_6 = 16
    ARRAY_7 = 17
    ARRAY_8 = 18
    ARRAY_9 = 19
    ARRAY_10 = 20
    ARRAY_11 = 21
    ARRAY_12 = 22


class Button:
    def __init__(self, nr, label, mf_button, dataref = None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.NONE):
        self.id = nr
        self.label = label
        self.mf_button = mf_button # Mobiflight
        self.dataref = dataref
        self.dreftype = dreftype
        self.type = button_type

class Led:
    def __init__(self, nr, label, dataref, dreftype = DREF_TYPE.NONE, eval = None):
        self.id = nr
        self.label = label
        self.dataref = dataref
        self.dreftype = dreftype
        self.eval = eval

xplane_connected = False
xp_dataref_ids = {}
buttonlist = []
ledlist = []

device_config = DEVICEMASK.NONE


def rawsfire_a107_set_leds(device, leds, brightness):
    if isinstance(leds, list):
        for i in range(len(leds)):
            rawsfire_a107_set_led(device, leds[i], brightness)
    else:
        rawsfire_a107_set_led(device, leds, brightness)


def rawsfire_a107_set_led(device, led, brightness):
    return # TODO
    if led.value < 100: # FCU
        data = [0x02, 0x10, 0xbb, 0, 0, 3, 0x49, led.value, brightness, 0,0,0,0,0]
    if 'data' in locals():
      cmd = bytes(data)
      device.write(cmd)


def lcd_init(ep):
    return # TODO
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

usb_retry = False

xp = None




def create_led_list_a107():
    ledlist.append(Led(0, "APU_MASTER_ON_LED", "AirbusFBW/APUMaster"))
    ledlist.append(Led(1, "APU_MASTER_FAULT_LED", None))
    ledlist.append(Led(2, "APU_STARTER_ON_LED", "AirbusFBW/APUStarter"))
    ledlist.append(Led(3, "APU_STARTER_AVAIL_LED", "AirbusFBW/APUAvail"))
    ledlist.append(Led(4, "APU_GEN_OFF_LED", "AirbusFBW/APUGenOHPArray", DREF_TYPE.ARRAY_0, "==0"))
    ledlist.append(Led(5, "AIR_APU_BLEED_ON_LED", "AirbusFBW/APUBleedSwitch"))
    ledlist.append(Led(6, "AIR_APU_BLEED_FAULT_LED", None))
    ledlist.append(Led(7, "AIR_PACK1_BLEED_OFF_LED", "AirbusFBW/Pack1Switch", DREF_TYPE.DATA, "==0"))
    ledlist.append(Led(8, "AIR_PACK1_BLEED_FAULT_LED", None))
    ledlist.append(Led(9, "AIR_PACK2_BLEED_OFF_LED", "AirbusFBW/Pack2Switch", DREF_TYPE.DATA, "==0"))
    ledlist.append(Led(10, "AIR_PACK2_BLEED_FAULT_LED", None))
    ledlist.append(Led(11, "ADIRS_ON_BAT_LED", "AirbusFBW/ADIRUOnBat"))
    ledlist.append(Led(12, "GPWS_FLAP3_ON_LED", "AirbusFBW/GPWSSwitchArray", DREF_TYPE.ARRAY_3))
    ledlist.append(Led(13, "RCDR_GND_CTL_ON_LED", "AirbusFBW/CvrGndCtrl"))
    ledlist.append(Led(14, "OXYGEN_CREW_SUPPLY_OFF_LED", "AirbusFBW/CrewOxySwitch", DREF_TYPE.DATA, "==0"))
    ledlist.append(Led(15, "ANTIICE_WING_ON_LED", None))
    ledlist.append(Led(16, "ANTIICE_WING_FAULT_LED", None))
    ledlist.append(Led(17, "ANTIICE_ENG1_ON_LED", None))
    ledlist.append(Led(18, "ANTIICE_ENG1_FAULT_LED", None))
    ledlist.append(Led(19, "ANTIICE_ENG2_ON_LED", None))
    ledlist.append(Led(20, "ANTIICE_ENG2_FAULT_LED", None))
    ledlist.append(Led(21, "ELEC_BAT1_OFF_LED", "AirbusFBW/BatOHPArray", DREF_TYPE.ARRAY_0, "==1"))
    ledlist.append(Led(22, "ELEC_BAT1_FAULT_LED", "AirbusFBW/BatOHPArray", DREF_TYPE.ARRAY_0, "==3"))
    ledlist.append(Led(23, "ELEC_BAT2_OFF_LED", "AirbusFBW/BatOHPArray", DREF_TYPE.ARRAY_1, "==1"))
    ledlist.append(Led(24, "ELEC_BAT2_FAULT_LED", "AirbusFBW/BatOHPArray", DREF_TYPE.ARRAY_1, "==3"))
    ledlist.append(Led(25, "ELEC_EXT_PWR_AVAIL_LED", "AirbusFBW/ExtPowOHPArray", DREF_TYPE.ARRAY_0, "==2"))
    ledlist.append(Led(26, "ELEC_EXT_PWR_ON_LED", "AirbusFBW/ExtPowOHPArray", DREF_TYPE.ARRAY_0, "==1"))
    ledlist.append(Led(27, "FUEL_L_PUMP1_OFF_LED", "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_0, "==1"))
    ledlist.append(Led(28, "FUEL_L_PUMP1_FAULT_LED", "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_0, "==3"))
    ledlist.append(Led(29, "FUEL_L_PUMP2_OFF_LED", "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_1, "==1"))
    ledlist.append(Led(30, "FUEL_L_PUMP2_FAULT_LED", "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_1, "==3"))
    ledlist.append(Led(31, "FUEL_PUMP1_OFF_LED", "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_2, "==1"))
    ledlist.append(Led(32, "FUEL_PUMP1_FAULT_LED", "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_2, "==3"))
    ledlist.append(Led(31, "FUEL_PUMP2_OFF_LED", "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_3, "==1"))
    ledlist.append(Led(32, "FUEL_PUMP2_FAULT_LED", "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_3, "==3"))
    ledlist.append(Led(33, "FUEL_R_PUMP1_OFF_LED", "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_4, "==1"))
    ledlist.append(Led(34, "FUEL_R_PUMP1_FAULT_LED", "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_4, "==3"))
    ledlist.append(Led(35, "FUEL_R_PUMP2_OFF_LED", "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_5, "==1"))
    ledlist.append(Led(36, "FUEL_R_PUMP2_FAULT_LED", "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_5, "==3"))
    ledlist.append(Led(37, "FUEL_MODE_SEL_OFF_LED", "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_6, "==1"))
    ledlist.append(Led(38, "FUEL_MODE_SEL_FAULT_LED", "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_6, "==3"))
    ledlist.append(Led(39, "FIRE_ENG1_ON_LED", None, DREF_TYPE.ARRAY_6))
    ledlist.append(Led(40, "FIRE_ENG2_ON_LED", None, DREF_TYPE.ARRAY_6))
    ledlist.append(Led(41, "FIRE_APU_ON_LED", "AirbusFBW/APUOnFire"))
    ledlist.append(Led(42, "ADIRS_IR1_ALTN_LED", "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_6))
    ledlist.append(Led(43, "ADIRS_IR1_FAULT_LED", "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_7))
    ledlist.append(Led(44, "ADIRS_IR2_ALTN_LED", "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_8))
    ledlist.append(Led(45, "ADIRS_IR2_FAULT_LED", "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_9))
    ledlist.append(Led(46, "ADIRS_IR3_ALTN_LED", "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_10))
    ledlist.append(Led(47, "ADIRS_IR3_FAULT_LED", "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_11))


def create_button_list_a107():
    create_led_list_a107()
    buttonlist.append(Button(0, "APU_MASTER", "MF_Name_APU_Master", "AirbusFBW/APUMaster", DREF_TYPE.DATA, BUTTON.TOGGLE))
    buttonlist.append(Button(1, "APU_START", "MF_Name_APU_Start", "AirbusFBW/APUStarter", DREF_TYPE.DATA, BUTTON.TOGGLE))
    buttonlist.append(Button(2, "APU_BLEED", "MF_Name_APU_Bleed", "AirbusFBW/AirbusFBW/APUBleedSwitch", DREF_TYPE.DATA, BUTTON.TOGGLE))
    buttonlist.append(Button(3, "Beacon Light", "MF_Name_Beacon_Light", "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_0, BUTTON.TOGGLE))
    buttonlist.append(Button(4, "Wing Light", "MF_Name_Wing_Light", "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_1, BUTTON.TOGGLE))
    buttonlist.append(Button(5, "Nav Light", "MF_Name_Nav_Light", "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_2, BUTTON.TOGGLE))
    buttonlist.append(Button(6, "Land Left Light", "MF_Name_Land_Left_Light", "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_3, BUTTON.TOGGLE))
    buttonlist.append(Button(7, "Land Tight Light", "MF_Name_Land_Right_Light", "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_4, BUTTON.TOGGLE))
    buttonlist.append(Button(8, "Nose Light", "MF_Name_Nose_Light", "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_5, BUTTON.TOGGLE))
    buttonlist.append(Button(9, "RWY Turn Light", "MF_Name_RWY_Turn_Light", "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_6, BUTTON.TOGGLE))
    buttonlist.append(Button(10, "Strobe Light", "MF_Name_Strobe_Light", "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_7, BUTTON.TOGGLE))
    buttonlist.append(Button(11, "Seatbelt", "MF_Name_Seatbelt", "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_11, BUTTON.TOGGLE))
    buttonlist.append(Button(12, "Smoke", "MF_Name_Smoke", "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_12, BUTTON.TOGGLE))


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
 

def startupscreen(device, device_config, version, new_version):
    print("TODO set startupscreen")


def xplane_get_dataref_ids(xp):
    global LICHTER
    global xp_dataref_ids
    global cmdrefs_ids

    print(f"[A107] getting led dataref ids ... ", end="")
    for l in ledlist:
        if l.dataref == None:
            continue
        id = xp.dataref_id_fetch(l.dataref)
        #print(f'name: {l.label}, id: {id}')
        if id in xp_dataref_ids:
            continue
        if l.dreftype.value >= DREF_TYPE.ARRAY_0.value:
            larray = []
            for l2 in ledlist:
                if l2.dataref == l.dataref:
                    larray.append(l2)
            xp_dataref_ids[id] = larray.copy()
        else:
            xp_dataref_ids[id] = l
    print("done")


def xplane_ws_listener(data):
    #print(f"[A107] recevice: {data}")
    if data.get("type") != "dataref_update_values":
        print(f"[A107] not defined {data}")
        return

    for ref_id_str, value in data["data"].items():
        ref_id = int(ref_id_str)
        print(f"[A107] searching for {ref_id}...", end='')
        if ref_id in xp_dataref_ids:
            ledobj = xp_dataref_ids[ref_id]

            if type(value) is list:
                if type(ledobj) != list:
                    #print("")
                    print(f"[A107] ERROR: led array dataref not registered as list!")
                    exit()
                #print(f"") # end line
                idx = 0
                for v in value:
                    for l2 in ledobj: # we received an array, send update to all objects
                        if idx == l2.dreftype.value - DREF_TYPE.ARRAY_0.value:
                            value_new = value[idx]
                            if l2.eval:
                                s = 'value_new' + l2.eval
                                value_new = eval(s)
                            print(f"[A107]                       array value[{idx}] of {l2.label} = {value_new}")
                            #TODO: update LED on panel 1/2
                    idx += 1
            else:
                if ledobj.eval != None:
                    s = 'value' + ledobj.eval
                    value = eval(s)
                print(f" found: {ledobj.label} = {value}")
                #TODO: update LED on panel 2/2
        else:
            print(f" not found")


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


class device:
    def __init__(self, UDP_IP, UDP_PORT):
        self.usb_mgr = None
        self.cyclic = Event()
        self.xp = XP_Websocket(XPLANE_REST_URL, XPLANE_WS_URL)


    def connected(self):
        global xplane_connected
        print(f"[A107] X-Plane connected")
        xplane_get_dataref_ids(self.xp)
        print(f"[A107] subsrcibe datarefs... ", end="")
        t = Thread(target=self.xp.datarefs_subscribe, args=(xp_dataref_ids, xplane_ws_listener))
        t.start()
        print(f"done")
        xplane_connected = True


    def disconnected(self):
        global xplane_connected
        xplane_connected = False
        print(f"[A107] X-Plane disconnected")
        startupscreen(self.usb_mgr.device, device_config, self.version, self.new_version)


    def cyclic_worker(self):
        global device_config

        self.cyclic.wait()
        apu_master = self.xp.dataref_id_fetch("AirbusFBW/APUMaster")
        strobe = self.xp.dataref_id_fetch("AirbusFBW/OHPLightSwitches")
        antiice = self.xp.command_id_fetch("toliss_airbus/antiicecommands/WingToggle")
        while True:
            self.xp.dataref_set_value(apu_master, 1)
            self.xp.dataref_set_value(strobe, 1, 7)
            self.xp.command_activate_duration(antiice, 1)
            time.sleep(2)
            self.xp.dataref_set_value(apu_master, 0)
            self.xp.dataref_set_value(strobe, 0, 7)
            time.sleep(2)


    def init_device(self, version: str = None, new_version: str = None):
        global xplane_connected
        global device_config
        global datacache

        self.version = version
        self.new_version = new_version

        self.usb_mgr = UsbManager()
        vid, pid, device_config = self.usb_mgr.find_device()

        if pid is None:
            return(f" [A107] No compatible rawsfire device found, quit")
        else:
            self.usb_mgr.connect_device(vid=vid, pid=pid)

        create_button_list_a107()
    
        startupscreen(self.usb_mgr.device, device_config, version, new_version)

        #usb_event_thread = Thread(target=fcu_create_events, args=[self.usb_mgr])
        #usb_event_thread.start()

        cyclic_thread = Thread(target=self.cyclic_worker)
        cyclic_thread.start()
