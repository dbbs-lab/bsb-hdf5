from bsb.exceptions import DatasetNotFoundError
from .resource import Resource, handles_handles, HANDLED
from bsb.storage._chunks import Chunk, chunklist
from bsb.storage.interfaces import ConnectivitySet as IConnectivitySet
import numpy as np
import json

_root = "/connectivity/"


class LocationOutOfBoundsError(Exception):
    pass


class ConnectivitySet(Resource, IConnectivitySet):
    """
    Fetches placement data from storage.

    .. note::

        Use :meth:`Scaffold.get_connectivity_set <bsb.core.Scaffold.get_connectivity_set>`
        to correctly obtain a :class:`~bsb.storage.interfaces.ConnectivitySet`.
    """

    def __init__(self, engine, tag):
        self.tag = tag
        self.pre = None
        self.post = None
        super().__init__(engine, _root + tag)
        with engine._read():
            with engine._handle("r") as h:
                if not self.exists(engine, tag, handle=h):
                    raise DatasetNotFoundError(f"ConnectivitySet '{tag}' does not exist")
                self._pre_name = h[self._path].attrs["pre"]
                self._post_name = h[self._path].attrs["post"]

    def __len__(self):
        return sum(len(data[0]) for _, _, _, data in self.flat_iter_connections("inc"))

    @classmethod
    def get_tags(cls, engine):
        """
        Returns all the connectivity tags in the network.
        """
        with engine._read():
            with engine._handle("r") as h:
                return list(h[_root].keys())

    @classmethod
    def create(cls, engine, pre_type, post_type, tag=None):
        """
        Create the structure for this connectivity set in the HDF5 file. Connectivity sets
        are stored under ``/connectivity/<tag>``.
        """
        if tag is None:
            tag = f"{pre_type.name}_to_{post_type.name}"
        path = _root + tag
        with engine._write():
            with engine._handle("a") as h:
                g = h.create_group(path)
                g.attrs["pre"] = pre_type.name
                g.attrs["post"] = post_type.name
                g.require_group(f"{path}/inc")
                g.require_group(f"{path}/out")
        cs = cls(engine, tag)
        cs.pre = pre_type
        cs.post = post_type
        return cs

    @staticmethod
    def exists(engine, tag, handle=None):
        """
        Checks whether a :class:`~.connectivity_set.ConnectivitySet` with the given tag
        exists.

        :param engine: Engine to use for the lookup.
        :type engine: :class:`.HDF5Engine`
        :param tag: Tag of the set to look for.
        :type tag: str
        :param handle: An open handle to use instead of opening one.
        :type handle: :class:`h5py.File`
        :returns: Whether the tag exists.
        :rtype: bool
        """

        def check(h):
            return _root + tag in h

        if handle is not None:
            return check(handle)
        else:
            with engine._read():
                with engine._handle("r") as h:
                    return check(h)

    @classmethod
    def require(cls, engine, pre_type, post_type, tag=None):
        """
        Get or create a :class:`~.connectivity_set.ConnectivitySet`.

        :param engine: Engine to fetch/write the data.
        :type engine: :class:`.HDF5Engine`
        :param pre_type: Presynaptic cell type.
        :type pre_type: :class:`~bsb.cell_types.CellType`
        :param post_type: Postsynaptic cell type.
        :type post_type: :class:`~bsb.cell_types.CellType`
        :param tag: Tag to store the set under. Defaults to
          ``{pre_type.name}_to_{post_type.name}``.
        :type tag: str
        :returns: Existing or new connectivity set.
        :rtype: :class:`~.connectivity_set.ConnectivitySet`
        """
        if tag is None:
            tag = f"{pre_type.name}_to_{post_type.name}"
        path = _root + tag
        with engine._write():
            with engine._handle("a") as h:
                g = h.require_group(path)
                if g.attrs.setdefault("pre", pre_type.name) != pre_type.name:
                    raise ValueError(
                        "Given and stored type mismatch:"
                        + f" {pre_type.name} vs {g.attrs['pre']}"
                    )
                if g.attrs.setdefault("post", post_type.name) != post_type.name:
                    raise ValueError(
                        "Given and stored type mismatch:"
                        + f" {post_type.name} vs {g.attrs['post']}"
                    )
                g.require_group(path + "/inc")
                g.require_group(path + "/out")
        cs = cls(engine, tag)
        cs.pre = pre_type
        cs.post = post_type
        return cs

    def clear(self):
        raise NotImplementedError("Will do once I have some sample data :)")

    @handles_handles("a")
    def connect(self, pre_set, post_set, src_locs, dest_locs, handle=HANDLED):
        src_locs = np.array(src_locs, copy=False, dtype=int)
        dest_locs = np.array(dest_locs, copy=False, dtype=int)
        if not len(src_locs):
            return
        if len(src_locs) != len(dest_locs):
            raise ValueError("Location matrices must be of same length.")
        if pre_set._requires_morpho_mapping():
            src_locs = pre_set._morpho_backmap(src_locs)
        if post_set._requires_morpho_mapping():
            dest_locs = post_set._morpho_backmap(dest_locs)
        for data in self._demux(pre_set, post_set, src_locs, dest_locs):
            if not len(data[-1]):
                # Don't write empty data
                continue
            self.chunk_connect(*data, handle=handle)

    def _demux(self, pre, post, src_locs, dst_locs):
        src_chunks = pre.get_loaded_chunks()
        lns = []
        for src in iter(src_chunks):
            with pre.chunk_context(src):
                lns.append(len(pre))
        dmax = 0
        # Iterate over each source chunk
        for dst in post.get_loaded_chunks():
            # Count the number of cells
            with post.chunk_context(dst):
                ln = len(post)
            dst_idx = dst_locs[:, 0] < ln
            dst_block = dst_locs[dst_idx]
            src_block = src_locs[dst_idx]
            if len(src_chunks) == 1:
                block_idx = np.lexsort((src_block[:, 0], dst_block[:, 0]))
                yield src, dst, src_block[block_idx], dst_block[block_idx]
            else:
                for src, sln in zip(iter(src_chunks), lns):
                    block_idx = (src_block[:, 0] >= 0) & (src_block[:, 0] < sln)
                    yield src, dst, src_block[block_idx], dst_block[block_idx]
                    src_block[:, 0] -= sln
            dst_locs = dst_locs[~dst_idx]
            src_locs = src_locs[~dst_idx]
            # We sifted `ln` cells out of the dataset, so reduce the ids.
            dst_locs[:, 0] -= ln
            dmax += ln
        if len(dst_locs) > 0:
            smax = sum(lns) - 1
            dmax -= 1
            src_msg = ""
            if (m := np.max(src_locs)) > smax:
                src_msg = f"Source maximum is {smax}, but up to {m} given."
            dst_msg = ""
            if (m := np.max(src_locs)) > smax:
                if src_msg:
                    dst_msg = "\n"
                dst_msg += f"Destination maximum is {dmax}, but up to {m} given."
            raise LocationOutOfBoundsError(
                f"Received {len(dst_locs)} out of bounds locations:"
                f"\n- Source locations:\n{src_locs}"
                f"\n- Destinations:\n{dst_locs}\n" + src_msg + dst_msg
            )

    def _store_pointers(self, group, chunk, n, total):
        chunks = [Chunk(t, (0, 0, 0)) for t in group.attrs.get("chunk_list", [])]
        if chunk in chunks:
            # Source chunk already existed, just increment the subseq. pointers
            inc_from = chunks.index(chunk) + 1
        else:
            # We are the last chunk, we start adding rows at the end.
            group.attrs[str(chunk.id)] = total
            # Move up the increment pointer to place ourselves after the
            # last element
            chunks.append(chunk)
            inc_from = len(chunks)
            group.attrs["chunk_list"] = chunks
        # Increment the pointers of the chunks that follow us, by `n` rows.
        for c in chunks[inc_from:]:
            group.attrs[str(c.id)] += n

    def _get_sorted_pointers(self, group):
        chunks = [Chunk(t, (0, 0, 0)) for t in group.attrs["chunk_list"]]
        ptrs = np.array([group.attrs[str(c.id)] for c in chunks])
        sorted = np.argsort(ptrs)
        chunks = [chunks[cid] for cid in sorted]
        return chunks, ptrs[sorted]

    def _get_insert_pointers(self, group, chunk):
        chunks = [Chunk(t, (0, 0, 0)) for t in group.attrs["chunk_list"]]
        iptr = group.attrs[str(chunk.id)]
        idx = chunks.index(chunk)
        if idx + 1 == len(chunks):
            # Last chunk, no end pointer
            eptr = None
        else:
            # Get the pointer of the next chunk
            eptr = group.attrs[str(chunks[idx + 1].id)]
        return iptr, eptr

    @handles_handles("a")
    def chunk_connect(self, src_chunk, dst_chunk, src_locs, dst_locs, handle=HANDLED):
        if len(src_locs) != len(dst_locs):
            raise ValueError("Location matrices must be of same length.")
        self._insert("inc", dst_chunk, src_chunk, dst_locs, src_locs, handle)
        self._insert("out", src_chunk, dst_chunk, src_locs, dst_locs, handle)
        self._track_add(handle, src_chunk, dst_chunk, len(src_locs))

    def _insert(self, tag, local_, global_, lloc, gloc, handle):
        grp = handle.require_group(f"{self._path}/{tag}/{local_.id}")
        unpack_me = [None, None]
        # require_dataset doesn't work for resizable datasets, see
        # https://github.com/h5py/h5py/issues/2018
        # So we create a little thingy for requiring src & dest
        for i, tag in enumerate(("local_locs", "global_locs")):
            if tag in grp:
                unpack_me[i] = grp[tag]
            else:
                unpack_me[i] = grp.create_dataset(
                    tag, shape=(0, 3), dtype=int, chunks=(1024, 3), maxshape=(None, 3)
                )
        lcl_ds, gbl_ds = unpack_me
        # Move the pointers that keep track of the chunks
        new_rows = len(lloc)
        total = len(lcl_ds)
        self._store_pointers(grp, global_, new_rows, total)
        iptr, eptr = self._get_insert_pointers(grp, global_)
        if eptr is None:
            eptr = total + new_rows
        # Resize and insert data.
        lcl_end = lcl_ds[(eptr - new_rows) :]
        gbl_end = gbl_ds[(eptr - new_rows) :]
        lcl_ds.resize(len(lcl_ds) + new_rows, axis=0)
        gbl_ds.resize(len(gbl_ds) + new_rows, axis=0)
        lcl_ds[iptr:eptr] = np.concatenate((lcl_ds[iptr : (eptr - new_rows)], lloc))
        lcl_ds[eptr:] = lcl_end
        gbl_ds[iptr:eptr] = np.concatenate((gbl_ds[iptr : (eptr - new_rows)], gloc))
        gbl_ds[eptr:] = gbl_end

    def _track_add(self, handle, src_chunk, dst_chunk, count):
        for tag, chunk in (("inc", dst_chunk), ("out", src_chunk)):
            id = str(chunk.id)
            # Track addition in global chunk stats
            global_stats = json.loads(handle.attrs.get("chunks", "{}"))
            stats = global_stats.setdefault(
                id, {"placed": 0, "connections": {"inc": 0, "out": 0}}
            )
            stats["connections"][tag] += count
            handle.attrs["chunks"] = json.dumps(global_stats)
            # Track addition in connectivity set
            group = handle[self._path]
            if tag == "out":
                group.attrs["len"] = group.attrs.get("len", 0) + count
            chunk_stats = json.loads(group.attrs.get("chunks", "{}"))
            stats = chunk_stats.setdefault(id, {"inc": 0, "out": 0})
            stats[tag] += count
            group.attrs["chunks"] = json.dumps(chunk_stats)

    @handles_handles("r")
    def get_local_chunks(self, direction, handle=HANDLED):
        return chunklist(
            Chunk.from_id(int(k), None) for k in handle[self._path][direction].keys()
        )

    @handles_handles("r")
    def get_global_chunks(self, direction, local_, handle=HANDLED):
        return chunklist(
            Chunk(k, None)
            for k in handle[self._path][f"{direction}/{local_.id}"].attrs["chunk_list"]
        )

    def nested_iter_connections(self, direction=None, local_=None, global_=None):
        """
        Iterates over the connectivity data, leaving room for the end-user to set up
        nested for loops:

        .. code-block:: python

          for dir, local_itr in self.nested_iter_connections():
              for lchunk, global_itr in local_itr:
                  print("I can do something at the start of a new local chunk")
                  for gchunk, data in global_itr:
                      print(f"Nested {dir} block between {lchunk} and {gchunk}")
                  print("Or right before we move to the next local chunk")

        If a keyword argument is given, that axis is not iterated over, and the amount of
        nested loops is reduced.

        :param direction: When omitted, iterates ``inc`` and ``out``, otherwise when
          given, pins it to the given value
        :type direction: str
        :param local_: When omitted, iterates over all local chunks in the set. When
          given, it restricts the iteration to the given value(s).
        :type local_: Union[~bsb.storage.Chunk, list[~bsb.storage.Chunk]]
        :param global_: When omitted, iterates over all global chunks in the set. When
          given, it restricts the iteration to the given value(s).
        :type global_: Union[~bsb.storage.Chunk, list[~bsb.storage.Chunk]]
        :returns: An iterator that produces the next unrestricted iteration values, or
          the connection dataset that matches the iteration combination.
        """
        return CSIterator(self, direction, local_, global_)

    def flat_iter_connections(self, direction=None, local_=None, global_=None):
        """
        Iterates over the connectivity data.

        .. code-block:: python

          for dir, lchunk, gchunk, data in self.flat_iter_connections():
              print(f"Flat {dir} block between {lchunk} and {gchunk}")

        If a keyword argument is given, that axis is not iterated over, and the value is
        fixed in each iteration.

        :param direction: When omitted, iterates ``inc`` and ``out``. When given, it
          restricts the iteration to the given value.
        :type direction: str
        :param local_: When omitted, iterates over all local chunks in the set. When
          given, it restricts the iteration to the given value(s).
        :type local_: Union[~bsb.storage.Chunk, list[~bsb.storage.Chunk]]
        :param global_: When omitted, iterates over all global chunks in the set. When
          given, it restricts the iteration to the given value(s).
        :type global_: Union[~bsb.storage.Chunk, list[~bsb.storage.Chunk]]
        :returns: Yields the direction, local chunk, global chunk, and data. The data is a
          tuple of the local and global connection locations.
        :rtype: Tuple[str, ~bsb.storage.Chunk, ~bsb.storage.Chunk,
          Tuple[numpy.ndarray, numpy.ndarray]]
        """
        itr = CSIterator(self, direction, local_, global_)
        for direction in get_dir_iter(direction):
            for lchunk in itr.get_local_iter(direction, local_):
                for gchunk in itr.get_global_iter(direction, lchunk, global_):
                    conns = self.load_block_connections(direction, lchunk, gchunk)
                    yield direction, lchunk, gchunk, conns

    @handles_handles("r")
    def load_block_connections(self, direction, local_, global_, handle=HANDLED):
        """
        Load the connection block with given direction between the given local and global
        chunk.

        :param direction: Either ``inc`` to load the connections from the incoming
          perspective or ``out`` for the outgoing perspective.
        :type direction: str
        :param local_: Local chunk
        :type local_: ~bsb.storage.Chunk
        :param global_: Global chunk
        :type global_: ~bsb.storage.Chunk
        :param handle: This parameter is injected and doesn't have to be passed.
        :returns: The local and global connections locations
        :rtype: Tuple[numpy.ndarray, numpy.ndarray]
        """
        try:
            local_grp = handle[self._path][f"{direction}/{local_.id}"]
            start, end = self._get_insert_pointers(local_grp, global_)
        except KeyError:
            # If a local or global chunk isn't found, return empty data.
            return (np.empty((0, 3), dtype=int), np.empty((0, 3), dtype=int))

        idx = slice(start, end)
        return (local_grp["local_locs"][idx], local_grp["global_locs"][idx])

    @handles_handles("r")
    def load_local_connections(self, direction, local_, handle=HANDLED):
        """
        Load all the connections of the given local chunk.

        :param direction: Either ``inc`` to load the connections from the incoming
          perspective or ``out`` for the outgoing perspective.
        :type direction: str
        :param local_: Local chunk
        :type local_: ~bsb.storage.Chunk
        :param handle: This parameter is injected and doesn't have to be passed.
        :returns: The local connection locations, a vector of the global connection chunks
          (1 chunk id per connection) and the global connections locations. To identify a
          cell in the global connections, use the corresponding chunk id from the second
          return value.
        :rtype: Tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]
        """
        local_grp = handle[self._path][f"{direction}/{local_.id}"]
        global_locs = local_grp["global_locs"][()]
        chunks, ptrs = self._get_sorted_pointers(local_grp)
        col = np.repeat([c.id for c in chunks], np.diff(ptrs, append=len(global_locs)))
        return local_grp["local_locs"][()], col, global_locs


