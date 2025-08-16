#!./myenv/bin/python3
from enum import Enum, IntEnum

from threading import Thread
from time import sleep

import serial
import serial.tools.list_ports as list_ports

MOBIFLIGHT_SERIAL = "SN-301-533"  # currently not used

class MF:
    # mobiflight returns inputs on every change, e.g: b'28,Analog InputA0,1001;\r\n'
    def __init__(self, port):
        self.ser = serial.Serial(port, 115200, timeout=1)
        self.init = True

        self.activated = False
        self.serialnumber = None

        self.value_changed_cb = None

        self.pinlist = []


    #@unique
    class CMD(Enum):
        SET_PIN = 2
        STATUS = 5
        BUTTON_CHANGE = 7
        GET_INFO = 9
        INFO = 10
        GET_CONFIG = 12
        CONFIG_ACTIVATED = 17
        ANALOG_CHANGE = 28
        INPUT_SHIFTER_CHANGE = 29
        DIGINMUX_CHANGE = 30
    
    class PINS:
        def __init__(self, name, port, pin):
            self.name = name
            self.port = port
            self.pin = pin
        def __str__(self):
            return(f"{self.name}: {self.port}.{self.pin}")
    

    def start(self, serialnumber, value_changed_cb):
        self.value_changed_cb = value_changed_cb

        self.rx_thread = Thread(target=self.__receive)
        self.rx_thread.start()

        tx_thread = Thread(target=self.__startup_device)
        tx_thread.start()
        tx_thread.join(timeout=4)

        if self.serialnumber == serialnumber:
            print("correct mobiflight serial:{serialnumber}")
            return True

        return False


    def serial_ports():
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
            cmd = self.CMD(int(msg_split[0]))

            if cmd == self.CMD.CONFIG_ACTIVATED and msg_split[1] == 'OK':
                self.activated = True
            
            if not self.activated:
                return

            if cmd == self.CMD.INFO and len(msg_split) == 6:
                self.serialnumber = msg_split[3]
                print(f"found serial number: {self.serialnumber}")
            
            if cmd == self.CMD.INFO and len(msg_split) == 2: # return from GET_CONFIG
                print(f"{cmd} {msg_split[1:]}")
                pins = msg_split[1].split(':')
                for p in pins:
                    pd = p.split('.')
                    if len(pd) <= 1:
                        continue
                    newpin = self.PINS(pd[-1],pd[0],pd[1]) # check with mux devices, same parts are missing
                    self.pinlist.append(newpin)
                    print(newpin)

            if cmd in [self.CMD.ANALOG_CHANGE, 
                       self.CMD.INPUT_SHIFTER_CHANGE, 
                       self.CMD.DIGINMUX_CHANGE,
                       self.CMD.BUTTON_CHANGE]:
                #print(f"CHANGE:{cmd} {msg_split[1:]}")
                if self.value_changed_cb:
                    self.value_changed_cb(msg_split[1],msg_split[2])
    

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
        #if self.activated:
        #    print(f"send {msg}")
        self.ser.write(bytearray(msg, 'ascii'))
    

    def set_pin(self, name, value):
        for p in self.pinlist:
            if p.name == name:
                self.__send_command(self.CMD.SET_PIN, [p.pin, value])
                return
        print(f"pin {name} not found")
    

    def close(self):
        self.init = False
        self.activated = False
        self.rx_thread.join() # whait for rx thread ended
        self.serialnumber = None
        self.ser.close()


def mf_value_changed(pin, value):
    print(f"Value changed: {pin}, {value}")


print("find mobiflight devices:")
ports = MF.serial_ports()
for port_mf in ports:
    print(f"testing {port_mf}")
    mf = MF(port_mf.device)
    if mf.start( MOBIFLIGHT_SERIAL, mf_value_changed):
        break
    else:
        mf.close()
        mf = None

if not mf:
    print("No mobiflight device found")
    exit()

mf.set_pin("Led", 255)
sleep(2)
mf.set_pin("Led", 0)

sleep(10)
mf.close()
print("-- END --")
