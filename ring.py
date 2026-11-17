import machine
import time

left = machine.Pin(26, machine.Pin.OUT)
right = machine.Pin(27, machine.Pin.OUT)

millis = lambda: int(round(time.time() * 1000))
usleep = lambda x: time.sleep(x/1000000.0)

def ring(hz, seconds):
  halfPeriod = 500000.0 / hz;

  endTime = millis() + seconds * 1000;

  while (millis() < endTime):
    left.on()
    right.off()
    usleep(halfPeriod);

    right.on()
    left.off()
    usleep(halfPeriod);

while True:
  # ring(16.7, 2);
  # time.sleep(1000);

  # ring(20, 2);
  # time.sleep(1000);

  # ring(25, 2);
  # time.sleep(1000);

  # ring(30, 2);
  # time.sleep(1000);

  # ring(33, 2);
  time.sleep(2);
  print(55)
  ring(55, 2);
  time.sleep(2);
  print(60)
  ring(60, 2);
