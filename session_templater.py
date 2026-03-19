#!/usr/bin/env python3
"""
Ableton Session Templater
==========================
Generate Ableton Live .als session files from YAML/JSON config templates.

How it works:
  1. You provide a "base" .als file (any valid Ableton Live set — even a blank default)
  2. You write a template config (YAML or JSON) describing the tracks, groups, colors, BPM, etc.
  3. This tool modifies the base .als XML to match your config and outputs a new .als file

This avoids reverse-engineering the entire ALS schema — instead we surgically modify
a known-good file. Ableton is very forgiving about extra XML it doesn't recognize,
so this approach is robust across versions.

Usage:
    python3 session_templater.py --base default.als --config my_template.yaml -o output.als
    python3 session_templater.py --base default.als --config hiphop.yaml --verbose
    python3 session_templater.py --create-config                # generate a sample config
    python3 session_templater.py --create-config --style mixing  # mixing template preset
    python3 session_templater.py --list-styles                  # list config presets
    python3 session_templater.py --inspect my_session.als       # inspect an existing .als

Requirements:
    - Python 3.7+
    - A "base" .als file from Ableton (File > Save As... on any set)
    - PyYAML (pip install pyyaml) OR use JSON configs (no extra deps)

No other dependencies. No internet required.
"""

import argparse
import copy
import gzip
import json
import os
import random
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# Try to import YAML, fall back gracefully
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# ──────────────────────────────────────────────
# Ableton Color Palette (Live 10/11/12)
# ──────────────────────────────────────────────
# These are the color indices used in Ableton's XML.
# The actual RGB values depend on the theme, but the
# indices are consistent. Here are the "canonical" names.

COLORS = {
    'red':          0,   'dark_red':      1,
    'orange':       2,   'dark_orange':   3,
    'yellow':       4,   'dark_yellow':   5,
    'lime':         6,   'dark_lime':     7,
    'green':        8,   'dark_green':    9,
    'teal':        10,   'dark_teal':    11,
    'cyan':        12,   'dark_cyan':    13,
    'blue':        14,   'dark_blue':    15,
    'purple':      16,   'dark_purple':  17,
    'violet':      18,   'dark_violet':  19,
    'magenta':     20,   'dark_magenta': 21,
    'pink':        22,   'dark_pink':    23,
    'white':       24,   'light_gray':   25,
    'gray':        26,   'dark_gray':    27,
    'cream':       28,   'sand':         29,
    'peach':       30,   'salmon':       31,
    'rose':        32,   'lavender':     33,
    'sky':         34,   'ice':          35,
    'mint':        36,   'moss':         37,
    'olive':       38,   'brown':        39,
    'tan':         40,   'copper':       41,
    'rust':        42,   'wine':         43,
    'plum':        44,   'grape':        45,
    'slate':       46,   'charcoal':     47,
    'coral':       48,   'gold':         49,
    # Aliases
    'kick':         3,   'snare':         0,
    'hihat':       12,   'bass':         16,
    'synth':       18,   'pad':          33,
    'vocal':       22,   'fx':           10,
    'guitar':       4,   'piano':        14,
    'strings':     44,   'brass':         2,
    'drums':        3,   'percussion':    6,
    'bus':         26,   'master':       24,
}


def resolve_color(color_input) -> int:
    """Resolve a color name or index to an Ableton color index."""
    if isinstance(color_input, int):
        return max(0, min(69, color_input))
    name = str(color_input).lower().replace(' ', '_').replace('-', '_')
    if name in COLORS:
        return COLORS[name]
    # Try parsing as int
    try:
        return max(0, min(69, int(name)))
    except ValueError:
        print(f"  Warning: Unknown color '{color_input}', using default (gray)")
        return 26


# ──────────────────────────────────────────────
# ALS File I/O
# ──────────────────────────────────────────────

def read_als(path: str) -> ET.Element:
    """Read an .als file (gzipped or raw XML) and return the root Element."""
    with open(path, 'rb') as f:
        header = f.read(2)

    try:
        # Try gzip first
        with gzip.open(path, 'rb') as f:
            xml_bytes = f.read()
    except (gzip.BadGzipFile, OSError):
        # Fall back to raw XML
        with open(path, 'rb') as f:
            xml_bytes = f.read()

    return ET.fromstring(xml_bytes)


def write_als(root: ET.Element, path: str, compress: bool = True):
    """Write an Element tree to an .als file."""
    xml_bytes = ET.tostring(root, encoding='UTF-8', xml_declaration=True)

    if compress:
        with gzip.open(path, 'wb') as f:
            f.write(xml_bytes)
    else:
        with open(path, 'wb') as f:
            f.write(xml_bytes)


# ──────────────────────────────────────────────
# ALS Inspection
# ──────────────────────────────────────────────

