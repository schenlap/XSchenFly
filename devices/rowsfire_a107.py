import asyncio

from dataclasses import dataclass
from enum import Enum, IntEnum

from threading import Thread, Event, Lock
from time import sleep

import json
import time

from requests import Session
import websockets

import mobiflight_client as mf

# it is compatible to mobiflight
# commands see https://github.com/MobiFlight/MobiFlight-FirmwareSource/blob/main/src/CommandMessenger.cpp

XPLANE_WS_URL = "ws://localhost:8086/api/v2"
XPLANE_REST_URL = "http://localhost:8086/api/v2"

MOBIFLIGHT_SERIAL = "SN-301-533"

xp = None
mf_dev = None

class XP_Websocket:
    def __init__(self, rest_url, ws_url):
        self.led_dataref_ids = {}  # dict: data_id -> led / ledarray
        self.buttonref_ids = {} # dict: button -> cmd_id
        #self.buttonref_ids = {} # dict: button -> data_id
        self.rest_url = rest_url
        self.ws_url = ws_url
        self.xp = Session()
        self.xp.headers["Accept"] = "application/json"
        self.xp.headers["Content-Type"] = "application/json"
        self.iddict = {}
        self.req_id = 0
        self.ws = None
        self.datacache = {}


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
            "data": int(value)
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
                    #"commands": [{"id": ref_id_cmd} for ref_id_cmd in buttonref_ids]
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
                except Exception as e:
                    print(f"[A107] Fehler im Listener: {e}")
                    break
                if update_callback:
                    update_callback(data, dataref_list)


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
    HOLD   = 8
    NONE = 10 # for testing


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
    def __init__(self, nr, label, mf_button, mf_pin, dataref = None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.NONE):
        self.id = nr
        self.label = label
        self.mf_button = mf_button # Mobiflight
        self.mf_pin = mf_pin # pin number (on shift or multiplexer)
        self.dataref = dataref
        self.dreftype = dreftype
        self.type = button_type


    def __str__(self):
            return(f"{self.label} -> {self.dataref} {self.type}")

class Led:
    def __init__(self, nr, label, mf_name, mf_pin, dataref, dreftype = DREF_TYPE.NONE, eval = None):
        self.id = nr
        self.label = label
        self.mf_name = mf_name # Mobiflight
        self.mf_pin = mf_pin # pin number (on shift or multiplexer)
        self.dataref = dataref
        self.dreftype = dreftype
        self.eval = eval

xplane_connected = False
buttonlist = []
ledlist = []

device_config = DEVICEMASK.NONE


def rawsfire_a107_set_leds(leds, brightness):
    if isinstance(leds, list):
        for i in range(len(leds)):
            rawsfire_a107_set_led(leds[i], brightness)
    else:
        rawsfire_a107_set_led(leds, brightness)


def rawsfire_a107_set_led(led, brightness):
    global mf_dev

    if brightness == 1:
        brightness = 255
    if brightness > 255:
        brightness = 255

    mf_dev.set_pin(led.mf_name, led.mf_pin, brightness)


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


MF_SR1 = "ShiftRegister 1" # Output
MF_SR2 = "ShiftRegister 2" # Output

MF_MP1 = "Multiplexer 1" # Input
MF_MP2 = "Multiplexer 2" # Input
MF_MP3 = "Multiplexer 3" # Input
MF_MP4 = "Multiplexer 4" # Input

