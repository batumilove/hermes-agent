"""Source wiring regression for the waited, gateway-only shutdown stage."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]

class Contract(unittest.TestCase):
    def test_short_sweep_and_waited_hook(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        self.assertNotIn('ENV S6_KILL_GRACETIME=80000', dockerfile)
        self.assertTrue((ROOT / 'docker/cont-finish.d/10-gateway-stop').is_file())
        self.assertIn('COPY --chmod=0755 docker/cont-finish.d/', dockerfile)

    def test_supported_outer_grace(self):
        for name in ('docker-compose.yml', 'docker-compose.windows.yml'):
            self.assertIn('stop_grace_period: 90s', (ROOT / name).read_text())

if __name__ == '__main__':
    unittest.main()
