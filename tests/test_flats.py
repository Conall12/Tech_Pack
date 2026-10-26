"""Run with: python -m unittest discover tests"""
import copy
import unittest
import xml.etree.ElementTree as ET

from techpack import flats
from techpack.flats import _Draw, draw_bottom, draw_top, render_flats, resolve_dims

BASE_SPEC = dict(template="top", sleeve_style="none", neckline="none", closure="none", two_way_zip=False, hem_style="plain",
                 cuff_style="none", pockets=[], panel_seams=[], waistband="none", leg_shape="none", piped_seams=False,
                 drawcord=False)


def make_pack(dims, callouts=(), units="cm", **spec_kw):
    """A minimal pack whose measurement table holds `dims` (role -> M value); S and L are +/-2."""
    meas, mapping = [], []
    for i, (role, val) in enumerate(dims.items()):
        code = chr(65 + i)
        meas.append({"code": code, "description": role, "tolerance": "1", "values": [str(val - 2), str(val), str(val + 2)]})
        mapping.append({"role": role, "pom_code": code})
    spec = dict(BASE_SPEC, **spec_kw)
    spec["dims"] = mapping
    return {
        "flat_spec": spec, "measurements": meas, "sizes": ["S", "M", "L"], "base_size": "M", "units": units,
        "callouts": [{"number": n, "label": a, "description": "", "anchor": a} for n, a in callouts],
    }


HOODIE = dict(body_length=70, chest_width=62, hem_width=58, shoulder_width=58, sleeve_inseam=46, bicep_width=24,
              cuff_width=10, neck_width=22, hood_height=36, hood_width=32, armhole_drop=26)
JOGGERS = dict(waist_width=38, hip_width=54, front_rise=30, inseam=74, thigh_width=33, knee_width=24, leg_opening=15)

CASES = {
    "hoodie": make_pack(HOODIE, [(1, "hood"), (2, "pocket"), (3, "cuff")], sleeve_style="drop_shoulder", neckline="hood",
                        hem_style="ribbed", cuff_style="ribbed", pockets=["kangaroo"], drawcord=True),
    "raglan zip jacket": make_pack(HOODIE, [(1, "closure"), (2, "panel_seam")], sleeve_style="raglan", neckline="hood",
                                   closure="full_zip", two_way_zip=True, hem_style="elastic", cuff_style="elastic",
                                   pockets=["welt_vertical"], panel_seams=["curved_side_panels"], piped_seams=True),
    "tee": make_pack(dict(body_length=72, chest_width=54, hem_width=54, shoulder_width=46, sleeve_length=21, cuff_width=18, neck_width=19),
                     [(1, "neck")], sleeve_style="set_in", neckline="crew", cuff_style="plain"),
    "collar jacket": make_pack(dict(body_length=68, chest_width=58, hem_width=54, shoulder_width=48, sleeve_length=62, cuff_width=9, neck_width=20),
                               [(1, "neck")], sleeve_style="set_in", neckline="collar", closure="buttons", pockets=["chest_patch"],
                               panel_seams=["yoke_front", "yoke_back"]),
    "vest": make_pack(dict(body_length=65, chest_width=52, hem_width=52, shoulder_width=40, neck_width=18),
                      [(1, "neck")], sleeve_style="sleeveless", neckline="v_neck"),
    "joggers": make_pack(JOGGERS, [(1, "waistband"), (2, "pocket"), (3, "hem")], template="bottom", waistband="drawcord",
                         hem_style="ribbed", pockets=["side_slant"], leg_shape="tapered"),
    "cargo": make_pack(dict(JOGGERS, thigh_width=35), [(1, "fly"), (2, "cargo_pocket"), (3, "back_pocket")], template="bottom",
                       closure="zip_fly", waistband="belt_loops", pockets=["cargo_patch", "back_patch"], panel_seams=["yoke_back"]),
}


