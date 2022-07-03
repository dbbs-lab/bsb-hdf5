from bsb.unittest.engines import (
    TestStorage as _TestStorage,
    TestPlacementSet as _TestPlacementSet,
)
import unittest


class TestStorage(_TestStorage, unittest.TestCase, engine_name="hdf5"):
    pass


class TestPlacementSet(_TestPlacementSet, unittest.TestCase, engine_name="hdf5"):
    pass
