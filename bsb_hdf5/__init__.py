from bsb import config
from bsb.services import MPILock
from bsb.storage.interfaces import Engine, StorageNode as IStorageNode
from .placement_set import PlacementSet
from .connectivity_set import ConnectivitySet
from .file_store import FileStore
from .morphology_repository import MorphologyRepository
from datetime import datetime
import json
import h5py
import os
import shutil
import shortuuid

__version__ = "0.5"
__all__ = [
    "PlacementSet",
    "ConnectivitySet",
    "FileStore",
    "MorphologyRepository",
    "HDF5Engine",
    "StorageNode",
]


def on_main(prep=None, ret=None):
    def decorator(f):
        def wrapper(self, *args, **kwargs):
            r = None
            self.comm.barrier()
            if self.comm.get_rank() == 0:
                r = f(self, *args, **kwargs)
            elif prep:
                prep(self, *args, **kwargs)
            self.comm.barrier()
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
            self.comm.barrier()
            if self.comm.get_rank() == 0:
                r = f(self, *args, **kwargs)
            elif prep:
                prep(self, *args, **kwargs)
            self.comm.barrier()
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


class NoopLock:
    def __enter__(self):
        return True

    def __exit__(self, exc_type, exc_val, exc_tb):
        return True


class HDF5Engine(Engine):
    def __init__(self, root, comm):
        super().__init__(root, comm)
        self._lock = MPILock.sync()
        self._readonly = False

    def __eq__(self, other):
        eq_format = self._format == getattr(other, "_format", None)
        eq_root = self._root == getattr(other, "_root", None)
        return eq_format and eq_root

    @property
    def root_slug(self):
        return os.path.relpath(self._root)

    @classmethod
    def recognizes(cls, root):
        try:
            h5py.File(root, "r").close()
            return True
        except Exception:
            return False

    def _read(self):
        if self._readonly:
            return NoopLock()
        else:
            return self._lock.read()

    def _write(self):
        if self._readonly:
            raise IOError("Can't perform write operations in readonly mode.")
        else:
            return self._lock.write()

    def _master_write(self):
        if self._readonly:
            raise IOError("Can't perform write operations in readonly mode.")
        else:
            return self._lock.single_write()

    def _handle(self, mode):
        if self._readonly and mode != "r":
            raise IOError("Can't perform write operations in readonly mode.")
        else:
            return h5py.File(self._root, mode)

    def exists(self):
        return os.path.exists(self._root)

    @on_main_until(lambda self: self.exists())
    def create(self):
        with self._handle("w") as handle:
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

    def get_chunk_stats(self):
        with self._handle("r") as handle:
            return json.loads(handle.attrs["chunks"])

    def read_only(self):
        return ReadOnlyManager(self)


class ReadOnlyManager:
    def __init__(self, engine):
        self._e = engine

    def __enter__(self):
        self._e._readonly = True

    def __exit__(self, *args):
        self._e._readonly = False


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
