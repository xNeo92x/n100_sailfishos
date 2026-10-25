import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "stock_catalog", Path(__file__).resolve().parents[1] / "tools/query-stock-firmware.py")
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class CatalogTests(unittest.TestCase):
    def fetch(self, ota=None, camel=False):
        def request(endpoint):
            if endpoint == "devices/all":
                return [{"id": 1, "name": "OnePlus Nord N100 (NA)"},
                        {"id": 2, "name": "OnePlus Nord N100 (EU)"},
                        {"id": 3, "name": "OnePlus Nord N10 (EU)"}]
            if endpoint == "updateMethods/2?language=en":
                return [{"id": 4, "name": "Stable (incremental)"},
                        {"id": 5, "name": "Stable (full)"}]
            if endpoint == "mostRecentUpdateData/2/5":
                return {"otaVersionNumber" if camel else "ota_version_number": ota}
            self.fail("Unexpected request: " + endpoint)
        return request

    def test_selects_eu_full_and_compares_exact_identity(self):
        result = catalog.collect(self.fetch(catalog.EXPECTED_OTA))
        self.assertTrue(result["catalog_ota_matches_installed"])
        self.assertFalse(result["package_metadata_verified"])
        self.assertFalse(result["device_accessed"])

    def test_camel_case_api_model(self):
        self.assertTrue(catalog.collect(self.fetch(catalog.EXPECTED_OTA, True))[
            "catalog_ota_matches_installed"])

    def test_missing_or_different_ota_never_accepted(self):
        for ota in (None, "", "2310092135", catalog.EXPECTED_OTA + "_other"):
            with self.subTest(ota=ota):
                self.assertFalse(catalog.collect(self.fetch(ota))["catalog_ota_matches_installed"])

    def test_refuses_wrong_region_or_ambiguous_selection(self):
        for rows in ([{"id": 1, "name": "OnePlus Nord N100 (NA)"}],
                     [{"id": 1, "name": "OnePlus Nord N100 (EU)"}] * 2):
            with self.assertRaises(ValueError):
                catalog.collect(lambda endpoint: rows)

    def test_invalid_response_or_id_rejected(self):
        for rows in ({"error": "denied"}, [None],
                     [{"id": "2", "name": "OnePlus Nord N100 (EU)"}],
                     [{"id": True, "name": "OnePlus Nord N100 (EU)"}]):
            with self.assertRaises(ValueError):
                catalog.collect(lambda endpoint: rows)


if __name__ == "__main__":
    unittest.main()
