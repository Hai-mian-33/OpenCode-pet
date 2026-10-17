# Contributing to OpenCode Pet

**English** | [简体中文](CONTRIBUTING.zh-CN.md)

Thank you for helping. This project is small on purpose, and the rules below
exist to keep it that way.

Issues and pull requests are welcome — translations and platform reports
especially.

---

## The one rule that matters most

**The project must stay dependency-free at runtime.** No `pip install`, no
`npm install`, no build step. A user clones the repository, installs PyQt5, and
runs the pet.

This is a real constraint, not a preference. The pet is a companion that has to
be *there* — a dependency that fails to resolve on a fresh machine means no pet,
and a pet that silently misbehaves is worse than no pet at all.

Allowed: `PyQt5` (the only requirement) and, optionally, `psutil` — which is
used when present and never required.

---

## Getting set up

```
git clone https://github.com/Hai-mian-33/OpenCode-pet.git
cd OpenCode-pet
pip install PyQt5
python opencode_pet.py --demo
```

`--demo` plays one synthetic task through every state, so you can see the whole
visual vocabulary without a live OpenCode session.

---

## Running the checks

```
python tests\run_tests.py      # 177 unit tests, no side effects
scripts\Check-All.cmd          # tests + render previews + installer report
```

Both must pass before a pull request. The preview step regenerates
`docs/images/` — **commit the regenerated images** so documentation can never
drift from the code.

---

## Repository map

| Path | Role |
| --- | --- |
| `opencode_pet.py` | The pet: observer threads, windows, tray, event bridge |
| `pet_watcher.py` | Headless sign-in sentinel — no Qt, no window, no tray |
| `install.py` | Copy, autostart launcher, uninstall, status |
| `petcore/` | The pure core: `reducer`, `endpoint`, `client`, `i18n`, `settings`, `paths`, `lock`, `bridge` |
| `petui/` | Presentation: `openo` (the character), `petwindow`, `cards`, `tray` |
| `art/` | Design documentation — how the character was drawn. Never installed |
| `assets/brand/` | The official OpenCode mark and the concept sketches |
| `assets/icons/` | Generated tray and application icons |
| `docs/images/` | Screenshots used by both READMEs |
| `scripts/` | Windows `.cmd` wrappers for people who do not use a terminal |
| `tests/` | The unit-test suite and its runner |
| `tools/` | Preview renderer, live checker, demo driver |

---

## Where to make a change

**UI strings belong in the catalogue, never inline.** Every user-facing string
lives in `petcore/i18n.py`. If you add one, add it to *both* language tables in
the same commit — `tests/run_tests.py` fails when the key sets diverge, which is
deliberate.

```python
# petcore/i18n.py
"card.paused": "已暂停",        # zh
"card.paused": "Paused",        # en
```

**Business logic belongs in the pure core.** `petcore/reducer.py` folds events
into the small fact set the pet is allowed to know and derives everything from
it: mood, card order, retention. No I/O, no clock, no globals — every behaviour
worth trusting is a pure function with a unit test.

The privacy boundary lives in exactly one auditable place: what survives the
fold is status, step counts, **tool names**, timestamps and a short machine
error code. Prompts, model replies, tool arguments, tool results, file contents
and full paths are dropped. Only the final path segment survives, as the project
label. Session titles are opt-in (`--no-titles` turns them off). If your change
would widen that set, it needs a very good reason and a test that proves the
opposite.

**Anything that can fail must not raise inside a Qt slot.** This is the single
most expensive trap in the project: an exception in a slot kills the event loop,
the sentinel silently relaunches the pet, and the log shows a clean start with
no error anywhere. Wrap what can fail, and prefer returning a result.

---

## Adding a state

1. Add the constant to `petcore/reducer.py` and give it a priority in `PRIORITY`.
2. Add the pose, gaze, mouth and lamp to `petui/openo.py` — every state needs
   all four or it falls through to `idle`.
3. Add `state.<name>` to **both** catalogues in `petcore/i18n.py`.
4. Add it to the list in `tools/gen_preview.py` and commit the new preview.
5. Add a test that pins what the pet shows.

---

## Adding a language

1. Add the code to `LANGS` in `petcore/i18n.py`.
2. Copy the `"zh"` table to a new key and translate it.
3. Extend `normalize_language()` so it recognises your locale.

The tray menu and `--lang` are both derived from `LANGS`, so there is no other
wiring to do.

---

## Platform support

Windows 10/11 is the supported platform; the process and window handling is
Win32-specific. If you want to add another platform, the honest places to start
are `petcore/endpoint.py` (process discovery) and `petcore/client.py` (window
matching). Both already return "I could not tell" rather than guessing.

---

## Maintainer notes

These cost real time and are worth reading before you change anything:

- **A poll is not a window check.** During shutdown the OpenCode window
  disappears ~1.5 s before `OpenCode.exe` does, so anything that asks "is OpenCode
  running?" must ask about the *window* — never the process, and never with a
  process fallback. A fallback was tried and re-introduced the original bug.

- **`log()` writes milliseconds.** Several bugs here were pure timing. With
  second-resolution stamps the log cannot tell a race from a slow poll.

- **A claim that asks something of the user must be confirmed.** A pending
  approval only becomes "waiting for your authorization" after
  `WAIT_CONFIRM_MS` — and that must stay longer than the poll interval, because
  `form.created` arrives instantly over SSE while its *absence* is only noticed
  by the poll.

- **Restart the pet after editing anything under `petcore/` or `petui/`.** Python
  caches modules in memory, so a pet left running serves the old code and
  produces a convincing phantom "the fix does nothing".

- **A fact about the world must not be gated on a rendering flag.** Hiding the
  card when the window closes happens before the `snapshot["changed"]` early
  return; putting it after let a consumed dirty flag keep a stale card up.

- **`tasklist` gives no command line for `pythonw.exe`.** Filtering processes by
  script name silently matches nothing. Take the pid from a window instead.

---

## Licence

MIT — see [LICENSE](LICENSE). The artwork is derived from the official OpenCode
logo and remains the property of its owner; it is included here for personal,
non-commercial desktop-pet use.