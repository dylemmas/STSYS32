// STASYS Enclosure — BASE (Bottom Half)
// OpenSCAD model for SLA/SLS 3D printing
// Print: flat on build plate, no supports
//
// Hardware BOM: see enclosure_BOM.md
//
// Material: PA12 SLS recommended (tough, fatigue-resistant)
// SLA: use high-strength resin (Formlabs Durable, Siraya Tech Fast ABS)
//

// ===============================
// PARAMETERS
// ===============================
/* [Main Dimensions] */
INNER_WIDTH = 60;      // Internal X — must fit ESP32 DEVKIT V1 (~55mm)
INNER_DEPTH = 45;      // Internal Y — must fit DEVKIT V1 (~27mm) + wiring
INNER_HEIGHT = 22;     // Internal Z height — DEVKIT V1 (~20mm) + components
WALL = 2.5;           // Wall thickness: 2.0mm SLA, 2.5mm PA12/SLS
BASE_HEIGHT = 8;       // Height of Picatinny mount body (below cavity)
LID_HEIGHT = 4;        // Lid cap height

/* [Picatinny Rail Interface] */
PICA_SPACING = 10.01; // MIL-STD-1913 slot spacing
PICA_SLOTS = 2;        // Number of rail slots base straddles
CROSSBOLT_D = 3.0;     // Cross-bolt (M3) diameter
RECOIL_LUG_H = 3.0;    // Lug protrusion into Picatinny slot notch

/* [Components] */
MPU6050_OFFSET_X = 6;  // MPU6050 position from base inner corner
MPU6050_OFFSET_Y = 6;
MPU6050_W = 21;
MPU6050_D = 16;
BATTERY_L = 50;
BATTERY_W = 34;

/* [Hardware] */
SCREW_D = 3.0;         // M3 screw diameter
SCREW_HEAD_D = 6.0;    // M3 countersunk head diameter
SCREW_CSINK = 1.5;     // Countersink depth
INSERT_D = 4.4;         // M3 heat-set insert OD

/* [Gasket] */
GASKET_D = 1.0;        // Gasket channel depth (into base top face)
GASKET_W = 2.0;        // Gasket channel width

/* [Features] */
USB_W = 10;            // USB-C cutout width
LED_PIPE_D = 2.5;      // LED light pipe bore diameter
VENT_D = 6.0;          // Vent membrane port diameter
PIEZO_PAD_D = 10;      // Piezo contact pad diameter
PIEZO_PAD_H = 2;       // Piezo pad protrusion

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

// ===============================
// GASKET CHANNEL 2D — using offset for clean geometry
// ===============================
// Outer boundary minus inner boundary = channel ring
// offset(-GASKET_D) shrinks inner rect, creating channel
module gasket_channel_2d() {
    difference() {
        square([OUTER_W, OUTER_H]);
        offset(delta = -GASKET_D)
            square([OUTER_W, OUTER_H]);
    }
    // Also cut a ring around the inner island
    // (inner island: INNER_W × INNER_D, shrunk by GASKET_W)
    intersection() {
        difference() {
            offset(delta = -GASKET_D)
                square([OUTER_W, OUTER_H]);
            offset(delta = -(GASKET_D + GASKET_W))
                square([OUTER_W, OUTER_H]);
        }
        square([OUTER_W, OUTER_H]);
    }
}

// ===============================
// BASE — 2D profile then extrude
// ===============================
module base_profile_2d() {
    difference() {
        // Picatinny body extends beyond main body on X sides
        translate([(OUTER_W - PICA_BODY_W)/2, 0])
            square([PICA_BODY_W, OUTER_H]);

        // Main outer footprint (for union with Picatinny extension)
        union() {
            square([OUTER_W, OUTER_H]);
        }

        // Gasket channel on top face
        translate([0, BASE_HEIGHT + INNER_HEIGHT])
            gasket_channel_2d();

        // Screw counterbores (at top of base = lid mating face)
        translate([0, BASE_HEIGHT + INNER_HEIGHT])
            for (p = boss_pos)
                translate(p)
                    circle(d = SCREW_HEAD_D + 0.3);

        // Threaded insert holes (partially through top of base)
        translate([0, BASE_HEIGHT + INNER_HEIGHT - 2])
            for (p = boss_pos)
                translate(p)
                    circle(d = INSERT_D + 0.2);

        // LED pipe bore (through top)
        translate([OUTER_W - 10, OUTER_H + 1])
            circle(d = LED_PIPE_D + 0.5);

        // Vent port (center, for reference)
        translate([OUTER_W/2, OUTER_H/2])
            circle(d = VENT_D);
    }
}

