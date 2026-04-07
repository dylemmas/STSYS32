# STASYS Enclosure — 3D Printable Design

Parametric OpenSCAD models for the STASYS training device enclosure.

## Files

| File | Description |
|------|-------------|
| `STASYS_enclosure.scad` | Full parametric model (all-in-one, assembly view) |
| `enclosure_base.scad` | Base half only — export as STL for printing |
| `enclosure_lid.scad` | Lid half only — export as STL for printing |
| `enclosure_BOM.md` | Bill of materials + assembly guide |

## Quick Start

1. Install [OpenSCAD](https://openscad.org/) (free, Windows/Mac/Linux)
2. Open `enclosure_base.scad`
3. Press **F5** for preview or **F6** for full render
4. Press **F7** to export as STL
5. Repeat for `enclosure_lid.scad`
6. Slice in your slicer (PrusaSlicer, Cura, Lychee, etc.)
7. Print in PA12 SLS or high-strength SLA resin

## Design Overview

```
TOP VIEW (cross-section at Z=max)

        ┌─────────────────────────────────────┐
        │  ╔═══════════════════════════════╗  │
        │  ║                               ║  │
        │  ║     INTERNAL CAVITY           ║  │
        │  ║   ┌─────────────────────┐    ║  │
        │  ║   │   ESP32 DEVKIT V1   │    ║  │
        │  ║   │   + MPU6050 + wires │    ║  │
        │  ║   │   + LiPo battery   │    ║  │
        │  ║   └─────────────────────┘    ║  │
        │  ║                               ║  │
        │  ╚═══════════════════════════════╝  │
        └─────────────────────────────────────┘
              ↑↑       ↑↑  ← GASKET CHANNEL (perimeter)
             (slots straddle Picatinny rail slots)

FRONT VIEW

     ┌─────────────────────────┐
     │  LED PIPE  │  VENT PORT│
     ├─────────────────────────┤
     │                         │
     │    INTERNAL CAVITY      │
     │                         │
     ├─────────────────────────┤
     │         USB-C           │
     └─────────────────────────┘
              ↓
        PICATINNY RAIL
        (slots engage below)

BOTTOM VIEW

        ════════════════════     ← Picatinny rail slot
        │▓▓▓▓▓│     │▓▓▓▓▓│        ← Recoil lugs (2x)
        │     │     │     │
        │     └─────┘     │        ← Cross-bolt goes here
        │                 │
        └─────────────────┘
        ┌─────────────────┐
        │ ● ● ● ●        │        ← MPU6050 recess
        │    PIEZO ●     │        ← Piezo contact pad
        └─────────────────┘
```

## Key Dimensions

| Parameter | Value |
|-----------|-------|
| Outer (W×D×H) | 65×50×34 mm |
| Internal cavity | 60×45×22 mm |
| Wall thickness | 2.5 mm |
| Base height | 8 mm |
| Lid height | 4 mm |
| Picatinny body | 24 mm (2 slots) |
| Recoil lug | 3 mm protrusion |
| Weight (printed PA12) | ~30g (base + lid) |
| Total with hardware | ~36g |
| Total on weapon | ~86g (with device) |

## Picatinny Rail Installation

1. Slide recoil lugs into Picatinny slot from the front
2. Slide device along rail until lugs sit in slot notch
3. Thread M3×16mm cross-bolt through base, into slot notch
4. Tighten to 0.5 N·m (hand tight + quarter turn)
5. Apply blue threadlocker to cross-bolt

## Parameter Customization

All key dimensions are defined at the top of each `.scad` file. Adjust these before rendering:

```scad
// Main dimensions
INNER_WIDTH = 60;      // Change if your PCB is wider
INNER_DEPTH = 45;      // Change if your PCB is deeper
WALL = 2.5;             // 2.0mm for SLA, 2.5mm for PA12

// Picatinny engagement
PICA_SLOTS = 2;         // 1 slot = less stable, 3 slots = more stable
RECOIL_LUG_H = 3.0;     // Increase if slot is deeper

// Component clearances
MPU6050_OFF_X = 6;      // Reposition MPU6050 pocket
PIEZO_PAD_D = 10;        // Larger/smaller piezo contact
```

## Recommended Print Settings

### PA12 SLS (recommended for base)
- Layer height: 60 µm
- Wall thickness: 2.5mm (3 perimeters)
- No supports needed (flat on build plate)
- Post-process: bead blast or tumble for surface smoothness

### High-Strength SLA
- Layer height: 50 µm
- Wall thickness: 2.0mm
- Material: Formlabs Durable, Siraya Tech Fast ABS, or equivalent
- Post-cure: 30 min at 60°C

## Post-Processing

1. **Heat-set inserts**: Soldering iron 250°C, press M3 brass inserts flush into base bosses
2. **Sand + prime**: 400-grit for support marks, filler primer for SLA
3. **Threadlocker**: Blue Loctite 243 on cross-bolt threads
4. **Silicone gasket**: Cut from 1mm sheet (see enclosure_BOM.md)

## Notes

- The base and lid are designed to be printed flat (no supports needed)
- PA12 SLS is recommended for the base due to fatigue resistance from recoil
- SLA resin is acceptable for the lid (non-structural)
- The Picatinny body extends 2.5mm beyond the main body on each side
- The recoil lugs are designed for MIL-STD-1913 Picatinny rails
- For Weaver rails (0.180" slots), file the lugs slightly narrower
