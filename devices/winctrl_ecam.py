from enum import Enum, IntEnum
import hid

from threading import Thread, Event
from time import sleep

import xp_websocket

# XSchenFly device module for the WINCTRL 32 ECAM.

BUTTONS_CNT = 22

REPORT_BUTTON_OFFSET = 1
REPORT_BUTTON_BYTES = 4
VALID_REPORT_LENGTHS = {12}


class DEVICEMASK(IntEnum):
    NONE = 0
    ECAM32 = 0x01


class BUTTON(Enum):
    SWITCH = 0
    TOGGLE = 1
    SEND_0 = 3
    SEND_1 = 4
    NONE = 99


class DREF_TYPE(Enum):
    DATA = 0
    CMD_SHORT = 1
    CMD_ON_OFF = 2
    ARRAY_0 = 10
    ARRAY_1 = 11
    ARRAY_2 = 12
    ARRAY_3 = 13
    ARRAY_4 = 14
    ARRAY_5 = 15
    ARRAY_6 = 16
    ARRAY_7 = 17
    DATA_MULTIPLE = 26
    NONE = 99


class Button:
    def __init__(self, pin_nr, label, dataref=None, dreftype=DREF_TYPE.DATA, button_type=BUTTON.NONE):
        self.label = label
        self.pin_nr = pin_nr
        self.dataref = dataref
        self.dreftype = dreftype
        self.type = button_type

    def __str__(self):
        return f"{self.label} -> {self.dataref} {self.type}"


class Led:
    def __init__(self, label, nr, dataref, dreftype=DREF_TYPE.NONE, eval=None):
        self.label = label
        self.nr = nr
        self.dataref = dataref
        self.dreftype = dreftype
        self.eval = eval

    def __str__(self):
        return f"{self.label} -> {self.dataref}"


class Leds(IntEnum):
    PANEL_BACKLIGHT = 0
    KEY_BACKLIGHT = 1
    EMER_CANC = 3
    ENG = 4
    BLEED = 5
    PRESS = 6
    ELEC = 7
    HYD = 8
    FUEL = 9
    APU = 10
    COND = 11
    DOOR = 12
    WHEEL = 13
    FCTL = 14
    CLR_L = 15
    STS = 16
    CLR_R = 17

    TO_CONFIG = 20
    RCL = 21
    ALL = 22


values_processed = Event()
xplane_connected = False
buttonlist = []
ledlist = []
buttons_press_event = [0] * BUTTONS_CNT
buttons_release_event = [0] * BUTTONS_CNT


def xor_bitmask(a, b, bitmask):
    return (a & bitmask) != (b & bitmask)


def eval_data(value, eval_string):
    if not eval_string:
        return value
    expr = eval_string.replace("$", "value")
    return eval(expr)


def ecam_button_event(usb_mgr):
    global xp
    for b in buttonlist:
        if not any(buttons_press_event) and not any(buttons_release_event):
            break
        if b.pin_nr is None:
            continue

        if buttons_press_event[b.pin_nr]:
            buttons_press_event[b.pin_nr] = 0
            print(f"[ECAM32] button {b.label} pressed")
            if not b.dataref:
                continue

            if b.type == BUTTON.SWITCH:
                if b.dreftype == DREF_TYPE.DATA:
                    xp.dataref_set_value(xp.buttonref_ids[b], 1)
                elif b.dreftype == DREF_TYPE.CMD_SHORT:
                    xp.command_activate_duration(xp.buttonref_ids[b], 0.15)
                elif b.dreftype == DREF_TYPE.CMD_ON_OFF:
                    xp.command_activate(xp.buttonref_ids[b], 1)
            elif b.type == BUTTON.TOGGLE:
                if b.dreftype == DREF_TYPE.CMD_SHORT:
                    xp.command_activate_duration(xp.buttonref_ids[b], 0.15)
                elif b.dreftype == DREF_TYPE.CMD_ON_OFF:
                    xp.command_activate(xp.buttonref_ids[b], 1)
            elif b.type == BUTTON.SEND_0:
                xp.dataref_set_value(xp.buttonref_ids[b], 0)
            elif b.type == BUTTON.SEND_1:
                xp.dataref_set_value(xp.buttonref_ids[b], 1)

        if buttons_release_event[b.pin_nr]:
            buttons_release_event[b.pin_nr] = 0
            print(f"[ECAM32] button {b.label} released")
            if not b.dataref:
                continue
            if b.type == BUTTON.SWITCH and b.dreftype == DREF_TYPE.DATA:
                xp.dataref_set_value(xp.buttonref_ids[b], 0)
            if b.dreftype == DREF_TYPE.CMD_ON_OFF:
                xp.command_activate(xp.buttonref_ids[b], 0)


