from bsb.storage.interfaces import FileStore as IFileStore
from .resource import Resource
from uuid import uuid4
import json
import io

_root = "files"


class FileStore(Resource, IFileStore):
    def __init__(self, engine):
        super().__init__(engine, _root)

    def all(self):
        with self._engine._read():
            with self._engine._handle("r") as root:
                store = root[self._path]
                return {id: dict(f.attrs.items()) for id, f in store.items()}

    def load(self, id):
        with self._engine._read():
            with self._engine._handle("r") as root:
                ds = root[f"{self._path}/{id}"]
                return ds[()].decode("utf-8"), dict(ds.attrs)

    def stream(self, id, binary=False):
        content = self.load(id)
        if not binary:
            return io.TextIOWrapper(content)
        else:
            return io.BytesIO(content)

    def remove(self, id):
        with self._engine._write():
            with self._engine._handle("a") as root:
                del root[f"{self._path}/{id}"]

    def store(self, content, meta=None, id=None):
        if id is None:
            id = str(uuid4())
        if meta is None:
            meta = {}
        with self._engine._write():
            with self._engine._handle("a") as root:
                store = root[self._path]
                try:
                    ds = store.create_dataset(id, data=content)
                except ValueError:
                    raise Exception(f"File `{id}` already exists in store.")
                for k, v in meta.items():
                    ds.attrs[k] = v
        return id

    def load_active_config(self):
        """
        Load the active configuration stored inside of the storage.

        :returns: The active configuration that is loaded when this storage object is.
        :rtype: ~bsb.config.Configuration
        """
        from bsb.config import Configuration

        cfg_id = self._active_config_id()
        if cfg_id is None:
            raise Exception("No active config")
        else:
            content, meta = self.load(cfg_id)
            # It's a serialized Python dict, so it should be JSON readable. We don't use
            # evaluate because files might originate from untrusted sources.
            tree = json.loads(content)
            cfg = Configuration(**tree)
            cfg._meta = meta
            return cfg

    def store_active_config(self, config):
        """
        Set the active configuration for this network.

        :param config: The active configuration that will be loaded when this storage
          object is.
        :type config: ~bsb.config.Configuration
        """
        id = self._active_config_id()
        self._engine.comm.barrier()
        if id is not None and self._engine.comm.get_rank() == 0:
            self.remove(id)
        config._meta["active_config"] = True
        active_id = None
        if self._engine.comm.get_rank() == 0:
            meta = {k: v for k, v in config._meta.items() if v is not None}
            active_id = self.store(json.dumps(config.__tree__()), meta)
        return self._engine.comm.bcast(active_id, root=0)

    def _active_config_id(self):
        match = (id for id, m in self.all().items() if m.get("active_config", False))
        return next(match, None)
