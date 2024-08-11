#!venv/bin/python
import argparse
import asyncio
import typing
from contextlib import contextmanager

import can
import sshkeyboard

DRILL_ARM_POWER = 0.5
DRILL_POWER = -0.2

MOTOR_GROUP = 0x4
SCIENCE_GROUP = 0x7
SCIENCE_SERIAL = 0x1
DRILL_ARM_SERIAL = 0xD
DRILL_SERIAL = 0xE

PANO_CAM_SERVO_ID = 0x7
SAMPLE_CUP_SERVO_ID = 0x3

PANO_SPEED = -10
SAMPLE_CUP_SPEED = 30

# associates serial with cyclic send task
can_resend_tasks: typing.Dict[int, can.CyclicSendTaskABC] = {}


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


def set_cont_servo_speed(bus: can.Bus, servo_id, speed):
    assert isinstance(servo_id, int) and isinstance(speed, int) and -128 <= speed <= 127
    data = [0x0E, servo_id, 0xFF & speed]
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


def init_motors(bus: can.Bus):
    for serial in [DRILL_ARM_SERIAL, DRILL_SERIAL]:
        can_id = construct_can_id(MOTOR_GROUP, serial)
        data = [0x0, 0x0]
        message = can.Message(arbitration_id=can_id, is_extended_id=False, data=data)
        bus.send(message)


async def key_pressed(args, bus: can.Bus, key: str):
    if args.debug:
        print(f"Pressed: {key}")
    if key == "w" or key == "s":
        power = DRILL_ARM_POWER * (1 if key == "w" else -1)
        set_motor_power(bus, DRILL_ARM_SERIAL, power)
    elif key == "space" or key == "z":
        power = DRILL_POWER * (1 if key == "space" else -1)
        set_motor_power(bus, DRILL_SERIAL, power)
    elif key == "left" or key == "right":
        pos = 90 + PANO_SPEED * (1 if key == "right" else -1)
        set_servo_pos(bus, PANO_CAM_SERVO_ID, pos)
    elif key == "a" or key == "d":
        pos = 90 + SAMPLE_CUP_SPEED * (1 if key == "d" else -1)
        set_servo_pos(bus, SAMPLE_CUP_SERVO_ID, pos)


async def key_released(args, bus, key):
    if args.debug:
        print(f"Released: {key}")
    if key == "space" or key == "z":
        set_motor_power(bus, DRILL_SERIAL, 0.0)
    elif key == "w" or key == "s":
        set_motor_power(bus, DRILL_ARM_SERIAL, 0.0)
    elif key == "left" or key == "right":
        set_servo_pos(bus, PANO_CAM_SERVO_ID, 90)
    elif key == "a" or key == "d":
        set_servo_pos(bus, SAMPLE_CUP_SERVO_ID, 90)


@contextmanager
def get_bus(args):
    if args.nocan:
        with MockBus() as bus:
            yield bus
    else:
        with can.Bus(channel="can0", interface="socketcan") as bus:
            yield bus


async def main():
    args = get_args()

    with get_bus(args) as bus:
        init_motors(bus)

        # initialize servos
        set_servo_pos(bus, PANO_CAM_SERVO_ID, 90)
        set_servo_pos(bus, SAMPLE_CUP_SERVO_ID, 90)

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