class DisplayManager:
    def __init__(self, device):
        self.device = device

    def startupscreen(self, version=None, new_version=None):
        self.clear()
        self.set_backlights(110)

    def clear(self):
        # For now we simply turn known LED slots off.
        for led in list(Leds):
            self.set_led(led, 0)

    def set_backlights(self, value: int):
        self.set_led(Leds.PANEL_BACKLIGHT, value)
        self.set_led(Leds.KEY_BACKLIGHT, value)

    def set_leds(self, leds, brightness: int):
        if isinstance(leds, list):
            for led in leds:
                self.set_led(led, brightness)
        else:
            self.set_led(leds, brightness)

    def set_led(self, led: int, brightness: int):
        # Output format is modeled after other WINCTRL devices, but this packet
        # is still a best-effort placeholder until the ECAM 32 output protocol is
        # confirmed.
        brightness = max(0, min(int(brightness), 255))
        data = [0x02, 0x70, 0xBB, 0, 0, 3, 0x49, int(led), brightness, 0, 0, 0, 0, 0]
        # lsusb shows an interrupt OUT endpoint with 64-byte max packet size.
        # We therefore pad the provisional packet to 64 bytes.
        #data.extend([0] * (64 - len(data)))
        try:
            self.device.write(bytes(data))
        except Exception as exc:
            print(f"[ECAM32] LED write failed for {led}: {exc}")


def xplane_ws_listener(data, led_dataref_ids):
    if data.get("type") != "dataref_update_values":
        if data.get("type") == "result" and data.get("success") is not True:
            print(f"[ECAM32] send failed for {data}")
        return

    for ref_id_str, value in data["data"].items():
        ref_id = int(ref_id_str)
        if ref_id not in led_dataref_ids:
            continue

        ledobj = led_dataref_ids[ref_id]
        if isinstance(ledobj, list) and not isinstance(value, list):
            for led in ledobj:
                display_manager.set_led(led.nr, eval_data(value, led.eval))
        elif isinstance(value, list):
            for idx, v in enumerate(value):
                for led in ledobj:
                    if idx == led.dreftype.value - DREF_TYPE.ARRAY_0.value:
                        display_manager.set_led(led.nr, eval_data(v, led.eval))
        else:
            display_manager.set_led(ledobj.nr, eval_data(value, ledobj.eval))


class UsbManager:
    def __init__(self):
        self.device = None
        self.device_config = 0

    def connect_device(self, vid: int, pid: int):
        try:
            self.device = hid.device()
            self.device.open(vid, pid)
        except AttributeError:
            self.device = hid.Device(vid=vid, pid=pid)

        if self.device is None:
            raise RuntimeError("[ECAM32] Device not found")

        try:
            self.device.set_nonblocking(False)
        except Exception:
            pass

        print("[ECAM32] Device connected.")

    def find_device(self):
        devlist = [
            {"vid": 0x4098, "pid": 0xBB70, "name": "WINWING ECAM", "mask": DEVICEMASK.ECAM32},
        ]
        for d in devlist:
            print(f"[ECAM32] now searching for {d['name']} ... ", end="")
            for dev in hid.enumerate():
                if dev["vendor_id"] == d["vid"] and dev["product_id"] == d["pid"]:
                    print("found")
                    self.device_config |= d["mask"]
                    return d["vid"], d["pid"], self.device_config
            print("not found")
        return None, None, 0


