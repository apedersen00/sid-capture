# SID Capture

Capture SID register writes from C64 music and convert them to [TT6581](https://github.com/apedersen00/tt6581) stimulus files.

This project emulates a 6502 CPU (via [py65emu](https://github.com/docmarionum1/py65emu)) to run SID player routines, logs every write to the SID address range (`$D400`-`$D41F`), and translates the output into a format suitable for driving a TT6581 SID core.

**The assembly files are from this repository:** [c64_6581_sid_players](https://github.com/realdmx/c64_6581_sid_players).

## How it works

1. **Assemble:** Build a `.sid` binary from a 6502 assembly source using ACME.

2. **Capture:** `sid_capture.py` loads the `.sid` file into the emulator, runs it for a given number of frames, and writes all SID register writes to a CSV file.

3. **Translate:** `sid_to_tt6581.py` reads the CSV and produces a TT6581 stimulus file, converting SID frequency values and control registers to match the TT6581 interface.

## Usage

Assemble the player source:

```bash
acme -o sid/Hubbard_Rob_Monty_on_the_Run.sid asm/Hubbard_Rob_Monty_on_the_Run.asm
```

Capture SID writes (here for 18 000 frames):

```bash
python sid_capture.py sid/Hubbard_Rob_Monty_on_the_Run.sid -n 18000
```

Convert the capture to a TT6581 stimulus file:

```bash
python sid_to_tt6581.py data/Hubbard_Rob_Monty_on_the_Run_capture.csv
```