def inspect_als(path: str):
    """Print a summary of an existing .als file."""
    root = read_als(path)
    ableton = root if root.tag == 'Ableton' else root
    live_set = ableton.find('LiveSet')

    if live_set is None:
        print("Error: Could not find LiveSet element. Is this a valid .als file?")
        return

    # Version info
    major = ableton.get('MajorVersion', '?')
    minor = ableton.get('MinorVersion', '?')
    creator = ableton.get('Creator', '?')
    print(f"\n{'═' * 60}")
    print(f"  Ableton Live Set: {os.path.basename(path)}")
    print(f"{'═' * 60}")
    print(f"  Creator: {creator}")
    print(f"  Version: Major={major}, Minor={minor}")

    # Tempo
    master = live_set.find('.//MasterTrack')
    tempo_el = live_set.find('.//Tempo/Manual')
    if tempo_el is not None:
        print(f"  Tempo: {tempo_el.get('Value', '?')} BPM")

    # Time signature
    numerator = live_set.find('.//TimeSignature/TimeSignatures/RemoteableTimeSignature/Numerator')
    denominator = live_set.find('.//TimeSignature/TimeSignatures/RemoteableTimeSignature/Denominator')
    if numerator is not None and denominator is not None:
        print(f"  Time Signature: {numerator.get('Value', '4')}/{denominator.get('Value', '4')}")

    # Tracks
    tracks_el = live_set.find('Tracks')
    if tracks_el is not None:
        midi_tracks = tracks_el.findall('MidiTrack')
        audio_tracks = tracks_el.findall('AudioTrack')
        group_tracks = tracks_el.findall('GroupTrack')
        return_tracks = live_set.findall('.//ReturnTrack')

        print(f"\n  Tracks: {len(midi_tracks)} MIDI, {len(audio_tracks)} Audio, "
              f"{len(group_tracks)} Group, {len(return_tracks)} Return")
        print(f"{'─' * 60}")

        for track in list(tracks_el):
            tag = track.tag
            track_type = tag.replace('Track', '')
            name_el = track.find('.//Name/EffectiveName')
            user_name_el = track.find('.//Name/UserName')
            name = (user_name_el.get('Value', '') if user_name_el is not None
                    and user_name_el.get('Value', '') else
                    name_el.get('Value', '?') if name_el is not None else '?')
            color_el = track.find('ColorIndex')
            color = color_el.get('Value', '?') if color_el is not None else '?'
            group_el = track.find('TrackGroupId')
            group = group_el.get('Value', '-1') if group_el is not None else '-1'
            group_str = f" (group {group})" if group != '-1' else ''
            print(f"  {track_type:>6} │ {name:<30} color={color}{group_str}")

        for rt in return_tracks:
            name_el = rt.find('.//Name/EffectiveName')
            user_name_el = rt.find('.//Name/UserName')
            name = (user_name_el.get('Value', '') if user_name_el is not None
                    and user_name_el.get('Value', '') else
                    name_el.get('Value', '?') if name_el is not None else '?')
            print(f"  Return │ {name:<30}")

    print()


# ──────────────────────────────────────────────
# ALS Modification
# ──────────────────────────────────────────────

def _next_id(root: ET.Element) -> int:
    """Find the next available Id in the document."""
    max_id = 0
    for el in root.iter():
        id_val = el.get('Id')
        if id_val is not None:
            try:
                max_id = max(max_id, int(id_val))
            except ValueError:
                pass
    return max_id + 1


def _set_track_name(track_el: ET.Element, name: str):
    """Set the user name of a track."""
    name_el = track_el.find('.//Name/UserName')
    if name_el is not None:
        name_el.set('Value', name)
    # Also set EffectiveName as fallback
    eff_el = track_el.find('.//Name/EffectiveName')
    if eff_el is not None:
        eff_el.set('Value', name)


def _set_track_color(track_el: ET.Element, color: int):
    """Set the color index of a track."""
    color_el = track_el.find('ColorIndex')
    if color_el is not None:
        color_el.set('Value', str(color))


def _set_track_group(track_el: ET.Element, group_id: int):
    """Set the group ID of a track (-1 for no group)."""
    group_el = track_el.find('TrackGroupId')
    if group_el is not None:
        group_el.set('Value', str(group_id))


def _clone_track(template_track: ET.Element, new_id: int) -> ET.Element:
    """Deep-clone a track element with a new ID."""
    new_track = copy.deepcopy(template_track)
    new_track.set('Id', str(new_id))
    return new_track


def set_tempo(live_set: ET.Element, bpm: float):
    """Set the session tempo."""
    tempo = live_set.find('.//Tempo/Manual')
    if tempo is not None:
        tempo.set('Value', str(bpm))
    # Also set the automation value
    tempo_event = live_set.find('.//Tempo/AutomationTarget')
    tempo_auto = live_set.find('.//Tempo/MidiCCOnOffThresholds')


