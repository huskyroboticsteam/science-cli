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
DRILL_ARM_SERIAL = 0xC
DRILL_SERIAL = 0xD

# associates serial with cyclic send task
can_resend_tasks: typing.Dict[int, can.CyclicSendTaskABC] = {}

# position of the first cup, in the range [0, N_SLOTS)
first_cup_idx = None


class MockBus(can.BusABC):
    def __init__(self):
        super().__init__(channel="foobar")

    def send(self, message, timeout=None):
        print(message.dlc, message.data)

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


def move_cup(bus: can.Bus, cup_idx):
    print(f"Moving first cup to slot {first_cup_idx}")
    assert cup_idx == (cup_idx & 0xFF)
    data = [0xC, cup_idx]
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
    global first_cup_idx
    if args.debug:
        print(f"Pressed: {key}")
    if key == "up" or key == "down":
        power = DRILL_ARM_POWER * (1 if key == "up" else -1)
        set_motor_power(bus, DRILL_ARM_SERIAL, power)
    elif key == "w" or key == "s":
        power = DRILL_POWER * (1 if key == "w" else -1)
        set_motor_power(bus, DRILL_SERIAL, power)
    elif key == "right":
        first_cup_idx += 1
        if first_cup_idx == N_SLOTS:
            first_cup_idx = 0
        move_cup(bus, first_cup_idx)
    elif key == "left":
        first_cup_idx -= 1
        if first_cup_idx == -1:
            first_cup_idx = N_SLOTS - 1
        move_cup(bus, first_cup_idx)


async def key_released(args, bus, key):
    if args.debug:
        print(f"Released: {key}")
    if key == "up" or key == "down":
        set_motor_power(bus, DRILL_ARM_SERIAL, 0.0)
    elif key == "w" or key == "s":
        set_motor_power(bus, DRILL_SERIAL, 0.0)


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

    global first_cup_idx
    while first_cup_idx is None:
        try:
            first_cup_idx = int(input("What is the position of the first cup? "))
            if not 0 <= first_cup_idx < N_SLOTS:
                first_cup_idx = None
                print(f"Valid slots are in between 0 and {N_SLOTS-1}. Try again.")
        except ValueError:
            print("Invalid input! Try again.")

    with get_bus(args) as bus:
        init_motors(bus)
        press_callback = functools.partial(key_pressed, args, bus)
        release_callback = functools.partial(key_released, args, bus)
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