def create_led_list_a107():  # TODO check sim/cockpit/electrical/avionics_on == 1
    ledlist.append(Led(0, "APU_MASTER_ON_LED", MF_SR2, 11, "AirbusFBW/APUMaster"))
    ledlist.append(Led(1, "APU_MASTER_FAULT_LED", MF_SR2, 10, None))
    ledlist.append(Led(2, "APU_STARTER_ON_LED", MF_SR2, 19, "AirbusFBW/APUStarter"))
    ledlist.append(Led(3, "APU_STARTER_AVAIL_LED", MF_SR2, 18, "AirbusFBW/APUAvail"))
    ledlist.append(Led(4, "APU_GEN_OFF_LED",  MF_SR2, 3, "AirbusFBW/APUGenOHPArray", DREF_TYPE.ARRAY_0, "==0"))
    ledlist.append(Led(5, "AIR_APU_BLEED_ON_LED", MF_SR2, 7, "AirbusFBW/APUBleedSwitch"))
    ledlist.append(Led(6, "AIR_APU_BLEED_FAULT_LED", MF_SR2, 6, None))
    ledlist.append(Led(7, "AIR_PACK1_BLEED_OFF_LED", MF_SR2, 5, "AirbusFBW/Pack1Switch", DREF_TYPE.DATA, "==0"))
    ledlist.append(Led(8, "AIR_PACK1_BLEED_FAULT_LED", MF_SR2, 4, "AirbusFBW/OHPLightsATA21_Raw", DREF_TYPE.ARRAY_7, "&2"))
    ledlist.append(Led(9, "AIR_PACK2_BLEED_OFF_LED", MF_SR2, 9, "AirbusFBW/Pack2Switch", DREF_TYPE.DATA, "==0"))
    ledlist.append(Led(10, "AIR_PACK2_BLEED_FAULT_LED", MF_SR2, "AirbusFBW/OHPLightsATA21_Raw", DREF_TYPE.ARRAY_9, "&2"))
    ledlist.append(Led(11, "ADIRS_ON_BAT_LED", MF_SR1, 1, "AirbusFBW/ADIRUOnBat"))
    ledlist.append(Led(12, "GPWS_FLAP3_ON_LED", MF_SR1, 5, "AirbusFBW/GPWSSwitchArray", DREF_TYPE.ARRAY_3))
    ledlist.append(Led(13, "RCDR_GND_CTL_ON_LED", MF_SR1, 6, "AirbusFBW/CvrGndCtrl"))
    ledlist.append(Led(14, "OXYGEN_CREW_SUPPLY_OFF_LED", MF_SR1, 7, "AirbusFBW/CrewOxySwitch", DREF_TYPE.DATA, "==0"))
    ledlist.append(Led(15, "ANTIICE_WING_ON_LED", MF_SR2, 13, "AirbusFBW/WAILights", DREF_TYPE.DATA, "&1"))
    ledlist.append(Led(16, "ANTIICE_WING_FAULT_LED", MF_SR2, 12, "AirbusFBW/WAILights", DREF_TYPE.DATA, "&2"))
    ledlist.append(Led(17, "ANTIICE_ENG1_ON_LED", MF_SR2, 15, "AirbusFBW/ENG1AILights", DREF_TYPE.DATA, "&1"))
    ledlist.append(Led(18, "ANTIICE_ENG1_FAULT_LED", MF_SR2, 14, "AirbusFBW/ENG1AILights", DREF_TYPE.DATA, "&2"))
    ledlist.append(Led(19, "ANTIICE_ENG2_ON_LED", MF_SR2, 17, "AirbusFBW/ENG1AILights", DREF_TYPE.DATA, "&1"))
    ledlist.append(Led(20, "ANTIICE_ENG2_FAULT_LED", MF_SR2, 8, "AirbusFBW/ENG1AILights", DREF_TYPE.DATA, "&2"))
    ledlist.append(Led(21, "ELEC_BAT1_OFF_LED", MF_SR1, 28, "AirbusFBW/BatOHPArray", DREF_TYPE.ARRAY_0, "==1"))
    ledlist.append(Led(22, "ELEC_BAT1_FAULT_LED", MF_SR1, 29, "AirbusFBW/BatOHPArray", DREF_TYPE.ARRAY_0, "==3"))
    ledlist.append(Led(23, "ELEC_BAT2_OFF_LED", MF_SR1, 30, "AirbusFBW/BatOHPArray", DREF_TYPE.ARRAY_1, "==1"))
    ledlist.append(Led(24, "ELEC_BAT2_FAULT_LED", MF_SR1, 31, "AirbusFBW/BatOHPArray", DREF_TYPE.ARRAY_1, "==3"))
    ledlist.append(Led(25, "ELEC_EXT_PWR_AVAIL_LED", MF_SR1, 24, "AirbusFBW/ExtPowOHPArray", DREF_TYPE.ARRAY_0, "==2"))
    ledlist.append(Led(26, "ELEC_EXT_PWR_ON_LED",  MF_SR2, 1,"AirbusFBW/ExtPowOHPArray", DREF_TYPE.ARRAY_0, "==1"))
    ledlist.append(Led(27, "FUEL_L_PUMP1_OFF_LED",  MF_SR1, 15,"AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_0, "==1"))
    ledlist.append(Led(28, "FUEL_L_PUMP1_FAULT_LED", MF_SR1, 14, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_0, "==3"))
    ledlist.append(Led(29, "FUEL_L_PUMP2_OFF_LED",  MF_SR1, 17, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_1, "==1"))
    ledlist.append(Led(30, "FUEL_L_PUMP2_FAULT_LED", MF_SR1, 8, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_1, "==3"))
    ledlist.append(Led(31, "FUEL_PUMP1_OFF_LED", MF_SR1, 19, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_2, "==1"))
    ledlist.append(Led(32, "FUEL_PUMP1_FAULT_LED", MF_SR1, 18, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_2, "==3"))
    ledlist.append(Led(31, "FUEL_PUMP2_OFF_LED", MF_SR1, 23, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_3, "==1"))
    ledlist.append(Led(32, "FUEL_PUMP2_FAULT_LED", MF_SR1, 22, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_3, "==3"))
    ledlist.append(Led(33, "FUEL_R_PUMP1_OFF_LED",  MF_SR1, 25, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_4, "==1"))
    ledlist.append(Led(34, "FUEL_R_PUMP1_FAULT_LED", MF_SR1, 16, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_4, "==3"))
    ledlist.append(Led(35, "FUEL_R_PUMP2_OFF_LED", MF_SR1, 27, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_5, "==1"))
    ledlist.append(Led(36, "FUEL_R_PUMP2_FAULT_LED", MF_SR1, 26, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_5, "==3"))
    ledlist.append(Led(37, "FUEL_MODE_SEL_OFF_LED", MF_SR1, 21, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_6, "==1"))
    ledlist.append(Led(38, "FUEL_MODE_SEL_FAULT_LED", MF_SR1, 20, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_6, "==3"))
    ledlist.append(Led(39, "FIRE_ENG1_ON_LED", MF_SR1, 2, None, DREF_TYPE.ARRAY_6))
    ledlist.append(Led(40, "FIRE_ENG2_ON_LED", MF_SR1, 4, None, DREF_TYPE.ARRAY_6))
    ledlist.append(Led(41, "FIRE_APU_ON_LED", MF_SR1, 3, "AirbusFBW/APUOnFire"))
    ledlist.append(Led(42, "ADIRS_IR1_ALTN_LED", MF_SR1, 9, "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_6))
    ledlist.append(Led(43, "ADIRS_IR1_FAULT_LED", MF_SR1, 0, "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_7))
    ledlist.append(Led(44, "ADIRS_IR2_ALTN_LED", MF_SR1, 13, "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_8))
    ledlist.append(Led(45, "ADIRS_IR2_FAULT_LED", MF_SR1, 12, "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_9))
    ledlist.append(Led(46, "ADIRS_IR3_ALTN_LED", MF_SR1, 11, "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_10))
    ledlist.append(Led(47, "ADIRS_IR3_FAULT_LED", MF_SR1, 10, "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_11))
    ledlist.append(Led(48, "APU_GEN_FAULT_LED",  MF_SR2, 2, None, DREF_TYPE.ARRAY_0, "==0"))
    ledlist.append(Led(49, "EMERGENCY EXIT LIGHT",  MF_SR2, 22, None, DREF_TYPE.ARRAY_0, "==0"))


def create_button_list_a107():
    create_led_list_a107()
    buttonlist.append(Button(0, "APU_MASTER", MF_MP1, 4, "AirbusFBW/APUMaster", DREF_TYPE.DATA, BUTTON.TOGGLE))
    buttonlist.append(Button(1, "APU_START", MF_MP1, 3, "AirbusFBW/APUStarter", DREF_TYPE.DATA, BUTTON.TOGGLE))
    buttonlist.append(Button(2, "APU_BLEED", MF_MP1, 6, "AirbusFBW/APUBleedSwitch", DREF_TYPE.DATA, BUTTON.TOGGLE))
    buttonlist.append(Button(3, "Beacon Light", MF_MP3, 2, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_0, BUTTON.TOGGLE))
    buttonlist.append(Button(4, "Wing Light", MF_MP3, 3, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_1, BUTTON.TOGGLE))
    buttonlist.append(Button(5, "Nav Light ON", MF_MP1, 4, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_2, BUTTON.TOGGLE))
    buttonlist.append(Button(6, "Nav Light AUTO", MF_MP1, 5, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_2, BUTTON.TOGGLE))
    buttonlist.append(Button(7, "Land Left Light ON", MF_MP3, 7, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_3, BUTTON.TOGGLE))
    buttonlist.append(Button(8, "Land Left Light OFF", MF_MP3, 8, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_3, BUTTON.TOGGLE))
    buttonlist.append(Button(9, "Land Right Light ON", MF_MP3, 9, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_4, BUTTON.TOGGLE))
    buttonlist.append(Button(10, "Land Right Light OFF", MF_MP3, 10, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_4, BUTTON.TOGGLE))
    buttonlist.append(Button(11, "Nose Light OFF", MF_MP3, 12, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_5, BUTTON.TOGGLE))
    buttonlist.append(Button(12, "Nose Light TO", MF_MP3, 11, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_5, BUTTON.TOGGLE))
    buttonlist.append(Button(13, "RWY Turn Light", MF_MP3, 6, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_6, BUTTON.TOGGLE))
    buttonlist.append(Button(14, "Strobe Light ON", MF_MP3, 0, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_7, BUTTON.TOGGLE))
    buttonlist.append(Button(15, "Strobe Light AUTO", MF_MP3, 1, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_7, BUTTON.TOGGLE))
    buttonlist.append(Button(16, "Seatbelt", MF_MP3, 13, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_11, BUTTON.TOGGLE))
    buttonlist.append(Button(17, "Ice Eng1", MF_MP1, 4, "toliss_airbus/antiicecommands/ENG1Toggle", DREF_TYPE.CMD, BUTTON.TOGGLE))
    buttonlist.append(Button(18, "Ice Eng2", MF_MP1, 0, "toliss_airbus/antiicecommands/ENG2Toggle", DREF_TYPE.CMD, BUTTON.TOGGLE))
    buttonlist.append(Button(19, "Ice Wing", MF_MP1, 2, "toliss_airbus/antiicecommands/WingToggle", DREF_TYPE.CMD, BUTTON.TOGGLE))
    buttonlist.append(Button(20, "Pack1", MF_MP1, 7, "toliss_airbus/aircondcommands/Pack1Toggle", DREF_TYPE.CMD, BUTTON.TOGGLE))
    buttonlist.append(Button(21, "Pack2", MF_MP1, 5, "toliss_airbus/aircondcommands/Pack2Toggle", DREF_TYPE.CMD, BUTTON.TOGGLE))
    buttonlist.append(Button(22, "Fire APU", MF_MP2, 0, None, DREF_TYPE.ARRAY_12, BUTTON.TOGGLE))
    buttonlist.append(Button(23, "Fire Eng1", MF_MP2, 1, "AirbusFBW/ENGFireSwitchArray", DREF_TYPE.ARRAY_0, BUTTON.TOGGLE))
    buttonlist.append(Button(24, "Fire Eng2", MF_MP3, 15, "AirbusFBW/ENGFireSwitchArray", DREF_TYPE.ARRAY_1, BUTTON.TOGGLE))
    buttonlist.append(Button(25, "Fire Test APU", MF_MP2, 4, "AirbusFBW/FireTestAPU", DREF_TYPE.CMD, BUTTON.HOLD))
    buttonlist.append(Button(26, "Fire Test Eng1", MF_MP2, 5, "AirbusFBW/FireTestENG1", DREF_TYPE.CMD, BUTTON.TOGGLE))
    buttonlist.append(Button(27, "Fire Test Eng2", MF_MP2, 3, "AirbusFBW/FireTestENG2", DREF_TYPE.CMD, BUTTON.TOGGLE))
    buttonlist.append(Button(28, "Adirs ON Bat", MF_MP1, 14, None, DREF_TYPE.ARRAY_12, BUTTON.TOGGLE))
    buttonlist.append(Button(29, "Smoking Light AUTO", MF_MP4, 3, "ckpt/oh/nosmoking/anim", DREF_TYPE.DATA, BUTTON.SEND_1))
    buttonlist.append(Button(30, "Smoking Light OFF", MF_MP3, 14, "ckpt/oh/nosmoking/anim", DREF_TYPE.DATA, BUTTON.SEND_0)) # todo send ON = 2
    buttonlist.append(Button(31, "Calls", MF_MP2, 2, "AirbusFBW/purser/fwd", DREF_TYPE.CMD, BUTTON.TOGGLE))
    buttonlist.append(Button(32, "Adirs 1-1", MF_MP4, 7, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_0, BUTTON.SEND_0))
    buttonlist.append(Button(33, "Adirs 1-2", MF_MP4, 11, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_0, BUTTON.SEND_1))
    buttonlist.append(Button(34, "Adirs 1-3", MF_MP4, 9, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_0, BUTTON.SEND_2))
    buttonlist.append(Button(35, "Adirs 2-1", MF_MP4, 6, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_1, BUTTON.SEND_0))
    buttonlist.append(Button(36, "Adirs 2-2", MF_MP4, 10, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_1, BUTTON.SEND_1))
    buttonlist.append(Button(37, "Adirs 2-3", MF_MP4, 8, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_1, BUTTON.SEND_2))
    buttonlist.append(Button(38, "Adirs 3-1", MF_MP4, 7, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_2, BUTTON.SEND_0))
    buttonlist.append(Button(39, "Adirs 3-2", MF_MP4, 11, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_2, BUTTON.SEND_1))
    buttonlist.append(Button(40, "Adirs 3-3", MF_MP4, 9, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_2, BUTTON.SEND_2))
    buttonlist.append(Button(41, "Gnd ctrl", MF_MP1, 10, "AirbusFBW/CvrGndCtrl", DREF_TYPE.DATA, BUTTON.TOGGLE))
    buttonlist.append(Button(42, "Crewsupply", MF_MP1, 9, "AirbusFBW/CrewOxySwitch", DREF_TYPE.ARRAY_12, BUTTON.TOGGLE)) # invers
    buttonlist.append(Button(43, "Pump1", MF_MP2, 10, "AirbusFBW/FuelOHPArray", DREF_TYPE.ARRAY_2, BUTTON.TOGGLE))
    buttonlist.append(Button(44, "Pump2", MF_MP2, 8, "AirbusFBW/FuelOHPArray", DREF_TYPE.ARRAY_3, BUTTON.TOGGLE))
    buttonlist.append(Button(45, "Left Pump1", MF_MP2, 12, "AirbusFBW/FuelOHPArray", DREF_TYPE.ARRAY_0, BUTTON.TOGGLE))
    buttonlist.append(Button(46, "Left Pump2", MF_MP2, 11, "AirbusFBW/FuelOHPArray", DREF_TYPE.ARRAY_1, BUTTON.TOGGLE))
    buttonlist.append(Button(47, "Pump Modesel", MF_MP2, 9, "AirbusFBW/FuelOHPArray", DREF_TYPE.ARRAY_6, BUTTON.TOGGLE))
    buttonlist.append(Button(48, "Right Pump1", MF_MP2, 7, "AirbusFBW/FuelOHPArray", DREF_TYPE.ARRAY_4, BUTTON.TOGGLE))
    buttonlist.append(Button(48, "Right Pump2", MF_MP2, 6, "AirbusFBW/FuelOHPArray", DREF_TYPE.ARRAY_5, BUTTON.TOGGLE))
    buttonlist.append(Button(50, "Bat1", MF_MP2, 15, "AirbusFBW/BatOHPArray", DREF_TYPE.ARRAY_0, BUTTON.TOGGLE))
    buttonlist.append(Button(51, "Bat2", MF_MP2, 14, "AirbusFBW/BatOHPArray", DREF_TYPE.ARRAY_11, BUTTON.TOGGLE)) # toto bats volt read AirbusFBW/BatVolts
    buttonlist.append(Button(52, "APU Gen", MF_MP1, 8, "AirbusFBW/APUGenOHPArray", DREF_TYPE.ARRAY_0, BUTTON.TOGGLE))
    buttonlist.append(Button(53, "IR1", MF_MP1, 13, None, DREF_TYPE.ARRAY_12, BUTTON.TOGGLE))
    buttonlist.append(Button(54, "IR2", MF_MP1, 12, None, DREF_TYPE.ARRAY_12, BUTTON.TOGGLE))
    buttonlist.append(Button(55, "IR3", MF_MP1, 11, None, DREF_TYPE.ARRAY_12, BUTTON.TOGGLE))
    buttonlist.append(Button(56, "ExtPwr", MF_MP2, 13, "toliss_airbus/eleccommands/ExtPowToggle", DREF_TYPE.CMD, BUTTON.TOGGLE)) # toto 1 .. on, 2 .. off
    buttonlist.append(Button(57, "TCAS TA", MF_MP4, 13, None, DREF_TYPE.ARRAY_12, BUTTON.TOGGLE))
    buttonlist.append(Button(58, "TCAS TA/TR", MF_MP4, 12, None, DREF_TYPE.ARRAY_12, BUTTON.TOGGLE))
    buttonlist.append(Button(59, "Exit ON", MF_MP1, 5, "toliss_airbus/lightcommands/EmerExitLightUp", DREF_TYPE.CMD, BUTTON.TOGGLE))
    buttonlist.append(Button(60, "Exit OFF", MF_MP1, 4, "toliss_airbus/lightcommands/EmerExitLightDown", DREF_TYPE.CMD, BUTTON.TOGGLE))
    buttonlist.append(Button(61, "Wiper OFF", MF_MP4, 14, "AirbusFBW/LeftWiperSwitch", DREF_TYPE.DATA, BUTTON.SEND_0))
    buttonlist.append(Button(62, "Wiper Fast", MF_MP4, 15, "AirbusFBW/LeftWiperSwitch", DREF_TYPE.DATA, BUTTON.SEND_2))
    #buttonlist.append(Button(64, "Wiper Slow", MF_MP4, 14, "AirbusFBW/LeftWiperSwitch", DREF_TYPE.DATA, BUTTON.SEND1)) # missing in config
    buttonlist.append(Button(63, "Flap 3", MF_MP1, 15, "toliss_airbus/gpwscommands/Flap3Toggle", DREF_TYPE.CMD, BUTTON.TOGGLE))
 

def startupscreen(device, device_config, version, new_version):
    print("TODO set startupscreen")


def xplane_get_dataref_ids(xp):
    print(f"[A107] getting led dataref ids ... ", end="")
    for data in [ledlist]:
        for l in data:
            if l.dataref == None:
                continue
            if l.dreftype == DREF_TYPE.CMD:
                continue
            xp.datacache[l.dataref] = 0
            id = xp.dataref_id_fetch(l.dataref)
            #print(f'name: {l.label}, id: {id}')
            if id in xp.led_dataref_ids:
                continue
            if l.dreftype.value >= DREF_TYPE.ARRAY_0.value:
                larray = []
                for l2 in ledlist:
                    if l2.dataref == l.dataref:
                        larray.append(l2)
                xp.led_dataref_ids[id] = larray.copy()
            else:
                xp.led_dataref_ids[id] = l
    print("done")
    print(f"[A107] getting button cmd & dataref ids ... ", end="")
    for b in buttonlist:
        if b.dataref == None:
            continue
        if b.dreftype == DREF_TYPE.CMD:
            id = xp.command_id_fetch(b.dataref)
        elif b.dreftype == DREF_TYPE.DATA:
            id = xp.dataref_id_fetch(b.dataref)
            xp.datacache[b.dataref] = 0
        #print(f'name: {l.label}, id: {id}')
        if id in xp.buttonref_ids:
            continue
        xp.buttonref_ids[b] = id
    print("done")


def xplane_ws_listener(data, led_dataref_ids): # receive ids and find led
    #print(f"[A107] recevice: {data}")
    if data.get("type") != "dataref_update_values":
        print(f"[A107] not defined {data}")
        return

    for ref_id_str, value in data["data"].items():
        ref_id = int(ref_id_str)
        print(f"[A107] searching for {ref_id}...", end='')
        if ref_id in led_dataref_ids:
            ledobj = led_dataref_ids[ref_id]

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
                                #todo set datachache
                            print(f"[A107]                       array value[{idx}] of {l2.label} = {value_new}")
                            rawsfire_a107_set_led(l2, value_new)
                    idx += 1
            else:
                if ledobj.eval != None:
                    s = 'value' + ledobj.eval
                    value = eval(s)
                print(f" found: {ledobj.label} = {value}")
                xp.datacache[ledobj.dataref] = value
                rawsfire_a107_set_led(ledobj, value)
        else:
            print(f" not found")


def send_change_to_xp(name, channel, value):
    global xp
    for b in buttonlist:
        if name == b.mf_button and channel == b.mf_pin:
            if b.type == BUTTON.NONE:
                break

            if b.dreftype == DREF_TYPE.DATA:
                # TODO arrays in datacache
                if b.type == BUTTON.TOGGLE and value == 0:
                    val = int(not xp.datacache[b.dataref])
                    xp.dataref_set_value(xp.buttonref_ids[b], val)
                    break

                if b.type == BUTTON.HOLD or b.type == BUTTON.SWITCH:
                    xp.datacache[b.dataref] = value
                    xp.dataref_set_value(xp.buttonref_ids[b], value)
                    break

            if b.dreftype == DREF_TYPE.CMD:
                print(f"dref cmd {value} {b.type}")
                if b.type == BUTTON.TOGGLE and value:
                    print("send cmd")
                    xp.command_activate_duration(xp.buttonref_ids[b], 0.5)
                if b.type == BUTTON.HOLD and value:
                    print("send cmd")
                    xp.command_activate_duration(xp.buttonref_ids[b], 4)
            break


def mf_value_changed(cmd, name, arg):
    if cmd == mf.MF.CMD.BUTTON_CHANGE:
        print(f"Value changed (Button): {name}, {int(arg[0])}") # value
        #send_change_to_xp(MF_MP2, 4, int(arg[0])) # fake APU Test FIRE
        send_change_to_xp(MF_MP1, 4, int(arg[0])) # fale APU Master
    elif cmd == mf.MF.CMD.DIGINMUX_CHANGE:
        print(f"Value changed (DigInMux): {name}, {arg[0]}, {int(arg[1])}") # channel, value
        send_change_to_xp(name, arg[0], int(arg[1]))
    elif cmd == mf.MF.CMD.ANALOG_CHANGE:
        print(f"Value changed (Analog): {name}, {int(arg[0])}") # value


class device:
    def __init__(self, UDP_IP, UDP_PORT):
        self.usb_mgr = None
        self.cyclic = Event()
        self.xp = XP_Websocket(XPLANE_REST_URL, XPLANE_WS_URL)


    def connected(self):
        global xplane_connected
        global xp
        print(f"[A107] X-Plane connected")
        xplane_get_dataref_ids(self.xp)
        print(f"[A107] subsrcibe datarefs... ", end="")
        t = Thread(target=self.xp.datarefs_subscribe, args=(self.xp.led_dataref_ids, xplane_ws_listener))
        t.start()
        print(f"done")
        xplane_connected = True
        xp = self.xp


    def disconnected(self):
        global xplane_connected
        xplane_connected = False
        print(f"[A107] X-Plane disconnected")
        startupscreen(mf_dev, device_config, self.version, self.new_version)


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
        global xplane_connected
        global mf_dev

        self.version = version
        self.new_version = new_version

        print("find mobiflight devices:")
        mf_dev = mf.MF()
        ports = mf_dev.serial_ports()
        mf_dev = None
        for port_mf in ports:
            print(f"testing {port_mf}")
            mf_dev = mf.MF(port_mf.device)
            if mf_dev.start(MOBIFLIGHT_SERIAL, mf_value_changed):
                break
            else:
                mf_dev.close()
                mf_dev = None

        if not mf_dev:
            print(f" [A107] No compatible rawsfire device found, quit")
            return

        print("Mobiflight device startet successful")

        create_button_list_a107()
    
        startupscreen(mf_dev, device_config, version, new_version)

        cyclic_thread = Thread(target=self.cyclic_worker)
        cyclic_thread.start()
