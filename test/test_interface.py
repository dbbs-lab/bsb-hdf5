import unittest

from bsb_test.engines import TestConnectivitySet as _TestConnectivitySet
from bsb_test.engines import TestMorphologyRepository as _TestMorphologyRepository
from bsb_test.engines import TestPlacementSet as _TestPlacementSet
from bsb_test.engines import TestStorage as _TestStorage


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
