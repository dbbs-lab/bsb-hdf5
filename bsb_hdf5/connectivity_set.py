from bsb.exceptions import *
from .resource import Resource
from bsb.storage._chunks import Chunk
from bsb.storage.interfaces import ConnectivitySet as IConnectivitySet
import numpy as np

_root = "/connectivity/"


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
        with engine._read():
            with engine._handle("r") as h:
                return list(h[_root].keys())

    @classmethod
    def create(cls, engine, pre_type, post_type, tag=None):
        """
        Create the structure for this connectivity set in the HDF5 file. Connectivity sets are
        stored under ``/connectivity/<tag>``.
        """
        if tag is None:
            tag = f"{pre_type.name}_to_{post_type.name}"
        path = _root + tag
        with engine._write() as fence:
            with engine._handle("a") as h:
                g = h.create_group(path)
                g.attrs["pre"] = pre_type.name
                g.attrs["post"] = post_type.name
                g.require_group(path + "/inc")
                g.require_group(path + "/out")
        cs = cls(engine, tag)
        cs.pre = pre_type
        cs.post = post_type
        return cs

    @staticmethod
    def exists(engine, tag, handle=None):
        check = lambda h: _root + tag in h
        if handle is not None:
            return check(handle)
        else:
            with engine._read():
                with engine._handle("r") as h:
                    return check(h)

    @classmethod
    def require(cls, engine, pre_type, post_type, tag=None):
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

    def connect(self, pre_set, post_set, src_locs, dest_locs):
        with self._engine._write():
            with self._engine._handle("a") as handle:
                for data in self._demux(pre_set, post_set, src_locs, dest_locs):
                    if not len(data[-1]):
                        # Don't write empty data
                        continue
                    self.chunk_connect(*data, handle=handle)

    def _demux(self, pre, post, src_locs, dst_locs):
        dst_chunks = post.get_loaded_chunks()
        lns = []
        for dst in iter(dst_chunks):
            with post.chunk_context(dst):
                lns.append(len(post))
        # Iterate over each source chunk
        for src in pre.get_loaded_chunks():
            # Count the number of cells
            with pre.chunk_context(src):
                ln = len(pre)
            src_idx = src_locs[:, 0] < ln
            src_block = src_locs[src_idx]
            dst_block = dst_locs[src_idx]
            if len(dst_chunks) == 1:
                block_idx = np.lexsort((dst_block[:, 0], src_block[:, 0]))
                yield src, dst, src_block[block_idx], dst_block[block_idx]
            else:
                dctr = 0
                for dst, dln in zip(iter(dst_chunks), lns):
                    block_idx = (dst_block[:, 0] >= dctr) & (dst_block[:, 0] < dctr + dln)
                    yield src, dst, src_block[block_idx], dst_block[block_idx]
                    dctr += dln
            src_locs = src_locs[~src_idx]
            dst_locs = dst_locs[~src_idx]
            # We sifted `ln` cells out of the dataset, so reduce the ids.
            src_locs[:, 0] -= ln

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

    def _get_chunk_data(self, dest_chunk):
        with self._engine._write():
            with self._engine._handle("a") as h:
                grp = h[f"{self._path}/{dest_chunk.id}"]
                src_chunks = grp.attrs["chunk_list"]
                chunk_ptrs = [grp.attrs[str(Chunk(c, (0, 0, 0)).id)] for c in src_chunks]
                src = grp["global_locs"][()]
                dest = grp["local_locs"][()]
        return src_chunks, chunk_ptrs, src, dest

    def chunk_connect(self, src_chunk, dst_chunk, src_locs, dst_locs, handle=None):
        if len(src_locs) != len(dst_locs):
            raise ValueError("Location matrices must be of same length.")
        if handle is None:
            with self._engine._write():
                with self._engine._handle("a") as handle:
                    self._connect(src_chunk, dst_chunk, src_locs, dst_locs, handle)
        else:
            self._connect(src_chunk, dst_chunk, src_locs, dst_locs, handle)

    def _connect(self, src_chunk, dest_chunk, lloc, gloc, handle):
        self._insert("inc", dest_chunk, src_chunk, gloc, lloc, handle)
        self._insert("out", src_chunk, dest_chunk, lloc, gloc, handle)

    def _insert(self, tag, local_, global_, lloc, gloc, handle):
        grp = handle.require_group(f"{self._path}/{tag}/{local_.id}")
        src_id = str(global_.id)
        unpack_me = [None, None]
        # require_dataset doesn't work for resizable datasets, see
        # https://github.com/h5py/h5py/issues/2018
        # So we create a little thingy for requiring src & dest
        for i, tag in enumerate(("global_locs", "local_locs")):
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
        src_end = lcl_ds[(eptr - new_rows) :]
        dest_end = gbl_ds[(eptr - new_rows) :]
        lcl_ds.resize(len(lcl_ds) + new_rows, axis=0)
        gbl_ds.resize(len(gbl_ds) + new_rows, axis=0)
        lcl_ds[iptr:eptr] = np.concatenate((lcl_ds[iptr : (eptr - new_rows)], lloc))
        lcl_ds[eptr:] = src_end
        gbl_ds[iptr:eptr] = np.concatenate((gbl_ds[iptr : (eptr - new_rows)], gloc))
        gbl_ds[eptr:] = dest_end

    def get_local_chunks(self, direction):
        with self._engine._read():
            with self._engine._handle("r") as handle:
                return [
                    Chunk.from_id(int(k), None)
                    for k in handle[self._path][direction].keys()
                ]

    def get_global_chunks(self, direction, local_):
        with self._engine._read():
            with self._engine._handle("r") as handle:
                return [
                    Chunk(k, None)
                    for k in handle[self._path][f"{direction}/{local_.id}"].attrs[
                        "chunk_list"
                    ]
                ]

    def nested_iter_connections(self, direction=None, local_=None, global_=None):
        return CSIterator(self, direction, local_, global_)

    def flat_iter_connections(self, direction=None, local_=None, global_=None):
        itr = CSIterator(self, direction, local_, global_)
        for dir in itr.get_dir_iter(direction):
            for lchunk in itr.get_local_iter(dir, local_):
                for gchunk in itr.get_global_iter(dir, lchunk, global_):
                    conns = self.load_connections(dir, lchunk, gchunk)
                    yield (dir, lchunk, gchunk, conns)

    def load_connections(self, direction, local_, global_):
        with self._engine._read():
            with self._engine._handle("r") as handle:
                local_grp = handle[self._path][f"{direction}/{local_.id}"]
                start, end = self._get_insert_pointers(local_grp, global_)
                idx = slice(start, end)
                return (local_grp["local_locs"][idx], local_grp["global_locs"][idx])

    def load_local_connections(self, direction, local_):
        raise NotImplementedError(
            "Return edges that satisfy double constraint as 1 big block, with gchunk as extra col or tuple with 2 items"
        )

    def load_all_connections(self, direction="out"):
        raise NotImplementedError(
            "Return all connections in the network, assumes everything is bidirectional and focuses on outgoing connections by defaults."
        )


