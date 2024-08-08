#!venv/bin/python
import argparse
import asyncio
import functools
import typing
from contextlib import contextmanager

import can
import sshkeyboard

N_SLOTS = 12
DRILL_ARM_POWER = 0.5
DRILL_POWER = 1.0

MOTOR_GROUP = 0x4
SCIENCE_GROUP = 0x7
SCIENCE_SERIAL = 0x1
DRILL_ARM_SERIAL = 0xD
DRILL_SERIAL = 0xE

PANO_CAM_SERVO_ID = 0x6
SAMPLE_CUP_SERVO_ID = 0x7

SAMPLE_CUP_POSITIONS = [0, 60, 120]
PANO_SPEED = 45 # deg/s
PANO_MIN_MAX = (0, 180)

SENSOR_TELEM_TYPES = [0x16]

# associates serial with cyclic send task
can_resend_tasks: typing.Dict[int, can.CyclicSendTaskABC] = {}
# tracked by the pano control ask
pano_servo_setpoint = 0
pano_servo_speed = 0


class MockBus(can.BusABC):
    def __init__(self):
        super().__init__(channel="foobar")

    def send(self, message, timeout=None):
        print([hex(x) for x in message.data])

    def _recv_internal(self, timeout=None):
        return None


def construct_can_id(group, serial):
    assert serial == (0b111111 & serial)
    assert group == (0b1111 & group)
    return (1 << 10) | (group << 6) | serial


def construct_pwm_packet_data(power):
    power_int = int(round((2**15 - 1) * power))
    return [0x3, 0xFF & (power_int >> 8), 0xFF & power_int]


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nocan", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def set_servo_pos(bus: can.Bus, servo_id, pos):
    assert isinstance(pos, int) and pos >= 0 and (0xFF & pos) == pos, pos
    data = [0x0D, servo_id, pos]
    can_id = construct_can_id(SCIENCE_GROUP, SCIENCE_SERIAL)
    message = can.Message(arbitration_id=can_id, is_extended_id=False, data=data)
    bus.send(message)


def set_motor_power(bus: can.Bus, serial, power):
    if serial in can_resend_tasks:
        can_resend_tasks[serial].stop()
        del can_resend_tasks[serial]
    can_id = construct_can_id(MOTOR_GROUP, serial)
    data = construct_pwm_packet_data(power)
    message = can.Message(arbitration_id=can_id, is_extended_id=False, data=data)
    if power != 0.0:
        can_resend_tasks[serial] = bus.send_periodic(message, 0.5, store_task=False)
    else:
        bus.send(message)

def send_telem_pull(bus: can.Bus, group, serial, telem_type):
    can_id = construct_can_id(group, serial)
    data = [0xf5, 0x2, 0x1, telem_type]
    message = can.Message(arbitration_id=can_id, is_extended_id=False, data=data)
    bus.send(message)


def init_motors(bus: can.Bus):
    for serial in [DRILL_ARM_SERIAL, DRILL_SERIAL]:
        can_id = construct_can_id(MOTOR_GROUP, serial)
        data = [0x0, 0x0]
        message = can.Message(arbitration_id=can_id, is_extended_id=False, data=data)
        bus.send(message)


async def pano_control_task(bus: can.Bus):
    ctrl_hz = 5
    while True:
        await asyncio.sleep(1 / ctrl_hz)
        global pano_servo_setpoint
        setpoint = max(PANO_MIN_MAX[0], min(PANO_MIN_MAX[1], pano_servo_setpoint + pano_servo_speed / ctrl_hz))
        if setpoint != pano_servo_setpoint:
            pano_servo_setpoint = setpoint
            set_servo_pos(bus, PANO_CAM_SERVO_ID, int(pano_servo_setpoint))


async def key_pressed(args, bus: can.Bus, key: str):
    if args.debug:
        print(f"Pressed: {key}")
    if key == "w" or key == "s":
        power = DRILL_ARM_POWER * (1 if key == "w" else -1)
        set_motor_power(bus, DRILL_ARM_SERIAL, power)
    elif key == " " or key == "z":
        power = DRILL_POWER * (1 if key == " " else -1)
        set_motor_power(bus, DRILL_SERIAL, power)
    elif key == "left" or key == "right":
        global pano_servo_speed
        pano_servo_speed = PANO_SPEED * (1 if key == "right" else -1)
    elif key.isdigit():
        cup_idx = int(key) - 1
        if 0 <= cup_idx < len(SAMPLE_CUP_POSITIONS):
            set_servo_pos(bus, SAMPLE_CUP_SERVO_ID, SAMPLE_CUP_POSITIONS[cup_idx])
        else:
            print(f"Invalid cup index: {cup_idx}")
    elif key == " ":
        for telem_type in SENSOR_TELEM_TYPES:
            send_telem_pull(bus, SCIENCE_GROUP, SCIENCE_SERIAL, telem_type)


async def key_released(args, bus, key):
    if args.debug:
        print(f"Released: {key}")
    if key == "up" or key == "down":
        set_motor_power(bus, DRILL_ARM_SERIAL, 0.0)
    elif key == "w" or key == "s":
        set_motor_power(bus, DRILL_SERIAL, 0.0)
    elif key == "left" or key == "right":
        global pano_servo_speed
        pano_servo_speed = 0

def telem_callback(msg: can.Message):
    data = msg.data
    if data[0] == 0xf6:
        value = int.from_bytes(bytes(data[-4:]), byteorder="big")
        print(f"({data[1]:x}, {data[2]:x}): telem type={data[3]:x}, sensor reading={value}")

@contextmanager
def get_bus(args):
    if args.nocan:
        with MockBus() as bus:
            yield bus
    else:
        with can.Bus(channel="can0", interface="socketcan") as bus:
            yield bus


@contextmanager
def create_notifier(bus: can.BusABC):
    notifier = can.Notifier(bus, [], loop=asyncio.get_running_loop())
    yield notifier
    notifier.stop()

async def main():
    args = get_args()

    with get_bus(args) as bus:
        with create_notifier(bus) as notifier:
            notifier.add_listener(telem_callback)
            init_motors(bus)

            # initialize servos
            set_servo_pos(bus, SAMPLE_CUP_SERVO_ID, SAMPLE_CUP_POSITIONS[0])
            set_servo_pos(bus, PANO_CAM_SERVO_ID, (PANO_MIN_MAX[0] + PANO_MIN_MAX[1]) // 2)
            global pano_servo_setpoint
            pano_servo_setpoint = (PANO_MIN_MAX[0] + PANO_MIN_MAX[1]) // 2

            asyncio.create_task(pano_control_task(bus))

            async def press_callback(key):
                return await key_pressed(args, bus, key)
            async def release_callback(key):
                return await key_released(args, bus, key)

            await sshkeyboard.listen_keyboard_manual(
                on_press=press_callback,
                on_release=release_callback,
                sequential=True,
                delay_second_char=0.05,
            )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