class RenderTests(unittest.TestCase):
    def test_every_garment_renders_valid_svg(self):
        for name, pack in CASES.items():
            with self.subTest(name):
                out = render_flats(pack)
                self.assertIsNotNone(out)
                for key in ("front", "back", "front_pom", "back_pom"):
                    root = ET.fromstring(out[key])
                    self.assertTrue(root.tag.endswith("svg"))
                self.assertGreater(len(out["front_pom"]), len(out["front"]), "markers should add content")

    def test_other_template_uses_stored_drawings(self):
        self.assertIsNone(render_flats({"flat_spec": {"template": "other"}}))
        self.assertIsNone(render_flats({}))

    def test_callouts_are_drawn_and_unknown_anchors_are_not(self):
        pack = make_pack(HOODIE, [(1, "hood"), (2, "raglan_seam"), (3, "not_a_thing"), (4, "")], sleeve_style="raglan", neckline="hood")
        out = render_flats(pack)
        self.assertEqual(out["drawn"], [1, 2], "aliases resolve; unknown/empty anchors are skipped")

    def test_view_prefix_limits_a_callout_to_one_view(self):
        pack = make_pack(HOODIE, [(1, "back:neck_label")], sleeve_style="set_in", neckline="hood")
        self.assertEqual(render_flats(pack)["drawn"], [1])

    def test_flats_follow_the_measurements(self):
        a = make_pack(HOODIE, sleeve_style="set_in", neckline="crew")
        b = copy.deepcopy(a)
        for m in b["measurements"]:
            if m["description"] == "chest_width":
                m["values"][1] = "70"
        self.assertNotEqual(render_flats(a)["front"], render_flats(b)["front"])


class TwoPieceTests(unittest.TestCase):
    def _set(self):
        pack = copy.deepcopy(CASES["hoodie"])
        pack["flat_spec_2"] = copy.deepcopy(CASES["joggers"]["flat_spec"])
        n = len(pack["measurements"])
        for i, m in enumerate(CASES["joggers"]["measurements"]):
            pack["measurements"].append(dict(m, code=f"J{i}"))
        for dm in pack["flat_spec_2"]["dims"]:
            dm["pom_code"] = "J" + str(ord(dm["pom_code"]) - 65)
        pack["callouts"] += [{"number": 7, "label": "wb", "description": "", "anchor": "p2:waistband"},
                             {"number": 8, "label": "pocket", "description": "", "anchor": "pocket"}]
        return pack

    def test_set_draws_two_garments_and_routes_callouts(self):
        pieces = flats.render_all(self._set())
        self.assertEqual([p["label"] for p in pieces], ["Top", "Bottom"])
        self.assertIn(7, pieces[1]["drawn"])
        self.assertNotIn(7, pieces[0]["drawn"])
        self.assertIn(8, pieces[0]["drawn"])
        self.assertNotIn(8, pieces[1]["drawn"])

    def test_render_flats_reports_every_piece(self):
        self.assertIn(7, render_flats(self._set())["drawn"])

    def test_single_garment_has_one_piece(self):
        self.assertEqual(len(flats.render_all(CASES["hoodie"])), 1)


class ColourTests(unittest.TestCase):
    def test_body_and_trim_colours_are_used(self):
        pack = make_pack(HOODIE, sleeve_style="raglan", neckline="hood", panel_seams=["sleeve_stripe"], piped_seams=True,
                         body_colour="#45494d", trim_colour="#f4f4f0")
        svg = render_flats(pack)["front"]
        self.assertIn("#45494d", svg)
        self.assertIn("#f4f4f0", svg)

    def test_bad_colour_falls_back_to_white(self):
        svg = render_flats(make_pack(HOODIE, sleeve_style="set_in", neckline="crew", body_colour="charcoal"))["front"]
        self.assertIn('fill="#ffffff"', svg)

    def test_thigh_wider_than_hip_is_flagged(self):
        pack = make_pack(dict(JOGGERS, thigh_width=40), template="bottom", waistband="elastic", leg_shape="straight")
        self.assertTrue(any("Thigh width" in w for w in render_flats(pack)["warnings"]))


