from dataclasses import dataclass
from enum import Enum, IntEnum
import hid

from threading import Thread, Event, Lock
from time import sleep
import time

import struct
import uinput

import xp_websocket

BUTTONS_CNT = 42 # TODO

#@unique
class DEVICEMASK(IntEnum):
    NONE =  0
    THROTTLE =  0x01


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
    SEND_025 = 12
    SEND_050 = 13
    SEND_075 = 14
    SWITCH_INVERSE = 15
    SWITCH_COMBINED = 16
    NONE = 99 # for testing


class DREF_TYPE(Enum):
    DATA = 0
    CMD_SHORT = 1 # fix duration
    CMD_ON_OFF = 2 # depending on press or release
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
    DATA_MULTIPLE = 26 # more leds use the same dataref
    NONE = 20 # for testing


class Button:
    def __init__(self, pin_nr, label, dataref = None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.NONE):
        self.label = label
        self.pin_nr = pin_nr
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
    def __init__(self, label, nr, dataref, dreftype = DREF_TYPE.NONE, eval = None):
        self.label = label
        self.nr = nr
        self.dataref = dataref
        self.dreftype = dreftype
        self.eval = eval

    def __str__(self):
        return(f"{self.label} -> {self.dataref}")


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
    LCD_DISPLAY = 200


values_processed = Event()
xplane_connected = False
buttonlist = []
ledlist = []
values = []
buttons_press_event = [0] * BUTTONS_CNT
buttons_release_event = [0] * BUTTONS_CNT
display_manager = None
motor1_old_value = 0


def set_datacache(usb_mgr, display_mgr, values):
    pass


def xor_bitmask(a, b, bitmask):
    return (a & bitmask) != (b & bitmask)


def um32_button_event(usb_mgr):
    global xp
    for b in buttonlist:
        if not any(buttons_press_event) and not any(buttons_release_event):
            break
        if b.pin_nr == None:
            continue
        if buttons_press_event[b.pin_nr]:
            buttons_press_event[b.pin_nr] = 0
            print(f'button {b.label} pressed')
            if not b.dataref:
                if b.label == "ENG1_FIRE":
                    usb_mgr.joystick_proxy.emit(uinput.BTN_THUMBL, 1)
                if b.label == "ENG2_FIRE":
                    usb_mgr.joystick_proxy.emit(uinput.BTN_THUMBR, 1)
                if b.label == "ENG_MODE_PUSH_BUTTON":
                    usb_mgr.joystick_proxy.emit(uinput.BTN_JOYSTICK, 1)
                continue
            #continue # TODO
            if b.type == BUTTON.TOGGLE:
                val = 0 #datacache[b.dataref]
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} from {bool(val)} to {not bool(val)}')
                    xp.dataref_set_value(b.dataref, not bool(val))
                elif b.dreftype== DREF_TYPE.CMD_SHORT:
                    print(f'send command {b.dataref}')
                    xp.command_activate_duration(b.dataref)
                elif b.dreftype== DREF_TYPE.CMD_ON_OFF:
                    xp.command_activate(b.dataref, 1)
            elif b.type == BUTTON.SWITCH:
                val = 0# datacache[b.dataref]
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 1')
                    xp.dataref_set_value(xp.buttonref_ids[b], 1)
                elif b.dreftype== DREF_TYPE.CMD_SHORT:
                    print(f'send command once {b.dataref}')
                    xp.command_activate_duration(xp.buttonref_ids[b], 0.1)
                elif b.dreftype== DREF_TYPE.CMD_ON_OFF:
                    print(f'send command ON_OFF with type SWITCH is not supported for {b.dataref}')
            elif b.type == BUTTON.SEND_0:
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 0')
                    xp.dataref_set_value(xp.buttonref_ids[b], 0)
            elif b.type == BUTTON.SEND_1:
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 1')
                    xp.dataref_set_value(xp.buttonref_ids[b], 1)
            elif b.type == BUTTON.SEND_2:
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 2')
                    xp.dataref_set_value(xp.buttonref_ids[b], 2)
            elif b.type == BUTTON.SEND_3:
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 3')
                    xp.dataref_set_value(xp.buttonref_ids[b], 3)
            elif b.type == BUTTON.SEND_4:
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 4')
                    xp.dataref_set_value(xp.buttonref_ids[b], 4)
            elif b.type == BUTTON.SEND_5:
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 5')
                    xp.dataref_set_value(xp.buttonref_ids[b], 5)
            elif b.type == BUTTON.SEND_025:
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 0.25')
                    xp.dataref_set_value(b.dataref, 0.25, isfloat = True)
            elif b.type == BUTTON.SEND_050:
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 0.5')
                    xp.dataref_set_value(xp.buttonref_ids[b], 0.5, isfloat = True)
            elif b.type == BUTTON.SEND_075:
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 0.75')
                    xp.dataref_set_value(xp.buttonref_ids[b], 0.75, isfloat = True)
            else:
                print(f'no known button type for button {b.label}')
        if buttons_release_event[b.pin_nr]:
            buttons_release_event[b.pin_nr] = 0
            print(f'button {b.label} released')
            if not b.dataref:
                if b.label == "ENG1_FIRE":
                    usb_mgr.joystick_proxy.emit(uinput.BTN_THUMBL, 0)
                if b.label == "ENG2_FIRE":
                    usb_mgr.joystick_proxy.emit(uinput.BTN_THUMBR, 0)
                if b.label == "ENG_MODE_PUSH_BUTTON":
                    usb_mgr.joystick_proxy.emit(uinput.BTN_JOYSTICK, 0)
                continue
            if b.type == BUTTON.SWITCH and b.dataref and b.dreftype == DREF_TYPE.DATA:
                xp.dataref_set_value(b.dataref, 0)
            if b.type == BUTTON.TOGGLE:
                if b.dreftype== DREF_TYPE.CMD_ON_OFF:
                    xp.command_activate(b.dataref, 0)


