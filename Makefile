# SID Capture — assemble, emulate, translate
#
# Usage:
#   make monty                    # full pipeline for Monty on the Run
#   make commando FRAMES=18000    # Commando, 6 minutes
#   make arkanoid SONG=3          # Arkanoid song 3 (in-game music)
#   make rambo                    # Rambo II Loader
#   make cybernoid2               # Cybernoid II
#   make all                      # build all known targets
#
# Variables you can override:
#   FRAMES  — number of 50 Hz frames to emulate (default: 18000 ≈ 6 min)
#   SONG    — song number (default: use SID header default)

PYTHON  := python
ACME    := acme
FRAMES  ?= 18000

SID_DIR  := sid
DATA_DIR := data
SRC_DIR  := c64_6581_sid_players

# --------------------------------------------------------------------------
# Source → target mappings
# --------------------------------------------------------------------------

# Hubbard — Rob
MONTY_ASM     := $(SRC_DIR)/Hubbard_Rob/Hubbard_Rob_Monty_on_the_Run.asm
MONTY_SID     := $(SID_DIR)/Hubbard_Rob_Monty_on_the_Run.sid
MONTY_CSV     := $(DATA_DIR)/Hubbard_Rob_Monty_on_the_Run_capture.csv
MONTY_STIM    := $(DATA_DIR)/Hubbard_Rob_Monty_on_the_Run_tt6581_stimulus.txt

COMMANDO_ASM  := $(SRC_DIR)/Hubbard_Rob/Hubbard_Rob_Commando.asm
COMMANDO_SID  := $(SID_DIR)/Hubbard_Rob_Commando.sid
COMMANDO_CSV  := $(DATA_DIR)/Hubbard_Rob_Commando_capture.csv
COMMANDO_STIM := $(DATA_DIR)/Hubbard_Rob_Commando_tt6581_stimulus.txt

# Galway — Martin
ARKANOID_ASM  := $(SRC_DIR)/Galway Martin/Galway_Martin_Arkanoid.asm
ARKANOID_SID  := $(SID_DIR)/Galway_Martin_Arkanoid.sid
ARKANOID_CSV  := $(DATA_DIR)/Galway_Martin_Arkanoid_capture.csv
ARKANOID_STIM := $(DATA_DIR)/Galway_Martin_Arkanoid_tt6581_stimulus.txt

RAMBO_ASM     := $(SRC_DIR)/Galway Martin/Galway_Martin_Rambo_Loader.asm
RAMBO_SID     := $(SID_DIR)/Galway_Martin_Rambo_Loader.sid
RAMBO_CSV     := $(DATA_DIR)/Galway_Martin_Rambo_Loader_capture.csv
RAMBO_STIM    := $(DATA_DIR)/Galway_Martin_Rambo_Loader_tt6581_stimulus.txt

# Tel — Jeroen
CYBERNOID2_ASM  := $(SRC_DIR)/Tel_Jeroen_MON/Tel_Jeroen_Cybernoid2.asm
CYBERNOID2_SID  := $(SID_DIR)/Tel_Jeroen_Cybernoid2.sid
CYBERNOID2_CSV  := $(DATA_DIR)/Tel_Jeroen_Cybernoid2_capture.csv
CYBERNOID2_STIM := $(DATA_DIR)/Tel_Jeroen_Cybernoid2_tt6581_stimulus.txt

# --------------------------------------------------------------------------
# Phony targets
# --------------------------------------------------------------------------

.PHONY: all monty commando arkanoid rambo cybernoid2 clean help

all: monty commando arkanoid rambo cybernoid2

monty:      $(MONTY_STIM)
commando:   $(COMMANDO_STIM)
arkanoid:   $(ARKANOID_STIM)
rambo:      $(RAMBO_STIM)
cybernoid2: $(CYBERNOID2_STIM)

