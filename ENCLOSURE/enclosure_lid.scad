// STASYS Enclosure — LID (Top Half)
// OpenSCAD model for SLA/SLS 3D printing
// Print: flat on build plate (lid height), no supports
//
// Hardware BOM: see enclosure_BOM.md
//
// Material: PA12 SLS recommended
//

// ===============================
// PARAMETERS (must match base)
// ===============================
INNER_WIDTH = 60;
INNER_DEPTH = 45;
INNER_HEIGHT = 22;
WALL = 2.5;
BASE_HEIGHT = 8;
LID_HEIGHT = 4;

PICA_SPACING = 10.01;
PICA_SLOTS = 2;

SCREW_D = 3.0;
SCREW_HEAD_D = 6.0;
SCREW_CSINK = 1.5;
INSERT_D = 4.4;

GASKET_D = 1.0;
GASKET_W = 2.0;

BATTERY_L = 50;
BATTERY_W = 34;
VENT_D = 6.0;

$fn = 64;

// ===============================
// COMPUTED
// ===============================
OUTER_W = INNER_WIDTH + 2 * WALL;
OUTER_D = INNER_DEPTH + 2 * WALL;
PICA_BODY_W = PICA_SLOTS * PICA_SPACING + 4;

BOSS_INSET = 5;
boss_pos = [
    [BOSS_INSET, BOSS_INSET],
    [BOSS_INSET, OUTER_D - BOSS_INSET],
    [OUTER_W - BOSS_INSET, BOSS_INSET],
    [OUTER_W - BOSS_INSET, OUTER_D - BOSS_INSET]
];

// ===============================
// GASKET CHANNEL 2D (matches base)
// ===============================
module gasket_channel_2d() {
    difference() {
        square([OUTER_W, LID_HEIGHT]);
        offset(delta = -GASKET_D)
            square([OUTER_W, LID_HEIGHT]);
    }
    intersection() {
        difference() {
            offset(delta = -GASKET_D)
                square([OUTER_W, LID_HEIGHT]);
            offset(delta = -(GASKET_D + GASKET_W))
                square([OUTER_W, LID_HEIGHT]);
        }
        square([OUTER_W, LID_HEIGHT]);
    }
}

// ===============================
// LID PROFILE 2D
// ===============================
module lid_profile_2d() {
    difference() {
        // Picatinny body + main lid footprint
        translate([(OUTER_W - PICA_BODY_W)/2, 0])
            square([PICA_BODY_W, LID_HEIGHT]);
        square([OUTER_W, LID_HEIGHT]);

        // Screw counterbores (top surface)
        for (p = boss_pos)
            translate(p)
                circle(d = SCREW_HEAD_D + 0.3);

        // Vent port (center)
        translate([OUTER_W/2, OUTER_D/2])
            circle(d = VENT_D);
    }
}

// ===============================
// LID 3D
// ===============================
// Outer shell
linear_extrude(height = LID_HEIGHT)
    lid_profile_2d();

// Gasket channel on bottom face (complementary to base)
translate([0, 0, LID_HEIGHT - GASKET_D])
    linear_extrude(height = GASKET_D + 0.5)
        gasket_channel_2d();

// Screw through-holes
for (p = boss_pos)
    translate([p[0], p[1], 0])
        cylinder(d = SCREW_D, h = LID_HEIGHT + 1);

// Battery recess (bottom face — battery sits against PCB, lid clears it)
translate([WALL, WALL, -0.1])
    cube([BATTERY_L + 4, BATTERY_W + 4, LID_HEIGHT - GASKET_D + 0.1]);

// Vent membrane port (partial depth, to accept membrane disc)
translate([OUTER_W/2, OUTER_D/2, LID_HEIGHT - 2])
    cylinder(d = VENT_D - 1, h = 3);

echo("=== LID DIMENSIONS ===");
echo(str("Outer (W×D×H): ", OUTER_W, "×", OUTER_D, "×", LID_HEIGHT, " mm"));
echo(str("Battery recess: ", BATTERY_L+4, "×", BATTERY_W+4, "×", LID_HEIGHT - GASKET_D, " mm"));
echo(str("Vent port: Ø", VENT_D, " mm"));
