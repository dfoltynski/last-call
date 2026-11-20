from machine import Pin, SDCard, I2S
import time
import os
import math

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEV_MODE = "DEV"          # "DEV" -> print logs, "PROD" -> append logs to SD
VERBOSE = True            # log every loop tick (also slows polling, which acts
                          # as a crude debounce for the dial pin - keep in mind)

SAMPLE_RATE = 16000
BITS_PER_SAMPLE = 16
CHANNELS = 1
BUFFER_LENGTH = 4096
WAV_HEADER_SIZE = 44
RECORD_SECONDS = 5

DIAL_RESET_MS = 1000      # pulse count is cleared after this much idle time
DIGIT_SETTLE_MS = 2       # time after last pulse before a digit is accepted

SD_MOUNT = "/sd"
MENU_DIR = "menu"
RECORDINGS_DIR = "user_recorded"
LOG_FILE = SD_MOUNT + "/logs/logs.txt"

MSG_INTRO = MENU_DIR + "/1_msg.wav"
MSG_SELECT_LISTEN = MENU_DIR + "/select_0_to_listen.wav"
MSG_SELECT_RECORD = MENU_DIR + "/select_1_to_msg.wav"
MSG_RECORD_PROMPT = MENU_DIR + "/message_recevier.wav"
MSG_BYE = MENU_DIR + "/bye_bye.wav"

# ---------------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------------

# SD card (SPI)
sd_cs = Pin(16)
sd_mosi = Pin(17)
sd_clk = Pin(5)
sd_miso = Pin(18)

# INMP441 microphone (I2S RX)
mic_sd = Pin(27)
mic_sck = Pin(14)
mic_ws = Pin(26)

# MAX98357A amplifier (I2S TX)
amp_din = Pin(25)
amp_clk = Pin(21)
amp_lrc = Pin(4)
amp_sd_mode = Pin(19, Pin.OUT)
amp_sd_mode.on()

# Phone hardware: 1 = handset lifted
hook_pin = Pin(13, Pin.IN, Pin.PULL_DOWN)
dial_pin = Pin(22, Pin.IN, Pin.PULL_DOWN)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(*args):
    message = " ".join(str(a) for a in args)
    if DEV_MODE == "DEV":
        print(message)
    elif DEV_MODE == "PROD":
        with open(LOG_FILE, "a") as f:
            f.write(f"{time.localtime()} - {message}\n")

# ---------------------------------------------------------------------------
# Hardware init
# ---------------------------------------------------------------------------

def init_sd_card(retries=3):
    if SD_MOUNT.lstrip("/") in os.listdir("/"):
        os.umount(SD_MOUNT)
    for attempt in range(retries):
        card = SDCard(slot=2, sck=sd_clk, miso=sd_miso, mosi=sd_mosi, cs=sd_cs)
        try:
            os.mount(card, SD_MOUNT)
            log("sd init")
            return card
        except OSError as e:
            log("sd init failed", e, "attempt", attempt + 1)
            card.deinit()
            time.sleep_ms(200)
    return None


def init_mic():
    mic = I2S(0,
              sck=mic_sck, ws=mic_ws, sd=mic_sd,
              mode=I2S.RX,
              bits=BITS_PER_SAMPLE,
              format=I2S.MONO,
              rate=SAMPLE_RATE,
              ibuf=BUFFER_LENGTH)
    log("INMP441 init")
    return mic


def init_amp():
    amp = I2S(1,
              sck=amp_clk, ws=amp_lrc, sd=amp_din,
              mode=I2S.TX,
              bits=BITS_PER_SAMPLE,
              format=I2S.MONO,
              rate=SAMPLE_RATE,
              ibuf=BUFFER_LENGTH)
    log("max98357a init")
    return amp

# ---------------------------------------------------------------------------
# WAV helpers
# ---------------------------------------------------------------------------

