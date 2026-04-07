// STASYS Picatinny Adapter Bracket
// Mounts the watch-style case to a Picatinny rail
// Measures from STL analysis of "MainCase watch esp 32.stl"
//
// Case: 66.2 x 55.5 x 30.3 mm overall
// Flat bottom: Z = 0 (reference)
// 4 bosses on bottom face: cylindrical features, dia ~3.8mm, rise ~2mm
//   BL: (-27.9, -18.9)  BR: (35.4, -18.9)
//   TL: (-27.4,  18.1)  TR: (34.3,  18.6)
//
// Print: PA12 SLS (recoil-safe), flat on build plate
// Assembly:
//   1. Drill 4x M3 clearance holes (3.2mm) through flat bottom at boss centers
//   2. Screw M3x0.5x12mm socket head caps through case bottom into adapter
//   3. Apply blue Loctite to M3x20mm cross-bolt
//   4. Slide adapter onto Picatinny rail
//   5. Tighten cross-bolt (hand tight + 1/4 turn, ~0.5 N·m)

// ===============================
// PARAMETERS
// ===============================

/* [Reference] */
// Case flat bottom face = Z = 0 (top of Picatinny body sits here)
// Bosses rise ~2mm above this plane
CASE_FLAT_Z = 0;

/* [4 Boss Positions — measured from STL] */
// Cylindrical bosses on case bottom face
// All 4 are used for maximum recoil resistance
BOSS_POSITIONS = [
    [-27.9, -18.9],  // bottom-left
     [35.4, -18.9],  // bottom-right
    [-27.4,  18.1],  // top-left
     [34.3,  18.6],  // top-right
];
BOSS_DIA = 3.8;    // boss outer diameter (mm) — M3 fits here
BOSS_RISE = 2.0;   // boss rise above flat bottom (mm)

/* [Adapter Body] */
// Full plate covers all 4 bosses
BODY_W = 70.0;      // adapter width (X)
BODY_D = 52.0;     // adapter depth (Y) — spans from Y~-24 to Y~+24
BODY_H = 6.0;       // body plate thickness — sits on flat bottom

/* [Picatinny Rail Interface] */
PICA_SLOTS = 2;         // slots to engage (2 = stable)
PICA_SLOT_W = 5.23;    // MIL-STD-1913 slot width
PICA_SPACING = 10.01;  // MIL-STD-1913 slot spacing
PICA_BODY_W = PICA_SLOTS * PICA_SPACING + 4.0;  // = 24mm
PICA_BODY_H = 10.0;    // height of Picatinny body below case

/* [Recoil Lugs] */
LUG_H = 3.0;    // protrusion into Picatinny slot
LUG_W = 4.5;    // lug width

/* [Cross-bolt] */
XBOLT_D = 3.0;          // M3 cross-bolt
XBOLT_HEAD_D = 5.5;     // M3 hex head
XBOLT_SOCKET = 2.5;     // M3 hex across-flats

/* [Mounting Screws] */
SCREW_D = 3.2;      // M3 clearance hole diameter
SCREW_CSK_D = 6.5; // M3 countersink diameter
SCREW_LEN = 14.0;  // M3x0.5x14mm — goes through flat bottom into boss

$fn = 64;

// ===============================
// COMPUTED
// ===============================
// Center of all 4 bosses
BOSS_CX = (BOSS_POSITIONS[0][0] + BOSS_POSITIONS[2][0] +
           BOSS_POSITIONS[1][0] + BOSS_POSITIONS[3][0]) / 4.0;
BOSS_CY = (BOSS_POSITIONS[0][1] + BOSS_POSITIONS[2][1] +
           BOSS_POSITIONS[1][1] + BOSS_POSITIONS[3][1]) / 4.0;

// Relative positions from boss cluster center
REL_BOSS = [
    [BOSS_POSITIONS[0][0] - BOSS_CX, BOSS_POSITIONS[0][1] - BOSS_CY],
    [BOSS_POSITIONS[1][0] - BOSS_CX, BOSS_POSITIONS[1][1] - BOSS_CY],
    [BOSS_POSITIONS[2][0] - BOSS_CX, BOSS_POSITIONS[2][1] - BOSS_CY],
    [BOSS_POSITIONS[3][0] - BOSS_CX, BOSS_POSITIONS[3][1] - BOSS_CY],
];

// Picatinny body centered on case
PICA_CX = BOSS_CX;   // center Picatinny body on boss cluster X
PICA_CY = BOSS_CY;   // center Picatinny body on boss cluster Y

// Recoil lug positions within Picatinny body
half_span = (PICA_SLOTS * PICA_SPACING) / 2.0 - LUG_W / 2.0;
LUG_X = [
    PICA_CX + half_span,
    PICA_CX - half_span
];

// Total adapter height
TOTAL_H = BODY_H + PICA_BODY_H;

// ===============================
// MODULES
// ===============================

// Recoil lug 2D (cross-section)
module lug_2d() {
    hull() {
        circle(d = LUG_W + 1.5);
        translate([0, -LUG_H])
            circle(d = LUG_W - 0.5);
    }
}

