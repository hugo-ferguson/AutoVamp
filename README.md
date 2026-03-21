# AutoVamp

A command-line audio player with interactive vamp loops. Define a
region in an audio file that loops until you choose to move on.
Useful for practising, rehearsing, or performing with backing
tracks.

## What is a vamp?

In music, a vamp is a repeating section that loops until a cue is
given to continue. AutoVamp lets you define vamp regions in any
audio file and choose how the transition out of the loop behaves.

## Behaviours

Each vamp has a behaviour that controls what happens when you
press `SPACE` to exit the loop:

- `jump`: immediately jumps past the vamp region.
- `continue`: finishes the current iteration, then moves on.
- `safety`: plays one additional iteration after you signal
  the exit, giving you time to prepare.
- `caesura`: pauses playback when the vamp region is reached.
  Press SPACE to resume.

## Installation

Requires Python 3.10 or newer.

```
pip install .
```

Or without installing:

```
pip install numpy sounddevice soundfile
python -m autovamp
```

### Pre-built binaries

Standalone executables for Linux, macOS, and Windows are
available on the
[releases page](https://github.com/hugo-ferguson/AutoVamp/releases).
No Python installation required.

On macOS, you may need to allow the program to run in the 'Privacy & Security'
page in settings.

## Usage

```
autovamp song.mp3 \
    --start 1:30 \
    --end 2:00 \
    --behaviour jump
```

Timestamps can be `S`, `MM:SS`, or `HH:MM:SS`, with an
optional `.mmm` millisecond suffix.

### Arguments

| Argument      | Description                                   |
|---------------|-----------------------------------------------|
| `file`        | Audio file to play (wav, flac, ogg, mp3)      |
| `--start`     | Vamp start timestamp                          |
| `--end`       | Vamp end timestamp                            |
| `--behaviour` | One of: `jump`, `continue`, `safety`,`caesura`|
| `--version`   | Show version and exit                         |

### Controls

- **SPACE** — exit the current vamp (`behaviour` determines how)
- **Q** — quit

## License

GNU General Public License v3. See [LICENSE](LICENSE).
