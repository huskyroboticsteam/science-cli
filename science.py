#!venv/bin/python
import argparse
import asyncio
import functools
import typing
from contextlib import contextmanager

import can
import sshkeyboard

N_SLOTS = 18
CONVEYOR_BELT_PIVOTS = 5

DRILL_ARM_POWER = 0.5
DRILL_POWER = 1.0
#CONVEYOR_BELT_POWER = 0


#CONVEYOR_OFFSET = 10
#CONVEYOR_SLOPE = 8
CONVEYOR_POSITIONS = ['58', '77', '99', '118', '143', '30']

LAZY_SUSAN_OFFSET = 0
LAZY_SUSAN_SLOPE = 9.13

SENSOR_TELEM = 0x16

TELEM_PULL_ID = 0xF5
TELEM_REQUEST_ID = 0xF6

MOTOR_GROUP = 0x4
SCIENCE_GROUP = 0x7
SCIENCE_SERIAL = 0x1
DRILL_ARM_SERIAL = 0xC
DRILL_SERIAL = 0xD

SCIENCE_SERVO_CONT = 0x0E
SCIENCE_SERVO_SET = 0x0D

CAN_CONVEYOR_BELT_CONT = 0x4
CAN_CONVEYOR_BELT_PIVOT = 0x0
CAN_SCIENCE_SERVO_LAZY_SUSAN = 0x0

# conveyor belt pivot servo positions
# conveyor belt continuous turn
# lazy susan find fixed positions on continuous servo

# associates serial with cyclic send task
can_resend_tasks: typing.Dict[int, can.CyclicSendTaskABC] = {}

# position of the first cup, in the range [0, N_SLOTS)
first_cup_idx = None
first_cup_pos = None

conveyor_belt_idx = None
conveyor_belt_pivot = None

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


def set_servo_pos(bus: can.Bus, servo_id, pos):
    assert isinstance(pos, int)
    data = [0x0D, servo_id, pos]
    can_id = construct_can_id(SCIENCE_GROUP, SCIENCE_SERIAL)
    message = can.Message(arbitration_id=can_id, is_extended_id=False, data=data)
    bus.send(message)

def set_servo_power(bus: can.Bus, servo_id, power):
    assert isinstance(power, int)
    power_int = int(power)
    data = [0x0E, servo_id, power_int]
    can_id = construct_can_id(SCIENCE_GROUP, SCIENCE_SERIAL)
    message = can.Message(arbitration_id=can_id, is_extended_id=False, data=data)
    bus.send(message)

# to delete since using set_servo_pos:
"""def move_cup(bus: can.Bus, cup_idx):
    print(f"Moving first cup to slot {first_cup_idx}")
    assert cup_idx == (cup_idx & 0xFF)
    data = [0xC, cup_idx]
    can_id = construct_can_id(SCIENCE_GROUP, SCIENCE_SERIAL)
    message = can.Message(arbitration_id=can_id, is_extended_id=False, data=data)
    bus.send(message)"""


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


def get_sensor_reading(bus: can.Bus):
    # send telem pull
    data = [TELEM_PULL_ID, SCIENCE_GROUP, SCIENCE_SERIAL, SENSOR_TELEM]
    can_id = construct_can_id(SCIENCE_GROUP, SCIENCE_SERIAL)
    message = can.Message(arbitration_id=can_id, is_extended_id=False, data=data)
    bus.send(message)
    # send telem request
    #print sensor reading to console"""


def init_motors(bus: can.Bus):
    for serial in [DRILL_ARM_SERIAL, DRILL_SERIAL]:
        can_id = construct_can_id(MOTOR_GROUP, serial)
        data = [0x0, 0x0]
        message = can.Message(arbitration_id=can_id, is_extended_id=False, data=data)
        bus.send(message)

