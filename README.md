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
- `safety`: exits the vamp at the end of the current loop by
  default. Press SPACE to add extra loops before exiting.
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

### Inline mode

Define vamps directly on the command line. Each `--vamp` takes
a comma-separated list of `key=value` pairs:

```
autovamp song.mp3 \
    --vamp start=0:01:30,end=0:02:00,behaviour=jump \
    --vamp start=0:03:00,end=0:03:30,behaviour=safety
```

### Config file mode

Create a `.toml` file and pass it as the only argument:

```
autovamp show.toml
```

The TOML file format:

```toml
file = "song.mp3"

[[vamp]]
start = "0:01:30"
end = "0:02:00"
behaviour = "jump"

[[vamp]]
start = "0:03:00"
end = "0:03:30"
behaviour = "safety"
```

Timestamps use `HH:MM:SS` format, with an optional `.mmm`
millisecond suffix (e.g. `0:01:30.5` for 1 minute 30.5 seconds).

### Arguments

| Argument    | Description                                     |
|-------------|-------------------------------------------------|
| `file`      | Audio file (wav, flac, ogg, mp3) or TOML config |
| `--vamp`    | Inline vamp definition (repeatable)             |
| `--version` | Show version and exit                           |

### Controls

- **SPACE**: exit the current vamp (`behaviour` determines how)
- **Q**: quit

## License

GNU General Public License v3. See [LICENSE](LICENSE).
