#!./myenv/bin/python3
from enum import Enum, IntEnum

from threading import Thread
from time import sleep

import serial
import serial.tools.list_ports as list_ports

class MF:
    # mobiflight returns inputs on every change, e.g: b'28,Analog InputA0,1001;\r\n'
    def __init__(self, port = None):
        if port:
            self.ser = serial.Serial(port, 115200, timeout=1)
        self.init = True

        self.activated = False
        self.serialnumber = None

        self.value_changed_cb = None

        self.pinlist = []


    #@unique
    class CMD(Enum):
        SET_MODUL = 1 # 7 segment
        SET_PIN = 2
        STATUS = 5
        BUTTON_CHANGE = 7
        GET_INFO = 9
        INFO = 10
        GET_CONFIG = 12
        CONFIG_ACTIVATED = 17
        SET_MODUL_BRIGHTNESS = 26
        SET_SHIFT_REGISTER_PINS = 27
        ANALOG_CHANGE = 28
        INPUT_SHIFTER_CHANGE = 29
        DIGINMUX_CHANGE = 30
    
    class TYPE(Enum):
        BUTTON = 1
        OUTPUT = 3
        SERVO = 6
        LCDDISPLAY_I2C = 7
        ENCODER = 8
        OUTPUT_SHIFTER = 10
        ANALOG_INPUT_DEPRECATED = 11
        INPUT_SHIFTER = 12
        MUX_DRIVER = 13
        DIG_IN_MUX = 14
        STEPPER = 15
        LED_SEGEMENT_MULTI = 16
        CUSTOM_DEVICE = 17
        ANALOG_INPUT = 18

    class DIRECTION(Enum):
        UNKNOWN = 0
        IN = 1
        OUT = 2
        BI = 3

    class PINS:
        def __init__(self, name, config):
            self.name = name
            self.config = config
            self.type = MF.TYPE(int(config[0]))
            if self.type in [MF.TYPE.BUTTON,MF.TYPE.ENCODER,
                             MF.TYPE.ANALOG_INPUT_DEPRECATED,
                             MF.TYPE.INPUT_SHIFTER,
                             MF.TYPE.DIG_IN_MUX,
                             MF.TYPE.ANALOG_INPUT]:

                self.dir = MF.DIRECTION.IN
            elif self.type == MF.TYPE.CUSTOM_DEVICE:
                self.dir = MF.DIRECTION.UNKNOWN
            else:
                self.dir = MF.DIRECTION.OUT

            self.msg_prefix = "" # must be set later when all devices are known


        def __str__(self):
            return(f"{self.name} -> {self.config} {self.type} {self.dir} set:{self.msg_prefix}")


        def is_output(self):
            return self.dir == MF.DIRECTION.OUT


        def set_output_message_prefix(self, cnt_of_type):
            if not self.is_output():
                return
            cmd = 0
            if self.type == MF.TYPE.OUTPUT_SHIFTER:
                cmd = MF.CMD.SET_SHIFT_REGISTER_PINS
                self.msg_prefix = str(cmd.value) + "," + str(cnt_of_type) + "," # pin and value follow
                return
            if self.type == MF.TYPE.OUTPUT:
                cmd = MF.CMD.SET_PIN
                self.msg_prefix = str(cmd.value) + "," # pin and value follow
            if self.type == MF.TYPE.LED_SEGEMENT_MULTI:
                cmd = MF.CMD.SET_MODUL
                self.msg_prefix = str(cmd.value) + "," # modul,submodul,string,decimal_maks,mask follow
    

    def start(self, serialnumber, value_changed_cb):
        self.value_changed_cb = value_changed_cb

        self.rx_thread = Thread(target=self.__receive)
        self.rx_thread.start()

        startup_thread = Thread(target=self.__startup_device)
        startup_thread.start()
        startup_thread.join(timeout=4)

        if self.serialnumber != None and serialnumber == None:
            print(f"   found mobiflight device {self.serialnumber} without checking it")
            return True
        if self.serialnumber == serialnumber or ():
            print(f"   found mobiflight device {serialnumber}")
            return True
        
        if not self.activated:
            print("   no mobiflight device found")
            return False

        print(f"   Not using mobiflight device {self.serialnumber}, expected {serialnumber}")
        return False


    def serial_ports(self):
        ports = list_ports.comports()
        result = []
        for p in ports:
            if p.pid != None:
                print(f"   {p.device}: {p.manufacturer} {p.vid}:{p.pid}")
                result.append(p)
        return result


    def __receive(self):
         while self.init:
            msg = self.ser.readline().decode('ascii')
            if not msg.endswith(';\r\n'):
                continue
            msg_decoded = msg.removesuffix(';\r\n')
            msg_split = msg_decoded.split(',')
            cmd = MF.CMD(int(msg_split[0]))

            if cmd == self.CMD.CONFIG_ACTIVATED and msg_split[1] == 'OK':
                self.activated = True
            
            if not self.activated:
                return

            if cmd == self.CMD.INFO and len(msg_split) == 6:
                self.serialnumber = msg_split[3]
                print(f"   received serial number: {self.serialnumber}")
            
            if cmd == self.CMD.INFO and len(msg_split) == 2: # return from GET_CONFIG
                print(f"{cmd} {msg_split[1:]}")
                pins = msg_split[1].split(':')
                for p in pins:
                    pd = p.split('.')
                    if len(pd) <= 1:
                        continue
                    newpin = self.PINS(pd[-1],pd[:-1]) # check with mux devices, same parts are missing
                    self.pinlist.append(newpin)

                # search which multipelxer number pin is
                idx_cur = 0
                for p in self.pinlist:
                    if not p.is_output():
                        idx_cur += 1
                        continue
                    #search for cont of multiplexers before
                    idx_pcnt = 0
                    cnt_type = 0
                    for pcnt in self.pinlist:
                        if idx_cur == idx_pcnt:
                            break
                        if p.type == pcnt.type:
                            cnt_type += 1
                        idx_pcnt +=1
                    p.set_output_message_prefix(cnt_type)
                    idx_cur += 1
                for p in self.pinlist:
                    print(p)


            if cmd in [self.CMD.ANALOG_CHANGE, 
                       self.CMD.INPUT_SHIFTER_CHANGE, 
                       self.CMD.DIGINMUX_CHANGE,
                       self.CMD.BUTTON_CHANGE]:
                #print(f"CHANGE:{cmd} {msg_split[1:]}")
                if self.value_changed_cb and self.serialnumber:
                    self.value_changed_cb(cmd, msg_split[1], msg_split[2:])
    

    def __startup_device(self):
        while not self.activated:
            sleep(0.2)
        self.__send_command(self.CMD.GET_INFO)
        while not self.serialnumber:
            sleep(0.2)
        self.__send_command(self.CMD.GET_CONFIG) # this returns CMD.INFO ['3.2.Output_Led2:1.3.Button_In3:11.14.5.Analog InputA0:3.13.Led:']
        while not self.pinlist:
            sleep(0.2)


    def __send_command(self, cmd, arg = None):
        if not arg:
            msg = str(cmd.value) + ";"
        else:
            msg = str(cmd.value) + "," + str(arg[0]) + "," + str(arg[1]) + ";"
        if self.activated:
            print(f"send {msg}")
        self.ser.write(bytearray(msg, 'ascii'))
    

    def set_pin(self, name, nr, value):
        for p in self.pinlist:
            #print(f"pin: {p.name} == {name}")
            if p.name == name:
                msg = p.msg_prefix + str(nr) + "," + str(value) + ";"
                #print(f"send {msg}")
                self.ser.write(bytearray(msg, 'ascii'))
                return
        if name != None:
            print(f"pin {name} not found")


    def set_modul(self, mask, value): # 7 segment display
        # wait same time - 0.02 to 0.03 sec before sending next command
        # otherwise the device will ignore the command
        # "1,0,0,xxx,16,56;" -> 16 .. comma mask
        msg = str(MF.CMD.SET_MODUL.value) + ",0,0," + str(value) + "," + str(mask[0]) + "," + str(mask[1]) + ";"
        self.ser.write(bytearray(msg, 'ascii'))


    def set_modul_brightness(self, name, nr, brightness):
        print(f"set modul brightness: {name} {nr} {brightness}")
    

    def close(self):
        self.init = False
        self.activated = False
        self.rx_thread.join() # whait for rx thread ended
        self.serialnumber = None
        self.ser.close()


def mf_value_changed(cmd, name, arg):
    if cmd == MF.CMD.BUTTON_CHANGE:
        print(f"Value changed (Button): {name}, {arg[0]}") # value
    elif cmd == MF.CMD.DIGINMUX_CHANGE:
        print(f"Value changed (DigInMux): {name}, {arg[0]}, {arg[1]}") # channel, value
    elif cmd == MF.CMD.ANALOG_CHANGE:
        print(f"Value changed (Analog): {name}, {arg[0]}") # value


def main():
    print("find mobiflight devices:")
    ports = MF.serial_ports(None)
    for port_mf in ports:
        print(f"testing {port_mf}")
        mf = MF(port_mf.device)
        if mf.start(None, mf_value_changed):
            break
        else:
            mf.close()
            mf = None

    if not mf:
        print("No mobiflight device found")
        exit()

    print("Mobiflight device startet successful")

    sleep(1)
    mf.set_pin("Led", 13, 255)
    sleep(2)
    mf.set_pin("Led", 13, 0)
    sleep(5)

    mf.close()
    print("-- END --")

if __name__ == "__main__":
    main()