"""Run with: python -m unittest discover tests"""
import copy
import unittest

from techpack.insights import apply_edits, check_pack, diff_packs, summarise
from tests.test_flats import HOODIE, make_pack


def pack(**kw):
    p = make_pack(HOODIE, [(1, "hood"), (2, "pocket")], sleeve_style="drop_shoulder", neckline="hood", pockets=["kangaroo"])
    p.update({
        "base_size": "M", "units": "cm", "front_flat_svg": "", "back_flat_svg": "",
        "bill_of_materials": [{"component": "Shell", "pantone": "19-4005 TCX"}],
        "construction": [{"area": "Shoulder", "seam_allowance": "1 cm", "callout_ref": "1"}],
        "sampling_requirements": ["fit sample"], "testing_requirements": [{"test": "wash"}],
        "compliance_notes": ["ISO 3758"], "fit_notes": ["relaxed"], "key_design_details": ["hood"],
        "style_name": "T", "assumptions": [], "questions_for_designer": [],
    })
    p.update(kw)
    return p


class CheckTests(unittest.TestCase):
    def texts(self, p):
        return [c["text"] for c in check_pack(p)]

    def test_clean_pack_has_no_findings(self):
        self.assertEqual(self.texts(pack()), [])

    def test_sizes_that_shrink_are_flagged(self):
        p = pack()
        p["measurements"][0]["values"] = ["70", "68", "72"]
        self.assertTrue(any("gets smaller" in t for t in self.texts(p)))

    def test_wrong_value_count_and_non_numeric(self):
        p = pack()
        p["measurements"][0]["values"] = ["70", "72"]
        p["measurements"][1]["values"] = ["a", "b", "c"]
        t = self.texts(p)
        self.assertTrue(any("2 values for 3 sizes" in x for x in t))
        self.assertTrue(any("not a number" in x for x in t))

    def test_missing_pantone_and_seam_allowance(self):
        p = pack()
        p["bill_of_materials"][0]["pantone"] = ""
        p["construction"][0]["seam_allowance"] = ""
        t = self.texts(p)
        self.assertTrue(any("no Pantone" in x for x in t))
        self.assertTrue(any("no seam allowance" in x for x in t))

    def test_placeholders_become_todos_with_a_path(self):
        p = pack(country_of_origin="Made in [country], to be confirmed")
        todo = [c for c in check_pack(p) if c["level"] == "todo"]
        self.assertEqual(todo[0]["path"], "country_of_origin")

    def test_callout_not_drawn_and_bad_construction_reference(self):
        p = pack()
        p["callouts"].append({"number": 9, "label": "Mystery", "description": "", "anchor": ""})
        p["construction"][0]["callout_ref"] = "42"
        t = self.texts(p)
        self.assertTrue(any("isn't attached to a drawn feature" in x for x in t))
        self.assertTrue(any("callout 42" in x for x in t))

    def test_unmapped_dimension_is_reported(self):
        p = pack()
        p["flat_spec"]["dims"][0]["pom_code"] = "QQ"
        self.assertTrue(any("QQ" in x for x in self.texts(p)))


class EditTests(unittest.TestCase):
    def test_apply_edits_sets_list_and_dict_values(self):
        p = pack()
        n = apply_edits(p, {"measurements.0.values.1": "99", "style_name": "New", "key_design_details.0": "x"})
        self.assertEqual(n, 3)
        self.assertEqual(p["measurements"][0]["values"][1], "99")

    def test_no_change_counts_zero(self):
        p = pack()
        self.assertEqual(apply_edits(p, {"style_name": "T"}), 0)

    def test_bad_paths_and_flat_svgs_are_rejected(self):
        p = pack()
        for path in ("measurements.99.values.0", "nope", "front_flat_svg", "measurements.0.code.5"):
            with self.subTest(path), self.assertRaises(ValueError):
                apply_edits(p, {path: "x"})

    def test_diff_groups_by_section_and_summarises(self):
        a, b = pack(), pack()
        apply_edits(b, {"style_name": "Other", "measurements.0.values.0": "1"})
        groups = diff_packs(a, b)
        self.assertEqual({g["section"] for g in groups}, {"Cover", "Measurements"})
        self.assertIn("2 changes", summarise(groups))
        self.assertEqual(summarise(diff_packs(a, copy.deepcopy(a))), "no changes")


if __name__ == "__main__":
    unittest.main()