class GeometryTests(unittest.TestCase):
    def test_sleeves_that_hang_below_the_hem_are_not_clipped(self):
        pack = make_pack(dict(HOODIE, body_length=60, sleeve_inseam=52), sleeve_style="raglan", neckline="hood")
        d, _ = resolve_dims(pack, "top")
        half, top, bottom = flats._top_extent(d, pack["flat_spec"])
        S, U, _, _ = flats._top_geom(d, pack["flat_spec"])
        Co, Ci, *_ = flats._sleeve_pts(d, pack["flat_spec"], S, U)
        self.assertGreater(bottom, max(Co[1], Ci[1]))

    def _marks(self, pack, view="front"):
        d, _ = resolve_dims(pack, pack["flat_spec"]["template"])
        dr = _Draw(5, 280, 40)
        fn = draw_top if pack["flat_spec"]["template"] == "top" else draw_bottom
        return d, fn(dr, d, pack["flat_spec"], view)

    def test_top_marker_lengths_equal_the_spec(self):
        d, marks = self._marks(CASES["hoodie"])
        (x0, _), (x1, _) = marks["chest_width"][1:3]
        self.assertAlmostEqual(x1 - x0, 62, places=3)
        (_, y0), (_, y1) = marks["body_length"][1:3]
        self.assertAlmostEqual(y1 - y0, 70, places=3)
        (_, y0), (_, y1) = marks["hood_height"][1:3]
        self.assertAlmostEqual(abs(y1 - y0), 36, places=3)

    def test_bottom_marker_lengths_equal_the_spec(self):
        d, marks = self._marks(CASES["joggers"])
        (x0, _), (x1, _) = marks["waist_width"][1:3]
        self.assertAlmostEqual(x1 - x0, 38, places=3)
        (_, y0), (_, y1) = marks["front_rise"][1:3]
        self.assertAlmostEqual(y1 - y0, 30, places=3)
        (_, y0), (_, y1) = marks["inseam"][1:3]
        self.assertAlmostEqual(y1 - y0, 74, places=3)

    def test_front_and_back_share_the_same_scale_and_size(self):
        out = render_flats(CASES["hoodie"])
        self.assertEqual(ET.fromstring(out["front"]).get("viewBox"), ET.fromstring(out["back"]).get("viewBox"))


class DimensionTests(unittest.TestCase):
    def test_base_size_value_is_used(self):
        d, w = resolve_dims(CASES["hoodie"], "top")
        self.assertEqual(d["chest_width"], 62)
        self.assertEqual(w, [])

    def test_unknown_pom_code_warns_and_defaults(self):
        pack = copy.deepcopy(CASES["tee"])
        pack["flat_spec"]["dims"][0]["pom_code"] = "ZZ"
        d, w = resolve_dims(pack, "top")
        self.assertTrue(any("ZZ" in x for x in w))
        self.assertIn("body_length", d)  # default filled in

    def test_circumference_is_halved(self):
        pack = make_pack(dict(body_length=70, chest_width=124, neck_width=19), neckline="crew", sleeve_style="set_in")
        d, w = resolve_dims(pack, "top")
        self.assertEqual(d["chest_width"], 62)
        self.assertTrue(any("halved" in x for x in w))

    def test_inches_are_converted(self):
        pack = make_pack(dict(body_length=28, chest_width=22, neck_width=7.5), units="in", neckline="crew", sleeve_style="set_in")
        d, _ = resolve_dims(pack, "top")
        self.assertAlmostEqual(d["body_length"], 28 * 2.54)

    def test_inseam_wins_over_sleeve_length(self):
        pack = make_pack(dict(HOODIE, sleeve_length=60), sleeve_style="set_in", neckline="crew")
        d, _ = resolve_dims(pack, "top")
        S, U, _, _ = flats._top_geom(d, pack["flat_spec"])
        Co, Ci, _, _, _, _ = flats._sleeve_pts(d, pack["flat_spec"], S, U)
        self.assertAlmostEqual(((Ci[0] - U[0]) ** 2 + (Ci[1] - U[1]) ** 2) ** 0.5, 46, places=3)


if __name__ == "__main__":
    unittest.main()
