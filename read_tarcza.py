import machine
import time

read_clock = machine.Pin(12, machine.Pin.IN)
counter = 0
prev = 0

while True:
  if read_clock.value() == 1:
    prev = 1

  if read_clock.value() == 0 and prev == 1:
    prev = 0
    counter += 1
  print("counter", counter, read_clock.value())
