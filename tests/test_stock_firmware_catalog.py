import importlib.util
from pathlib import Path
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location(
    "stock_catalog", Path(__file__).resolve().parents[1] / "tools/query-stock-firmware.py")
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class CatalogTests(unittest.TestCase):
    def test_catalog_request_uses_the_app_protocol_user_agent(self):
        # This header is required by the live public catalog: our previous
        # generic User-Agent received HTTP 403 before any device selection.
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'[]'
        with mock.patch.object(catalog, 'urlopen', return_value=response) as request:
            self.assertEqual(catalog.get_json('devices/all'), [])
        sent, = request.call_args.args
        self.assertEqual(sent.full_url, catalog.API + 'devices/all')
        self.assertEqual(sent.get_header('User-agent'), 'Oxygen_updater_7.1.0')
        self.assertEqual(request.call_args.kwargs['timeout'], 30)

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