async def key_pressed(args, bus: can.Bus, key: str):
    global first_cup_idx
    global conveyor_belt_idx
    global first_cup_pos
    global conveyor_belt_pivot
    if args.debug:
        print(f"Pressed: {key}")
    if key == "up" or key == "down":
        power = DRILL_ARM_POWER * (1 if key == "up" else -1)
        set_motor_power(bus, DRILL_ARM_SERIAL, power)
    elif key == "w" or key == "s":
        power = DRILL_POWER * (1 if key == "w" else -1)
        set_motor_power(bus, DRILL_SERIAL, power)
    elif key == "u" or key == "j":
        #continuous conveyor belt
        power = (0 if key == "u" else 180)
        set_servo_power(bus, CAN_CONVEYOR_BELT_CONT, int(power))
    elif key == "i":
        #positional conveyor belt positive direction
        conveyor_belt_idx += 1
        if (conveyor_belt_idx >= CONVEYOR_BELT_PIVOTS or conveyor_belt_idx < 0):
            print(f"Conveyor belt index out of range. Choose different index.")
        else:
            conveyor_belt_pivot = int(CONVEYOR_POSITIONS[conveyor_belt_idx])
            print(f"Moving conveyor belt to position {conveyor_belt_idx}")
            set_servo_pos(bus, CAN_CONVEYOR_BELT_PIVOT, conveyor_belt_pivot)
    elif key == "k":
        #positional conveyor belt negative direction
        conveyor_belt_idx -= 1
        if (conveyor_belt_idx >= CONVEYOR_BELT_PIVOTS or conveyor_belt_idx < 0):
            print(f"Conveyor belt index out of range. Choose different index.")
        else:
            print(f"Moving conveyor belt to position {conveyor_belt_idx}")
            conveyor_belt_pivot = int(CONVEYOR_POSITIONS[conveyor_belt_idx])
            set_servo_pos(bus, CAN_CONVEYOR_BELT_PIVOT, conveyor_belt_pivot)
    elif key == "right":
        first_cup_idx += 1  
        if first_cup_idx == N_SLOTS:
            first_cup_idx = 0
        first_cup_pos = int(LAZY_SUSAN_SLOPE * first_cup_idx) + LAZY_SUSAN_OFFSET
        print(f"Moving first cup to slot {first_cup_idx}")
        set_servo_pos(bus, CAN_SCIENCE_SERVO_LAZY_SUSAN, int(first_cup_pos))
    elif key == "left":
        first_cup_idx -= 1 
        if first_cup_idx == -1:
            first_cup_idx = N_SLOTS - 1
        print(f"Moving first cup to slot {first_cup_idx}")
        first_cup_pos = int(LAZY_SUSAN_SLOPE * first_cup_idx) + LAZY_SUSAN_OFFSET
        set_servo_pos(bus, CAN_SCIENCE_SERVO_LAZY_SUSAN, int(first_cup_pos))
    elif key == "1":
        print(f"receiving sensor reading for _")
        get_sensor_reading(bus)

async def key_released(args, bus, key):
    if args.debug:
        print(f"Released: {key}")
    if key == "up" or key == "down":
        set_motor_power(bus, DRILL_ARM_SERIAL, 0.0)
    elif key == "w" or key == "s":
        set_motor_power(bus, DRILL_SERIAL, 0.0)
    elif key == "u" or key == "j":
        set_servo_power(bus, CAN_CONVEYOR_BELT_CONT, 90)

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

    global first_cup_idx
    global conveyor_belt_idx
    global first_cup_pos
    global conveyor_belt_pivot
    while (first_cup_idx is None) or (conveyor_belt_idx is None):
        try:
            print(first_cup_idx)
            print(conveyor_belt_idx)
            if (first_cup_idx is None):
                first_cup_idx = int(input("What is the position of the first cup? "))
                first_cup_pos = LAZY_SUSAN_SLOPE * first_cup_idx + LAZY_SUSAN_OFFSET
                if not 0 <= first_cup_idx < N_SLOTS:
                    first_cup_idx = None
                    print(f"Valid slots are in between 0 and {N_SLOTS-1}. Try again.")
            if (conveyor_belt_idx is None):
                conveyor_belt_idx= int(input("What is the position of conveyor belt? "))
                if not 0 <= conveyor_belt_idx < CONVEYOR_BELT_PIVOTS:
                    conveyor_belt_idx = None
                    print(f"Valid indices are in between 0 and {CONVEYOR_BELT_PIVOTS-1}. Try again.")
                else:
                    conveyor_belt_pivot = int(CONVEYOR_POSITIONS[conveyor_belt_idx])
        except ValueError:  
            print("Invalid input! Try again.")

    with get_bus(args) as bus:
        with create_notifier(bus) as notifier:
            notifier.add_listener(telem_callback)
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
