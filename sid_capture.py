"""
Run a SID program in py65emu and capture all writes in the SID address range.

Outputs a file in csv file.
"""

import sys
import os
import struct
import json
import argparse

sys.path.insert(0, 'py65emu')
from py65emu.cpu import CPU
from py65emu.mmu import MMU

# SID Registers
SID_BASE = 0xD400

SID_REG_NAMES = {
    0x00: "V1_FREQ_LO",
    0x01: "V1_FREQ_HI",
    0x02: "V1_PW_LO",
    0x03: "V1_PW_HI",
    0x04: "V1_CTRL",
    0x05: "V1_AD",
    0x06: "V1_SR",
    0x07: "V2_FREQ_LO",
    0x08: "V2_FREQ_HI",
    0x09: "V2_PW_LO",
    0x0A: "V2_PW_HI",
    0x0B: "V2_CTRL",
    0x0C: "V2_AD",
    0x0D: "V2_SR",
    0x0E: "V3_FREQ_LO",
    0x0F: "V3_FREQ_HI",
    0x10: "V3_PW_LO",
    0x11: "V3_PW_HI",
    0x12: "V3_CTRL",
    0x13: "V3_AD",
    0x14: "V3_SR",
    0x15: "FC_LO",
    0x16: "FC_HI",
    0x17: "RES_FILT",
    0x18: "MODE_VOL",
}

class SidMMU(MMU):
    """
    Subclass of py65emu's MMU that captures writes to the SID.
    """

    def __init__(self, blocks):
        super().__init__(blocks)
        self.sid_regs = [0] * 0x20  # Shadow copy of SID registers
        self.write_log = []         # List of (cycle, offset, value)
        self.read_log = []          # List of (cycle, offset, value)
        self.total_cycles = 0       # Updated externally after each step

    def write(self, addr, value):
        if SID_BASE <= addr < SID_BASE + 0x20:
            offset = addr - SID_BASE
            self.sid_regs[offset] = value & 0xFF
            self.write_log.append((self.total_cycles, offset, value & 0xFF))
        else:
            super().write(addr, value)

    def read(self, addr):
        if SID_BASE <= addr < SID_BASE + 0x20:
            offset = addr - SID_BASE
            value = self.sid_regs[offset]
            self.read_log.append((self.total_cycles, offset, value))
            return value
        else:
            return super().read(addr)

def load_psid(filepath):
    """Parse a PSID/RSID file and return the metadata and code bytes."""
    with open(filepath, 'rb') as f:
        data = f.read()

    magic = data[0:4]
    if magic not in (b'PSID', b'RSID'):
        raise ValueError(f"Not a PSID/RSID file (magic={magic})")

    version      = struct.unpack('>H', data[4:6])[0]
    data_offset  = struct.unpack('>H', data[6:8])[0]
    load_addr    = struct.unpack('>H', data[8:10])[0]
    init_addr    = struct.unpack('>H', data[10:12])[0]
    play_addr    = struct.unpack('>H', data[12:14])[0]
    num_songs    = struct.unpack('>H', data[14:16])[0]
    start_song   = struct.unpack('>H', data[16:18])[0]

    # If load address is 0, it's embedded as the first 2 bytes of data (little-endian)
    code_data = data[data_offset:]
    if load_addr == 0:
        load_addr = struct.unpack('<H', code_data[0:2])[0]
        code_data = code_data[2:]

    title  = data[0x16:0x36].split(b'\x00')[0].decode('ascii', errors='replace')
    author = data[0x36:0x56].split(b'\x00')[0].decode('ascii', errors='replace')

    print(f"  Title:      {title}")
    print(f"  Author:     {author}")
    print(f"  Version:    {version}")
    print(f"  Load addr:  ${load_addr:04X}")
    print(f"  Init addr:  ${init_addr:04X}")
    print(f"  Play addr:  ${play_addr:04X}")
    print(f"  Songs:      {num_songs} (start: {start_song})")
    print(f"  Code size:  {len(code_data)} bytes")

    return {
        'load_addr': load_addr,
        'init_addr': init_addr,
        'play_addr': play_addr,
        'num_songs': num_songs,
        'start_song': start_song,
        'code': list(code_data),
        'title': title,
        'author': author,
    }

