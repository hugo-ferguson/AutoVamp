# AutoVamp

A command-line audio player with interactive cues. Mark points
and regions in an audio file that pause, loop, or jump during
playback. Useful for practising, rehearsing, or performing with
backing tracks.

## What are cues?

A cue is a marked point or region in the audio where something
happens during playback. Some cues vamp (loop a section until
you choose to move on), while others pause or skip ahead. The
name AutoVamp comes from the musical term: a vamp is a repeating
passage that loops until a cue is given to continue.

## Behaviours

Each cue has a behaviour that controls what happens during
playback:

- `jump`: loops the region until you press ENTER, then
  immediately jumps past it.
- `continue`: loops the region. On ENTER, finishes the current
  iteration before moving on. Supports an optional `repetitions`
  count to loop a set number of times before exiting
  automatically. Pressing ENTER adds one more loop.
- `safety`: plays through the region once and exits by default.
  Press ENTER to queue additional loops before it ends.
- `caesura`: pauses playback at the marked point, like a fermata
  or a conductor's hold. Press ENTER to resume.

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

Define cues directly on the command line. Each `--cue` takes
a comma-separated list of `key=value` pairs:

```
autovamp song.mp3 \
    --cue start=0:01:30,end=0:02:00,behaviour=jump \
    --cue start=0:03:00,end=0:03:30,behaviour=safety
```

### Config file mode

Create a `.toml` file and pass it as the only argument:

```
autovamp show.toml
```

The TOML file format:

```toml
file = "song.mp3"

[[cue]]
start = "0:01:30"
end = "0:02:00"
behaviour = "jump"

[[cue]]
start = "0:03:00"
end = "0:03:30"
behaviour = "continue"
repetitions = 3
```

Timestamps use `HH:MM:SS` format, with an optional `.mmm`
millisecond suffix (e.g. `0:01:30.5` for 1 minute 30.5 seconds).

### Arguments

| Argument    | Description                                     |
|-------------|-------------------------------------------------|
| `file`      | Audio file (wav, flac, ogg, mp3) or TOML config |
| `--cue`     | Inline cue definition (repeatable)              |
| `--version` | Show version and exit                           |

### Controls

- **ENTER**: exit the current cue (`behaviour` determines how)
- **SPACE**: play/pause
- **Q**: quit

## License

GNU General Public License v3. See [LICENSE](LICENSE).
