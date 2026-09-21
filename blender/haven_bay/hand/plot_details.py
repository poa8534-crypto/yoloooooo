# Every plot gets a readable "Plot N" sign and a cute mailbox at its gate, and
# a canvas starter tent on a wooden pad at the back, so an empty plot still
# looks lived in until its player builds a cottage.
import math

import bpy

PLOTS = {"Plot_1": (-118, 92), "Plot_2": (-130, 5), "Plot_3": (-120, -75), "Plot_4": (-60, -125)}
for n, (name, (x, y)) in enumerate(PLOTS.items(), 1):
    for stale in (f"{name}_Mailbox", f"{name}_MailboxBase", f"{name}_Sign"):
        old = bpy.data.objects.get(stale)
        if old is not None:
            bpy.data.objects.remove(old, do_unlink=True)
    rz = math.atan2(-x, y)  # the gate faces the town square
    c, s = math.cos(rz), math.sin(rz)

    def world(lx, ly):
        return x + lx * c - ly * s, y + lx * s + ly * c

    sx, sy = world(7, -18.5)
    kit.plot_sign(f"{name}_Sign", f"Plot {n}", sx, sy, rz=rz)
    mx, my = world(-6, -17.5)
    kit.mailbox(f"{name}_Mailbox", mx, my, rz=rz, colour=kit.PASTELS[n % len(kit.PASTELS)])
    tx, ty = world(0, 7)
    kit.tent(f"{name}_Tent", tx, ty, rz=rz, colour=("cloth_white", "pastel_yellow", "pastel_blue", "pastel_pink")[n - 1])
