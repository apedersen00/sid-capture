"""
Run a SID program in py65emu and capture all writes in the SID address range.

Supports both PSID (frame-based play routine) and RSID (CIA timer IRQ-driven)
formats.

Outputs a CSV file.
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

# ---------------------------------------------------------------------------
# CIA #1 register offsets (base $DC00)
# ---------------------------------------------------------------------------
CIA_BASE      = 0xDC00
CIA_TIMER_A_LO = 0x04   # $DC04  Timer A latch / counter low byte
CIA_TIMER_A_HI = 0x05   # $DC05  Timer A latch / counter high byte
CIA_ICR        = 0x0D   # $DC0D  Interrupt Control Register
CIA_CRA        = 0x0E   # $DC0E  Control Register A

# ICR bits
ICR_TIMER_A    = 0x01   # Bit 0 — Timer A underflow
ICR_SET_CLEAR  = 0x80   # Bit 7 — 1 = set bits, 0 = clear bits  (write)
ICR_IRQ_FLAG   = 0x80   # Bit 7 — IRQ occurred                  (read)

# CRA bits
CRA_START      = 0x01   # Bit 0 — Start timer
CRA_ONE_SHOT   = 0x08   # Bit 3 — One-shot mode (1) vs continuous (0)
CRA_FORCE_LOAD = 0x10   # Bit 4 — Force load latch into counter


class SidMMU(MMU):
    """
    Subclass of py65emu's MMU that intercepts:
      - SID  registers at $D400-$D41F  (capture writes)
      - CIA1 registers at $DC00-$DC0F  (timer A emulation)
    """

    def __init__(self, blocks):
        super().__init__(blocks)

        # SID shadow / logging
        self.sid_regs = [0] * 0x20
        self.write_log = []         # (cycle, offset, value)
        self.read_log = []          # (cycle, offset, value)
        self.total_cycles = 0       # updated externally after each step

        # CIA1 Timer A state
        self.cia_timer_a_latch  = 0xFFFF  # 16-bit latch (written via $DC04/05)
        self.cia_timer_a_counter = 0xFFFF # 16-bit down-counter
        self.cia_cra   = 0x00             # Control Register A
        self.cia_icr_mask = 0x00          # ICR mask (which sources can trigger IRQ)
        self.cia_icr_data = 0x00          # ICR data (which sources have fired)
        self.irq_pending = False          # flag checked by the run-loop

    # ----- helpers ----------------------------------------------------------
    def _cia_timer_running(self):
        return bool(self.cia_cra & CRA_START)

    # ----- CIA tick — call AFTER advancing total_cycles ----------------------
    def tick_cia(self, elapsed_cycles):
        """Advance CIA Timer A by *elapsed_cycles*. Set irq_pending on underflow."""
        if not self._cia_timer_running():
            return

        remaining = elapsed_cycles
        while remaining > 0 and self._cia_timer_running():
            if remaining <= self.cia_timer_a_counter:
                self.cia_timer_a_counter -= remaining
                remaining = 0
            else:
                # underflow
                remaining -= (self.cia_timer_a_counter + 1)
                self.cia_icr_data |= ICR_TIMER_A   # flag the underflow

                if self.cia_icr_mask & ICR_TIMER_A:
                    self.cia_icr_data |= ICR_IRQ_FLAG
                    self.irq_pending = True

                if self.cia_cra & CRA_ONE_SHOT:
                    self.cia_cra &= ~CRA_START      # stop timer
                    self.cia_timer_a_counter = self.cia_timer_a_latch
                    return
                else:
                    # continuous — reload from latch
                    self.cia_timer_a_counter = self.cia_timer_a_latch

    # ----- memory intercepts ------------------------------------------------
    def write(self, addr, value):
        value &= 0xFF

        # SID $D400-$D41F
        if SID_BASE <= addr < SID_BASE + 0x20:
            offset = addr - SID_BASE
            self.sid_regs[offset] = value
            self.write_log.append((self.total_cycles, offset, value))
            return

        # CIA1 $DC00-$DC0F
        if CIA_BASE <= addr < CIA_BASE + 0x10:
            offset = addr - CIA_BASE
            if offset == CIA_TIMER_A_LO:
                self.cia_timer_a_latch = (self.cia_timer_a_latch & 0xFF00) | value
            elif offset == CIA_TIMER_A_HI:
                self.cia_timer_a_latch = (self.cia_timer_a_latch & 0x00FF) | (value << 8)
            elif offset == CIA_ICR:
                if value & ICR_SET_CLEAR:
                    self.cia_icr_mask |= (value & 0x7F)
                else:
                    self.cia_icr_mask &= ~(value & 0x7F)
            elif offset == CIA_CRA:
                if value & CRA_FORCE_LOAD:
                    self.cia_timer_a_counter = self.cia_timer_a_latch
                    value &= ~CRA_FORCE_LOAD   # bit is strobe, not stored
                self.cia_cra = value
            # Other CIA regs — ignore silently
            return

        super().write(addr, value)

    def read(self, addr):
        # SID $D400-$D41F
        if SID_BASE <= addr < SID_BASE + 0x20:
            offset = addr - SID_BASE
            value = self.sid_regs[offset]
            self.read_log.append((self.total_cycles, offset, value))
            return value

        # CIA1 $DC00-$DC0F
        if CIA_BASE <= addr < CIA_BASE + 0x10:
            offset = addr - CIA_BASE
            if offset == CIA_TIMER_A_LO:
                return self.cia_timer_a_counter & 0xFF
            elif offset == CIA_TIMER_A_HI:
                return (self.cia_timer_a_counter >> 8) & 0xFF
            elif offset == CIA_ICR:
                # Reading ICR returns current flags and clears them
                val = self.cia_icr_data
                self.cia_icr_data = 0
                self.irq_pending = False
                return val
            elif offset == CIA_CRA:
                return self.cia_cra
            return 0

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

    is_rsid = (magic == b'RSID')

    print(f"  Format:     {'RSID' if is_rsid else 'PSID'}")
    print(f"  Title:      {title}")
    print(f"  Author:     {author}")
    print(f"  Version:    {version}")
    print(f"  Load addr:  ${load_addr:04X}")
    print(f"  Init addr:  ${init_addr:04X}")
    print(f"  Play addr:  ${play_addr:04X}")
    print(f"  Songs:      {num_songs} (start: {start_song})")
    print(f"  Code size:  {len(code_data)} bytes")

    return {
        'magic': magic,
        'is_rsid': is_rsid,
        'load_addr': load_addr,
        'init_addr': init_addr,
        'play_addr': play_addr,
        'num_songs': num_songs,
        'start_song': start_song,
        'code': list(code_data),
        'title': title,
        'author': author,
    }


# ---------------------------------------------------------------------------
# Trigger a 6502 IRQ on the CPU (same sequence as BRK but B flag clear)
# ---------------------------------------------------------------------------
def trigger_irq(cpu):
    """Push PC and P onto the stack and jump to the IRQ vector."""
    if cpu.r.getFlag('I'):
        return  # IRQs masked
    cpu.r.clearFlag('B')
    cpu.stackPushWord(cpu.r.pc)
    cpu.stackPush(cpu.r.p)
    cpu.r.setFlag('I')
    cpu.r.pc = cpu.mmu.readWord(0xFFFE)


# ---------------------------------------------------------------------------
# PSID run-loop  (original behaviour — frame-based play routine)
# ---------------------------------------------------------------------------
def run_psid(cpu, mmu, play_addr, num_frames, cycles_per_frame):
    """
    For PSID files: call the play routine once per frame via a trampoline.
    """
    PLAY_HALT = 0xE013
    frame_boundaries = []

    for frame in range(num_frames):
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

        remaining = cycles_per_frame - frame_cycles
        if remaining > 0:
            mmu.total_cycles += remaining

    return frame_boundaries


# ---------------------------------------------------------------------------
# RSID run-loop  (CIA timer drives IRQs, CPU runs continuously)
# ---------------------------------------------------------------------------
def run_rsid(cpu, mmu, num_frames, cycles_per_frame):
    """
    For RSID files: run the CPU continuously. The CIA timer fires IRQs
    which the player's own ISR handles.  We still chunk time into frames
    just so we can honour the requested duration.
    """
    HALT_ADDR = 0xE005  # spin address the main-line code lands on
    frame_boundaries = []
    total_target = int(num_frames * cycles_per_frame)

    while mmu.total_cycles < total_target:
        frame_boundaries.append(mmu.total_cycles)
        frame_end = mmu.total_cycles + cycles_per_frame

        while mmu.total_cycles < frame_end:
            # Check for pending CIA IRQ before executing the next instruction
            if mmu.irq_pending and not cpu.r.getFlag('I'):
                trigger_irq(cpu)
                mmu.irq_pending = False

            cpu.step()
            elapsed = cpu.cc
            mmu.total_cycles += elapsed
            mmu.tick_cia(elapsed)

    return frame_boundaries


def run_sid_capture(sid_file, num_frames=500, song=None):
    """
    Run the SID player and capture all writes.

    Args:
        sid_file:   Path to the assembled .sid file
        num_frames: Number of 50 Hz frames to capture
        song:       Song number to play (default: start_song from header)
    """
    print(f"Loading {sid_file}...")
    sid = load_psid(sid_file)

    load_addr = sid['load_addr']
    init_addr = sid['init_addr']
    play_addr = sid['play_addr']
    is_rsid   = sid['is_rsid']
    code      = sid['code']
    code_len  = len(code)

    if song is None:
        song = sid['start_song'] - 1   # SID files are 1-based
    else:
        song = song - 1                # convert to 0-based for the A register

    CPU_FREQ = 1e6          # 1 MHz
    FRAME_RATE = 50         # PAL 50 Hz
    CYCLES_PER_FRAME = CPU_FREQ // FRAME_RATE

    # ------------------------------------------------------------------
    # Memory layout
    # ------------------------------------------------------------------
    # We allocate RAM from $0000 up to just below the SID at $D400.
    # This covers zero-page, stack, program code, and any variables
    # the player uses (e.g. Arkanoid's TEMP at $5FFF).
    # ------------------------------------------------------------------
    # $0000 - $D3FF   General RAM (code is loaded inside this region)
    # $D400 - $D41F   SID registers (intercepted by SidMMU)
    # $DC00 - $DC0F   CIA1 registers (intercepted by SidMMU)
    # $E000 - $E0FF   Trampoline ROM
    # $FFFA - $FFFF   Interrupt vectors (RAM — RSID players write here)

    ram_end = 0xD400  # exclusive — everything below is RAM

    # Build init/play trampolines at $E000
    trampoline = [0xEA] * 0x100

    # Init trampoline at $E000:  LDA #song; JSR init; JMP $E005
    trampoline[0x00] = 0xA9                         # LDA #imm
    trampoline[0x01] = song & 0xFF                  # song number
    trampoline[0x02] = 0x20                         # JSR
    trampoline[0x03] = init_addr & 0xFF             # lo
    trampoline[0x04] = (init_addr >> 8) & 0xFF      # hi
    trampoline[0x05] = 0x4C                         # JMP $E005 (halt)
    trampoline[0x06] = 0x05
    trampoline[0x07] = 0xE0

    if play_addr != 0:
        # Play trampoline at $E010:  JSR play; JMP $E013
        trampoline[0x10] = 0x20                     # JSR
        trampoline[0x11] = play_addr & 0xFF         # lo
        trampoline[0x12] = (play_addr >> 8) & 0xFF  # hi
        trampoline[0x13] = 0x4C                     # JMP $E013 (halt)
        trampoline[0x14] = 0x13
        trampoline[0x15] = 0xE0

    # Prepare initial RAM contents — load program code at load_addr
    ram = [0] * ram_end
    for i, b in enumerate(code):
        if load_addr + i < ram_end:
            ram[load_addr + i] = b

    # Default interrupt vectors (may be overwritten by RSID init)
    irq_vectors = [
        0xF0, 0xE0,    # $FFFA NMI  → $E0F0 (NOP sled)
        0x00, 0xE0,    # $FFFC RESET→ $E000
        0xF0, 0xE0,    # $FFFE IRQ  → $E0F0 (NOP sled)
    ]

    mmu = SidMMU([
        # Full RAM from $0000 to $D3FF
        (0x0000, ram_end, False, ram),
        # Trampoline ROM  $E000-$E0FF
        (0xE000, 0x0100, True, trampoline),
        # Interrupt vectors $FFFA-$FFFF  (writable — RSID players update these)
        (0xFFFA, 0x06, False, irq_vectors),
    ])

    cpu = CPU(mmu, pc=0xE000)

    # ------------------------------------------------------------------
    # Run init
    # ------------------------------------------------------------------
    INIT_HALT = 0xE005

    print(f"\nRunning init (song {song + 1})...")
    max_init_cycles = 2_000_000    # generous limit for complex inits
    init_cycles = 0
    while True:
        # For RSID: init may enable the CIA timer and then enter an infinite
        # main-loop (e.g. Arkanoid's SampleGeneration loop).  We need to
        # tick the CIA and service IRQs even during init.
        if mmu.irq_pending and not cpu.r.getFlag('I'):
            trigger_irq(cpu)
            mmu.irq_pending = False

        cpu.step()
        elapsed = cpu.cc
        init_cycles += elapsed
        mmu.total_cycles += elapsed
        mmu.tick_cia(elapsed)

        if cpu.r.pc == INIT_HALT:
            break
        if init_cycles > max_init_cycles:
            if is_rsid:
                # For RSID it is normal that init never returns (infinite
                # main-loop) — we've given it enough time to set everything
                # up and service many IRQs.
                print(f"Init did not return after {init_cycles} cycles (RSID — expected)")
            else:
                print(f"WARNING: Init did not return after {init_cycles} cycles")
            break

    print(f"Init completed in {init_cycles} cycles")
    irq_count_init = len([w for w in mmu.write_log])
    print(f"  SID writes during init: {irq_count_init}")

    # ------------------------------------------------------------------
    # Run playback
    # ------------------------------------------------------------------
    if is_rsid or play_addr == 0:
        print(f"\nRunning RSID playback ({num_frames} frames via CIA IRQ)...")
        frame_boundaries = run_rsid(cpu, mmu, num_frames, CYCLES_PER_FRAME)
    else:
        print(f"\nRunning PSID playback ({num_frames} frames)...")
        frame_boundaries = run_psid(cpu, mmu, play_addr, num_frames, CYCLES_PER_FRAME)

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
    parser.add_argument('-s', '--song', type=int, default=None,
                        help='Song number to play (default: start song from SID header)')
    parser.add_argument('-o', '--output', default=None,
                        help='Output CSV file path (default: <sid_name>_capture.csv)')

    args = parser.parse_args()

    if args.output is None:
        base = os.path.splitext(os.path.basename(args.sid_file))[0]
        args.output = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'data',
            f'{base}_capture.csv')

    result = run_sid_capture(args.sid_file, num_frames=args.frames, song=args.song)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    print(f"\nSaving capture data...")
    save_csv(result, args.output)

if __name__ == '__main__':
    main()