def throttle_create_events(xp, usb_mgr, display_mgr):
    global values
    sleep(2) # wait for values to be available
    buttons_last = 0
    #xplane_connected = True # TODO remove
    while True:
        if not xplane_connected: # wait for x-plane
            sleep(1)
            continue

        set_datacache(usb_mgr, display_mgr, values.copy())
        values_processed.set()
        sleep(0.01) # todo 0.005
        #print('#', end='', flush=True) # TEST1: should print many '#' in console
        try:
            data_in = usb_mgr.device.read(0x81, 25)
        except Exception as error:
            print(f'[UM32]  *** continue after usb-in error: {error} ***') # TODO
            sleep(0.5) # TODO remove
            continue
        if len(data_in) == 64 or len(data_in) == 14: # we get this often but don't understand yet. May have someting to do with leds set
            continue
        if len(data_in) != 37:
            print(f'[UM32] rx data count {len(data_in)} not valid for {usb_mgr}')
            continue
        #print(f"data_in: {data_in}")

        #create button bit-pattern
        buttons = 0
        for i in range(6):
            buttons |= data_in[i + 1] << (8 * i)
        #print(hex(buttons)) # TEST2: you should see a difference when pressing buttons
        for i in range (BUTTONS_CNT):
            mask = 0x01 << i
            if xor_bitmask(buttons, buttons_last, mask):
                #print(f"buttons: {format(buttons, "#04x"):^14}")
                if buttons & mask:
                    buttons_press_event[i] = 1
                else:
                    buttons_release_event[i] = 1
                um32_button_event(usb_mgr)
        buttons_last = buttons

        th_left = struct.unpack('<H', bytes(data_in[13:15]))[0]
        th_right = struct.unpack('<H', bytes(data_in[15:17]))[0]
        spoiler = struct.unpack('<H', bytes(data_in[19:21]))[0]

        usb_mgr.joystick_proxy.emit(uinput.ABS_X, th_left, syn=False)
        usb_mgr.joystick_proxy.emit(uinput.ABS_Y, th_right, syn=False)
        usb_mgr.joystick_proxy.emit(uinput.ABS_Z, spoiler)


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
    global display_manager
    global motor1_old_value
    if data.get("type") != "dataref_update_values":
        if data.get("type") == "result":
            if data.get("success") != True:
                print(f"[UM32] send failed for {data}")
        else:
            print(f"[UM32] not defined {data}")
        return
    for ref_id_str, value in data["data"].items():
        ref_id = int(ref_id_str)
        #print(f"[A107] searching for {ref_id}...", end='')
        if ref_id in led_dataref_ids:
            ledobj = led_dataref_ids[ref_id]

            if type(value) is list: # dataref array, ledlist array
                if type(ledobj) != list:
                    print(f"[UM32] ERROR: led array dataref not registered as list!")
                    exit()
                idx = 0
                for v in value:
                    for l2 in ledobj: # we received an array, send update to all objects
                        if idx == l2.dreftype.value - DREF_TYPE.ARRAY_0.value:
                            value_new = eval_data(value[idx], l2.eval)
                            display_manager.set_leds(l2.nr, value_new)

                    idx += 1
            elif type(ledobj) == list and type(value) != list: # multiple leds on same dataref (without dataref arry), for eval
                for l in ledobj:
                    value_new = eval_data(value, l.eval)
                    print(f" found: {l.label} = {value_new}")
                    #xp.datacache[ledobj.dataref] = value
                    if l.nr is not None:
                        display_manager.set_led(l.nr, value_new)
            else: # single object (pin or segment)
                value = eval_data(value, ledobj.eval)
                print(f" found: {ledobj.label} = {value}")

                if ledobj.nr is not None:
                    if ledobj.nr.value < Leds.LCD_DISPLAY.value:
                        display_manager.set_leds(ledobj.nr, value)
                    else:
                        display_manager.set_lcd(value)
        else:
            print(f"[UM32] {ref_id} not found")



