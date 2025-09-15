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

MOBIFLIGHT_SERIAL = "SN-XXX-XXX"

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
        if index != None:
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
    TOGGLE_INVERSE = 2
    SEND_0 = 3
    SEND_1 = 4
    SEND_2 = 5
    SEND_3 = 6
    SEND_4 = 7
    SEND_5 = 8
    HOLD   = 9
    SEND_1_2 = 10 # 0 -> 1, 1 -> 2
    SEND_2_1 = 11 # 0 -> 2, 1 -> 1
    SWITCH_INVERSE = 15
    SWITCH_COMBINED = 16
    NONE = 99 # for testing


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
    ARRAY_13 = 23
    ARRAY_14 = 24
    DATA_MULTIPLE = 25 # more leds use the same dataref


class Button:
    def __init__(self, label, mf_button, mf_pin, dataref = None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.NONE):
        self.label = label
        self.mf_button = mf_button # Mobiflight
        self.mf_pin = mf_pin # pin number (on shift or multiplexer)
        self.dataref = dataref
        self.dreftype = dreftype
        self.type = button_type


    def __str__(self):
            return(f"{self.label} -> {self.dataref} {self.type}")


class Combined:
    def __init__(self, label, button_names, truth_table):
        self.label = label
        self.button_names = button_names
        self.truth_table = truth_table
        self.dataref = None
        self.buttons = [None, None]


    def __str__(self):
            return(f"{self.label} -> {self.dataref} {self.truth_table}")


class Led:
    def __init__(self, label, mf_name, mf_pin, dataref, dreftype = DREF_TYPE.NONE, eval = None):
        self.label = label
        self.mf_name = mf_name # Mobiflight
        self.mf_pin = mf_pin # pin number (on shift, multiplexer or 7-segments)
        self.dataref = dataref
        self.dreftype = dreftype
        self.eval = eval

xplane_connected = False
buttonlist = []
combinedlist = [] # combined buttons
ledlist = []

device_config = DEVICEMASK.NONE


def rawsfire_a107_set_leds(leds, brightness):
    if isinstance(leds, list):
        for i in range(len(leds)):
            rowsfire_a107_set_led(leds[i], brightness)
    else:
        rowsfire_a107_set_led(leds, brightness)


def rowsfire_a107_set_led(led, brightness):
    global mf_dev

    if brightness == 1:
        brightness = 255
    if brightness > 255:
        brightness = 255

    mf_dev.set_pin(led.mf_name, led.mf_pin, brightness)


def rowsfire_a107_set_lcd(led, value):
    mf_dev.set_modul(led.mf_pin, value)


def rawsfire_107_set_lcd(device, speed, heading, alt, vs):
    global usb_retry
    return


datacache = {}
xp = None


MF_SR1 = "ShiftRegister 1" # Output
MF_SR2 = "ShiftRegister 2" # Output

MF_MP1 = "Multiplexer 1" # Input
MF_MP2 = "Multiplexer 2" # Input
MF_MP3 = "Multiplexer 3" # Input
MF_MP4 = "Multiplexer 4" # Input

MF_SEGMENT1 = "SEGMENT 1" # Bat1&2 display

