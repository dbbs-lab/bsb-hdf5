import unittest
from .test_setup import timeout
import mpi4py.MPI as MPI
import os
from bsb.storage import Storage


class StorageCase(unittest.TestCase):
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._open_storages = []

    @timeout(10, abort=True)
    def setUp(self):
        MPI.COMM_WORLD.Barrier()

    @timeout(10, abort=True)
    def tearDown(self):
        MPI.COMM_WORLD.Barrier()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if not MPI.COMM_WORLD.Get_rank():
            for s in cls._open_storages:
                os.remove(s)

    def random_storage(self):
        rstr = f"random_storage_{len(self.__class__._open_storages)}.hdf5"
        self.__class__._open_storages.append(rstr)
        s = Storage("hdf5", rstr)
        return s
