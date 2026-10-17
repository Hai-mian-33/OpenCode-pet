#!/usr/bin/env python
"""Regenerate everything the README shows.

    python tools/gen_preview.py            # both contact sheets + per-state PNGs
    python tools/gen_preview.py --all      # also the tray/app icons
    python tools/gen_preview.py --lang en  # force the sheet's language

Every image is rendered by the real character code from the real state
machine, so the documentation can never drift from the pet.

The contact sheet carries text, so it is written once per language -
``states-zh.png`` and ``states-en.png`` - exactly as the screenshots in this
project's sibling pets are paired.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from petcore import i18n, paths                             # noqa: E402
from petcore.reducer import STATES                          # noqa: E402

TIMES = {                       # a frame where each state is most readable
    "idle": 900.0,
    "thinking": 900.0,
    "executing": 160.0,
    "waiting": 120.0,
    "done": 520.0,
    "error": 120.0,
    "longtask": 850.0,
    "drag": 0.0,
    "offline": 900.0,
    "closed": 900.0,
}


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true",
                        help="also write the tray/app icon")
    parser.add_argument("--lang", choices=i18n.available(), default=None,
                        help="language for the labelled contact sheet")
    args = parser.parse_args(argv)

    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["opencode-pet-preview"])
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    ratio = app.devicePixelRatio()

    from petui.openo import render_contact_sheet, render_icon, render_state

    out_dir = os.path.join(paths.docs_dir(), "images")
    os.makedirs(out_dir, exist_ok=True)

    written = []
    languages = [args.lang] if args.lang else list(i18n.available())
    for lang in languages:
        # The sheet renders labels through ``i18n.t``, so the language has to be
        # set before it is drawn.
        i18n.set_language(lang)
        name = "states-%s.png" % lang
        render_contact_sheet(device_pixel_ratio=ratio).save(
            os.path.join(out_dir, name))
        written.append("docs/images/" + name)

    for state in STATES:
        pixmap = render_state(132, state, TIMES.get(state, 900.0), ratio)
        name = "%s.png" % state
        pixmap.save(os.path.join(out_dir, name))
        written.append("docs/images/" + name)

    if args.all:
        icons_dir = os.path.join(paths.assets_dir(), "icons")
        os.makedirs(icons_dir, exist_ok=True)
        for size in (16, 24, 32, 48, 64, 128, 256):
            render_icon(size, 1.0).save(os.path.join(icons_dir,
                                                    "openo-%d.png" % size))
            written.append("assets/icons/openo-%d.png" % size)

    print("wrote %d images" % len(written))
    for name in written:
        print("  " + name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
