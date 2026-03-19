# Ableton Session Templater

Generate Ableton Live `.als` session files from YAML/JSON config templates. Define your track layout, groups, colors, BPM, and time signature in a simple config file — no manual setup needed.

## How It Works

1. Save any Ableton Live set as your **base** `.als` file (`File > Save As`)
2. Write (or generate) a **config** file describing your track layout
3. Run the script — it surgically modifies the base file's XML and outputs a new `.als`

This approach avoids reverse-engineering the full ALS schema and is robust across Ableton versions.

## Requirements

- Python 3.7+
- An Ableton Live `.als` base file
- PyYAML (optional, for `.yaml` configs): `pip install pyyaml`

No other dependencies. No internet required.

## Usage

```bash
# Generate a session from a config
python3 session_templater.py --base default.als --config my_template.yaml -o output.als

# Generate a sample config (YAML)
python3 session_templater.py --create-config

# Generate a config using a style preset
python3 session_templater.py --create-config --style hiphop

# List all style presets
python3 session_templater.py --list-styles

# List available color names
python3 session_templater.py --list-colors

# Inspect an existing .als file
python3 session_templater.py --inspect my_session.als
```

## Workflow

```
1. Save a base Ableton set:   File > Save As > default.als
2. Generate a config:         python session_templater.py --create-config --style hiphop
3. Edit template_hiphop.yaml to taste
4. Generate the session:      python session_templater.py --base default.als --config template_hiphop.yaml -o session.als
5. Open in Ableton:           File > Open Live Set > session.als
```

## Config Format

Configs can be written in YAML (recommended) or JSON.

```yaml
bpm: 120
time_signature: "4/4"

tracks:
  - name: Drums
    type: group
    color: dark_orange

  - name: Kick
    type: midi
    color: orange
    group: Drums      # assigns this track to the "Drums" group

  - name: Bass
    type: midi
    color: purple

  - name: Vocal
    type: audio
    color: pink

returns:
  - name: Reverb
    color: blue
  - name: Delay
    color: cyan
```

### Track Types

| Type    | Description              |
|---------|--------------------------|
| `midi`  | MIDI track               |
| `audio` | Audio track              |
| `group` | Group/folder track       |

### Colors

Use named colors or raw Ableton color index integers (0–69).

**Named colors:** `red`, `orange`, `yellow`, `lime`, `green`, `teal`, `cyan`, `blue`, `purple`, `violet`, `magenta`, `pink`, `white`, `gray`, `dark_*` variants, and more.

**Instrument aliases:** `kick`, `snare`, `hihat`, `bass`, `synth`, `pad`, `vocal`, `fx`, `guitar`, `piano`, `drums`, `bus`, `master`

Run `--list-colors` to see all available names and their index values.

## Built-in Style Presets

| Style              | Description                                      | BPM |
|--------------------|--------------------------------------------------|-----|
| `default`          | Basic starter: drums, bass, synth, vocal         | 120 |
| `hiphop`           | Hip-hop / beat production layout                 | 90  |
| `techno`           | Techno / electronic production layout            | 130 |
| `ambient`          | Ambient / textural / atmospheric layout          | 80  |
| `mixing`           | Mixing/mastering template with stem groups       | 120 |
| `live-performance` | Live performance / DJ hybrid set                 | 126 |

## CLI Reference

| Flag                   | Description                                        |
|------------------------|----------------------------------------------------|
| `--base`, `-b`         | Base `.als` file (required for generation)         |
| `--config`, `-c`       | Config file path (YAML or JSON)                    |
| `--output`, `-o`       | Output `.als` filename (default: `session_output.als`) |
| `--no-compress`        | Save as uncompressed XML instead of gzipped        |
| `--inspect`, `-i`      | Print a summary of an existing `.als` file         |
| `--create-config`      | Generate a sample config file                      |
| `--style`              | Style preset to use with `--create-config`         |
| `--json`               | Output config as JSON (default is YAML)            |
| `--list-styles`        | List all available style presets                   |
| `--list-colors`        | List all available color names                     |
| `--verbose`, `-v`      | Print detailed output during generation            |

## Notes

- The base `.als` provides the XML structure — it determines which device chains, plugin wrappers, and Ableton version metadata are present
- The config controls track names, types, colors, group assignments, BPM, and time signature
- Run `--inspect` on your base file first to understand its existing structure
- Ableton reads both gzipped and raw XML `.als` files
- Color index values are consistent across Ableton versions, though rendered colors may vary by theme
