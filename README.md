# Science-CLI
A simple CLI for interacting with the science station

## Setup instructions

This assumes that python3 is installed on the system.

1. Clone this repo onto the jetson and `cd` into it.
2. `python3 -m venv venv`
3. `source venv/bin/activate`
4. `pip install -r requirements.txt`

## Running instructions

You can simply do:
```bash
./science.py
```

The script will ask for the position of the first cup. If the rover has just turned on, then this is 0. If the lazy susan has been moved around before this script is run, then you should input whatever the position was last set to.

Controls
- up/down controls drill arm
- w/s controls drill
- left/right will move the lazy susan by one slot in the corresponding direction
