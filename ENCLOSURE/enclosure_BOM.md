# STASYS Enclosure — Bill of Materials

## 3D Printed Parts

| Qty | Part | File | Material | Print Orientation |
|-----|------|------|----------|-----------------|
| 1 | Base (bottom half + Picatinny rail) | `enclosure_base.scad` | PA12 SLS (recommended) | Flat on build plate |
| 1 | Lid (top half) | `enclosure_lid.scad` | PA12 SLS (recommended) | Flat on build plate |
| 1 | Silicone gasket (optional) | Cut from 1mm silicone sheet | Silicone rubber | N/A |

**Alternative materials**: High-strength SLA resin (Formlabs Durable, Siraya Tech Fast ABS-like). Avoid standard clear/white SLA — brittle under recoil.

## Hardware (Purchase)

### Fasteners

| Qty | Description | Spec | Notes |
|-----|-------------|------|-------|
| 4 | M3×0.5×6mm countersunk head screw | ISO 7380 | Lid-to-base assembly |
| 1 | M3×0.5×16mm socket head cap screw | ISO 4762 | Picatinny cross-bolt |
| 4 | M3 brass heat-set threaded insert | OD 4.2mm, ID M3, length 5mm | Base screw bosses |
| 4 | M3 flat washer | OD 6mm, ID 3.2mm | Under cross-bolt / lid screws |
| 1 | Threadlocker (blue Loctite 243) | Medium strength | On cross-bolt threads |

### Threaded Insert Reference
Install heat-set inserts into base after printing:
- **Method**: Preheat soldering iron to 250°C, press insert flush
- **PA12**: Pre-heat insert area with hot air gun first
- **SLA resin**: Can insert directly, no pre-heat needed

### Vent Membrane (Optional — for IP54)

| Qty | Description | Spec | Source |
|-----|-------------|------|--------|
| 1 | ePTFE breathable membrane | 6mm disc, 0.2mm thick | e.g., eWON Vent (Digikey: 1834-1015-ND) |
| 1 | Vent housing ring (3D printed) | Fits VENT_PORT_D recess | Print with lid |

### Gasket (Optional — for IP54)

Cut from 1mm silicone sheet (craft rubber or silicone gasket material):
- Trace the outer perimeter of the base top face
- Cut out the inner rectangle (gasket channel)
- Punch holes at 4 screw positions
- Punch hole at vent port center

## Assembly Guide

### Tools Required
- Phillips #0 screwdriver (for M3 countersunk screws)
- 2.5mm hex key / 2.5mm hex driver (for cross-bolt)
- Soldering iron with 4mm flat tip (for heat-set inserts)
- Calipers (to verify dimensions)
- Multimeter (for electrical testing)

### Assembly Steps

#### Step 1: Post-Processing (SLA parts)
1. Remove supports carefully
2. Wash in IPA (or water for water-washable resin)
3. Post-cure per resin manufacturer's instructions (30 min at 60°C for Formlabs)
4. Sand any support marks with 400-grit sandpaper
5. Fill and prime if desired for surface finish

#### Step 2: Install Heat-Set Inserts (Base)
1. Heat soldering iron to 250°C
2. Using tweezers, place insert into base screw boss hole
3. Press insert flush with flat face of iron
4. Insert should melt into plastic, sit 0.2mm below surface
5. Let cool 30 seconds before handling
6. Repeat for all 4 bosses

#### Step 3: Test Fit
1. Place ESP32 DEVKIT V1 into base cavity — verify clearances
2. Test lid fits on base — verify gasket channel alignment
3. Test M3 screws thread into inserts smoothly

#### Step 4: Wire Routing
1. Route USB-C cable from ESP32 to USB cutout on side
2. Route MPU6050 I2C wires to front area
3. Route piezo wire to piezo contact pad location
4. Use small zip ties or adhesive foam to secure wires

#### Step 5: Install Vent Membrane (Optional)
1. Apply thin layer of silicone adhesive around vent port recess
2. Press ePTFE membrane disc into recess
3. Let cure 24 hours before assembly

#### Step 6: Install Silicone Gasket (Optional)
1. Cut gasket from 1mm silicone sheet
2. Place gasket into base gasket channel
3. Verify gasket is seated fully in channel

#### Step 7: Final Assembly
1. Insert ESP32 DEVKIT V1 + wiring into base
2. Place lid on base — align carefully
3. Install 4× M3×6mm countersunk screws — snug, do not over-torque
4. Torque: 0.3–0.5 N·m (hand tight plus 1/4 turn with hex key)

#### Step 8: Picatinny Rail Installation
1. Slide recoil lug into Picatinny slot from the front
2. Slide device along rail until lug sits in slot notch
3. Thread M3×16mm cross-bolt through base, into slot notch
4. Tighten cross-bolt with hex key — 0.5 N·m (hand tight)
5. Apply blue threadlocker to bolt threads before final tightening

### Disassembly for Programming
1. Loosen cross-bolt, slide device off rail
2. Remove 4 lid screws
3. Lift lid off
4. Access USB-C port for programming
5. Reassemble in reverse order

## Dimension Summary

```
┌─────────────────────────────────────────────┐
│ ENCLOSURE OUTER (W×D×H): 65 × 50 × 34 mm   │
│ INTERNAL CAVITY (W×D×H): 60 × 45 × 22 mm   │
│ BASE HEIGHT: 8 mm                           │
│ LID HEIGHT: 4 mm                            │
│ WALL THICKNESS: 2.5 mm                      │
│ PICA BODY WIDTH: 24 mm (2-slot)            │
│ RECOIL LUG HEIGHT: 3 mm                     │
│ CROSS-BOLT: M3×16mm                        │
│ LID SCREWS: M3×6mm countersunk (4x)         │
│ BATTERY RECESS: 54×38×2 mm                 │
│ MPU6050 POCKET: 23×18×2 mm                 │
│ PIEZO PAD: Ø10mm × 2mm protrusion          │
│ LED PIPE: Ø2.5mm bore                      │
│ VENT PORT: Ø6mm recess                      │
│ USB CUTOUT: 10×8mm on side face             │
└─────────────────────────────────────────────┘
```

## Estimated Weight

| Component | Weight |
|-----------|--------|
| Base (PA12 printed, ~25g) | ~20g |
| Lid (PA12 printed, ~12g) | ~10g |
| Gasket (silicone, 1mm) | ~2g |
| M3×6mm screws (4x) | ~2g |
| M3×16mm cross-bolt | ~1g |
| Heat-set inserts (4x) | ~1g |
| **Total** | **~36g** |
| + Device (ESP32 + components + battery) | ~50g |
| **Grand total on weapon** | **~86g** |