def set_time_signature(live_set: ET.Element, numerator: int, denominator: int):
    """Set the time signature."""
    num_el = live_set.find('.//TimeSignature/TimeSignatures/RemoteableTimeSignature/Numerator')
    den_el = live_set.find('.//TimeSignature/TimeSignatures/RemoteableTimeSignature/Denominator')
    if num_el is not None:
        num_el.set('Value', str(numerator))
    if den_el is not None:
        den_el.set('Value', str(denominator))


def apply_config(root: ET.Element, config: dict, verbose: bool = False) -> ET.Element:
    """
    Apply a template config to an Ableton Live set XML tree.

    Config structure:
      bpm: 120
      time_signature: "4/4"
      tracks:
        - name: "Kick"
          type: midi          # midi, audio, or group
          color: orange
          group: "Drums"      # optional: group name
        - name: "Drums"
          type: group
          color: dark_orange
      returns:
        - name: "Reverb"
          color: blue
        - name: "Delay"
          color: cyan
    """
    live_set = root.find('LiveSet') if root.tag == 'Ableton' else root.find('LiveSet')
    if live_set is None:
        print("Error: No LiveSet found in base file.")
        sys.exit(1)

    tracks_el = live_set.find('Tracks')
    if tracks_el is None:
        print("Error: No Tracks element found in base file.")
        sys.exit(1)

    # ── Set global parameters ──
    bpm = config.get('bpm')
    if bpm:
        set_tempo(live_set, float(bpm))
        if verbose:
            print(f"  Set BPM: {bpm}")

    time_sig = config.get('time_signature')
    if time_sig:
        parts = str(time_sig).split('/')
        if len(parts) == 2:
            set_time_signature(live_set, int(parts[0]), int(parts[1]))
            if verbose:
                print(f"  Set time signature: {time_sig}")

    # ── Gather template tracks from the base file ──
    # We need at least one MIDI and one Audio track to clone from
    existing_midi = tracks_el.findall('MidiTrack')
    existing_audio = tracks_el.findall('AudioTrack')
    existing_group = tracks_el.findall('GroupTrack')

    midi_template = existing_midi[0] if existing_midi else None
    audio_template = existing_audio[0] if existing_audio else None
    group_template = existing_group[0] if existing_group else None

    if midi_template is None and audio_template is None:
        print("Error: Base file has no tracks to use as templates.")
        print("  Please provide a base .als with at least one MIDI or Audio track.")
        sys.exit(1)

    # ── Process track definitions ──
    track_defs = config.get('tracks', [])
    return_defs = config.get('returns', [])

    if not track_defs:
        if verbose:
            print("  No tracks defined in config, keeping base file tracks.")
        return root

    # Clear existing tracks
    for child in list(tracks_el):
        tracks_el.remove(child)

    # First pass: identify groups and assign IDs
    current_id = _next_id(root)
    group_ids = {}  # group_name -> assigned_id

    for tdef in track_defs:
        ttype = tdef.get('type', 'midi').lower()
        if ttype == 'group':
            group_ids[tdef['name']] = current_id
            current_id += 1
        else:
            current_id += 1

    # Second pass: create tracks
    current_id = _next_id(root)

    for tdef in track_defs:
        name = tdef.get('name', 'Track')
        ttype = tdef.get('type', 'midi').lower()
        color = resolve_color(tdef.get('color', 'gray'))
        group_name = tdef.get('group', None)

        # Select template
        if ttype == 'midi':
            if midi_template is None:
                if verbose:
                    print(f"  Warning: No MIDI template, skipping '{name}'")
                continue
            template = midi_template
        elif ttype == 'audio':
            if audio_template is None:
                # Fall back to MIDI template and change tag
                if verbose:
                    print(f"  Warning: No Audio template for '{name}', using MIDI")
                template = midi_template
            else:
                template = audio_template
        elif ttype == 'group':
            if group_template is not None:
                template = group_template
            elif midi_template is not None:
                template = midi_template
                if verbose:
                    print(f"  Warning: No Group template for '{name}', using MIDI")
            else:
                continue
        else:
            print(f"  Warning: Unknown track type '{ttype}' for '{name}', skipping")
            continue

        # Clone and configure
        track = _clone_track(template, current_id)

        # If we're making a group but cloned from non-group, change tag
        if ttype == 'group' and track.tag != 'GroupTrack':
            track.tag = 'GroupTrack'
        elif ttype == 'midi' and track.tag != 'MidiTrack':
            track.tag = 'MidiTrack'
        elif ttype == 'audio' and track.tag != 'AudioTrack':
            track.tag = 'AudioTrack'

        _set_track_name(track, name)
        _set_track_color(track, color)

        # Assign to group
        if group_name and group_name in group_ids:
            _set_track_group(track, group_ids[group_name])
        else:
            _set_track_group(track, -1)

        tracks_el.append(track)

        if verbose:
            group_str = f" → group '{group_name}'" if group_name else ''
            print(f"  + {ttype:>5} │ {name:<25} color={color}{group_str}")

        current_id += 1

    # ── Process return tracks ──
    if return_defs:
        existing_returns = live_set.findall('.//ReturnTrack')
        if existing_returns:
            return_template = existing_returns[0]
            # Find the parent of return tracks
            # In ALS XML, returns are usually under SendsListPreset or directly under LiveSet
            # We'll find and modify them
            for i, rdef in enumerate(return_defs):
                rname = rdef.get('name', f'Return {chr(65 + i)}')
                rcolor = resolve_color(rdef.get('color', 'gray'))

                if i < len(existing_returns):
                    # Modify existing return
                    _set_track_name(existing_returns[i], rname)
                    _set_track_color(existing_returns[i], rcolor)
                else:
                    # Clone a new return
                    new_return = _clone_track(return_template, current_id)
                    _set_track_name(new_return, rname)
                    _set_track_color(new_return, rcolor)
                    # Insert after existing returns
                    parent = live_set
                    for p in live_set.iter():
                        if return_template in list(p):
                            parent = p
                            break
                    parent.append(new_return)
                    current_id += 1

                if verbose:
                    print(f"  + return │ {rname:<25} color={rcolor}")

    return root


