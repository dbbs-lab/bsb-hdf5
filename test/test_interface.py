from bsb.unittest.engines import (
    TestStorage as _TestStorage,
    TestPlacementSet as _TestPlacementSet,
)
import unittest


class TestStorage(_TestStorage, unittest.TestCase, engine_name="hdf5"):
    # def test_x(self):
    #     self.assertFalse(True)
    pass


class TestPlacementSet(_TestPlacementSet, unittest.TestCase, engine_name="hdf5"):
    pass
