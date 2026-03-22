import machine
import time

Led = machine.Pin(25, machine.Pin.OUT)

while True:
    Led.value(1)
    time.sleep(0.5)
    Led.value(0)
    time.sleep(0.5)