def _better_than_concat(arrs, cols, dtype):
    if not len(arrs):
        return np.empty((0, cols), dtype=dtype)
    cat = np.empty((sum(len(a) for a in arrs), cols), dtype=dtype)
    ptr = 0
    for arr in arrs:
        cat[ptr : ptr + len(arr)] = arr.reshape(-1, cols)
        ptr += len(arr)
    return cat


def get_dir_iter(direction):
    return ("inc", "out") if direction is None else (direction,)


class CSIterator:
    def __init__(self, cs, direction=None, local_=None, global_=None):
        self._cs = cs
        self._dir = direction
        self._lchunks = chunklist(local_) if local_ is not None else None
        self._gchunks = chunklist(global_) if local_ is not None else None

    def __iter__(self):
        if self._dir is None:
            yield from (
                (
                    direction,
                    CSIterator(self._cs, direction, self._lchunks, self._gchunks),
                )
                for direction in get_dir_iter(self._dir)
            )
        elif not isinstance(self._lchunks, Chunk):
            yield from (
                (lchunk, CSIterator(self._cs, self._dir, lchunk, self._gchunks))
                for lchunk in self.get_local_iter(self._dir, self._lchunks)
            )
        elif not isinstance(self._gchunks, Chunk):
            yield from (
                (
                    gchunk,
                    self._cs.load_block_connections(self._dir, self._lchunks, gchunk),
                )
                for gchunk in self.get_global_iter(
                    self._dir, self._lchunks, self._gchunks
                )
            )
        else:
            yield self._cs.load_block_connections(self._dir, self._lchunks, self._gchunks)

    def get_local_iter(self, direction, local_):
        if local_ is None:
            return self._cs.get_local_chunks(direction)
        elif isinstance(local_, Chunk):
            return (local_,)
        else:
            return iter(local_)

    def get_global_iter(self, direction, local_, global_):
        if global_ is None:
            return self._cs.get_global_chunks(direction, local_)
        elif isinstance(global_, Chunk):
            return (global_,)
        else:
            return iter(global_)
