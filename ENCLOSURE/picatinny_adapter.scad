// STASYS Picatinny Adapter Bracket
// Mounts the watch-style case to a Picatinny rail
// Bolts to the 4 existing corner bosses on the case bottom
//
// Print: PA12 SLS recommended (recoil-safe), flat on build plate
//
// Assembly:
//   1. Print adapter flat on build plate
//   2. Drill M2 clearance holes through case at boss positions
//   3. Screw M2×0.4×6mm socket head caps through case into adapter
//   4. Apply blue Loctite to M3×16mm cross-bolt
//   5. Slide adapter onto Picatinny rail (lugs into slot)
//   6. Tighten cross-bolt — do NOT over-torque (hand tight + 1/4 turn)
//
// Material: PA12 SLS | SLA: use Formlabs Durable or Siraya Tech Fast ABS

// ===============================
// PARAMETERS — adjust for your case
// ===============================

/* [Case Bottom — measure your case bosses!] */
// Corner boss positions (X, Y) on the case bottom face
// Default values from STL analysis — verify with calipers!
BOSS_POSITIONS = [
    [-28.0,  14.0],   // top-left
    [-28.0, -18.0],   // bottom-left
    [ 36.0, -18.0],   // bottom-right
    [ 36.0,  14.0],   // top-right
];
BOSS_D = 2.0;          // M2 screw diameter
BOSS_RISE = 1.2;       // Boss height above case bottom (mm)

/* [Adapter Geometry] */
BODY_W = 68.0;         // Adapter width (X) — must span all 4 bosses
BODY_D = 20.0;         // Adapter depth (Y) — smaller than case, just covers lugs
BODY_H = 6.0;          // Adapter thickness (Z) — flat plate above Picatinny body
PICA_BODY_W = 24.0;   // Picatinny body width
PICA_BODY_H = 10.0;    // Picatinny body height (below adapter)

/* [Picatinny Rail Interface] */
PICA_SLOTS = 2;        // Slots to engage (2 = most stable)
RECOIL_LUG_H = 3.0;   // Lug protrusion into Picatinny slot
RECOIL_LUG_W = 4.0;   // Lug width
CROSSBOLT_D = 3.0;    // M3 cross-bolt diameter
CROSSBOLT_HEAD_D = 5.5; // M3 hex bolt head diameter
CROSSBOLT_SOCKET_D = 2.5; // M3 hex socket across flats

/* [Case Interface] */
CASE_BOTTOM_Z = 0;     // Reference Z — top of Picatinny body

$fn = 64;

// ===============================
// COMPUTED
// ===============================
PICA_SLOT_SPACING = 10.01;  // MIL-STD-1913
PICA_BODY_W = PICA_SLOTS * PICA_SLOT_SPACING + 4;

// Boss center offset (center of the 4-boss cluster)
BOSS_CX = (BOSS_POSITIONS[0][0] + BOSS_POSITIONS[2][0]) / 2.0;
BOSS_CY = (BOSS_POSITIONS[0][1] + BOSS_POSITIONS[1][1]) / 2.0;

// Relative boss positions from cluster center
REL_BOSS = [
    [BOSS_POSITIONS[0][0] - BOSS_CX, BOSS_POSITIONS[0][1] - BOSS_CY],
    [BOSS_POSITIONS[1][0] - BOSS_CX, BOSS_POSITIONS[1][1] - BOSS_CY],
    [BOSS_POSITIONS[2][0] - BOSS_CX, BOSS_POSITIONS[2][1] - BOSS_CY],
    [BOSS_POSITIONS[3][0] - BOSS_CX, BOSS_POSITIONS[3][1] - BOSS_CY],
];

// ===============================
// MODULES
// ===============================

// ── Recoil lug (2D) ───────────────────────────────────────────────────────
module recoil_lug_2d() {
    // Tapered lug: wider at base, narrower at tip
    hull() {
        circle(d = RECOIL_LUG_W + 1.5);
        translate([0, -RECOIL_LUG_H])
            circle(d = RECOIL_LUG_W - 0.5);
    }
}

// ── Full 3D adapter ────────────────────────────────────────────────────────
module stasys_picatinny_adapter() {
    total_h = BODY_H + PICA_BODY_H;