def wav_header(sample_rate, bits_per_sample, channels, num_samples):
    block_align = channels * bits_per_sample // 8
    datasize = num_samples * block_align
    o = b"RIFF"
    o += (datasize + 36).to_bytes(4, "little")
    o += b"WAVE"
    o += b"fmt "
    o += (16).to_bytes(4, "little")                         # fmt chunk size
    o += (1).to_bytes(2, "little")                          # PCM
    o += channels.to_bytes(2, "little")
    o += sample_rate.to_bytes(4, "little")
    o += (sample_rate * block_align).to_bytes(4, "little")  # byte rate
    o += block_align.to_bytes(2, "little")
    o += bits_per_sample.to_bytes(2, "little")
    o += b"data"
    o += datasize.to_bytes(4, "little")
    return o

# ---------------------------------------------------------------------------
# SD card utilities (not used in the main flow, handy from the REPL)
# ---------------------------------------------------------------------------

def write_sample_cos_wave(path=SD_MOUNT + "/test1.wav", freq=440, seconds=5,
                          sample_rate=2000):
    """Write a test tone to the SD card."""
    num_samples = sample_rate * seconds
    samples = bytearray(num_samples * 2)
    for i in range(num_samples):
        value = int(32767 * math.cos(2 * math.pi * freq * i / sample_rate))
        samples[i * 2:i * 2 + 2] = value.to_bytes(2, "little")
    with open(path, "wb") as f:
        f.write(wav_header(sample_rate, BITS_PER_SAMPLE, CHANNELS, num_samples))
        f.write(samples)
    log("test tone written:", path)


def clear_sd():
    """Delete all files in the SD root (directories are kept)."""
    log("CLEAR_SD CALLED")
    for name, type_, *_ in os.ilistdir(SD_MOUNT):
        if type_ == 0x8000:
            os.remove(f"{SD_MOUNT}/{name}")
            log(f"deleted {SD_MOUNT}/{name}")
    log("CLEAR_SD DONE")

# ---------------------------------------------------------------------------
# Recordings
# ---------------------------------------------------------------------------

def recordings_path():
    return f"{SD_MOUNT}/{RECORDINGS_DIR}"


def recording_numbers():
    """Sorted numbers N of existing message_N.wav files."""
    nums = []
    for name in os.listdir(recordings_path()):
        if name.startswith("message_") and name.endswith(".wav"):
            try:
                nums.append(int(name[len("message_"):-len(".wav")]))
            except ValueError:
                pass
    nums.sort()
    return nums


def latest_recording():
    """Relative path (under SD) of the newest recording, or None."""
    nums = recording_numbers()
    if nums:
        return f"{RECORDINGS_DIR}/message_{nums[-1]}.wav"
    others = sorted(os.listdir(recordings_path()))
    return f"{RECORDINGS_DIR}/{others[-1]}" if others else None


def next_recording_path():
    nums = recording_numbers()
    n = nums[-1] + 1 if nums else 1
    return f"{recordings_path()}/message_{n}.wav"

# ---------------------------------------------------------------------------
# Audio I/O (aborts with HangUp as soon as the handset is put down)
# ---------------------------------------------------------------------------

class HangUp(Exception):
    pass


def check_hangup():
    if not hook_pin.value():
        raise HangUp()


def play_msg(rel_path):
    log(f"play_msg start: {rel_path}")
    with open(f"{SD_MOUNT}/{rel_path}", "rb") as f:
        f.seek(WAV_HEADER_SIZE)
        while True:
            check_hangup()
            n = f.readinto(buf)
            if not n:
                break
            amp.write(buf[:n])
    log(f"{rel_path} done")