def create_led_list_a107():  # TODO check sim/cockpit/electrical/avionics_on == 1
    ledlist.append(Led("APU_MASTER_ON_LED", MF_SR2, 11, "AirbusFBW/OHPLightsATA49_Raw", DREF_TYPE.ARRAY_0))
    ledlist.append(Led("APU_MASTER_FAULT_LED", MF_SR2, 10, "AirbusFBW/OHPLightsATA49_Raw", DREF_TYPE.ARRAY_1))
    ledlist.append(Led("APU_STARTER_ON_LED", MF_SR2, 19, "AirbusFBW/APUStarter"))
    ledlist.append(Led("APU_STARTER_AVAIL_LED", MF_SR2, 18, "AirbusFBW/APUAvail"))
    ledlist.append(Led("APU_GEN_OFF_LED",  MF_SR2, 3, "AirbusFBW/APUGenOHPArray", DREF_TYPE.ARRAY_0, "==0"))
    ledlist.append(Led("AIR_APU_BLEED_ON_LED", MF_SR2, 7, "AirbusFBW/OHPLightsATA21_Raw", DREF_TYPE.ARRAY_4))
    #ledlist.append(Led "AIR_APU_BLEED_FAULT_LED", MF_SR2, 6, None))
    ledlist.append(Led("AIR_PACK1_BLEED_OFF_LED", MF_SR2, 5, "AirbusFBW/OHPLightsATA21_Raw", DREF_TYPE.ARRAY_6))
    ledlist.append(Led("AIR_PACK1_BLEED_FAULT_LED", MF_SR2, 4, "AirbusFBW/OHPLightsATA21_Raw", DREF_TYPE.ARRAY_7))
    ledlist.append(Led("AIR_PACK2_BLEED_OFF_LED", MF_SR2, 9, "AirbusFBW/OHPLightsATA21_Raw", DREF_TYPE.ARRAY_8))
    ledlist.append(Led("AIR_PACK2_BLEED_FAULT_LED", MF_SR2, 0, "AirbusFBW/OHPLightsATA21_Raw", DREF_TYPE.ARRAY_9))
    ledlist.append(Led("ADIRS_ON_BAT_LED", MF_SR1, 1, "AirbusFBW/ADIRUOnBat"))
    ledlist.append(Led("GPWS_FLAP3_ON_LED", MF_SR1, 5, "AirbusFBW/GPWSSwitchArray", DREF_TYPE.ARRAY_3))
    ledlist.append(Led("RCDR_GND_CTL_ON_LED", MF_SR1, 6, "AirbusFBW/CvrGndCtrl"))
    ledlist.append(Led("OXYGEN_CREW_SUPPLY_OFF_LED", MF_SR1, 7, "AirbusFBW/CrewOxySwitch", DREF_TYPE.DATA, "==0"))
    ledlist.append(Led("ANTIICE_WING_ON_LED", MF_SR2, 13, "AirbusFBW/WAILights", DREF_TYPE.DATA_MULTIPLE, "&1"))
    ledlist.append(Led("ANTIICE_WING_FAULT_LED", MF_SR2, 12, "AirbusFBW/WAILights", DREF_TYPE.DATA_MULTIPLE, "&2"))
    ledlist.append(Led("ANTIICE_ENG1_ON_LED", MF_SR2, 15, "AirbusFBW/ENG1AILights", DREF_TYPE.DATA_MULTIPLE, "&1"))
    ledlist.append(Led("ANTIICE_ENG1_FAULT_LED", MF_SR2, 14, "AirbusFBW/ENG1AILights", DREF_TYPE.DATA_MULTIPLE, "&2"))
    ledlist.append(Led("ANTIICE_ENG2_ON_LED", MF_SR2, 17, "AirbusFBW/ENG2AILights", DREF_TYPE.DATA_MULTIPLE, "&1"))
    ledlist.append(Led("ANTIICE_ENG2_FAULT_LED", MF_SR2, 8, "AirbusFBW/ENG2AILights", DREF_TYPE.DATA_MULTIPLE, "&2"))
    ledlist.append(Led("ELEC_BAT1_OFF_LED", MF_SR1, 28, "AirbusFBW/BatOHPArray", DREF_TYPE.ARRAY_0, "not($&1)"))
    ledlist.append(Led("ELEC_BAT1_FAULT_LED", MF_SR1, 29, "AirbusFBW/BatOHPArray", DREF_TYPE.ARRAY_0, "==3"))
    ledlist.append(Led("ELEC_BAT2_OFF_LED", MF_SR1, 30, "AirbusFBW/BatOHPArray", DREF_TYPE.ARRAY_1, "not($&1)"))
    ledlist.append(Led("ELEC_BAT2_FAULT_LED", MF_SR1, 31, "AirbusFBW/BatOHPArray", DREF_TYPE.ARRAY_1, "==3"))
    ledlist.append(Led("ELEC_EXT_PWR_AVAIL_LED", MF_SR1, 24, "AirbusFBW/ExtPowOHPArray", DREF_TYPE.ARRAY_0, "==2"))
    ledlist.append(Led("ELEC_EXT_PWR_ON_LED",  MF_SR2, 1,"AirbusFBW/ExtPowOHPArray", DREF_TYPE.ARRAY_0, "==1"))
    ledlist.append(Led("FUEL_L_PUMP1_OFF_LED",  MF_SR1, 15,"AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_0, "==0"))
    ledlist.append(Led("FUEL_L_PUMP1_FAULT_LED", MF_SR1, 14, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_0, "==3"))
    ledlist.append(Led("FUEL_L_PUMP2_OFF_LED",  MF_SR1, 17, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_1, "==0"))
    ledlist.append(Led("FUEL_L_PUMP2_FAULT_LED", MF_SR1, 8, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_1, "==3"))
    ledlist.append(Led("FUEL_PUMP1_OFF_LED", MF_SR1, 19, "AirbusFBW/FuelAutoPumpOHPArray", DREF_TYPE.ARRAY_2, "==0"))
    ledlist.append(Led("FUEL_PUMP1_FAULT_LED", MF_SR1, 18, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_2, "==3"))
    ledlist.append(Led("FUEL_PUMP2_OFF_LED", MF_SR1, 23, "AirbusFBW/FuelAutoPumpOHPArray", DREF_TYPE.ARRAY_3, "==0"))
    ledlist.append(Led("FUEL_PUMP2_FAULT_LED", MF_SR1, 22, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_3, "==3"))
    ledlist.append(Led("FUEL_R_PUMP1_OFF_LED",  MF_SR1, 25, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_4, "==0"))
    ledlist.append(Led("FUEL_R_PUMP1_FAULT_LED", MF_SR1, 16, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_4, "==3"))
    ledlist.append(Led("FUEL_R_PUMP2_OFF_LED", MF_SR1, 27, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_5, "==0"))
    ledlist.append(Led("FUEL_R_PUMP2_FAULT_LED", MF_SR1, 26, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_5, "==3"))
    ledlist.append(Led("FUEL_MODE_SEL_MAN_LED", MF_SR1, 21, "AirbusFBW/FuelOHPArray", DREF_TYPE.ARRAY_6, "==0"))
    ledlist.append(Led("FUEL_MODE_SEL_FAULT_LED", MF_SR1, 20, "AirbusFBW/FuelAutoPumpSDArray", DREF_TYPE.ARRAY_6, "==3"))
    ledlist.append(Led("FIRE_ENG1_ON_LED", MF_SR1, 2, "AirbusFBW/OHPLightsATA70_Raw", DREF_TYPE.ARRAY_11))
    ledlist.append(Led("FIRE_ENG2_ON_LED", MF_SR1, 4, "AirbusFBW/OHPLightsATA70_Raw", DREF_TYPE.ARRAY_13))
    ledlist.append(Led("FIRE_APU_ON_LED", MF_SR1, 3, "AirbusFBW/FireAgentSwitchAnim", DREF_TYPE.ARRAY_14))
    ledlist.append(Led("ADIRS_IR1_ALTN_LED", MF_SR1, 9, "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_6))
    ledlist.append(Led("ADIRS_IR1_FAULT_LED", MF_SR1, 0, "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_7))
    ledlist.append(Led("ADIRS_IR2_ALTN_LED", MF_SR1, 13, "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_8))
    ledlist.append(Led("ADIRS_IR2_FAULT_LED", MF_SR1, 12, "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_9))
    ledlist.append(Led("ADIRS_IR3_ALTN_LED", MF_SR1, 11, "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_10))
    ledlist.append(Led("ADIRS_IR3_FAULT_LED", MF_SR1, 10, "AirbusFBW/OHPLightsATA34_Raw", DREF_TYPE.ARRAY_11))
    ledlist.append(Led("APU_GEN_FAULT_LED",  MF_SR2, 2, "AirbusFBW/OHPLightsATA24_Raw", DREF_TYPE.ARRAY_5))
    ledlist.append(Led("EMERGENCY EXIT LIGHT OFF",  MF_SR2, 22, "AirbusFBW/OHPLightsATA31_Raw", DREF_TYPE.ARRAY_12, "==0"))
    ledlist.append(Led("BAT1_VOLTAGE",  MF_SEGMENT1, [16, 56], "AirbusFBW/BatVolts", DREF_TYPE.ARRAY_0, "int(($+0.05)*10)")) # round fist decimal
    ledlist.append(Led("BAT2_VOLTAGE",  MF_SEGMENT1, [2, 7], "AirbusFBW/BatVolts", DREF_TYPE.ARRAY_1, "int(($+0.05)*10)"))


def create_button_list_a107():
    create_led_list_a107()
    buttonlist.append(Button("APU_MASTER", MF_MP1, 4, "AirbusFBW/APUMaster", DREF_TYPE.DATA, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("APU_START", MF_MP1, 3, "AirbusFBW/APUStarter", DREF_TYPE.DATA, BUTTON.TOGGLE))
    buttonlist.append(Button("APU_BLEED", MF_MP1, 6, "AirbusFBW/APUBleedSwitch", DREF_TYPE.DATA, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("Beacon Light", MF_MP3, 2, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_0, BUTTON.TOGGLE_INVERSE))
    buttonlist.append(Button("Wing Light", MF_MP3, 3, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_1, BUTTON.TOGGLE_INVERSE))
    buttonlist.append(Button("Nav Light 1", MF_MP3, 5, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_2, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Nav Light 2", MF_MP3, 4, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_2, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Land Left 1", MF_MP3, 8, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_4, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Land Left 2", MF_MP3, 7, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_4, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Land Right 1", MF_MP3, 10, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_5, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Land Right 2", MF_MP3, 9, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_5, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Nose Light 1", MF_MP3, 11, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_3, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Nose Light 2", MF_MP3, 12, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_3, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("RWY Turn Light", MF_MP3, 6, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_6, BUTTON.TOGGLE_INVERSE))
    buttonlist.append(Button("Strobe 1", MF_MP3, 1, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_7, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Strobe 2", MF_MP3, 0, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_7, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Seatbelt", MF_MP3, 13, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_11, BUTTON.TOGGLE_INVERSE))
    buttonlist.append(Button("Ice Eng1 On", MF_MP1, 1, "toliss_airbus/antiicecommands/ENG1On", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("Ice Eng1 Off", MF_MP1, 1, "toliss_airbus/antiicecommands/ENG1Off", DREF_TYPE.CMD, BUTTON.SWITCH))
    buttonlist.append(Button("Ice Eng2 On", MF_MP1, 0, "toliss_airbus/antiicecommands/ENG2On", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("Ice Eng2 Off", MF_MP1, 0, "toliss_airbus/antiicecommands/ENG2Off", DREF_TYPE.CMD, BUTTON.SWITCH))
    buttonlist.append(Button("Ice Wing On", MF_MP1, 2, "toliss_airbus/antiicecommands/WingOn", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("Ice Wing Off", MF_MP1, 2, "toliss_airbus/antiicecommands/WingOff", DREF_TYPE.CMD, BUTTON.SWITCH))
    buttonlist.append(Button("Pack1 On", MF_MP1, 7, "toliss_airbus/aircondcommands/Pack1On", DREF_TYPE.CMD, BUTTON.SWITCH))
    buttonlist.append(Button("Pack1 Off", MF_MP1, 7, "toliss_airbus/aircondcommands/Pack1Off", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("Pack2 On", MF_MP1, 5, "toliss_airbus/aircondcommands/Pack2On", DREF_TYPE.CMD, BUTTON.SWITCH))
    buttonlist.append(Button("Pack2 Off", MF_MP1, 5, "toliss_airbus/aircondcommands/Pack2Off", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("Fire APU", MF_MP2, 0, "sim/electrical/APU_fire_shutoff", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE)) # does not work
    buttonlist.append(Button("Fire Eng1", MF_MP2, 1, "AirbusFBW/ENGFireSwitchArray", DREF_TYPE.ARRAY_0, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("Fire Eng2", MF_MP3, 15, "AirbusFBW/ENGFireSwitchArray", DREF_TYPE.ARRAY_1, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("Fire Test APU", MF_MP2, 4, "AirbusFBW/FireTestAPU", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("Fire Test Eng1", MF_MP2, 5, "AirbusFBW/FireTestENG1", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("Fire Test Eng2", MF_MP2, 3, "AirbusFBW/FireTestENG2", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("Adirs ON Bat", MF_MP1, 14, "sim/annunciator/clear_master_warning", DREF_TYPE.CMD, BUTTON.TOGGLE))
    buttonlist.append(Button("Smoking Light 1", MF_MP4, 3, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_12, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Smoking Light 2", MF_MP3, 14, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_12, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Calls", MF_MP2, 2, "AirbusFBW/purser/fwd", DREF_TYPE.CMD, BUTTON.HOLD))
    buttonlist.append(Button("Adirs 1-1", MF_MP4, 6, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_0, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Adirs 1-2", MF_MP4, 7, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_0, BUTTON.SWITCH_COMBINED))
    #buttonlist.append(Button("Adirs 1-3", MF_MP4, 9, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_0, BUTTON.SEND_2)) # not needed
    buttonlist.append(Button("Adirs 2-1", MF_MP4, 10, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_1, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Adirs 2-2", MF_MP4, 11, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_1, BUTTON.SWITCH_COMBINED))
    #buttonlist.append(Button("Adirs 2-3", MF_MP4, 8, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_1, BUTTON.SEND_2)) # not needed
    buttonlist.append(Button("Adirs 3-1", MF_MP4, 8, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_2, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Adirs 3-2", MF_MP4, 9, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_2, BUTTON.SWITCH_COMBINED))
    #buttonlist.append(Button("Adirs 3-3", MF_MP4, 9, "AirbusFBW/ADIRUSwitchArray", DREF_TYPE.ARRAY_2, BUTTON.SEND_2)) # not needed
    buttonlist.append(Button("Gnd ctrl", MF_MP1, 10, "AirbusFBW/CvrGndCtrl", DREF_TYPE.DATA, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("Crewsupply", MF_MP1, 9, "AirbusFBW/CrewOxySwitch", DREF_TYPE.DATA, BUTTON.SWITCH))
    buttonlist.append(Button("Pump1", MF_MP2, 10, "AirbusFBW/FuelOHPArray", DREF_TYPE.ARRAY_2, BUTTON.SWITCH))
    buttonlist.append(Button("Pump2", MF_MP2, 8, "AirbusFBW/FuelOHPArray", DREF_TYPE.ARRAY_3, BUTTON.SWITCH))
    buttonlist.append(Button("Left Pump1", MF_MP2, 12, "AirbusFBW/FuelOHPArray", DREF_TYPE.ARRAY_0, BUTTON.SWITCH))
    buttonlist.append(Button("Left Pump2", MF_MP2, 11, "AirbusFBW/FuelOHPArray", DREF_TYPE.ARRAY_1, BUTTON.SWITCH))
    buttonlist.append(Button("Pump Modesel", MF_MP2, 9, "AirbusFBW/FuelOHPArray", DREF_TYPE.ARRAY_6, BUTTON.SWITCH))
    buttonlist.append(Button("Right Pump1", MF_MP2, 7, "AirbusFBW/FuelOHPArray", DREF_TYPE.ARRAY_4, BUTTON.SWITCH))
    buttonlist.append(Button("Right Pump2", MF_MP2, 6, "AirbusFBW/FuelOHPArray", DREF_TYPE.ARRAY_5, BUTTON.SWITCH))
    buttonlist.append(Button("Bat1 On", MF_MP2, 15, "toliss_airbus/eleccommands/Bat1On", DREF_TYPE.CMD, BUTTON.SWITCH))
    buttonlist.append(Button("Bat1 Off", MF_MP2, 15, "toliss_airbus/eleccommands/Bat1Off", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("Bat2 On", MF_MP2, 14, "toliss_airbus/eleccommands/Bat2On", DREF_TYPE.CMD, BUTTON.SWITCH))
    buttonlist.append(Button("Bat2 Off", MF_MP2, 14, "toliss_airbus/eleccommands/Bat2Off", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("APU Gen On", MF_MP1, 8, "sim/electrical/APU_generator_on", DREF_TYPE.CMD, BUTTON.SWITCH)) # does not work
    buttonlist.append(Button("APU Gen Off", MF_MP1, 8, "sim/electrical/APU_generator_off", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE)) # does not work
    buttonlist.append(Button("IR1", MF_MP1, 13, "sim/flight_controls/brakes_1_auto", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("IR2", MF_MP1, 12, "sim/flight_controls/brakes_2_auto", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("IR3", MF_MP1, 11, "sim/flight_controls/brakes_max_auto", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("ExtPwrON", MF_MP2, 13, "toliss_airbus/eleccommands/ExtPowOn", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE))
    buttonlist.append(Button("ExtPwrOFF", MF_MP2, 13, "toliss_airbus/eleccommands/ExtPowOff", DREF_TYPE.CMD, BUTTON.SWITCH))
    buttonlist.append(Button("TCAS 1", MF_MP4, 13, "AirbusFBW/XPDRPower", DREF_TYPE.DATA, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("TCAS 2", MF_MP4, 12, "AirbusFBW/XPDRPower", DREF_TYPE.DATA, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Exit 1", MF_MP4, 5, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_10, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Exit 2", MF_MP4, 4, "AirbusFBW/OHPLightSwitches", DREF_TYPE.ARRAY_10, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Wiper 1", MF_MP4, 15, "AirbusFBW/LeftWiperSwitch", DREF_TYPE.DATA, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Wiper 2", MF_MP4, 14, "AirbusFBW/LeftWiperSwitch", DREF_TYPE.DATA, BUTTON.SWITCH_COMBINED))
    buttonlist.append(Button("Flap 3 Off", MF_MP1, 15, "toliss_airbus/gpwscommands/Flap3Off", DREF_TYPE.CMD, BUTTON.SWITCH))
    buttonlist.append(Button("Flap 3 On", MF_MP1, 15, "toliss_airbus/gpwscommands/Flap3On", DREF_TYPE.CMD, BUTTON.SWITCH_INVERSE))
 

def create_combined_button_list_a107():
    combinedlist.append(Combined("Wiper_combined", ["Wiper 1", "Wiper 2"], [None, 2, 0, 1]))
    combinedlist.append(Combined("Exit_combined", ["Exit 1", "Exit 2"], [None, 0, 2, 1]))
    combinedlist.append(Combined("TCAS_combined", ["TCAS 1", "TCAS 2"], [0, 0, 4, 3]))
    combinedlist.append(Combined("Adirs 1_combined", ["Adirs 1-1", "Adirs 1-2"], [None, 2, 0, 1]))
    combinedlist.append(Combined("Adirs 2_combined", ["Adirs 2-1", "Adirs 2-2"], [None, 2, 0, 1]))
    combinedlist.append(Combined("Adirs 3_combined", ["Adirs 3-1", "Adirs 3-2"], [None, 2, 0, 1]))
    combinedlist.append(Combined("Land Left_combined", ["Land Left 1", "Land Left 2"], [None, 0, 2, 1]))
    combinedlist.append(Combined("Land Right_combined", ["Land Right 1", "Land Right 2"], [None, 0, 2, 1]))
    combinedlist.append(Combined("Nav Light_combined", ["Nav Light 1", "Nav Light 2"], [None, 0, 2, 1]))
    combinedlist.append(Combined("Strobe_combined", ["Strobe 1", "Strobe 2"], [None, 0, 2, 1]))
    combinedlist.append(Combined("Nose Light_combined", ["Nose Light 1", "Nose Light 2"], [None, 2, 0, 1]))
    combinedlist.append(Combined("Smoking Light_combined", ["Smoking Light 1", "Smoking Light 2"], [None, 0, 2, 1]))
    for c in combinedlist:
        for b in buttonlist:
            if c.button_names[0] == b.label:
                c.buttons[0] = b
                c.dataref = b.dataref
                xp.datacache[b.dataref + '_' + b.label] = None
            if c.button_names[1] == b.label:
                c.buttons[1] = b
                xp.datacache[b.dataref + '_' + b.label] = None


def startupscreen(device, device_config, version, new_version):
    for l in ledlist:
        if "VOLTAGE" in l.label:
            for i in range(0,2):
                rowsfire_a107_set_lcd(l,284)
        rowsfire_a107_set_led(l,1)
    sleep(2)
    for l in ledlist:
        if "VOLTAGE" in l.label:
            for i in range(0,2):
                rowsfire_a107_set_lcd(l,"---")
        rowsfire_a107_set_led(l,0)


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
            if l.dreftype.value >= DREF_TYPE.ARRAY_0.value or l.dreftype == DREF_TYPE.DATA_MULTIPLE:
                larray = []
                idx = 0
                for l2 in ledlist:
                    if l2.dataref == l.dataref:
                        larray.append(l2)
                        xp.datacache[l.dataref + '_' + str(idx)] = 0
                        idx += 1
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
        elif b.dreftype == DREF_TYPE.DATA or b.dreftype.value >= DREF_TYPE.ARRAY_0.value:
            id = xp.dataref_id_fetch(b.dataref)
            xp.datacache[b.dataref] = 0
        #print(f'name: {l.label}, id: {id}')
        if id in xp.buttonref_ids:
            continue
        xp.buttonref_ids[b] = id
    print("done")


def eval_data(value, eval_string):
    if not eval_string:
        return value
    if not "$" in eval_string:
        s = 'int(value)' + eval_string
        #print(f"    eval: {s} - {value}")
        value = eval(s)
    if "$" in eval_string:
        s = eval_string
        s = s.replace("$", "value")
        #print(f"    eval: {s} - {value}")
        value = eval(s)
    return value


def xplane_ws_listener(data, led_dataref_ids): # receive ids and find led
    #print(f"[A107] recevice: {data}")
    if data.get("type") != "dataref_update_values":
        print(f"[A107] not defined {data}")
        return

    for ref_id_str, value in data["data"].items():
        ref_id = int(ref_id_str)
        #print(f"[A107] searching for {ref_id}...", end='')
        if ref_id in led_dataref_ids:
            ledobj = led_dataref_ids[ref_id]

            if type(value) is list: # dataref array, ledlist array
                if type(ledobj) != list:
                    print(f"[A107] ERROR: led array dataref not registered as list!")
                    exit()
                idx = 0
                for v in value:
                    for l2 in ledobj: # we received an array, send update to all objects
                        if idx == l2.dreftype.value - DREF_TYPE.ARRAY_0.value:
                            value_new = eval_data(value[idx], l2.eval)
                            #print(f"[A107] array value[{idx}] of {l2.label} = {value_new}")
                            if "SEGMENT" not in l2.mf_name:
                                rowsfire_a107_set_led(l2, value_new)
                            else: # SEGEMENT
                                if value_new != xp.datacache[l2.dataref + '_' + str(idx)]:
                                    print(f"[A107] array value[{idx}] of {l2.label} = {value_new}")
                                    rowsfire_a107_set_lcd(l2, value_new)
                            xp.datacache[l2.dataref + '_' + str(idx)] = value_new

                    idx += 1
            elif type(ledobj) == list and type(value) != list: # multiple leds on same dataref (without dataref arry), for eval
                for l in ledobj:
                    value_new = eval_data(value, l.eval)
                    #print(f" found: {l.label} = {value}")
                    #xp.datacache[ledobj.dataref] = value
                    rowsfire_a107_set_led(l, value_new)
            else: # single object (pin or segment)
                value = eval_data(value, ledobj.eval)
                print(f" found: {ledobj.label} = {value}")
                if "SEGMENT" not in ledobj.mf_name:
                    xp.datacache[ledobj.dataref] = value
                    rowsfire_a107_set_led(ledobj, value)
                else:
                    value = int((value + 0.05) * 10) # add first decimal place and round
                    if value != xp.datacache[ledobj.dataref]:
                        rowsfire_a107_set_lcd(ledobj, value)
                xp.datacache[ledobj.dataref] = value
        else:
            print(f"[A107] {ref_id} not found")


def send_change_to_xp(name, channel, value_orig):
    global xp
    print(f"[A107] search for: {name}, {channel}")

    found = False
    for b in buttonlist:
        value = value_orig
        if name == b.mf_button and channel == b.mf_pin:
            print(f"found {b}")
            found = True
            if b.type == BUTTON.NONE or b.dataref == None:
                break
            if b.type == BUTTON.TOGGLE_INVERSE or b.type == BUTTON.SWITCH_INVERSE:
                value = not value
            if b.type == BUTTON.SEND_1_2:
                value = value + 1
            if b.type == BUTTON.SEND_2_1:
                if value == 0:
                    value = 2

            if not xplane_connected:
                print(f"not connected to x-plane")
                break

            if b.dreftype == DREF_TYPE.DATA:
                # TODO arrays in datacache
                if b.type == BUTTON.TOGGLE and value == 0:
                    val = int(not xp.datacache[b.dataref])
                    print(f"set dataref to {val}")
                    xp.dataref_set_value(xp.buttonref_ids[b], val)
                    break

                if b.type == BUTTON.HOLD or b.type == BUTTON.SWITCH or b.type == BUTTON.SWITCH_INVERSE or b.type == BUTTON.SEND_2_1 or b.type == BUTTON.TOGGLE_INVERSE:
                    xp.datacache[b.dataref] = value
                    xp.dataref_set_value(xp.buttonref_ids[b], value)
                    break

                if b.type == BUTTON.SWITCH_COMBINED:
                    xp.datacache[b.dataref + '_' + b.label] = value
                    process_combined_button(b)

            if b.dreftype.value >= DREF_TYPE.ARRAY_0.value:
                index = b.dreftype.value - DREF_TYPE.ARRAY_0.value

                if b.type == BUTTON.SWITCH_COMBINED:
                    xp.datacache[b.dataref + '_' + b.label] = value
                    process_combined_button(b, index)
                else:
                    #print(f"sending array {b.dreftype} [{index}] = {value}")
                    xp.dataref_set_value(xp.buttonref_ids[b], value, index)

            if b.dreftype == DREF_TYPE.CMD:
                print(f"dref cmd {value} {b.type}")
                if b.type == BUTTON.TOGGLE:
                    print("send cmd")
                    xp.command_activate_duration(xp.buttonref_ids[b], 0.5)
                if b.type == BUTTON.HOLD and value:
                    print("send cmd")
                    xp.command_activate_duration(xp.buttonref_ids[b], 4)
                if (b.type == BUTTON.SWITCH  or b.type == BUTTON.SWITCH_INVERSE) and value:
                    print("send cmd of switch")
                    xp.command_activate_duration(xp.buttonref_ids[b], 4)
            #break
    if not found:
        print(f"[A107] {name}, {channel} not found")


def mf_value_changed(cmd, name, arg):
    if cmd == mf.MF.CMD.BUTTON_CHANGE:
        print(f"Value changed (Button): {name}, {int(arg[0])}") # value
        #send_change_to_xp(MF_MP2, 4, int(arg[0])) # fake APU Test FIRE
        send_change_to_xp(MF_MP1, 4, int(arg[0])) # fale APU Master
    elif cmd == mf.MF.CMD.DIGINMUX_CHANGE:
        print(f"Value changed (DigInMux): {name}, {arg[0]}, {int(arg[1])}") # channel, value
        send_change_to_xp(name, int(arg[0]), int(arg[1]))
    elif cmd == mf.MF.CMD.ANALOG_CHANGE:
        print(f"Value changed (Analog): {name}, {int(arg[0])}") # value


def process_combined_button(button, index = None):
    print(f"[A107] search for combined buttonx: {button}")
    for cb in combinedlist:
        for i in range(0,2):
            if button == cb.buttons[i]:
                b1_state = xp.datacache[cb.dataref + '_' + cb.buttons[0].label]
                b2_state = xp.datacache[cb.dataref + '_' + cb.buttons[1].label]
                if b1_state != None and b2_state != None:
                    idx = b1_state + 2 * b2_state
                    value = cb.truth_table[idx]
                    print(f"[A107] found combined button {cb} [{idx}] = {value}")
                    xp.dataref_set_value(xp.buttonref_ids[cb.buttons[0]], value, index)
                else:
                    print(f"[A107] found combined button {cb} but missing second switch data")


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
        create_combined_button_list_a107()
        mf_dev.force_sync(2)


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