# ──────────────────────────────────────────────
# Config Presets / Styles
# ──────────────────────────────────────────────

STYLES = {
    'default': {
        'description': 'Basic starter template: drums, bass, synth, vocal + 2 returns',
        'config': {
            'bpm': 120,
            'time_signature': '4/4',
            'tracks': [
                {'name': 'Drums',   'type': 'group', 'color': 'dark_orange'},
                {'name': 'Kick',    'type': 'midi',  'color': 'orange',  'group': 'Drums'},
                {'name': 'Snare',   'type': 'midi',  'color': 'orange',  'group': 'Drums'},
                {'name': 'Hats',    'type': 'midi',  'color': 'yellow',  'group': 'Drums'},
                {'name': 'Perc',    'type': 'midi',  'color': 'yellow',  'group': 'Drums'},
                {'name': 'Bass',    'type': 'midi',  'color': 'purple'},
                {'name': 'Synth 1', 'type': 'midi',  'color': 'violet'},
                {'name': 'Synth 2', 'type': 'midi',  'color': 'violet'},
                {'name': 'Vocal',   'type': 'audio', 'color': 'pink'},
            ],
            'returns': [
                {'name': 'Reverb', 'color': 'blue'},
                {'name': 'Delay',  'color': 'cyan'},
            ],
        }
    },
    'hiphop': {
        'description': 'Hip-hop / beat production layout',
        'config': {
            'bpm': 90,
            'time_signature': '4/4',
            'tracks': [
                {'name': 'Drums',    'type': 'group', 'color': 'dark_orange'},
                {'name': 'Kick',     'type': 'midi',  'color': 'orange',     'group': 'Drums'},
                {'name': 'Snare',    'type': 'midi',  'color': 'red',        'group': 'Drums'},
                {'name': 'Hats',     'type': 'midi',  'color': 'yellow',     'group': 'Drums'},
                {'name': 'Perc',     'type': 'midi',  'color': 'gold',       'group': 'Drums'},
                {'name': '808',      'type': 'midi',  'color': 'dark_red'},
                {'name': 'Melody',   'type': 'midi',  'color': 'purple'},
                {'name': 'Chords',   'type': 'midi',  'color': 'violet'},
                {'name': 'Sample',   'type': 'audio', 'color': 'teal'},
                {'name': 'Vocal',    'type': 'audio', 'color': 'pink'},
                {'name': 'Adlibs',   'type': 'audio', 'color': 'rose'},
            ],
            'returns': [
                {'name': 'Reverb',   'color': 'blue'},
                {'name': 'Delay',    'color': 'cyan'},
                {'name': 'Distort',  'color': 'red'},
            ],
        }
    },
    'techno': {
        'description': 'Techno / electronic production layout',
        'config': {
            'bpm': 130,
            'time_signature': '4/4',
            'tracks': [
                {'name': 'Drums',    'type': 'group', 'color': 'dark_orange'},
                {'name': 'Kick',     'type': 'midi',  'color': 'orange',     'group': 'Drums'},
                {'name': 'Clap',     'type': 'midi',  'color': 'red',        'group': 'Drums'},
                {'name': 'CH',       'type': 'midi',  'color': 'yellow',     'group': 'Drums'},
                {'name': 'OH',       'type': 'midi',  'color': 'yellow',     'group': 'Drums'},
                {'name': 'Perc',     'type': 'midi',  'color': 'gold',       'group': 'Drums'},
                {'name': 'Ride',     'type': 'midi',  'color': 'cream',      'group': 'Drums'},
                {'name': 'Bass',     'type': 'midi',  'color': 'dark_purple'},
                {'name': 'Lead',     'type': 'midi',  'color': 'violet'},
                {'name': 'Pad',      'type': 'midi',  'color': 'lavender'},
                {'name': 'Stab',     'type': 'midi',  'color': 'magenta'},
                {'name': 'FX',       'type': 'midi',  'color': 'teal'},
                {'name': 'Noise',    'type': 'audio', 'color': 'gray'},
            ],
            'returns': [
                {'name': 'Reverb',   'color': 'blue'},
                {'name': 'Delay',    'color': 'cyan'},
                {'name': 'Chorus',   'color': 'mint'},
                {'name': 'Saturate', 'color': 'rust'},
            ],
        }
    },
    'ambient': {
        'description': 'Ambient / textural / atmospheric layout',
        'config': {
            'bpm': 80,
            'time_signature': '4/4',
            'tracks': [
                {'name': 'Pad A',     'type': 'midi',  'color': 'lavender'},
                {'name': 'Pad B',     'type': 'midi',  'color': 'sky'},
                {'name': 'Texture',   'type': 'audio', 'color': 'mint'},
                {'name': 'Drone',     'type': 'midi',  'color': 'dark_blue'},
                {'name': 'Melody',    'type': 'midi',  'color': 'ice'},
                {'name': 'Field Rec', 'type': 'audio', 'color': 'moss'},
                {'name': 'Granular',  'type': 'midi',  'color': 'purple'},
                {'name': 'Sub',       'type': 'midi',  'color': 'dark_purple'},
            ],
            'returns': [
                {'name': 'Big Verb',  'color': 'blue'},
                {'name': 'Shimmer',   'color': 'lavender'},
                {'name': 'Delay',     'color': 'cyan'},
            ],
        }
    },
    'mixing': {
        'description': 'Mixing/mastering template with stem groups',
        'config': {
            'bpm': 120,
            'time_signature': '4/4',
            'tracks': [
                {'name': 'Drums',     'type': 'group', 'color': 'dark_orange'},
                {'name': 'Kick',      'type': 'audio', 'color': 'orange',     'group': 'Drums'},
                {'name': 'Snare',     'type': 'audio', 'color': 'orange',     'group': 'Drums'},
                {'name': 'Overheads', 'type': 'audio', 'color': 'yellow',     'group': 'Drums'},
                {'name': 'Room',      'type': 'audio', 'color': 'sand',       'group': 'Drums'},
                {'name': 'Bass',      'type': 'group', 'color': 'dark_purple'},
                {'name': 'Bass DI',   'type': 'audio', 'color': 'purple',     'group': 'Bass'},
                {'name': 'Bass Amp',  'type': 'audio', 'color': 'purple',     'group': 'Bass'},
                {'name': 'Guitars',   'type': 'group', 'color': 'dark_yellow'},
                {'name': 'GTR L',     'type': 'audio', 'color': 'yellow',     'group': 'Guitars'},
                {'name': 'GTR R',     'type': 'audio', 'color': 'yellow',     'group': 'Guitars'},
                {'name': 'GTR Clean', 'type': 'audio', 'color': 'gold',       'group': 'Guitars'},
                {'name': 'Keys',      'type': 'group', 'color': 'dark_blue'},
                {'name': 'Piano',     'type': 'audio', 'color': 'blue',       'group': 'Keys'},
                {'name': 'Synth',     'type': 'audio', 'color': 'blue',       'group': 'Keys'},
                {'name': 'Vocals',    'type': 'group', 'color': 'dark_pink'},
                {'name': 'Lead Vox',  'type': 'audio', 'color': 'pink',       'group': 'Vocals'},
                {'name': 'Dubs',      'type': 'audio', 'color': 'rose',       'group': 'Vocals'},
                {'name': 'BGVs',      'type': 'audio', 'color': 'salmon',     'group': 'Vocals'},
                {'name': 'Harmony',   'type': 'audio', 'color': 'peach',      'group': 'Vocals'},
            ],
            'returns': [
                {'name': 'Short Verb',  'color': 'blue'},
                {'name': 'Long Verb',   'color': 'dark_blue'},
                {'name': 'Slap Delay',  'color': 'cyan'},
                {'name': 'Long Delay',  'color': 'dark_cyan'},
                {'name': 'Parallel',    'color': 'red'},
            ],
        }
    },
    'live-performance': {
        'description': 'Live performance / DJ hybrid set',
        'config': {
            'bpm': 126,
            'time_signature': '4/4',
            'tracks': [
                {'name': 'Deck A',     'type': 'audio', 'color': 'blue'},
                {'name': 'Deck B',     'type': 'audio', 'color': 'red'},
                {'name': 'Drums',      'type': 'group', 'color': 'dark_orange'},
                {'name': 'Kick',       'type': 'midi',  'color': 'orange',     'group': 'Drums'},
                {'name': 'Perc',       'type': 'midi',  'color': 'yellow',     'group': 'Drums'},
                {'name': 'Hats',       'type': 'midi',  'color': 'gold',       'group': 'Drums'},
                {'name': 'Synths',     'type': 'group', 'color': 'dark_violet'},
                {'name': 'Bass',       'type': 'midi',  'color': 'purple',     'group': 'Synths'},
                {'name': 'Lead',       'type': 'midi',  'color': 'violet',     'group': 'Synths'},
                {'name': 'Stabs',      'type': 'midi',  'color': 'magenta',    'group': 'Synths'},
                {'name': 'FX Riser',   'type': 'audio', 'color': 'teal'},
                {'name': 'FX Impact',  'type': 'audio', 'color': 'cyan'},
                {'name': 'Vox Chops',  'type': 'audio', 'color': 'pink'},
            ],
            'returns': [
                {'name': 'Reverb',     'color': 'blue'},
                {'name': 'Ping Pong',  'color': 'cyan'},
                {'name': 'Filter',     'color': 'green'},
            ],
        }
    },
}