// ===============================
// BASE 3D ASSEMBLY
// ===============================
// Outer shell
translate([0, 0, 0])
    linear_extrude(height = BASE_HEIGHT + INNER_HEIGHT)
        base_profile_2d();

// LED light pipe sleeve (protrudes above top face)
translate([OUTER_W - 10, OUTER_D/2, 0])
    cylinder(d = LED_PIPE_D - 0.5, h = BASE_HEIGHT + INNER_HEIGHT + 3);

// Cross-bolt channel (through Picatinny body, perpendicular to rail axis Y)
translate([(OUTER_W - PICA_BODY_W)/2 + 2, OUTER_D/2, 0])
    rotate([90, 0, 0])
        translate([0, 0, -PICA_BODY_W/2 - 2])
            cylinder(d = CROSSBOLT_D, h = PICA_BODY_W + 4);

// Screw through-holes (full depth)
for (p = boss_pos)
    translate([p[0], p[1], 0])
        cylinder(d = SCREW_D, h = BASE_HEIGHT + INNER_HEIGHT + LID_HEIGHT + 2);

// Counterbores
for (p = boss_pos)
    translate([p[0], p[1], BASE_HEIGHT + INNER_HEIGHT - SCREW_CSINK])
        cylinder(d = SCREW_HEAD_D + 0.3, h = SCREW_CSINK + 1);

// Threaded insert holes
for (p = boss_pos)
    translate([p[0], p[1], BASE_HEIGHT + INNER_HEIGHT])
        cylinder(d = INSERT_D, h = LID_HEIGHT + 2);

// USB cutout (Y=0 face)
translate([OUTER_W/2 - USB_W/2, -1, BASE_HEIGHT + INNER_HEIGHT/2 - 4])
    cube([USB_W, WALL + 2, 8]);

// LED bore (horizontal, through side)
translate([OUTER_W - 10, OUTER_D/2 + 2, BASE_HEIGHT + INNER_HEIGHT/2])
    rotate([0, 90, 0])
        cylinder(d = LED_PIPE_D + 0.5, h = WALL + 3);

// Recoil lugs (protrude from Picatinny body bottom)
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

// Interior cavity (hollow out)
translate([WALL, WALL, BASE_HEIGHT])
    cube([OUTER_W - 2*WALL, OUTER_D - 2*WALL, INNER_HEIGHT + 1]);

// PCB standoffs (inside base, on bottom)
translate([WALL, WALL, BASE_HEIGHT - 1]) {
    standoff_pos = [
        [BOSS_INSET + 2, BOSS_INSET + 2],
        [BOSS_INSET + 2, INNER_DEPTH - 2],
        [INNER_WIDTH - 2, BOSS_INSET + 2],
        [INNER_WIDTH - 2, INNER_DEPTH - 2]
    ];
    for (s = standoff_pos)
        translate([s[0], s[1], 0])
            cylinder(d = 4, h = 2);
}

// MPU6050 recess pocket (in base bottom)
translate([WALL + MPU6050_OFFSET_X, WALL + MPU6050_OFFSET_Y, 0])
    cube([MPU6050_W + 2, MPU6050_D + 2, 2]);

// Piezo contact pad (protrudes from base bottom for barrel contact)
translate([WALL + INNER_WIDTH - PIEZO_PAD_D - 4,
           WALL + INNER_DEPTH - PIEZO_PAD_D - 4,
           -PIEZO_PAD_H])
    cylinder(d = PIEZO_PAD_D, h = PIEZO_PAD_H);

// Battery recess in base (optional — battery can also sit in lid)
translate([WALL + 2, WALL + 2, BASE_HEIGHT + INNER_HEIGHT - BATTERY_L/2])
    rotate([90, 0, 0])
        cube([BATTERY_W + 2, 4, BATTERY_L + 2]);

// ===============================
// DIMENSIONS ECHO
// ===============================
echo("=== BASE DIMENSIONS ===");
echo(str("Outer (W×D×H): ", OUTER_W, "×", OUTER_D, "×", OUTER_H, " mm"));
echo(str("Internal cavity (W×D×H): ", INNER_WIDTH, "×", INNER_DEPTH, "×", INNER_HEIGHT, " mm"));
echo(str("Picatinny body: ", PICA_BODY_W, " mm wide (", PICA_SLOTS, " slots)"));
echo(str("Recoil lug height: ", RECOIL_LUG_H, " mm"));
echo(str("Cross-bolt: M3×", PICA_BODY_W + 4, " mm"));
echo(str("Lid screws: M3×6 mm CSK (4×)"));
echo(str("Wall thickness: ", WALL, " mm"));