class CSIterator:
    def __init__(self, cs, direction=None, local_=None, global_=None):
        self._cs = cs
        self._dir = direction
        self._lchunks = local_
        self._gchunks = global_

    def __iter__(self):
        if self._dir is None:
            yield from (
                (dir, CSIterator(self._cs, dir, self._lchunks, self._gchunks))
                for dir in self.get_dir_iter(self._dir)
            )
        elif not isinstance(self._lchunks, Chunk):
            yield from (
                (lchunk, CSIterator(self._cs, self._dir, lchunk, self._gchunks))
                for lchunk in self.get_local_iter(self._dir, self._lchunks)
            )
        elif not isinstance(self._gchunks, Chunk):
            yield from (
                (gchunk, self._cs.load_connections(self._dir, self._lchunks, gchunk))
                for gchunk in self.get_global_iter(
                    self._dir, self._lchunks, self._gchunks
                )
            )
        else:
            yield self._cs.load_connections(self._dir, self._lchunks, self._gchunks)

    def get_dir_iter(self, dir):
        if dir is None:
            return ("inc", "out")
        else:
            return (dir,)

    def get_local_iter(self, dir, local_):
        if local_ is None:
            return self._cs.get_local_chunks(dir)
        elif isinstance(local_, Chunk):
            return (local_,)
        else:
            return iter(local_)

    def get_global_iter(self, dir, local_, global_):
        if global_ is None:
            return self._cs.get_global_chunks(dir, local_)
        elif isinstance(global_, Chunk):
            return (global_,)
        else:
            return iter(global_)


def _sort_triple(a, b):
    # Comparator for chunks by bitshift and sum of the coords.
    return (a[0] << 42 + a[1] << 21 + a[2]) > (b[0] << 42 + b[1] << 21 + b[2])
