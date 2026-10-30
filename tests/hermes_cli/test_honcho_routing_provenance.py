"""Offline limitation contract; no application imports or live calls."""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

P = Path(__file__).resolve().parents[2] / 'hermes_cli' / 'honcho_monitor.py'
spec = importlib.util.spec_from_file_location('honcho_monitor_isolated', P)
assert spec is not None and spec.loader is not None
hm = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hm
spec.loader.exec_module(hm)

class ProvenanceLimitTests(unittest.TestCase):
    def test_unknown_names_unproven_layers(self):
        r = hm.parse_pipeline_env('')[' _routing'.strip()]
        self.assertEqual(r['status'], 'unknown')
        for layer in ('initializer', 'dotenv', 'TOML', 'fallback', 'dialectic'):
            self.assertIn(layer, r['reason'])

    def test_env_equality_is_not_effective_proof(self):
        r = hm.parse_pipeline_env('DERIVER_MODEL_CONFIG__MODEL=approved\nDERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=https://approved.invalid/v1')
        self.assertEqual(r['_routing']['status'], 'unknown')

    def test_unknown_route_never_triggers_paid_probe(self):
        pipeline = {'deriver': {'model': 'served', 'base_url': 'http://192.168.10.211:8001/v1'}, '_routing': {'status': 'unknown'}}
        self.assertFalse(hm.spark_probe_authorized(pipeline))

    def test_missing_proof_never_triggers_paid_probe(self):
        self.assertFalse(hm.spark_probe_authorized({'deriver': {'base_url': 'http://192.168.10.211:8001/v1'}}))

    def test_status_string_is_not_approved_probe_policy(self):
        for status in ('verified', 'verified_cloud_only', 'fallback_used', 'mismatch', '$(touch forbidden)'):
            with self.subTest(status=status):
                self.assertFalse(hm.spark_probe_authorized({'_routing': {'status': status}}))

    def test_reason_does_not_echo_input(self):
        secret = 'SECRET_SENTINEL;$(touch /forbidden)'
        r = hm.parse_pipeline_env('DERIVER_MODEL_CONFIG__MODEL=' + secret)
        self.assertNotIn(secret, r['_routing']['reason'])

    def test_overflow_retains_unknown(self):
        r = hm.parse_pipeline_env('x' * (1024 * 1024 + 1))
        self.assertEqual(list(r), ['_routing'])
        self.assertEqual(r['_routing']['status'], 'unknown')

    def test_collection_cannot_probe_unknown(self):
        with patch.object(hm, '_state_path', return_value=P), patch.object(hm, 'ssh', return_value=''), patch.object(hm, 'latest_local_message_timestamp', return_value=None), patch.object(hm, 'build_ingestion_status', return_value={}), patch.object(hm, 'collect_recent_deriver_errors', return_value={}), patch.object(hm, 'parse_pipeline_env', return_value={'deriver': {'model': 'served', 'base_url': 'http://192.168.10.211:8001/v1'}, '_routing': {'status': 'unknown'}}), patch.object(hm, 'curl_post_json') as paid:
            snapshot, _ = hm.collect_snapshot()
            paid.assert_not_called()
            self.assertTrue(snapshot.spark_goat['skipped'])

if __name__ == '__main__':
    unittest.main()
