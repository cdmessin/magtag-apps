import board
import digitalio
import storage
import supervisor

#   NORMAL (Button A not held):
#     boot.py  -> remounts filesystem writable for CircuitPython code
#                 (USB drive becomes read-only to host computer)
#
#   DEV MODE (hold Button A during reset/power-on):
#     boot.py  -> skips remount, USB drive stays writable for host computer
#                 (code.py cannot write to filesystem)
#
btn = digitalio.DigitalInOut(board.D15)
btn.direction = digitalio.Direction.INPUT
btn.pull = digitalio.Pull.UP

if btn.value:  # Button A not held (active low) — normal mode
    storage.remount("/", readonly=False)
# else: Button A held — dev mode, USB stays writable for host

btn.deinit()