    difference() {
        union() {
            // ── Main plate (top face, bolts to case) ──────────────────────
            translate([-BODY_W/2, -BODY_D/2, CASE_BOTTOM_Z + PICA_BODY_H])
                cube([BODY_W, BODY_D, BODY_H]);

            // ── Picatinny body (extends below) ────────────────────────────
            translate([-PICA_BODY_W/2, -BODY_D/2, CASE_BOTTOM_Z])
                cube([PICA_BODY_W, BODY_D, PICA_BODY_H]);

            // ── Recoil lugs (from bottom of Picatinny body) ────────────────
            half_span = (PICA_SLOTS * PICA_SLOT_SPACING) / 2.0 - RECOIL_LUG_W / 2.0;
            for (x = [-half_span, half_span]) {
                translate([x, -BODY_D/2 - 1, CASE_BOTTOM_Z - RECOIL_LUG_H])
                    rotate([-90, 0, 0])
                        linear_extrude(height = BODY_D + 2)
                            recoil_lug_2d();
            }
        }

        // ── M2 bolt clearance holes (through entire adapter) ───────────────
        for (b = REL_BOSS) {
            translate([BOSS_CX + b[0], BOSS_CY + b[1], CASE_BOTTOM_Z - 2])
                cylinder(d = BOSS_D + 0.4, h = total_h + 4);
        }

        // ── M2 countersinks (top face — flush with adapter top) ────────────
        for (b = REL_BOSS) {
            translate([BOSS_CX + b[0], BOSS_CY + b[1], CASE_BOTTOM_Z + total_h - 2])
                cylinder(d = 4.5, h = 2.5);
        }

        // ── Cross-bolt channel (horizontal, along Y axis) ──────────────────
        // Goes through the Picatinny body, perpendicular to rail
        // Bolt threads into Picatinny slot walls on both sides
        translate([-PICA_BODY_W/2 - 2, 0, PICA_BODY_H / 2])
            rotate([90, 0, 0])
                translate([0, 0, -BODY_D/2 - 2])
                    cylinder(d = CROSSBOLT_D + 0.3, h = BODY_D + 4);

        // ── Cross-bolt hex socket (from top face) ─────────────────────────
        // Counterbore for hex bolt head
        translate([-PICA_BODY_W/2 - 2, 0, CASE_BOTTOM_Z + PICA_BODY_H + BODY_H - 1])
            cylinder(d = CROSSBOLT_HEAD_D + 0.5, h = 3);
        // Hex socket
        translate([-PICA_BODY_W/2 - 2, 0, CASE_BOTTOM_Z + PICA_BODY_H - 1])
            cylinder(d = CROSSBOLT_SOCKET_D, h = BODY_H + 2, $fn = 6);
    }
}

// ── Ghost case bottom (reference only) ──────────────────────────────────────
module case_ghost() {
    // Simplified case bottom outline for alignment reference
    color([0.5, 0.5, 0.5, 0.3])
    translate([-32, -27.5, CASE_BOTTOM_Z + PICA_BODY_H + BODY_H + 0.5])
        cube([64, 55, 0.5]);

    // Boss indicators
    for (b = REL_BOSS) {
        color([0.7, 0.3, 0.3, 0.4])
        translate([BOSS_CX + b[0], BOSS_CY + b[1], CASE_BOTTOM_Z + PICA_BODY_H + BODY_H - BOSS_RISE])
            cylinder(d = 3, h = BOSS_RISE + 0.5);
    }
}

// ===============================
// RENDER
// ===============================
stasys_picatinny_adapter();
case_ghost();

// ===============================
// ECHO
// ===============================
echo("=== PICATINNY ADAPTER ===");
echo(str("Body (W×D×H): ", BODY_W, "×", BODY_D, "×", BODY_H, " mm"));
echo(str("Picatinny body (W×H): ", PICA_BODY_W, "×", PICA_BODY_H, " mm (", PICA_SLOTS, " slots)"));
echo(str("Recoil lug: ", RECOIL_LUG_W, "×", RECOIL_LUG_H, " mm"));
echo(str("Cross-bolt: M3×", PICA_BODY_H + 16, " mm (hex socket from top)"));
echo(str("Bolt holes: M2 clearance (4×) at:"));
for (b = REL_BOSS) {
    echo(str("  (", BOSS_CX+b[0]:6.1f, ", ", BOSS_CY+b[1]:6.1f, ")"));
}
echo(str("Adapter total height: ", BODY_H + PICA_BODY_H, " mm"));
echo(str("Protrudes below case by: ", PICA_BODY_H, " mm"));
echo(str("Boss cluster center: (", BOSS_CX, ", ", BOSS_CY, ")"));
echo("");
echo("=== HARDWARE NEEDED ===");
echo("  4× M2×0.4×6mm socket head cap screw (case → adapter)");
echo("  4× M2 flat washer");
echo("  1× M3×0.5×16mm socket head cap screw (cross-bolt)");
echo("  Blue Loctite on cross-bolt threads");
echo("");
echo("=== VERIFY BOSS POSITIONS ===");
echo("  Measure actual boss positions on your case with calipers!");
echo("  Update BOSS_POSITIONS array above to match your measurements.");