def create_led_list_ecam32():
    # Common pedestal backlight used by ToLiss pedestal panels.
    ledlist.append(Led("PEDESTAL_BRIGHTNESS", Leds.PANEL_BACKLIGHT, "AirbusFBW/PanelBrightnessLevel", DREF_TYPE.DATA_MULTIPLE, "int($ * 255)"))
    ledlist.append(Led("KEY_BACKLIGHT", Leds.KEY_BACKLIGHT, "AirbusFBW/PanelBrightnessLevel", DREF_TYPE.DATA_MULTIPLE, "int($ * 255)"))
    ledlist.append(Led("LED_CLR_L", Leds.CLR_L, "AirbusFBW/CLRillum", DREF_TYPE.DATA_MULTIPLE))
    ledlist.append(Led("LED_CLR_R", Leds.CLR_R, "AirbusFBW/CLRillum", DREF_TYPE.DATA_MULTIPLE))
    ledlist.append(Led("LED_ENG", Leds.ENG, "AirbusFBW/SDENG", DREF_TYPE.DATA))
    ledlist.append(Led("LED_BLEED", Leds.BLEED, "AirbusFBW/SDBLEED", DREF_TYPE.DATA))
    ledlist.append(Led("LED_PRESS", Leds.PRESS, "AirbusFBW/SDPRESS", DREF_TYPE.DATA))

    ledlist.append(Led("LED_ELEC", Leds.ELEC, "AirbusFBW/SDELEC", DREF_TYPE.DATA))
    ledlist.append(Led("LED_HYD", Leds.HYD, "AirbusFBW/SDHYD", DREF_TYPE.DATA))
    ledlist.append(Led("LED_FUEL", Leds.FUEL, "AirbusFBW/SDFUEL", DREF_TYPE.DATA))
    ledlist.append(Led("LED_APU", Leds.APU, "AirbusFBW/SDAPU", DREF_TYPE.DATA))
    ledlist.append(Led("LED_COND", Leds.COND, "AirbusFBW/SDCOND", DREF_TYPE.DATA))
    ledlist.append(Led("LED_DOOR", Leds.DOOR, "AirbusFBW/SDDOOR", DREF_TYPE.DATA))
    ledlist.append(Led("LED_WHEEL", Leds.WHEEL, "AirbusFBW/SDWHEEL", DREF_TYPE.DATA))
    ledlist.append(Led("LED_FCTL", Leds.FCTL, "AirbusFBW/SDFCTL", DREF_TYPE.DATA))
    ledlist.append(Led("LED_STS", Leds.STS, "AirbusFBW/SDSTATUS", DREF_TYPE.DATA))
    

    # The following LED states are conservative placeholders. Update them if you
    # have exact ECAM / ECP annunciator datarefs from DataRefTool.
    #ledlist.append(Led("TO CONFIG", Leds.TO_CONFIG, "AirbusFBW/PanelBrightnessLevel", DREF_TYPE.DATA_MULTIPLE, "max(int($*170), 10) if $ > 0 else 0"))
    #ledlist.append(Led("EMER CANC", Leds.EMER_CANC, "AirbusFBW/PanelBrightnessLevel", DREF_TYPE.DATA_MULTIPLE, "max(int($*200), 15) if $ > 0 else 0"))


def create_button_list_ecam32():
    create_led_list_ecam32()

    # Visible keys. Commands are based on publicly shared ToLiss mappings where
    # available. Page keys use the most common AirbusFBW naming convention; if a
    # local ToLiss build differs, only these strings need to be adjusted.
    mappings = [
        (1, "TO_CONFIG", "AirbusFBW/TOConfigPress"),
        (3, "EMER_CANC", "AirbusFBW/EmerCancel"),
        (4, "ENG", "AirbusFBW/ECP/SelectEnginePage"),
        (5, "BLEED", "AirbusFBW/ECP/SelectBleedPage"),
        (6, "PRESS", "AirbusFBW/ECP/SelectPressPage"),
        (7, "ELEC", "AirbusFBW/ECP/SelectElecACPage"),
        (8, "HYD", "AirbusFBW/ECP/SelectHydraulicPage"),
        (9, "FUEL", "AirbusFBW/ECP/SelectFuelPage"),
        (10, "APU", "AirbusFBW/ECP/SelectAPUPage"),
        (11, "COND", "AirbusFBW/ECP/SelectConditioningPage"),
        (12, "DOOR", "AirbusFBW/ECP/SelectDoorOxyPage"),
        (13, "WHEEL", "AirbusFBW/ECP/SelectWheelPage"),
        (14, "FCTL", "AirbusFBW/ECP/SelectFlightControlPage"),
        (15, "ALL", "AirbusFBW/ECAMAll"),
        (16, "CLR_L", "AirbusFBW/ECP/CaptainClear"),
        (18, "STS", "AirbusFBW/ECP/SelectStatusPage"),
        (19, "RCL", "AirbusFBW/ECAMRecall"),
        (21, "CLR_L", "AirbusFBW/ECP/CopilotClear"),
        
        
        # Four hidden/configurable keys advertised by WINCTRL. Left as generic
        # placeholders so you can bind them to popups, checklists, CLR FO, etc.
        #(0, "HIDDEN_1", "AirbusFBW/PopUpEWD"),
        #(2, "HIDDEN_2", "AirbusFBW/PopUpSD"),
        #(17, "HIDDEN_3", "AirbusFBW/ECP/CopilotClear"),
        #(20, "HIDDEN_4", "sim/operation/screenshot"),
    ]

    for pin_nr, label, cmd in mappings:
        buttonlist.append(Button(pin_nr, label, cmd, DREF_TYPE.CMD_SHORT, BUTTON.SWITCH))


