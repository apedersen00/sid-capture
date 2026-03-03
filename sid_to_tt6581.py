"""
Translate SID write capture (CSV) to TT6581 stimulus file.
"""

import os
import csv
import argparse

# SID voice register offsets
SID_FREQ_LO = 0x00
SID_FREQ_HI = 0x01

# SID global
SID_MODE_VOL = 0x18

# TT6581 volume register
TT_VOLUME = 0x1A

TT6581_SAMPLE_RATE = 50000   # 50 kHz
TT6581_CLK_FREQ    = 50000000  # 50 MHz
TT6581_PHASE_BITS  = 19
SID_CPU_FREQ       = 1000000
SID_PHASE_BITS     = 24

VOICE_BASES = [0x00, 0x07, 0x0E]


def sid_freq_to_hz(sid_freq_word):
    return sid_freq_word * SID_CPU_FREQ / (1 << SID_PHASE_BITS)


def hz_to_tt6581_fcw(freq_hz):
    fcw = int(freq_hz * (1 << TT6581_PHASE_BITS) / TT6581_SAMPLE_RATE)
    return max(0, min(0xFFFF, fcw))


def cpu_cycles_to_tt_clk(cpu_cycles):
    return int(cpu_cycles * (TT6581_CLK_FREQ / SID_CPU_FREQ))


def translate_sid_to_tt6581(capture_csv_path, output_path):
    """
    Read SID capture CSV and produce TT6581 stimulus file.

    CSV columns: cycle, sid_offset, value, reg_name
    Output format: clk_tick addr data (one write per line, TT6581 50 MHz)
    """

    with open(capture_csv_path, 'r') as f:
        writes = list(csv.DictReader(f))

    print(f"Input: {len(writes)} SID writes")

    sid_voice_freq = {v: {'lo': 0, 'hi': 0} for v in range(3)}
    tt_writes = []

    for w in writes:
        cpu_cycle = float(w['cycle'])
        offset = int(w['sid_offset'], 16)
        value = int(w['value'], 16)
        clk_tick = cpu_cycles_to_tt_clk(cpu_cycle)

        if offset <= 0x14:
            # Voice register — find which voice
            voice_idx = next(
                vi for vi, base in enumerate(VOICE_BASES)
                if base <= offset < base + 7
            )
            voice_offset = offset - VOICE_BASES[voice_idx]

            if voice_offset in (0, 1):  # FREQ_LO or FREQ_HI
                key = 'lo' if voice_offset == 0 else 'hi'
                sid_voice_freq[voice_idx][key] = value
                lo = sid_voice_freq[voice_idx]['lo']
                hi = sid_voice_freq[voice_idx]['hi']
                tt_fcw = hz_to_tt6581_fcw(sid_freq_to_hz((hi << 8) | lo))
                base = VOICE_BASES[voice_idx]
                tt_writes.append((clk_tick, base + SID_FREQ_LO, tt_fcw & 0xFF))
                tt_writes.append((clk_tick, base + SID_FREQ_HI, (tt_fcw >> 8) & 0xFF))

            elif voice_offset == 4:  # Control register
                # Clear SID TEST bit (bit 3) — TT6581 uses it for SINE
                tt_writes.append((clk_tick, offset, value & ~0x08))

            else:
                # PW_LO, PW_HI, AD, SR — pass through
                tt_writes.append((clk_tick, offset, value))

        elif offset == SID_MODE_VOL:
            # Volume: SID 4-bit → TT6581 8-bit (0–15 → 0–255)
            tt_writes.append((clk_tick, TT_VOLUME, (value & 0x0F) * 17))

    print(f"Output: {len(tt_writes)} TT6581 writes")

    with open(output_path, 'w') as f:
        f.write(f"# TT6581 Stimulus File\n")
        f.write(f"# Generated from: {os.path.basename(capture_csv_path)}\n")
        f.write(f"# TT6581 clock: {TT6581_CLK_FREQ} Hz\n")
        f.write(f"# Format: clk_tick addr data\n")
        f.write(f"#\n")
        for clk_tick, addr, data in tt_writes:
            f.write(f"{clk_tick} 0x{addr:02X} 0x{data:02X}\n")

    print(f"Saved to {output_path}")
    return tt_writes


def main():
    parser = argparse.ArgumentParser(
        description='Translate SID capture CSV to TT6581 stimulus file')
    parser.add_argument('capture_csv', help='Path to SID capture CSV file')
    parser.add_argument('-o', '--output', default=None,
                        help='Output stimulus file path')

    args = parser.parse_args()

    if args.output is None:
        base = os.path.splitext(os.path.basename(args.capture_csv))[0]
        base = base.replace('_capture', '')
        args.output = os.path.join(
            os.path.dirname(os.path.abspath(args.capture_csv)),
            f'{base}_tt6581_stimulus.txt')

    print("Starting...")
    translate_sid_to_tt6581(args.capture_csv, args.output)


if __name__ == '__main__':
    main()
