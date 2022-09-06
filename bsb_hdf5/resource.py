import numpy as np
import h5py
import inspect
import functools


# Semantic marker for things that get injected
HANDLED = None


# Decorator to inject handles
def handles_handles(handle_type, handler=lambda self: self._engine):
    lock_f = {"r": lambda eng: eng._read, "a": lambda eng: eng._write}.get(handle_type)

    def decorator(f):
        sig = inspect.signature(f)
        if "handle" not in sig.parameters:
            raise ValueError(
                f"`{f.__module__}.{f.__name__}` needs handle to be handled by handles_handles. Clearly."
            )

        @functools.wraps(f)
        def decorated(self, *args, handle=None, **kwargs):
            engine = handler(self)
            lock = lock_f(engine)
            bound = sig.bind(self, *args, **kwargs)
            if bound.arguments.get("handle", None) is None:
                with lock():
                    with engine._handle(handle_type) as handle:
                        bound.arguments["handle"] = handle
                        return f(*bound.args, **bound.kwargs)
            else:
                return f(*bound.args, **bound.kwargs)

        return decorated

    return decorator


class Resource:
    def __init__(self, engine, path):
        self._engine = engine
        self._path = path

    def __eq__(self, other):
        return (
            self._engine == getattr(other, "_engine", None) and self._path == other._path
        )

    def require(self, handle):
        return handle.require_group(self._path)

    def create(self, data, *args, **kwargs):
        with self._engine._write():
            with self._engine._handle("a") as f:
                f.create_dataset(self._path, data=data, *args, **kwargs)

    def keys(self):
        with self._engine._read():
            with self._engine._handle("r") as f:
                node = f[self._path]
                if isinstance(node, h5py.Group):
                    return list(node.keys())

    def remove(self):
        with self._engine._write():
            with self._engine._handle("a") as f:
                del f[self._path]

    def get_dataset(self, selector=()):
        with self._engine._read():
            with self._engine._handle("r") as f:
                return f[self._path][selector]

    @property
    def attributes(self):
        with self._engine._read():
            with self._engine._handle("r") as f:
                return dict(f[self._path].attrs)

    def get_attribute(self, name):
        attrs = self.attributes
        if name not in attrs:
            raise AttributeError(
                "Attribute '{}' not found in '{}'".format(name, self._path)
            )
        return attrs[name]

    def exists(self):
        with self._engine._read():
            with self._engine._handle("r") as f:
                return self._path in f

    def unmap(self, selector=(), mapping=lambda m, x: m[x], data=None):
        if data is None:
            data = self.get_dataset(selector)
        map = self.get_attribute("map")
        unmapped = []
        for record in data:
            unmapped.append(mapping(map, record))
        return np.array(unmapped)

    def unmap_one(self, data, mapping=None):
        if mapping is None:
            return self.unmap(data=[data])
        else:
            return self.unmap(data=[data], mapping=mapping)

    def __iter__(self):
        return iter(self.get_dataset())

    @property
    def shape(self):
        with self._engine._read():
            with self._engine._handle("r") as f:
                return f[self._path].shape

    def __len__(self):
        return self.shape[0]

    def append(self, new_data, dtype=float):
        if type(new_data) is not np.ndarray:
            new_data = np.array(new_data)
        with self._engine._write():
            with self._engine._handle("a") as f:
                try:
                    d = f[self._path]
                except Exception:
                    shape = list(new_data.shape)
                    shape[0] = None
                    d = f.create_dataset(
                        self._path, data=new_data, dtype=dtype, maxshape=tuple(shape)
                    )
                else:
                    len_ = d.shape[0]
                    len_ += len(new_data)
                    d.resize(len_, axis=0)
                    d[-len(new_data) :] = new_data
