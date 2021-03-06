from bsb import config
from bsb.services import MPILock
from bsb.config.nodes import StorageNode as IStorageNode
from bsb.storage.interfaces import Engine
from .placement_set import PlacementSet
from .connectivity_set import ConnectivitySet
from .file_store import FileStore
from .morphology_repository import MorphologyRepository
from contextlib import contextmanager
from datetime import datetime
import h5py
import os
import shutil
import shortuuid

__version__ = "0.2.4"


def on_main(prep=None, ret=None):
    def decorator(f):
        def wrapper(self, *args, **kwargs):
            r = None
            self.comm.Barrier()
            if self.comm.Get_rank() == 0:
                r = f(self, *args, **kwargs)
            elif prep:
                prep(self, *args, **kwargs)
            self.comm.Barrier()
            if not ret:
                return self.comm.bcast(r, root=0)
            else:
                return ret(self, *args, **kwargs)

        return wrapper

    return decorator


def on_main_until(until, prep=None, ret=None):
    def decorator(f):
        def wrapper(self, *args, **kwargs):
            global _procpass
            r = None
            self.comm.Barrier()
            if self.comm.Get_rank() == 0:
                r = f(self, *args, **kwargs)
            elif prep:
                prep(self, *args, **kwargs)
            self.comm.Barrier()
            while not until(self, *args, **kwargs):
                pass
            if not ret:
                return self.comm.bcast(r, root=0)
            else:
                return ret(self, *args, **kwargs)

        return wrapper

    return decorator


def _set_root(self, root):
    self._root = root


def _set_active_cfg(self, config):
    config._meta["active_config"] = True


class HDF5Engine(Engine):
    def __init__(self, root, comm):
        super().__init__(root, comm)
        self._lock = MPILock.sync()

    def __eq__(self, other):
        eq_format = self._format == getattr(other, "_format", None)
        eq_root = self._root == getattr(other, "_root", None)
        return eq_format and eq_root

    @property
    def root_slug(self):
        return os.path.relpath(self._root)

    def _read(self):
        return self._lock.read()

    def _write(self):
        return self._lock.write()

    def _master_write(self):
        return self._lock.single_write()

    def _handle(self, mode):
        return h5py.File(self._root, mode)

    def exists(self):
        return os.path.exists(self._root)

    @on_main_until(lambda self: self.exists())
    def create(self):
        with self._handle("w") as handle:
            handle.create_group("cells")
            handle.create_group("placement")
            handle.create_group("connectivity")
            handle.create_group("files")
            handle.create_group("morphologies")

    @on_main_until(lambda self, r: self.exists(), _set_root)
    def move(self, new_root):
        shutil.move(self._root, new_root)
        self._root = new_root

    @on_main_until(lambda self, r: self.__class__(self.root, self.comm).exists())
    def copy(self, new_root):
        shutil.copy(self._root, new_root)

    @on_main_until(lambda self: not self.exists())
    def remove(self):
        os.remove(self._root)

    @on_main_until(
        lambda self, ct: PlacementSet.exists(self, ct),
        prep=None,
        ret=lambda self, ct: PlacementSet(self, ct),
    )
    def require_placement_set(self, ct):
        return PlacementSet.require(self, ct)

    @on_main()
    def clear_placement(self):
        with self._handle("a") as handle:
            handle.require_group("placement")
            del handle["placement"]
            handle.require_group("placement")

    @on_main()
    def clear_connectivity(self):
        with self._handle("a") as handle:
            handle.require_group("connectivity")
            del handle["connectivity"]
            handle.require_group("connectivity")


def _get_default_root():
    return os.path.abspath(
        os.path.join(
            ".",
            "scaffold_network_"
            + datetime.now().strftime("%Y_%m_%d")
            + "_"
            + shortuuid.uuid()
            + ".hdf5",
        )
    )


@config.node
class StorageNode(IStorageNode):
    root = config.attr(type=str, default=_get_default_root, call_default=True)
    """
    Path to the HDF5 network storage file.
    """
