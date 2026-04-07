// STASYS Enclosure — Full Parametric Model
// OpenSCAD parametric 3D design
//
// Files:
//   STASYS_enclosure.scad  — This file (full combined model)
//   enclosure_base.scad    — Base half only (for STL export)
//   enclosure_lid.scad     — Lid half only (for STL export)
//   enclosure_BOM.md       — Hardware BOM + assembly guide
//
// Usage in OpenSCAD:
//   1. File → Open → enclosure_base.scad
//   2. Design → Render (F6) or Preview (F5)
//   3. File → Export → Export as STL
//   4. Repeat for enclosure_lid.scad
//
//   Or open this file and comment/uncomment the render calls below
//
// For slicer (PrusaSlicer, Cura, etc.):
//   Import STL files. Recommended settings:
//     SLA/resin: 50µm layer, 2.0mm walls, standard resin
//     SLS (PA12): 60µm layer, 2.5mm walls
//

// ===============================
// PARAMETERS — edit these to customize
// ===============================
/* [Main Dimensions] */
INNER_WIDTH = 60;      // Internal X — must fit ESP32 DEVKIT V1
INNER_DEPTH = 45;      // Internal Y
INNER_HEIGHT = 22;     // Internal Z height
WALL = 2.5;           // Wall: 2.0mm SLA, 2.5mm PA12/SLS
BASE_HEIGHT = 8;       // Picatinny mount body height
LID_HEIGHT = 4;        // Lid cap height

/* [Picatinny] */
PICA_SPACING = 10.01; // MIL-STD-1913 slot spacing
PICA_SLOTS = 2;        // Slots base straddles
CROSSBOLT_D = 3.0;     // M3 cross-bolt
RECOIL_LUG_H = 3.0;    // Lug height into slot

/* [Components] */
MPU6050_OFF_X = 6;
MPU6050_OFF_Y = 6;
MPU6050_W = 21;
MPU6050_D = 16;
BATTERY_L = 50;
BATTERY_W = 34;

/* [Hardware] */
SCREW_D = 3.0;
SCREW_HEAD_D = 6.0;
SCREW_CSINK = 1.5;
INSERT_D = 4.4;

/* [Gasket] */
GASKET_D = 1.0;
GASKET_W = 2.0;

/* [Features] */
USB_W = 10;
LED_PIPE_D = 2.5;
VENT_D = 6.0;
PIEZO_PAD_D = 10;
PIEZO_PAD_H = 2;

$fn = 64;

// ===============================
// COMPUTED
// ===============================
OUTER_W = INNER_WIDTH + 2 * WALL;
OUTER_D = INNER_DEPTH + 2 * WALL;
OUTER_H = BASE_HEIGHT + INNER_HEIGHT + LID_HEIGHT;
PICA_BODY_W = PICA_SLOTS * PICA_SPACING + 4;

BOSS_INSET = 5;
boss_pos = [
    [BOSS_INSET, BOSS_INSET],
    [BOSS_INSET, OUTER_D - BOSS_INSET],
    [OUTER_W - BOSS_INSET, BOSS_INSET],
    [OUTER_W - BOSS_INSET, OUTER_D - BOSS_INSET]
];
standoff_pos = [
    [BOSS_INSET + 2, BOSS_INSET + 2],
    [BOSS_INSET + 2, INNER_DEPTH - 2],
    [INNER_WIDTH - 2, BOSS_INSET + 2],
    [INNER_WIDTH - 2, INNER_DEPTH - 2]
];

// ===============================
// GASKET CHANNEL 2D
// ===============================
module gasket_channel_2d(w, h, d, dw) {
    difference() {
        square([w, h]);
        offset(delta = -d)
            square([w, h]);
    }
    intersection() {
        difference() {
            offset(delta = -d)
                square([w, h]);
            offset(delta = -(d + dw))
                square([w, h]);
        }
        square([w, h]);
    }
}

// ===============================
// BASE PROFILE 2D
// ===============================
module base_profile_2d() {
    difference() {
        translate([(OUTER_W - PICA_BODY_W)/2, 0])
            square([PICA_BODY_W, OUTER_H]);
        union() {
            square([OUTER_W, OUTER_H]);
        }
        translate([0, BASE_HEIGHT + INNER_HEIGHT])
            gasket_channel_2d(OUTER_W, OUTER_H, GASKET_D, GASKET_W);
        for (p = boss_pos)
            translate(p)
                circle(d = SCREW_HEAD_D + 0.3);
        for (p = boss_pos)
            translate(p)
                circle(d = INSERT_D + 0.2);
        translate([OUTER_W - 10, OUTER_H + 1])
            circle(d = LED_PIPE_D + 0.5);
    }
}

// ===============================
// LID PROFILE 2D
// ===============================
module lid_profile_2d() {
    difference() {
        translate([(OUTER_W - PICA_BODY_W)/2, 0])
            square([PICA_BODY_W, LID_HEIGHT]);
        square([OUTER_W, LID_HEIGHT]);
        for (p = boss_pos)
            translate(p)
                circle(d = SCREW_HEAD_D + 0.3);
        translate([OUTER_W/2, OUTER_D/2])
            circle(d = VENT_D);
    }
}

// ===============================
// BASE ASSEMBLY
// ===============================
module stasys_base() {
    // Main body
    translate([0, 0, 0])
        linear_extrude(height = BASE_HEIGHT + INNER_HEIGHT)
            base_profile_2d();

