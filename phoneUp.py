import machine
import time 

control = machine.Pin(26, machine.Pin.OUT)
isPhoneUp = machine.Pin(27, machine.Pin.IN, machine.Pin.PULL_DOWN)


control.on()

while True:
  if isPhoneUp.value() == 0:
    print("phone down")
  if isPhoneUp.value() == 1:
    print("phone up")