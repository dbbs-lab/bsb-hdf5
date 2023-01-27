import setuptools
import os

_findver = "__version__ = "
_rootpath = os.path.join(os.path.dirname(__file__), "bsb_hdf5", "__init__.py")
with open(_rootpath, "r") as f:
    for line in f:
        if "__version__ = " in line:
            f = line.find(_findver)
            __version__ = eval(line[line.find(_findver) + len(_findver) :])
            break
    else:
        raise Exception(f"No `__version__` found in '{_rootpath}'.")

with open("README.md", "r") as fh:
    long_description = fh.read()

requires = [
    "bsb~=4.0.0a47",
    "shortuuid",
]

setuptools.setup(
    name="bsb-hdf5",
    version=__version__,
    author="Robin De Schepper, Egidio D'Angelo, Claudia Casellato",
    author_email="robingilbert.deschepper@unipv.it",
    description="The HDF5 storage engine of the BSB",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/dbbs-lab/bsb-hdf5",
    license="GPLv3",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Operating System :: OS Independent",
    ],
    entry_points={
        "bsb.storage.engines": ["hdf5 = bsb_hdf5"],
    },
    python_requires="~=3.8",
    install_requires=requires,
    project_urls={
        "Bug Tracker": "https://github.com/dbbs-lab/bsb-hdf5/issues/",
        "Documentation": "https://bsb-hdf5.readthedocs.io/",
        "Source Code": "https://github.com/dbbs-lab/bsb-hdf5/",
    },
    extras_require={"dev": ["sphinx", "sphinxemoji", "furo", "sphinx_design"]},
)