    // LED pipe sleeve
    translate([OUTER_W - 10, OUTER_D/2, 0])
        cylinder(d = LED_PIPE_D - 0.5, h = BASE_HEIGHT + INNER_HEIGHT + 3);

    // Cross-bolt channel
    translate([(OUTER_W - PICA_BODY_W)/2 + 2, OUTER_D/2, 0])
        rotate([90, 0, 0])
            translate([0, 0, -PICA_BODY_W/2 - 2])
                cylinder(d = CROSSBOLT_D, h = PICA_BODY_W + 4);

    // Screw holes + counterbores
    for (p = boss_pos) {
        translate([p[0], p[1], 0])
            cylinder(d = SCREW_D, h = BASE_HEIGHT + INNER_HEIGHT + LID_HEIGHT + 2);
        translate([p[0], p[1], BASE_HEIGHT + INNER_HEIGHT - SCREW_CSINK])
            cylinder(d = SCREW_HEAD_D + 0.3, h = SCREW_CSINK + 1);
        translate([p[0], p[1], BASE_HEIGHT + INNER_HEIGHT])
            cylinder(d = INSERT_D, h = LID_HEIGHT + 2);
    }

    // USB cutout
    translate([OUTER_W/2 - USB_W/2, -1, BASE_HEIGHT + INNER_HEIGHT/2 - 4])
        cube([USB_W, WALL + 2, 8]);

    // LED bore (side)
    translate([OUTER_W - 10, OUTER_D/2 + 2, BASE_HEIGHT + INNER_HEIGHT/2])
        rotate([0, 90, 0])
            cylinder(d = LED_PIPE_D + 0.5, h = WALL + 3);

    // Recoil lugs
    translate([OUTER_W/2, 0, 0]) {
        half_span = (PICA_SLOTS * PICA_SPACING) / 2 - 1;
        for (x = [-half_span, half_span]) {
            translate([x, -0.1])
                hull() {
                    circle(d = 6);
                    translate([0, -RECOIL_LUG_H])
                        circle(d = 4);
                }
        }
    }

    // Interior cavity
    translate([WALL, WALL, BASE_HEIGHT])
        cube([OUTER_W - 2*WALL, OUTER_D - 2*WALL, INNER_HEIGHT + 1]);

    // PCB standoffs
    translate([WALL, WALL, BASE_HEIGHT - 1])
        for (s = standoff_pos)
            translate([s[0], s[1], 0])
                cylinder(d = 4, h = 2);

    // MPU6050 recess
    translate([WALL + MPU6050_OFF_X, WALL + MPU6050_OFF_Y, 0])
        cube([MPU6050_W + 2, MPU6050_D + 2, 2]);

    // Piezo contact pad
    translate([WALL + INNER_WIDTH - PIEZO_PAD_D - 4,
               WALL + INNER_DEPTH - PIEZO_PAD_D - 4,
               -PIEZO_PAD_H])
        cylinder(d = PIEZO_PAD_D, h = PIEZO_PAD_H);
}

// ===============================
// LID ASSEMBLY
// ===============================
module stasys_lid() {
    // Main body
    linear_extrude(height = LID_HEIGHT)
        lid_profile_2d();

    // Gasket channel on bottom face
    translate([0, 0, LID_HEIGHT - GASKET_D])
        linear_extrude(height = GASKET_D + 0.5)
            gasket_channel_2d(OUTER_W, LID_HEIGHT, GASKET_D, GASKET_W);

    // Screw holes
    for (p = boss_pos)
        translate([p[0], p[1], 0])
            cylinder(d = SCREW_D, h = LID_HEIGHT + 1);

    // Battery recess
    translate([WALL, WALL, -0.1])
        cube([BATTERY_L + 4, BATTERY_W + 4, LID_HEIGHT - GASKET_D + 0.1]);

    // Vent port recess
    translate([OUTER_W/2, OUTER_D/2, LID_HEIGHT - 2])
        cylinder(d = VENT_D - 1, h = 3);
}

// ===============================
// SILICONE GASKET (flat profile)
// ===============================
module stasys_gasket() {
    linear_extrude(height = 1)
        gasket_channel_2d(OUTER_W, OUTER_H, GASKET_D, GASKET_W);
}

// ===============================
// RENDER — uncomment the part you want
// ===============================
// Base (bottom half with Picatinny mount)
stasys_base();

// Lid (top half)
//stasys_lid();

// Silicone gasket (1mm flat sheet, print from 1mm silicone or cut from sheet)
//translate([OUTER_W + 10, 0, 0])
//    stasys_gasket();

// ===============================
// ASSEMBLY VIEW
// ===============================
// Full assembly with gap between base and lid
translate([0, 0, BASE_HEIGHT + INNER_HEIGHT])
    stasys_lid();

// Echo dimensions
echo("=== STASYS ENCLOSURE ===");
echo(str("Outer: ", OUTER_W, "×", OUTER_D, "×", OUTER_H, " mm"));
echo(str("Inner: ", INNER_WIDTH, "×", INNER_DEPTH, "×", INNER_HEIGHT, " mm"));
echo(str("Picatinny body: ", PICA_BODY_W, " mm (", PICA_SLOTS, " slots)"));
echo(str("Cross-bolt: M3×", PICA_BODY_W+4, " mm"));
echo(str("Lid screws: M3×6 mm CSK (4×)"));