# ──────────────────────────────────────────────
# Config Generation
# ──────────────────────────────────────────────

def create_sample_config(style: str = 'default', output_format: str = 'yaml') -> str:
    """Generate a sample config file."""
    if style not in STYLES:
        print(f"Error: Unknown style '{style}'. Use --list-styles to see options.")
        sys.exit(1)

    preset = STYLES[style]
    config = preset['config']

    # Add header comments
    header = f"# Ableton Session Template: {style}\n"
    header += f"# {preset['description']}\n"
    header += f"# Edit this file and run: session_templater.py --base your_set.als --config this_file.yaml\n\n"

    if output_format == 'yaml' and HAS_YAML:
        content = header + yaml.dump(config, default_flow_style=False, sort_keys=False)
        ext = 'yaml'
    else:
        content = json.dumps(config, indent=2)
        ext = 'json'

    script_dir = os.path.dirname(os.path.abspath(__file__))
    filename = os.path.join(script_dir, f"template_{style}.{ext}")
    with open(filename, 'w') as f:
        f.write(content)

    return filename


def load_config(path: str) -> dict:
    """Load a config from YAML or JSON."""
    with open(path, 'r') as f:
        content = f.read()

    # Try YAML first
    if HAS_YAML and (path.endswith('.yaml') or path.endswith('.yml')):
        return yaml.safe_load(content)

    # Try JSON
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        if HAS_YAML:
            return yaml.safe_load(content)
        else:
            print("Error: Could not parse config. Install PyYAML for .yaml support:")
            print("  pip install pyyaml")
            sys.exit(1)


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description='Ableton Session Templater — generate .als files from config templates',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --base default.als --config template.yaml -o my_session.als
  %(prog)s --create-config                     # default template
  %(prog)s --create-config --style techno       # techno template
  %(prog)s --create-config --style mixing       # mixing template
  %(prog)s --list-styles                        # see all presets
  %(prog)s --inspect my_session.als             # inspect existing .als

