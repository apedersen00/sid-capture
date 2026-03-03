"""
Translate SID write capture (CSV) to TT6581 stimulus file.

SID and TT6581 differ significantly in filter register layout:
  - SID uses a raw 11-bit cutoff value and 4-bit resonance.
  - TT6581 uses pre-calculated filter cutoff and damping coefficients.
  - SID splits routing and mode across $17/$18; TT6581 merges them in $19.

See the TT6581 README register map for full details.
"""

import os
import csv
import math
import argparse

# ---------- SID register offsets ----------
SID_FREQ_LO   = 0x00
SID_FREQ_HI   = 0x01
SID_FC_LO     = 0x15   # Filter cutoff low  (bits 2:0 used)
SID_FC_HI     = 0x16   # Filter cutoff high (bits 7:0)
SID_RES_FILT  = 0x17   # Resonance (7:4), filter routing (2:0)
SID_MODE_VOL  = 0x18   # Filter mode (6:4), volume (3:0)

# ---------- TT6581 register addresses ----------
TT_F_LO    = 0x15   # Filter cutoff coefficient low  (Q1.15 signed)
TT_F_HI    = 0x16   # Filter cutoff coefficient high (Q1.15 signed)
TT_Q_LO    = 0x17   # Filter damping coefficient low  (Q4.12 signed)
TT_Q_HI    = 0x18   # Filter damping coefficient high (Q4.12 signed)
TT_EN_MODE = 0x19   # Filter enable (5:3) + mode select (2:0)
TT_VOLUME  = 0x1A   # Global volume (8-bit)

# ---------- Constants ----------
TT6581_SAMPLE_RATE = 50000      # 50 kHz
TT6581_CLK_FREQ    = 50000000   # 50 MHz
TT6581_PHASE_BITS  = 19
SID_CPU_FREQ       = 1000000    # ~1 MHz (C64 CPU clock)
SID_PHASE_BITS     = 24

# SID 11-bit FC → Hz: linear approximation for MOS6581.
# Real 6581 has a non-linear response that varies between chips.
# 5.8 Hz/bit gives roughly 0–11.9 kHz across the 0–2047 range.
SID_FC_HZ_PER_BIT = 5.8

VOICE_BASES = [0x00, 0x07, 0x0E]


# ---------- Voice frequency conversion ----------

def sid_freq_to_hz(sid_freq_word):
    """Convert SID 16-bit frequency register to Hz."""
    return sid_freq_word * SID_CPU_FREQ / (1 << SID_PHASE_BITS)


def hz_to_tt6581_fcw(freq_hz):
    """Convert voice frequency (Hz) to TT6581 16-bit phase FCW."""
    fcw = int(freq_hz * (1 << TT6581_PHASE_BITS) / TT6581_SAMPLE_RATE)
    return max(0, min(0xFFFF, fcw))


# ---------- Filter conversion ----------

def sid_fc_to_hz(fc_11bit):
    """Convert SID 11-bit filter cutoff to approximate Hz (MOS6581)."""
    return fc_11bit * SID_FC_HZ_PER_BIT


def hz_to_tt6581_fcc(fc_hz):
    """
    Convert cutoff frequency (Hz) to TT6581 filter cutoff coefficient.

    Formula from TT6581 docs:  FCC = 2 * sin(pi * fc / Fs) * 32768
    Result is Q1.15 signed, clamped to [0, 32767].
    """
    fcc = int(2.0 * math.sin(math.pi * fc_hz / TT6581_SAMPLE_RATE) * 32768)
    return max(0, min(32767, fcc))


def sid_res_to_q(res_4bit):
    """
    Convert SID 4-bit resonance (0–15) to Q factor.

    Approximate MOS6581 response:
      RES=0  → Q ≈ 0.707 (no resonance peak)
      RES=15 → Q ≈ 9.7   (strong resonance peak)
    """
    if res_4bit == 0:
        return 0.707
    return 0.707 + (res_4bit / 15.0) * 9.0


def q_to_tt6581_fdc(q):
    """
    Convert Q factor to TT6581 filter damping coefficient.

    Formula from TT6581 docs:  FDC = (1/Q) * 4096
    Result is Q4.12 signed, clamped to [0, 32767].
    """
    fdc = int((1.0 / q) * 4096)
    return max(0, min(32767, fdc))


# ---------- Timing ----------

def cpu_cycles_to_tt_clk(cpu_cycles):
    """Convert SID capture CPU cycles to TT6581 50 MHz clock ticks."""
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

    # Voice frequency tracking (for 16-bit recombination)
    sid_voice_freq = {v: {'lo': 0, 'hi': 0} for v in range(3)}

    # Filter state tracking (needed because TT6581 merges routing+mode
    # from two SID registers into a single EN_MODE register)
    sid_fc_lo = 0           # SID $15 bits 2:0
    sid_fc_hi = 0           # SID $16 bits 7:0
    sid_filt_routing = 0    # SID $17 bits 2:0 (filt1, filt2, filt3)
    sid_filt_mode = 0       # SID $18 bits 6:4 shifted to 2:0

    tt_writes = []

    for w in writes:
        cpu_cycle = float(w['cycle'])
        offset = int(w['sid_offset'], 16)
        value = int(w['value'], 16)
        clk_tick = cpu_cycles_to_tt_clk(cpu_cycle)

        # --- Voice registers ($00–$14) ---
        if offset <= 0x14:
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

        # --- Filter cutoff low ($15) ---
        elif offset == SID_FC_LO:
            sid_fc_lo = value & 0x07
            fc_11 = (sid_fc_hi << 3) | sid_fc_lo
            fcc = hz_to_tt6581_fcc(sid_fc_to_hz(fc_11))
            tt_writes.append((clk_tick, TT_F_LO, fcc & 0xFF))
            tt_writes.append((clk_tick, TT_F_HI, (fcc >> 8) & 0xFF))

        # --- Filter cutoff high ($16) ---
        elif offset == SID_FC_HI:
            sid_fc_hi = value
            fc_11 = (sid_fc_hi << 3) | sid_fc_lo
            fcc = hz_to_tt6581_fcc(sid_fc_to_hz(fc_11))
            tt_writes.append((clk_tick, TT_F_LO, fcc & 0xFF))
            tt_writes.append((clk_tick, TT_F_HI, (fcc >> 8) & 0xFF))

        # --- Resonance + filter routing ($17) ---
        elif offset == SID_RES_FILT:
            res = (value >> 4) & 0x0F
            sid_filt_routing = value & 0x07  # filt1, filt2, filt3
            # Resonance → Q → damping coefficient
            q = sid_res_to_q(res)
            fdc = q_to_tt6581_fdc(q)
            tt_writes.append((clk_tick, TT_Q_LO, fdc & 0xFF))
            tt_writes.append((clk_tick, TT_Q_HI, (fdc >> 8) & 0xFF))
            # Reconstruct EN_MODE (routing changed)
            en_mode = (sid_filt_routing << 3) | sid_filt_mode
            tt_writes.append((clk_tick, TT_EN_MODE, en_mode))

        # --- Filter mode + volume ($18) ---
        elif offset == SID_MODE_VOL:
            sid_filt_mode = (value >> 4) & 0x07  # LP, BP, HP
            vol = value & 0x0F
            # Reconstruct EN_MODE (mode changed)
            en_mode = (sid_filt_routing << 3) | sid_filt_mode
            tt_writes.append((clk_tick, TT_EN_MODE, en_mode))
            # Volume: SID 4-bit → TT6581 8-bit (0–15 → 0–255)
            tt_writes.append((clk_tick, TT_VOLUME, vol * 17))

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