def record_msg(path, seconds=RECORD_SECONDS):
    log(f"record_msg start: {path}, {seconds}s")
    total = SAMPLE_RATE * BITS_PER_SAMPLE // 8 * CHANNELS * seconds
    written = 0
    with open(path, "wb") as f:
        f.write(wav_header(SAMPLE_RATE, BITS_PER_SAMPLE, CHANNELS, 0))
        try:
            while written < total:
                check_hangup()
                n = min(mic.readinto(buf), total - written)
                f.write(buf[:n])
                written += n
        finally:
            # keep a valid header even if the caller hung up mid-recording
            f.seek(0)
            f.write(wav_header(SAMPLE_RATE, BITS_PER_SAMPLE, CHANNELS,
                               written // (BITS_PER_SAMPLE // 8)))
            log(f"recorded {written} bytes")

# ---------------------------------------------------------------------------
# Rotary dial
# ---------------------------------------------------------------------------

class RotaryDial:
    """Counts pulses on the dial pin by polling. Digit 0 = 10 pulses."""

    def __init__(self, pin):
        self.pin = pin
        self.count = 0
        self.prev = 0
        self.last_pulse_ms = time.ticks_ms()

    def reset(self):
        self.count = 0
        self.prev = 0

    def _since_last_pulse(self):
        return time.ticks_diff(time.ticks_ms(), self.last_pulse_ms)

    def poll(self):
        if VERBOSE:
            log(f"current ring value: {self.count}")
        value = self.pin.value()
        if value:
            self.prev = 1
            if self.count and self._since_last_pulse() >= DIAL_RESET_MS:
                log("ring_number reset by debounce")
                self.count = 0
        elif self.prev:
            self.prev = 0
            self.count += 1
            self.last_pulse_ms = time.ticks_ms()
            log(f"ring pulse counted, ring_number: {self.count}")

    def dialed(self, digit):
        pulses = 10 if digit == 0 else digit
        return self.count == pulses and self._since_last_pulse() >= DIGIT_SETTLE_MS

# ---------------------------------------------------------------------------
# Call flow
# ---------------------------------------------------------------------------

START = "START"
AWAITING_LISTEN = "AWAITING_RESPONSE_LISTEN"
AWAITING_RECORD = "AWAITING_RESPONSE_RECORD"
END = "END"


class CallFlow:
    def __init__(self, dial):
        self.dial = dial
        self.state = START

    def hang_up(self):
        if self.state != START:
            log("call end")
        self.state = START
        self.dial.reset()

    def step(self):
        off_hook = hook_pin.value()
        if VERBOSE:
            log(f"is_phone_up: {off_hook}, flow_state: {self.state}")

        if not off_hook:
            self.hang_up()
            return

        if self.state == START:
            play_msg(MSG_INTRO)
            play_msg(MSG_SELECT_LISTEN)
            self.state = AWAITING_LISTEN

        elif self.state == AWAITING_LISTEN and self.dial.dialed(0):
            log("listening...")
            self.dial.reset()
            latest = latest_recording()
            log(f"latest message: {latest}")
            if latest:
                play_msg(latest)
            play_msg(MSG_SELECT_RECORD)
            play_msg(MSG_RECORD_PROMPT)
            self.state = AWAITING_RECORD

        elif self.state == AWAITING_RECORD and self.dial.dialed(1):
            log("recording...")
            self.dial.reset()
            path = next_recording_path()
            record_msg(path)
            log(f"recording {path} ended")
            play_msg(MSG_BYE)
            self.state = END

        if self.state in (AWAITING_LISTEN, AWAITING_RECORD):
            self.dial.poll()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

sd_card = init_sd_card()
if sd_card is None:
    raise RuntimeError("SD card init failed")

try:
    os.mkdir(recordings_path())
except OSError:
    pass  # already exists

mic = init_mic()
amp = init_amp()
buf = bytearray(BUFFER_LENGTH)

flow = CallFlow(RotaryDial(dial_pin))

while True:
    try:
        flow.step()
    except HangUp:
        log("hang up during playback/recording, back to START")
        flow.hang_up()
        amp.deinit()
        amp = init_amp()           # flush any half-played audio
        time.sleep_ms(300)         # let the hook contact settle
