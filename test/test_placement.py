from bsb.core import Scaffold
from bsb.config import Configuration
from bsb.exceptions import *
from . import StorageCase


cfg = Configuration.default(cell_types=dict(a=dict(spatial=dict(radius=2, density=1e-3))))


class TestPlacementSet(StorageCase):
    def test_create(self):
        storage = self.random_storage()
        ps = storage._PlacementSet.create(storage._engine, cfg.cell_types.a)
        self.assertEqual("a", ps.tag, "tag should be cell type name")
        self.assertEqual(0, len(ps), "new ps should be empty")
