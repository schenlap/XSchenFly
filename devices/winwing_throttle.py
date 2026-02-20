from dataclasses import dataclass
from enum import Enum, IntEnum
import hid

from threading import Thread, Event, Lock
from time import sleep
import time

import struct
import uinput

import xp_websocket

# sim/cockpit2/controls/speedbrake_ratio	float	y	ratio	This is how much the speebrake HANDLE is deflected, in ratio, where 0.0 is fully retracted, 0.5 is halfway down, and 1.0 is fully down, and -0.5 is speedbrakes ARMED.

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
    DATA_MULTIPLE = 26 # more leds use the same dataref
    CMD_ONCE = 27


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



values_processed = Event()
xplane_connected = False
buttonlist = []
values = []
buttons_press_event = [0] * BUTTONS_CNT
buttons_release_event = [0] * BUTTONS_CNT


def set_datacache(usb_mgr, display_mgr, values):
    pass


def xor_bitmask(a, b, bitmask):
    return (a & bitmask) != (b & bitmask)


def um32_button_event():
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
                continue
            #continue # TODO
            if b.type == BUTTON.TOGGLE:
                val = 0 #datacache[b.dataref]
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} from {bool(val)} to {not bool(val)}')
                    xp.dataref_set_value(b.dataref, not bool(val))
                elif b.dreftype== DREF_TYPE.CMD:
                    print(f'send command {b.dataref}')
                    xp.command_activate_duration(b.dataref)
            elif b.type == BUTTON.SWITCH:
                val = 0# datacache[b.dataref]
                if b.dreftype== DREF_TYPE.DATA:
                    print(f'set dataref {b.dataref} to 1')
                    xp.dataref_set_value(xp.buttonref_ids[b], 1)
                elif b.dreftype== DREF_TYPE.CMD:
                    print(f'send command {b.dataref}')
                    xp.command_activate_duration(xp.buttonref_ids[b])
                elif b.dreftype== DREF_TYPE.CMD_ONCE:
                    print(f'send command once {b.dataref}')
                    xp.command_activate_duration(xp.buttonref_ids[b], 0.1)
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
            if b.type == BUTTON.SWITCH and b.dataref and b.dreftype == DREF_TYPE.DATA:
                xp.dataref_set_value(b.dataref, 0)


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
        #if len(data_in) == 14: # we get this often but don't understand yet. May have someting to do with leds set
        #    continue
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
                um32_button_event()
        buttons_last = buttons

        th_left = struct.unpack('<H', bytes(data_in[13:15]))[0]
        th_right = struct.unpack('<H', bytes(data_in[15:17]))[0]
        spoiler = struct.unpack('<H', bytes(data_in[19:21]))[0]

        usb_mgr.joystick_proxy.emit(uinput.ABS_X, th_left, syn=False)
        usb_mgr.joystick_proxy.emit(uinput.ABS_Y, th_right, syn=False)
        usb_mgr.joystick_proxy.emit(uinput.ABS_Z, spoiler)

def xplane_ws_listener(data, led_dataref_ids): # receive ids and find led
    pass



def create_button_list_um32():
    buttonlist.append(Button(0, "ENG1_MASTER_ON", "AirbusFBW/ENG1MasterSwitch", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_1))
    buttonlist.append(Button(1, "ENG1_MASTER_OFF", "AirbusFBW/ENG1MasterSwitch", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_0))
    buttonlist.append(Button(2, "ENG2_MASTER_ON", "AirbusFBW/ENG2MasterSwitch", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_1))
    buttonlist.append(Button(3, "ENG2_MASTER_OFF", "AirbusFBW/ENG2MasterSwitch", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_0))
    buttonlist.append(Button(4, "ENG1_FIRE", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(5, "ENG2_FIRE", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(6, "ENG_MODE_CRANK", "AirbusFBW/ENGModeSwitch", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_0))
    buttonlist.append(Button(7, "ENG_MODE_NORM", "AirbusFBW/ENGModeSwitch", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_1))
    buttonlist.append(Button(8, "ENG_MODE_START", "AirbusFBW/ENGModeSwitch", dreftype = DREF_TYPE.DATA, button_type = BUTTON.SEND_2))
    buttonlist.append(Button(9, "LEFT_THROTTLE_AUTO_TRUST_DISC", "sim/autopilot/autothrottle_off", dreftype = DREF_TYPE.CMD, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(10, "RIGHT_THROTTLE_AUTO_TRUST_DISC", "sim/autopilot/autothrottle_off", dreftype = DREF_TYPE.CMD, button_type = BUTTON.SWITCH))
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
    buttonlist.append(Button(24, "TRIM_REST", "sim/flight_controls/rudder_trim_center", dreftype = DREF_TYPE.CMD, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(25, "RUDDER_TRIM_L", "sim/flight_controls/rudder_trim_left", dreftype = DREF_TYPE.CMD_ONCE, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(26, "RUDDER_TRIM_NEUTRAL", None, dreftype = DREF_TYPE.DATA, button_type = BUTTON.SWITCH))
    buttonlist.append(Button(27, "RUDDER_TRIM_R", "sim/flight_controls/rudder_trim_right", dreftype = DREF_TYPE.CMD_ONCE, button_type = BUTTON.SWITCH))
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
            uinput.BTN_THUMB,
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
    if False: # TODO
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
        if b.dreftype == DREF_TYPE.CMD or b.dreftype == DREF_TYPE.CMD_ONCE:
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
        xplane_get_dataref_ids(self.xp)
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

        create_button_list_um32()

        usb_event_thread = Thread(target=throttle_create_events, args=[self.xp, self.usb_mgr, self.display_mgr])
        usb_event_thread.start()

        cyclic_thread = Thread(target=self.cyclic_worker)
        cyclic_thread.start()