help:
	@echo "Targets:  monty  commando  arkanoid  rambo  cybernoid2  all  clean"
	@echo ""
	@echo "Variables:"
	@echo "  FRAMES=18000   Number of 50 Hz frames (default: 18000 ≈ 6 min)"
	@echo "  SONG=N         Song number (only needed for multi-song SIDs)"
	@echo ""
	@echo "Examples:"
	@echo "  make monty"
	@echo "  make arkanoid SONG=3 FRAMES=1000"
	@echo "  make commando FRAMES=18000"
	@echo "  make cybernoid2"
	@echo "  make all"

clean:
	rm -f $(SID_DIR)/*.sid
	rm -f $(DATA_DIR)/*_capture.csv
	rm -f $(DATA_DIR)/*_tt6581_stimulus.txt

# --------------------------------------------------------------------------
# Directories
# --------------------------------------------------------------------------

$(SID_DIR) $(DATA_DIR):
	mkdir -p $@

# --------------------------------------------------------------------------
# Build the SONG flag if set
# --------------------------------------------------------------------------

SONG_FLAG = $(if $(SONG),-s $(SONG),)

# --------------------------------------------------------------------------
# Rules — Monty on the Run
# --------------------------------------------------------------------------

$(MONTY_SID): $(MONTY_ASM) | $(SID_DIR)
	$(ACME) -o $@ $<

$(MONTY_CSV): $(MONTY_SID) sid_capture.py | $(DATA_DIR)
	$(PYTHON) sid_capture.py $< -n $(FRAMES) $(SONG_FLAG) -o $@

$(MONTY_STIM): $(MONTY_CSV) sid_to_tt6581.py
	$(PYTHON) sid_to_tt6581.py $< -o $@

# --------------------------------------------------------------------------
# Rules — Commando
# --------------------------------------------------------------------------

$(COMMANDO_SID): $(COMMANDO_ASM) | $(SID_DIR)
	$(ACME) -o $@ $<

$(COMMANDO_CSV): $(COMMANDO_SID) sid_capture.py | $(DATA_DIR)
	$(PYTHON) sid_capture.py $< -n $(FRAMES) $(SONG_FLAG) -o $@

$(COMMANDO_STIM): $(COMMANDO_CSV) sid_to_tt6581.py
	$(PYTHON) sid_to_tt6581.py $< -o $@

# --------------------------------------------------------------------------
# Rules — Arkanoid
# --------------------------------------------------------------------------

$(ARKANOID_SID): | $(SID_DIR)
	$(ACME) -o $@ "$(ARKANOID_ASM)"

$(ARKANOID_CSV): $(ARKANOID_SID) sid_capture.py | $(DATA_DIR)
	$(PYTHON) sid_capture.py $< -n $(FRAMES) $(SONG_FLAG) -o $@

$(ARKANOID_STIM): $(ARKANOID_CSV) sid_to_tt6581.py
	$(PYTHON) sid_to_tt6581.py $< -o $@

# --------------------------------------------------------------------------
# Rules — Rambo II Loader
# --------------------------------------------------------------------------

$(RAMBO_SID): | $(SID_DIR)
	$(ACME) -o $@ "$(RAMBO_ASM)"

$(RAMBO_CSV): $(RAMBO_SID) sid_capture.py | $(DATA_DIR)
	$(PYTHON) sid_capture.py $< -n $(FRAMES) $(SONG_FLAG) -o $@

$(RAMBO_STIM): $(RAMBO_CSV) sid_to_tt6581.py
	$(PYTHON) sid_to_tt6581.py $< -o $@

# --------------------------------------------------------------------------
# Rules — Cybernoid II
# --------------------------------------------------------------------------

$(CYBERNOID2_SID): $(CYBERNOID2_ASM) | $(SID_DIR)
	$(ACME) -o $@ $<

$(CYBERNOID2_CSV): $(CYBERNOID2_SID) sid_capture.py | $(DATA_DIR)
	$(PYTHON) sid_capture.py $< -n $(FRAMES) $(SONG_FLAG) -o $@

$(CYBERNOID2_STIM): $(CYBERNOID2_CSV) sid_to_tt6581.py
	$(PYTHON) sid_to_tt6581.py $< -o $@