def create_button_list_um32():
    create_led_list_um32()
    buttonlist.append(Button(0, "ENG1_MASTER_ON", "AirbusFBW/ENG1MasterSwitch", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_1))
    buttonlist.append(Button(1, "ENG1_MASTER_OFF", "AirbusFBW/ENG1MasterSwitch", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_0))
    buttonlist.append(Button(2, "ENG2_MASTER_ON", "AirbusFBW/ENG2MasterSwitch", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_1))
    buttonlist.append(Button(3, "ENG2_MASTER_OFF", "AirbusFBW/ENG2MasterSwitch", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_0))
    buttonlist.append(Button(4, "ENG1_FIRE", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(5, "ENG2_FIRE", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(6, "ENG_MODE_CRANK", "AirbusFBW/ENGModeSwitch", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_0))
    buttonlist.append(Button(7, "ENG_MODE_NORM", "AirbusFBW/ENGModeSwitch", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_1))
    buttonlist.append(Button(8, "ENG_MODE_START", "AirbusFBW/ENGModeSwitch", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_2))
    buttonlist.append(Button(9, "LEFT_THROTTLE_AUTO_TRUST_DISC", "sim/autopilot/autothrottle_off", dreftype = DREF_TYPE.CMD_SHORT, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(10, "RIGHT_THROTTLE_AUTO_TRUST_DISC", "sim/autopilot/autothrottle_off", dreftype = DREF_TYPE.CMD_SHORT, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(11, "LEFT_THROTTLE_TO/GA", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(12, "LEFT_THROTTLE_FLEX", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(13, "LEFT_THROTTLE_CL", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(14, "LEFT_THROTTLE_IDLE", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(15, "LEFT_THROTTLE_IDLE_REVERSE", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(16, "LEFT_THROTTLE_FULL_REVERSE", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(17, "RIGHT_THROTTLE_TO/GA", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(18, "RIGHT_THROTTLE_FLEX", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(19, "RIGHT_THROTTLE_CL", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(20, "RIGHT_THROTTLE_IDLE", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(21, "RIGHT_THROTTLE_IDLE_REVERSE", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(22, "RIGHT_THROTTLE_FULL_REVERSE", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(23, "ENG_MODE_PUSH_BUTTON", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(24, "TRIM_REST", "sim/flight_controls/rudder_trim_center", dreftype = DREF_TYPE.CMD_SHORT, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(25, "RUDDER_TRIM_L", "sim/flight_controls/rudder_trim_left", dreftype = DREF_TYPE.CMD_ON_OFF, button_type = BUTTON.TOGGLE))
    buttonlist.append(Button(26, "RUDDER_TRIM_NEUTRAL", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(27, "RUDDER_TRIM_R", "sim/flight_controls/rudder_trim_right", dreftype = DREF_TYPE.CMD_ON_OFF, button_type = BUTTON.TOGGLE))
    buttonlist.append(Button(28, "PARKING_BRAKE_OFF", "AirbusFBW/ParkBrake", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_0))
    buttonlist.append(Button(29, "PARKING_BRAKE_ON", "AirbusFBW/ParkBrake", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_1))
    buttonlist.append(Button(30, "FLAPS_4", "AirbusFBW/FlapLeverRatio", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_1))
    buttonlist.append(Button(31, "FLAPS_3", "AirbusFBW/FlapLeverRatio", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_075))
    buttonlist.append(Button(32, "FLAPS_2", "AirbusFBW/FlapLeverRatio", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_050))
    buttonlist.append(Button(33, "FLAPS_1", "AirbusFBW/FlapLeverRatio", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_025))
    buttonlist.append(Button(34, "FLAPS_0", "AirbusFBW/FlapLeverRatio", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_0))
    buttonlist.append(Button(35, "SPOILER_FULL", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(36, "SPOILER_ONE_HALF", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(37, "SPOILER_RET", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(38, "SPOILER_ARM", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(39, "LEFT_THROTTLE_REVERSE_MODE_ON", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(40, "RIGHT_THROTTLE_REVERSE_MODE_ON", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))


def create_led_list_um32():  # TODO check sim/cockpit/electrical/avionics_on == 1
    ledlist.append(Led("PEDESTAL_BRIGHTNESS", Leds.LCD_BACKLIGHT, "AirbusFBW/PanelBrightnessLevel", DREF_TYPE.DATA_MULTIPLE, "int($*255)"))
    ledlist.append(Led("PEDESTAL_BRIGHTNESS", Leds.PACK_32_BACKLIGHT, "AirbusFBW/PanelBrightnessLevel", DREF_TYPE.DATA_MULTIPLE, "max(int($*150), 15) if $ > 0 else 0"))
    ledlist.append(Led("PEDESTAL_BRIGHTNESS", Leds.THROTTLE_BACKLIGHT, "AirbusFBW/PanelBrightnessLevel", DREF_TYPE.DATA_MULTIPLE, "int($*255)"))
    ledlist.append(Led("PEDESTAL_BRIGHTNESS", Leds.MARKER_BACKLIGHT, "AirbusFBW/PanelBrightnessLevel", DREF_TYPE.DATA_MULTIPLE, "int($*255)"))
    ledlist.append(Led("RUD Trim", Leds.LCD_DISPLAY, "sim/flightmodel/controls/rud_trim", DREF_TYPE.DATA, "round($/0.833*25,1)"))
    ledlist.append(Led("ENG 1 FIRE", Leds.ENG1_FIRE, "AirbusFBW/OHPLightsATA70_Raw", DREF_TYPE.ARRAY_11, "int($)"))
    ledlist.append(Led("ENG 2 FIRE", Leds.ENG2_FIRE, "AirbusFBW/OHPLightsATA70_Raw", DREF_TYPE.ARRAY_13, "int($)"))
    #ledlist.append(Led("FORCES", Leds.MOTOR1, "sim/flightmodel2/gear/on_noisy", DREF_TYPE.ARRAY_0, "$*20"))
    ledlist.append(Led("FORCES", Leds.MOTOR1, "AirbusFBW/ENGTLASettingEPR", DREF_TYPE.ARRAY_0, "int(int($>1.30)*($-1.30)*10*200)"))

class UsbManager:
    def __init__(self):
        self.device = None
        self.device_config = 0
        self.joystick_proxy = None

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


        events = (
            uinput.ABS_X + (0, 65535, 255, 1024),
            uinput.ABS_Y + (0, 65535, 255, 1024),
            uinput.ABS_Z + (0, 65535, 255, 1024),
            uinput.BTN_JOYSTICK,
            uinput.BTN_THUMBL,
            uinput.BTN_THUMBR,
        )
        try:
            self.joystick_proxy = uinput.Device(events, name="XSchenfly Throttle Joystick Proxy", bustype=0x0006,
                     vendor=0x0001, product=0x0001, version=0x01)
            print("[UM32] Virtuelles Joystick Device erstellt.")
        except OSError as e:
            if e.errno == 19:
                print("\n[FEHLER] uinput Gerät konnte nicht geöffnet werden.")
                print("Ursache: Das Kernel-Modul 'uinput' ist nicht geladen")
                print("oder /dev/uinput existiert nicht.\n")

                print("Lösung:")
                print("  sudo modprobe uinput")

                print("\nFalls das Problem weiterhin besteht, prüfen:")
                print("  ls /dev/input/js*")
                print("  ls -l /dev/uinput")
                print("  sudo chmod 666 /dev/uinput  (nur zu Testzwecken)")
            else:
                raise  # other OSError

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


def xplane_get_dataref_ids(xp):
    print(f"[A107] getting led dataref ids ... ", end="")
    for data in [ledlist]:
        for l in data:
            if l.dataref == None:
                continue
            if l.dreftype == DREF_TYPE.CMD_SHORT or l.dreftype == DREF_TYPE.CMD_ON_OFF:
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
        if b.dreftype == DREF_TYPE.CMD_SHORT or b.dreftype == DREF_TYPE.CMD_ON_OFF:
            id = xp.command_id_fetch(b.dataref)
        elif b.dreftype == DREF_TYPE.DATA or b.dreftype.value >= DREF_TYPE.ARRAY_0.value:
            id = xp.dataref_id_fetch(b.dataref)
            xp.datacache[b.dataref] = 0
        #print(f'name: {l.label}, id: {id}')
        if id in xp.buttonref_ids:
            continue
        xp.buttonref_ids[b] = id
    print("done")


class DisplayManager:
    # 7-segment encoding table: digit -> (a, b, c, d, e, f, g)
    SEVEN_SEG = {
        0: (1,1,1,1,1,1,0), 1: (0,1,1,0,0,0,0), 2: (1,1,0,1,1,0,1),
        3: (1,1,1,1,0,0,1), 4: (0,1,1,0,0,1,1), 5: (1,0,1,1,0,1,1),
        6: (1,0,1,1,1,1,1), 7: (1,1,1,0,0,0,0), 8: (1,1,1,1,1,1,1),
        9: (1,1,1,1,0,1,1),
    }
    BLANK_SEG = (0, 0, 0, 0, 0, 0, 0)  # all segments off = blank digit
    SLOT_SEG_INDEX = [5, 4, 3, 2, 1, 0]  # slot -> segment: f, e, d, c, b, a
    SIDE_BITS = {'L': [1, 1, 1, 0, 0, 0], 'R': [1, 1, 0, 1, 1, 1]}


    def __init__(self, device):
        self.device = device
        self.ledlist = []
    
    
    def startupscreen(self, version: str = None, new_version: str = None):
        self.set_backlights(120)
        self.clear()
        self.set_lcd(-0.5)


    def set_backlights(self, value : int):
        self.set_leds([Leds.LCD_BACKLIGHT,
                       Leds.PACK_32_BACKLIGHT,
                       Leds.THROTTLE_BACKLIGHT,
                       Leds.MARKER_BACKLIGHT], value)


    def clear(self):
        self.set_leds([Leds.ENG1_FAULT,
                       Leds.ENG1_FIRE,
                       Leds.ENG2_FAULT,
                       Leds.ENG2_FIRE,
                       Leds.MOTOR1,
                       Leds.MOTOR2], 0)


    def set_leds(self, leds : Leds, brightness : int):
        if isinstance(leds, list):
            for i in range(len(leds)):
                self.set_led(leds[i], brightness)
        else:
            self.set_led(leds, brightness)


    def set_led(self, led : Leds, brightness : int):
        global motor1_old_value

        if brightness > 255:
            brightness = 255

        if led == Leds.MOTOR1: # dirty fast hack :-)
            if brightness != motor1_old_value:
                motor1_old_value = brightness
            else:
                return

        cmd = 0x10
        value = led.value

        if value >= Leds.PACK_32_BACKLIGHT.value:
            cmd = 0x01
            value -= Leds.PACK_32_BACKLIGHT.value

        data = [0x02, cmd, 0xb9, 0, 0, 3, 0x49, value, brightness, 0,0,0,0,0]
        cmd = bytes(data)
        self.device.write(cmd)


    def _calc_lcd_params(self, side_right : bool, integer, fractional):
        """
        Encode LCD value to protocol bytes.
        """
        tens = integer // 10
        ones = integer % 10

        frac_segs = self.SEVEN_SEG[fractional]  # (a, b, c, d, e, f, g)
        ones_segs = self.SEVEN_SEG[ones]
        tens_segs = self.SEVEN_SEG[tens] if tens > 0 else self.BLANK_SEG

        side_bit = 1 if side_right else 0
        b29 = (frac_segs[6] << 3) | (ones_segs[6] << 2) | (tens_segs[6] << 1) | side_bit

        bit0 = self.SIDE_BITS['R' if side_right else 'L']
        slots = []
        for i in range(6):
            seg_idx = self.SLOT_SEG_INDEX[i]
            frac_bit = frac_segs[seg_idx]
            ones_bit = ones_segs[seg_idx]
            tens_bit = tens_segs[seg_idx]
            slot_val = (frac_bit << 3) | (ones_bit << 2) | (tens_bit << 1) | bit0[i]
            slots.append(slot_val)

        return b29, slots


    def set_lcd(self, value, counter=0):
        # Usage (integer and fractional both support 0-9):
        # set_lcd(-0.5)  # Display "L 0.5"
        # set_lcd(1.0)   # Display "R 1.0"
        # set_lcd(-2.3)  # Display "L 2.3"
        integer = int(value)
        fractional = int((value - integer) * 10)
        b29, slots = self._calc_lcd_params(value > 0, abs(integer), abs(fractional))

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
        xplane_get_dataref_ids(self.xp)
        print(f"[UM32] subsrcibe datarefs... ", end="")
        t = Thread(target=self.xp.datarefs_subscribe, args=(self.xp.led_dataref_ids, xplane_ws_listener))
        t.start()
        print(f"done")
        xplane_connected = True
        xp = self.xp


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
        global display_manager

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
        display_manager = self.display_mgr # very ulgy....

        create_button_list_um32()

        usb_event_thread = Thread(target=throttle_create_events, args=[self.xp, self.usb_mgr, self.display_mgr])
        usb_event_thread.start()

        cyclic_thread = Thread(target=self.cyclic_worker)
        cyclic_thread.start()
