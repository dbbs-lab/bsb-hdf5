from bsb.unittest.engines import (
    TestStorage as _TestStorage,
    TestPlacementSet as _TestPlacementSet,
    TestMorphologyRepository as _TestMorphologyRepository,
    TestConnectivitySet as _TestConnectivitySet,
)
import unittest


class TestStorage(_TestStorage, unittest.TestCase, engine_name="hdf5"):
    pass


class TestPlacementSet(_TestPlacementSet, unittest.TestCase, engine_name="hdf5"):
    pass


class TestMorphologyRepository(
    _TestMorphologyRepository, unittest.TestCase, engine_name="hdf5"
):
    pass


class TestConnectivitySet(_TestConnectivitySet, unittest.TestCase, engine_name="hdf5"):
    pass