def xplane_get_dataref_ids(xp):
    print("[ECAM32] getting LED dataref ids ... ", end="")
    for led in ledlist:
        if led.dataref is None or led.dreftype in (DREF_TYPE.CMD_SHORT, DREF_TYPE.CMD_ON_OFF):
            continue
        xp.datacache[led.dataref] = 0
        ref_id = xp.dataref_id_fetch(led.dataref)
        if ref_id in xp.led_dataref_ids:
            continue
        if led.dreftype.value >= DREF_TYPE.ARRAY_0.value or led.dreftype == DREF_TYPE.DATA_MULTIPLE:
            grouped = []
            idx = 0
            for led2 in ledlist:
                if led2.dataref == led.dataref:
                    grouped.append(led2)
                    xp.datacache[f"{led.dataref}_{idx}"] = 0
                    idx += 1
            xp.led_dataref_ids[ref_id] = grouped.copy()
        else:
            xp.led_dataref_ids[ref_id] = led
    print("done")

    print("[ECAM32] getting button cmd/dataref ids ... ", end="")
    for button in buttonlist:
        if button.dataref is None:
            continue
        if button.dreftype in (DREF_TYPE.CMD_SHORT, DREF_TYPE.CMD_ON_OFF):
            ref_id = xp.command_id_fetch(button.dataref)
        else:
            ref_id = xp.dataref_id_fetch(button.dataref)
            xp.datacache[button.dataref] = 0
        xp.buttonref_ids[button] = ref_id
    print("done")


def ecam32_create_events(xp, usb_mgr, display_mgr):
    buttons_last = 0
    while True:
        if not xplane_connected:
            sleep(1)
            continue

        values_processed.set()
        sleep(0.01)
        try:
            # hidapi reads by report length, not by endpoint address.
            data_in = usb_mgr.device.read(64)
        except Exception as error:
            print(f"[ECAM32] continue after usb-in error: {error}")
            sleep(0.5)
            continue

        if len(data_in) not in VALID_REPORT_LENGTHS:
            if len(data_in) != 0:
                print(f"[ECAM32] rx data count {len(data_in)} not yet supported")
            continue
        if len(data_in) < REPORT_BUTTON_OFFSET + REPORT_BUTTON_BYTES:
            continue

        buttons = 0
        for i in range(REPORT_BUTTON_BYTES):
            buttons |= data_in[REPORT_BUTTON_OFFSET + i] << (8 * i)

        for i in range(BUTTONS_CNT):
            mask = 0x01 << i
            if xor_bitmask(buttons, buttons_last, mask):
                if buttons & mask:
                    buttons_press_event[i] = 1
                else:
                    buttons_release_event[i] = 1
                ecam_button_event(usb_mgr)
        buttons_last = buttons


class device:
    def __init__(self, UDP_IP=None, UDP_PORT=None):
        self.usb_mgr = None
        self.cyclic = Event()
        self.xp = xp_websocket.XP_Websocket()

    def connected(self):
        global xplane_connected
        global xp
        print("[ECAM32] X-Plane connected")
        xplane_get_dataref_ids(self.xp)
        print("[ECAM32] subscribe datarefs ... ", end="")
        t = Thread(target=self.xp.datarefs_subscribe, args=(self.xp.led_dataref_ids, xplane_ws_listener))
        t.start()
        print("done")
        xplane_connected = True
        xp = self.xp

    def disconnected(self):
        global xplane_connected
        xplane_connected = False
        print("[ECAM32] X-Plane disconnected")

    def init_device(self, version: str = None, new_version: str = None):
        global display_manager

        self.version = version
        self.new_version = new_version

        self.usb_mgr = UsbManager()
        vid, pid, _device_config = self.usb_mgr.find_device()
        if pid is None:
            print("[ECAM32] No compatible WINCTRL device found, quit")
            return

        self.usb_mgr.connect_device(vid=vid, pid=pid)
        self.display_mgr = DisplayManager(self.usb_mgr.device)
        self.display_mgr.startupscreen(self.version, self.new_version)
        display_manager = self.display_mgr

        create_button_list_ecam32()

        usb_event_thread = Thread(target=ecam32_create_events, args=[self.xp, self.usb_mgr, self.display_mgr])
        usb_event_thread.start()
