import unittest, os, sys, numpy as np, h5py, json, string, random

from bsb.core import Scaffold
from bsb.config import from_json
from bsb.exceptions import *
from bsb.storage import Chunk
from bsb.storage import _util
from .test_setup import get_config, timeout
from . import StorageCase, MPI
import pathlib


class _ScaffoldDummy:
    def __init__(self, cfg):
        self.cfg = self.configuration = cfg

    def get_cell_types(self):
        return list(self.cfg.cell_types.values())


# WATCH OUT! These tests are super sensitive to race conditions! Especially through use
# of the @on_master etc decorators in storage.py functions under MPI! We need a more
# detailed MPI checkpointing system, instead of a Barrier system. Consecutive barriers
# can cause slippage, where 1 node skips a Barrier, and it causes sync and race issues,
# and eventually deadlock when it doesn't join the others for the last collective Barrier.
class TestHDF5Storage(StorageCase):
    @timeout(10)
    def test_init(self):
        print("TESTING INIT")
        # Use the init function to instantiate a storage container to its initial
        # empty state. This test avoids the `Scaffold` object as instantiating it might
        # create or remove data by relying on `renew` or `init` in its constructor.
        cfg = from_json(get_config("test_single"))
        s = self.random_storage()
        s.create()
        self.assertTrue(os.path.exists(s._root))
        self.assertTrue(s.exists())
        s.init(_ScaffoldDummy(cfg))
        # Test that `init` created the placement sets for each cell type
        for cell_type in cfg.cell_types.values():
            with self.subTest(type=cell_type.name):
                ps = s._PlacementSet(s._engine, cell_type)
                # Test that the placement set is functional after init call
                ps.append_data(Chunk((0, 0, 0), (100, 100, 100)), [0])

    @timeout(10)
    def test_renew(self):
        # Use the renew mechanism to reinstantiate a storage container to its initial
        # empty state. This test avoids the `Scaffold` object as instantiating it might
        # create or remove data by relying on `renew` or `init` in its constructor.
        cfg = from_json(get_config("test_single"))
        s = self.random_storage()
        s.create()
        self.assertTrue(os.path.exists(s._root))
        ps = s._PlacementSet.require(s._engine, cfg.cell_types.test_cell)
        with ps._engine._master_write() as fence:
            fence.guard()
            ps.append_data(Chunk((0, 0, 0), (100, 100, 100)), [0])
        self.assertEqual(
            1,
            len(ps.load_positions()),
            "Failure to setup `storage.renew()` test due to chunk reading error.",
        )
        MPI.COMM_WORLD.Barrier()
        # Spoof a scaffold here, `renew` only requires an object with a
        # `.get_cell_types()` method for its `storage.init` call.
        s.renew(_ScaffoldDummy(cfg))
        ps = s._PlacementSet.require(s._engine, cfg.cell_types.test_cell)
        self.assertEqual(
            0,
            len(ps.load_positions()),
            "`storage.renew()` did not clear placement data.",
        )

    @timeout(10)
    def test_move(self):
        s = self.random_storage()
        old_root = s._root
        s.create()
        self.assertTrue(os.path.exists(s._root))
        s.move(f"2x2{s._root}")
        self.assertFalse(os.path.exists(old_root))
        self.assertTrue(os.path.exists(s._root))
        s.move(old_root)
        self.assertTrue(os.path.exists(old_root))
        self.assertTrue(os.path.exists(s._root))
        self.assertTrue(s.exists())

    @timeout(10)
    def test_remove_create(self):
        s = self.random_storage()
        s.remove()
        self.assertFalse(os.path.exists(s._root))
        self.assertFalse(s.exists())
        s.create()
        self.assertTrue(os.path.exists(s._root))
        self.assertTrue(s.exists())

    def test_eq(self):
        s = self.random_storage()
        s2 = self.random_storage()
        self.assertEqual(s, s, "Same storage should be equal")
        self.assertNotEqual(s, s2, "Diff storages should be unequal")
        self.assertEqual(s.files, s.files, "Singletons equal")
        self.assertNotEqual(s.files, s2.files, "Diff singletons unequal")
        self.assertNotEqual(s.files, s.morphologies, "Diff singletons unequal")
        self.assertEqual(s.morphologies, s.morphologies, "Singletons equal")
        self.assertNotEqual(s.morphologies, s2.morphologies, "Dff singletons unequal")
        self.assertNotEqual(s.morphologies, "hello", "weird comp should be unequal")