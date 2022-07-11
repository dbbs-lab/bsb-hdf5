import os, sys, unittest, threading
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def get_config(file):
    return os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "data",
            "configs",
            file + (".json" if not file.endswith(".json") else ""),
        )
    )


def get_morphology(file):
    return os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "data",
            "morphologies",
            file,
        )
    )
