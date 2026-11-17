import machine
from machine import SDCard, I2S
import time
import os
import math
import micropython

sd_cs = machine.Pin(16)
sd_mosi = machine.Pin(17)
sd_clk = machine.Pin(5)
sd_miso = machine.Pin(18)

mic_sd = machine.Pin(14)
mic_sck = machine.Pin(27)
mic_ws = machine.Pin(26)

amp_din = machine.Pin(25)
amp_clk = machine.Pin(21)
amp_lrc = machine.Pin(4)
amp_sd_mode = machine.Pin(19,machine.Pin.OUT)
amp_sd_mode.on()

phone_state_trigger = machine.Pin(13, machine.Pin.IN, machine.Pin.PULL_DOWN)
ring_number_pin = machine.Pin(22, machine.Pin.IN, machine.Pin.PULL_DOWN)

SAMPLE_RATE = 16000
BITS_PER_SAMPLE = 16
BUFFER_LENGTH = 4096
DEBOUNCE_TIME = 1000 # in ms
DEV_MODE = "DEV"

def write_sample_cos_wave():
    SAMPLE_RATE = 2000
    num_samples = SAMPLE_RATE * 5
    wav_header = write_wav_header(sample_rate=SAMPLE_RATE, bit_per_sample=BITS_PER_SAMPLE, channels=1, num_samples=num_samples)

    with open("/sd/test1.wav", "w") as f:
        f.write(wav_header)

        samples = bytearray(num_samples * 2)
        for i in range(num_samples):
            angle = 2 * math.pi * 440 * i / SAMPLE_RATE
            value = int(32767 * math.cos(angle))
            samples[i*2:i*2+2] = value.to_bytes(2, "little")

        f.write(samples)
        print("done")
        f.close()

def write_wav_header(sample_rate, bit_per_sample, channels, num_samples):
    log("WRITING WAV HEADER")
    datasize = num_samples * channels * bit_per_sample // 8
    o = bytes("RIFF", "ascii")
    o += (datasize+44).to_bytes(4, "little")
    o += bytes("WAVE", "ascii")
    o += bytes("fmt ", "ascii")
    o += (16).to_bytes(4, "little")
    o += (1).to_bytes(2, "little")
    o += (channels).to_bytes(2, "little")
    o += (sample_rate).to_bytes(4, "little")
    o += (sample_rate * bit_per_sample * channels).to_bytes(4, "little")
    o += (bit_per_sample * channels).to_bytes(2, "little")
    o += (bit_per_sample).to_bytes(2, "little")
    o += bytes("data", "ascii")
    o += (datasize).to_bytes(4, "little")

    log("DONE WRITING WAV HEADER")
    return o


def clear_sd():
    log("CLEAR_SD CALLED")
    for name, type_, *_ in os.ilistdir("/sd"):
        if type_ == 0x8000:
            log(f'deleted /sd/{name}')
            os.remove(f'/sd/{name}')
    log("CLEAR_SD DONE")

def init_sd_card(retries=3):
    if "sd" in os.listdir("/"):
        os.umount("/sd")
    for attempt in range(retries):
        sd_card = SDCard(slot=2, sck=sd_clk, miso=sd_miso, mosi=sd_mosi, cs=sd_cs)
        try:
            os.mount(sd_card, "/sd")
            log("sd init")
            return sd_card
        except OSError as e:
            log("sd init failed", e, "attempt", attempt + 1)
            sd_card.deinit()
            time.sleep_ms(200)
    return None

# INMP441
def init_mic():
    audio_in = I2S(0,
            sck=mic_sck, ws=mic_ws, sd=mic_sd,
            mode=I2S.RX,
            bits=BITS_PER_SAMPLE,
            format=I2S.MONO,
            rate=SAMPLE_RATE,
            ibuf=BUFFER_LENGTH)


    if audio_in:
       log("INMP441 init") 
    else:
       log("INMP441 init failed") 

    return audio_in

# max98357a
def init_amp():
    audio_out = I2S(1, 
            sck=amp_clk, 
            sd=amp_din, 
            ws=amp_lrc,
            mode=I2S.TX,
            bits=BITS_PER_SAMPLE,
            format=I2S.MONO,
            rate=SAMPLE_RATE,
            ibuf=BUFFER_LENGTH)
    if audio_out:
       log("max98357a init")
    else:
       log("max98357a init failed")

    return audio_out


class HangUp(Exception):
    pass

def check_hangup():
    if not phone_state_trigger.value():
        raise HangUp()