Workflow:
  1. Save any Ableton set as your base:  File > Save As > default.als
  2. Generate a config:  %(prog)s --create-config --style hiphop
  3. Edit template_hiphop.yaml to taste
  4. Generate:  %(prog)s --base default.als --config template_hiphop.yaml -o session.als
  5. Open session.als in Ableton Live!

Notes:
  - The base .als provides the XML structure (version, device chains, etc.)
  - The config controls track layout, naming, colors, groups, tempo
  - Use --inspect on your base file first to understand its structure
  - Ableton reads both gzipped and raw XML .als files
        """
    )

    parser.add_argument('--base', '-b', type=str,
                        help='Base .als file to use as template (any valid Ableton set)')
    parser.add_argument('--config', '-c', type=str,
                        help='Config file (YAML or JSON) defining the session')
    parser.add_argument('--output', '-o', type=str, default='session_output.als',
                        help='Output .als filename (default: session_output.als)')
    parser.add_argument('--no-compress', action='store_true',
                        help='Save as uncompressed XML (Ableton can read both)')

    parser.add_argument('--inspect', '-i', type=str, metavar='ALS_FILE',
                        help='Inspect an existing .als file')

    parser.add_argument('--create-config', action='store_true',
                        help='Generate a sample config file')
    parser.add_argument('--style', type=str, default='default',
                        help='Style preset for --create-config (default: default)')
    parser.add_argument('--json', action='store_true',
                        help='Output config as JSON instead of YAML')
    parser.add_argument('--list-styles', action='store_true',
                        help='List available style presets')
    parser.add_argument('--list-colors', action='store_true',
                        help='List available color names')

    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Print detailed output')

    # Sample picker flags
    parser.add_argument('--samples', type=str, metavar='DIR',
                        help='Sample library directory to pick drum samples from')
    parser.add_argument('--vibe', type=str, default=None,
                        help='Filter samples by vibe keyword (e.g. trap, lofi, dark)')
    parser.add_argument('--picks', type=int, default=3,
                        help='Number of sample options per drum slot (default: 3)')
    parser.add_argument('--exclude', nargs='+', default=None,
                        help='Keywords to exclude from sample picks')
    parser.add_argument('--seed', type=int, default=None,
                        help='Random seed for reproducible sample picks')

    return parser.parse_args()


def main():
    args = parse_args()

    # ── List styles ──
    if args.list_styles:
        print('\nAvailable Style Presets:')
        print('=' * 60)
        for name, style in STYLES.items():
            config = style['config']
            track_count = len(config.get('tracks', []))
            return_count = len(config.get('returns', []))
            print(f"\n  {name}")
            print(f"    {style['description']}")
            print(f"    {track_count} tracks, {return_count} returns, {config.get('bpm', '?')} BPM")
        print()
        return

    # ── List colors ──
    if args.list_colors:
        print('\nAvailable Color Names:')
        print('=' * 60)
        # Group by category
        seen = set()
        for name, idx in sorted(COLORS.items(), key=lambda x: x[1]):
            if idx not in seen:
                seen.add(idx)
                aliases = [n for n, i in COLORS.items() if i == idx and n != name]
                alias_str = f" (also: {', '.join(aliases)})" if aliases else ''
                print(f"  {idx:>3} │ {name:<20}{alias_str}")
        print()
        return

    # ── Create config ──
    if args.create_config:
        fmt = 'json' if args.json else 'yaml'
        filename = create_sample_config(args.style, fmt)
        print(f"✓ Created config: {filename}")
        print(f"  Edit it, then run:")
        print(f"  python session_templater.py --base your_set.als --config {filename}")
        return

    # ── Inspect ──
    if args.inspect:
        if not os.path.exists(args.inspect):
            print(f"Error: File not found: {args.inspect}")
            sys.exit(1)
        inspect_als(args.inspect)
        return

    # ── Generate session ──
    if not args.base or not args.config:
        print("Error: Both --base and --config are required to generate a session.")
        print("  Use --create-config to generate a sample config file.")
        print("  Use --help for full usage instructions.")
        sys.exit(1)

    if not os.path.exists(args.base):
        print(f"Error: Base file not found: {args.base}")
        sys.exit(1)
    if not os.path.exists(args.config):
        print(f"Error: Config file not found: {args.config}")
        sys.exit(1)

    # Load
    if args.verbose:
        print(f"\n{'═' * 60}")
        print(f"  Ableton Session Templater")
        print(f"{'═' * 60}")
        print(f"  Base: {args.base}")
        print(f"  Config: {args.config}")
        print(f"  Output: {args.output}")
        print(f"{'─' * 60}")

    config = load_config(args.config)
    root = read_als(args.base)

    # Apply
    root = apply_config(root, config, verbose=args.verbose)

    # Save
    write_als(root, args.output, compress=not args.no_compress)

    print(f"\n✓ Generated: {args.output}")
    track_count = len(config.get('tracks', []))
    return_count = len(config.get('returns', []))
    print(f"  {track_count} tracks, {return_count} returns, {config.get('bpm', '?')} BPM")
    print(f"  Open in Ableton Live: File > Open Live Set > {args.output}")

    if args.samples:
        samples_out = os.path.join(os.path.dirname(args.output) or '.', 'session_samples')
        run_sample_picker(
            samples_dir=args.samples,
            output_dir=samples_out,
            picks=max(1, min(10, args.picks)),
            vibe=args.vibe,
            exclude=args.exclude,
            seed=args.seed,
            verbose=args.verbose,
        )


# ──────────────────────────────────────────────
# Sample Picker (built-in)
# ──────────────────────────────────────────────

_SLOTS = {
    'kick': {
        'primary': ['kick', 'kck', 'kik', 'bd', 'bassdrum', 'bass_drum', 'bass drum'],
        'secondary': ['808'],
        'exclude': ['sidekick', 'hat', 'snare', 'perc', 'rim', 'clap', 'bass'],
    },
    'snare': {
        'primary': ['snare', 'snr', 'sd', 'clap', 'clp', 'rim', 'rimshot', 'snap'],
        'secondary': [],
        'exclude': ['kick', 'hat', 'ride'],
    },
    'hat': {
        'primary': ['hat', 'hh', 'hihat', 'hi-hat', 'hi hat', 'closedhat', 'openhat',
                    'closed_hat', 'open_hat', 'ch', 'oh'],
        'secondary': ['cymbal', 'ride', 'crash'],
        'exclude': ['kick', 'snare', 'clap'],
    },
    'perc': {
        'primary': ['perc', 'percussion', 'conga', 'bongo', 'shaker', 'tambourine',
                    'tamb', 'cowbell', 'woodblock', 'clave', 'guiro', 'triangle',
                    'tom', 'timbale', 'djembe', 'tabla', 'agogo', 'cabasa',
                    'maracas', 'vibraslap', 'castanet'],
        'secondary': ['click', 'tick', 'knock', 'tap', 'hit', 'impact',
                      'noise', 'fx', 'effect', 'rattle', 'scrape', 'metallic'],
        'exclude': ['kick', 'snare', 'hat', 'hihat', 'clap'],
    },
}
_AUDIO_EXTENSIONS = {'.wav', '.aif', '.aiff', '.flac', '.mp3', '.ogg'}


def _sp_classify(search_str: str, filename_str: str) -> Optional[str]:
    for slot, rules in _SLOTS.items():
        for keyword in rules['primary']:
            if keyword in filename_str or keyword in search_str.split(os.sep)[-3:]:
                if not any(ex in filename_str for ex in rules['exclude']):
                    return slot
    for slot, rules in _SLOTS.items():
        for keyword in rules['secondary']:
            if keyword in filename_str:
                if not any(ex in filename_str for ex in rules['exclude']):
                    return slot
    return None


def _sp_scan(root_dir: str) -> Dict[str, List[str]]:
    classified: Dict[str, List[str]] = defaultdict(list)
    root_path = Path(root_dir)
    if not root_path.exists():
        print(f"Error: Sample directory not found: {root_dir}")
        sys.exit(1)
    for filepath in root_path.rglob('*'):
        if filepath.is_file() and filepath.suffix.lower() in _AUDIO_EXTENSIONS:
            slot = _sp_classify(str(filepath).lower(), filepath.stem.lower())
            if slot:
                classified[slot].append(str(filepath))
    return dict(classified)


def _sp_filter_vibe(samples: Dict[str, List[str]], vibe: str) -> Dict[str, List[str]]:
    return {slot: ([p for p in paths if vibe.lower() in p.lower()] or paths)
            for slot, paths in samples.items()}


def _sp_filter_exclude(samples: Dict[str, List[str]], exclude: List[str]) -> Dict[str, List[str]]:
    return {slot: ([p for p in paths if not any(ex.lower() in p.lower() for ex in exclude)] or paths)
            for slot, paths in samples.items()}


def _sp_pick(samples: Dict[str, List[str]], picks: int, seed: Optional[int]) -> Dict[str, List[str]]:
    if seed is not None:
        random.seed(seed)
    result = {}
    for slot in ['kick', 'snare', 'hat', 'perc']:
        pool = samples.get(slot, [])
        result[slot] = random.sample(pool, min(picks, len(pool))) if pool else []
    return result


def _sp_copy(picks: Dict[str, List[str]], output_dir: str):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    for slot in ['kick', 'snare', 'hat', 'perc']:
        slot_dir = out / slot
        slot_dir.mkdir(exist_ok=True)
        for i, src in enumerate(picks.get(slot, []), 1):
            shutil.copy2(src, slot_dir / f"{i}_{Path(src).name}")
    # Write sample map
    lines = ["Sample Picks", "=" * 40, ""]
    for slot in ['kick', 'snare', 'hat', 'perc']:
        paths = picks.get(slot, [])
        lines.append(f"{slot.upper()} ({len(paths)} options):")
        for i, p in enumerate(paths, 1):
            lines.append(f"  {i}. {Path(p).name}")
            lines.append(f"     Source: {p}")
        lines.append("")
    (out / "sample_map.txt").write_text('\n'.join(lines))


def run_sample_picker(samples_dir: str, output_dir: str, picks: int,
                      vibe: Optional[str], exclude: Optional[List[str]],
                      seed: Optional[int], verbose: bool):
    samples_dir = os.path.expanduser(samples_dir)
    print(f"\n  Scanning samples: {samples_dir}")
    classified = _sp_scan(samples_dir)
    total = sum(len(v) for v in classified.values())
    if total == 0:
        print("  Warning: No drum samples found in that directory — skipping sample pick.")
        return
    if verbose:
        for slot in ['kick', 'snare', 'hat', 'perc']:
            print(f"    {slot:>6}: {len(classified.get(slot, []))} found")
    filtered = classified
    if vibe:
        filtered = _sp_filter_vibe(filtered, vibe)
        print(f"  Vibe filter: '{vibe}'")
    if exclude:
        filtered = _sp_filter_exclude(filtered, exclude)
    picked = _sp_pick(filtered, picks, seed)
    _sp_copy(picked, output_dir)
    print(f"  Samples → {output_dir}/")
    for slot in ['kick', 'snare', 'hat', 'perc']:
        n = len(picked.get(slot, []))
        print(f"    {slot:>6}: {n} option{'s' if n != 1 else ''}")


if __name__ == '__main__':
    main()
