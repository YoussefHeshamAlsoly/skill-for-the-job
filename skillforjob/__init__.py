"""The *skill-for-the-job* pipeline.

The package is split by concern, one module per job:

``config``      paths, constants and the JSON backed configuration
``logs``        the shared logger
``files``       crash safe file helpers
``deps``        optional dependency handling
``interpreter`` finding the Python interpreter that has the dependencies
``dataset``     downloading, normalising and summarising the dataset
``dates``       parsing and applying the free form date ranges
``matching``    matching a job title against the dataset
``skills``      the skill histogram of a matched job title
``diagnostics`` the ``doctor`` command
``cli``         the command line interface

``main.py`` in the project root is only the entry point::

    python main.py skills "software engineer"
"""

__version__ = "1.0.0"