def record_msg(path, seconds=5):
    log(f"record_msg start: {path}, {seconds}s")
    total = SAMPLE_RATE * BITS_PER_SAMPLE // 8 * seconds
    written = 0
    with open(path, "wb") as f:
        f.write(write_wav_header(SAMPLE_RATE, BITS_PER_SAMPLE, 1, 0))
        while written < total:
            check_hangup()
            n = mic.readinto(buf)
            n = min(n, total - written)
            f.write(buf[:n])
            written += n
        f.seek(0)
        f.write(write_wav_header(SAMPLE_RATE, BITS_PER_SAMPLE, 1, written // 2))
    log(f"recorded {written} bytes")


def play_msg(msg):
    log(f"play_msg start: {msg}")
    with open(f"/sd/{msg}", "rb") as f:
        f.seek(44)
        while True:
            check_hangup()
            n = f.readinto(buf)
            if not n:
                break
            amp.write(buf[:n])
        log(f"{msg} done")

def ring_value_debounce(called_time, DEBOUNCE=DEBOUNCE_TIME)->bool:
    # return time.time() >= called_time+DEBOUNCE_TIME
    log(f"debounce passed: {time.ticks_diff(time.ticks_ms(), called_time) >= DEBOUNCE}")
    log(f"debounce elapsed ms: {time.ticks_diff(time.ticks_ms(), called_time)}")
    return time.ticks_diff(time.ticks_ms(), called_time) >= DEBOUNCE

def log(*args):
    message = " ".join(str(a) for a in args)
    if DEV_MODE == "DEV":
        print(message)
    if DEV_MODE == "PROD":
        with open("/sd/logs/logs.txt", "a") as f:
            f.write(f"{time.localtime()} - {message}\n")

def reset(_):
    machine.reset()

sd_card = init_sd_card()
# clear_sd()

mic = init_mic()
amp = init_amp()

buf = bytearray(BUFFER_LENGTH)

recording = False
playing = False
byte_count = 0
f_record = None
f_play = None
recording_no = 1
ring_number = 0
flow_state = "START"
prev = 0
ring_value_input_time = time.ticks_ms()

def run_flow():
    global flow_state, recording, recording_no, ring_number, prev, ring_value_input_time, buf
    is_phone_up = phone_state_trigger.value()
    log(f"is_phone_up: {is_phone_up}, flow_state: {flow_state}")
    buf = bytearray(BUFFER_LENGTH)
    time_start = time.time()
    if flow_state == "START" and is_phone_up and not recording:
        log("is_phone_up and not recording")
        play_msg("menu/1_msg.wav")
        play_msg("menu/select_0_to_listen.wav")
        flow_state = "AWAITING_RESPONSE_LISTEN"
    if flow_state == "AWAITING_RESPONSE_LISTEN" and is_phone_up and not recording and ring_number == 10 and ring_value_debounce(ring_value_input_time, 2):
        log("listening...")
        ring_number = 0
        recording = False
        latest_message = os.listdir("/sd/user_recorded")[-1]
        log(f"latest message: {latest_message}")
        play_msg(f"user_recorded/{latest_message}")
        play_msg("menu/select_1_to_msg.wav")
        play_msg("menu/message_recevier.wav")
        flow_state = "AWAITING_RESPONSE_RECORD"
    if flow_state == "AWAITING_RESPONSE_RECORD" and is_phone_up and not recording and ring_number == 1 and ring_value_debounce(ring_value_input_time, 2):
        log("recording...")
        ring_number = 0
        recording = True
        recording_no += 1
        record_msg(f"/sd/user_recorded/message_{recording_no}.wav")
        log(f'recording message_{recording_no} ended')
        play_msg("menu/bye_bye.wav")
        flow_state = "END"
    if is_phone_up == 0:
        log("call end")
        flow_state = "START"
        recording = False
        ring_number = 0
    if flow_state == "AWAITING_RESPONSE_LISTEN" or flow_state == 'AWAITING_RESPONSE_RECORD':
        log(f"current ring value: {ring_number}")
        # log("ring_number_pin.value()", ring_number_pin.value())
        # log("ring_value_input_time", ring_value_input_time)
        # log("ring_value_debounce(ring_value_input_time)", ring_value_debounce(ring_value_input_time))
        # log("prev", prev)
        if ring_number_pin.value() == 1:
            prev = 1
            pass
            
        if ring_number_pin.value() == 0 and prev == 1:
            prev = 0
            ring_number += 1
            log(f"ring pulse counted, ring_number: {ring_number}")
            ring_value_input_time = time.ticks_ms()

        if ring_number_pin.value() == 1 and prev == 1 and ring_value_debounce(ring_value_input_time):
            log("ring_number reset by debounce")
            ring_number = 0

while True:
    try:
        run_flow()
    except HangUp:
        log("hang up during playback/recording, back to START")
        flow_state = "START"
        recording = False
        ring_number = 0
        prev = 0
        amp.deinit()
        amp = init_amp()
        time.sleep_ms(300)  # let the hook contact settle