// Full adapter
module stasys_picatinny_adapter() {
    difference() {
        union() {
            // --- Main plate (sits on flat bottom) ---
            translate([-BODY_W/2, -BODY_D/2, CASE_FLAT_Z])
                cube([BODY_W, BODY_D, BODY_H]);

            // --- Picatinny body (extends below case) ---
            translate([PICA_CX - PICA_BODY_W/2, -BODY_D/2, CASE_FLAT_Z - PICA_BODY_H])
                cube([PICA_BODY_W, BODY_D, PICA_BODY_H]);

            // --- Recoil lugs (protrude below Picatinny body) ---
            for (lx in LUG_X) {
                translate([lx, -BODY_D/2 - 1, CASE_FLAT_Z - PICA_BODY_H - LUG_H])
                    rotate([-90, 0, 0])
                        linear_extrude(height: BODY_D + 2)
                            lug_2d();
            }
        }

        // --- M3 clearance holes (through entire adapter) ---
        for (b in REL_BOSS) {
            translate([BOSS_CX + b[0], BOSS_CY + b[1], CASE_FLAT_Z - PICA_BODY_H - 2])
                cylinder(d = SCREW_D, h = TOTAL_H + 4);
        }

        // --- M3 countersinks (top face) ---
        for (b in REL_BOSS) {
            translate([BOSS_CX + b[0], BOSS_CY + b[1], CASE_FLAT_Z + TOTAL_H - 2.0])
                cylinder(d = SCREW_CSK_D, h = 2.5);
        }

        // --- Cross-bolt channel (horizontal, along Y axis) ---
        // Goes through Picatinny body, threads into rail slot walls
        translate([PICA_CX, 0, CASE_FLAT_Z - PICA_BODY_H/2])
            rotate([90, 0, 0])
                translate([0, 0, -BODY_D/2 - 2])
                    cylinder(d = XBOLT_D + 0.4, h = BODY_D + 4);

        // --- Cross-bolt hex socket (top face, centered on Picatinny body) ---
        // Counterbore for hex head
        translate([PICA_CX, PICA_CY, CASE_FLAT_Z + TOTAL_H - 1.5])
            cylinder(d = XBOLT_HEAD_D + 1.0, h = 2.5);
        // Hex socket
        translate([PICA_CX, PICA_CY, CASE_FLAT_Z + TOTAL_H - 1.0])
            cylinder(d = XBOLT_SOCKET, h = BODY_H + PICA_BODY_H + 2, $fn = 6);
    }
}

// Ghost: case bottom outline + boss indicators
module case_ghost() {
    // Flat bottom face outline
    translate([-33, -25.5, CASE_FLAT_Z + 0.3])
        cube([66, 55, 0.3]);

    // Boss indicators
    for (b in REL_BOSS) {
        color([0.8, 0.2, 0.2, 0.5])
        translate([BOSS_CX + b[0], BOSS_CY + b[1], CASE_FLAT_Z + BOSS_RISE])
            cylinder(d = BOSS_DIA + 0.2, h = 0.3);
    }
}

// ===============================
// RENDER
// ===============================
stasys_picatinny_adapter();
case_ghost();

// ===============================
// INFO
// ===============================
echo("=== PICATINNY ADAPTER (MEASURED) ===");
echo("Case boss positions (from STL):");
for (i = [0:3]) {
    echo(str("  ", i+1, ": (", BOSS_POSITIONS[i][0]:5.1f, ", ", BOSS_POSITIONS[i][1]:5.1f, ")"));
}
echo(str("Boss cluster center: (", BOSS_CX:5.1f, ", ", BOSS_CY:5.1f, ")"));
echo(str("Boss diameter: ", BOSS_DIA, "mm"));
echo(str("Boss rise: ", BOSS_RISE, "mm above flat bottom"));
echo("");
echo(str("Adapter body: ", BODY_W, "x", BODY_D, "x", BODY_H, " mm"));
echo(str("Picatinny body: ", PICA_BODY_W, "x", BODY_D, "x", PICA_BODY_H, " mm (", PICA_SLOTS, " slots)"));
echo(str("Picatinny center: (", PICA_CX:5.1f, ", ", PICA_CY:5.1f, ")"));
echo(str("Recoil lug positions: X=", LUG_X[0]:5.1f, " and X=", LUG_X[1]:5.1f));
echo(str("Recoil lug: ", LUG_W, "x", LUG_H, " mm"));
echo(str("Cross-bolt: M3x", PICA_BODY_H + 10, "mm (hex socket from top)"));
echo(str("Mounting screws: M3x", SCREW_LEN, "mm (4x, through flat bottom)"));
echo(str("Total adapter height: ", TOTAL_H, "mm (", BODY_H, " plate + ", PICA_BODY_H, "mm Pica body)"));
echo(str("Protrudes below case: ", PICA_BODY_H + LUG_H, "mm total"));
echo("");
echo("=== HARDWARE LIST ===");
echo("  4x M3x0.5x14mm socket head cap screw (case -> adapter)");
echo("  4x M3 flat washer");
echo("  1x M3x0.5x20mm socket head cap screw (cross-bolt)");
echo("  Blue Loctite 243 on cross-bolt");