def run_sid_capture(sid_file, num_frames=500):
    """
    Run the SID player and capture all writes.

    Args:
        sid_file: Path to the assembled .sid file
        num_frames: Number of 50Hz frames to capture
    """
    print(f"Loading {sid_file}...")
    sid = load_psid(sid_file)

    load_addr = sid['load_addr']
    init_addr = sid['init_addr']
    play_addr = sid['play_addr']
    code = sid['code']
    code_len = len(code)

    CPU_FREQ = 1e6          # 1 MHz
    FRAME_RATE = 50         # The program is 50 Hz
    CYCLES_PER_FRAME = CPU_FREQ // FRAME_RATE

    # Memory Layout 
    #
    # $0000-$00FF  Zero page (RAM, used for indirect addressing)
    # $0100-$01FF  Stack (RAM)
    # $0200-$03FF  Extra RAM
    # $8000-$XXXX  Program code (ROM)
    # $D400-$D41F  SID registers (intercepted by SidMMU)
    # $E000-$E0FF  Trampoline area (ROM) - contains JSR init/play + RTS stubs

    trampoline = [0xEA] * 0x100 
    # Init trampoline at $E000
    trampoline[0x00] = 0xA9                         # LDA #imm
    trampoline[0x01] = 0xFF                         # song number
    trampoline[0x02] = 0x20                         # JSR
    trampoline[0x03] = init_addr & 0xFF             # lo
    trampoline[0x04] = (init_addr >> 8) & 0xFF      # hi
    trampoline[0x05] = 0x4C                         # JMP $E005 (infinite loop = halt)
    trampoline[0x06] = 0x05
    trampoline[0x07] = 0xE0

    # Play trampoline at $E010
    trampoline[0x10] = 0x20                         # JSR
    trampoline[0x11] = play_addr & 0xFF             # lo
    trampoline[0x12] = (play_addr >> 8) & 0xFF      # hi
    trampoline[0x13] = 0x4C                         # JMP $E013 (infinite loop = halt)
    trampoline[0x14] = 0x13
    trampoline[0x15] = 0xE0

    # Configure memory layout
    mmu = SidMMU([
        # Zero page + stack + extra RAM
        (0x0000, 0x0400, False),
        # Program code loaded at load_addr
        (load_addr, max(code_len, 0x100), False, code),
        # SID
        (0xD400, 0x0020, False),
        # Trampoline ROM
        (0xE000, 0x0100, True, trampoline),
        # Interrupt vectors at $FFFA-$FFFF
        (0xFFFA, 0x06, True, [
            0xF0, 0xE0,
            0x00, 0xE0,
            0xF0, 0xE0,
        ]),
    ])

    cpu = CPU(mmu, pc=0xE000)

    INIT_HALT = 0xE005
    PLAY_HALT = 0xE013

    print(f"\nRunning init...")
    max_init_cycles = 100000
    init_cycles = 0
    while True:
        cpu.step()
        init_cycles += cpu.cc
        mmu.total_cycles += cpu.cc
        if cpu.r.pc == INIT_HALT:
            break
    
    print(f"Init completed in {init_cycles} cycles")

    frame_boundaries = []  # Track cycle count at each frame start

    for frame in range(num_frames):
        # Reset PC to play trampoline
        cpu.r.pc = 0xE010
        frame_start_cycle = mmu.total_cycles
        frame_boundaries.append(frame_start_cycle)

        frame_cycles = 0
        while True:
            cpu.step()
            frame_cycles += cpu.cc
            mmu.total_cycles += cpu.cc
            if cpu.r.pc == PLAY_HALT:
                break

        remaining = CYCLES_PER_FRAME - frame_cycles
        if remaining > 0:
            mmu.total_cycles += remaining

    total_writes = len(mmu.write_log)
    total_reads = len(mmu.read_log)
    print(f"\nDone...")
    print(f"  Total cycles:  {mmu.total_cycles}")
    print(f"  Total writes:  {total_writes}")
    print(f"  Total reads:   {total_reads}")

    return {
        'metadata': {
            'title': sid['title'],
            'author': sid['author'],
            'init_addr': init_addr,
            'play_addr': play_addr,
            'load_addr': load_addr,
            'cpu_freq': CPU_FREQ,
            'frame_rate': FRAME_RATE,
            'cycles_per_frame': CYCLES_PER_FRAME,
            'num_frames': num_frames,
            'total_cycles': mmu.total_cycles,
        },
        'writes': mmu.write_log,
        'reads': mmu.read_log,
        'frame_boundaries': frame_boundaries,
    }

def save_csv(result, output_path):
    with open(output_path, 'w') as f:
        f.write("cycle,sid_offset,value,reg_name\n")
        for cycle, offset, value in result['writes']:
            name = SID_REG_NAMES.get(offset, f"REG_{offset:02X}")
            f.write(f"{cycle},0x{offset:02X},0x{value:02X},{name}\n")

def main():
    parser = argparse.ArgumentParser(
        description='Run a SID player in py65emu and capture SID register writes')
    parser.add_argument('sid_file', help='Path to .sid file')
    parser.add_argument('-n', '--frames', type=int, default=500,
                        help='Number of 50Hz frames to emulate (default: 500 = 10s)')
    parser.add_argument('-o', '--output', default=None,
                        help='Output CSV file path (default: <sid_name>_capture.csv)')

    args = parser.parse_args()

    if args.output is None:
        base = os.path.splitext(os.path.basename(args.sid_file))[0]
        args.output = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'data',
            f'{base}_capture.csv')

    result = run_sid_capture(args.sid_file, num_frames=args.frames)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    print(f"\nSaving capture data...")
    save_csv(result, args.output)

if __name__ == '__main__':
    main()
