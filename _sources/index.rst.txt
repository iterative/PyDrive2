.. _welcome-to-pydrive2-s-documentation:

PyDrive2 documentation
======================

PyDrive2 2.0 is a Python client for Google Drive API v3 and an
fsspec-compatible filesystem for Google Drive. Use the :doc:`quickstart`
to sign in, upload a file, and list a folder. For path-based access with
``gdrive://`` URLs, see :doc:`fsspec`.

.. _how-to-install:

Installation
------------

PyDrive2 requires Python 3.11 or newer.

.. code-block:: sh

    python -m pip install PyDrive2

To use the fsspec filesystem, install the optional dependencies.

.. code-block:: sh

    python -m pip install 'PyDrive2[fsspec]'

.. _project-info:

The `DataChain team <https://www.datachain.ai/>`_ maintains PyDrive2.
Find the source on `GitHub <https://github.com/iterative/PyDrive2>`_,
the package on `PyPI <https://pypi.org/project/PyDrive2/>`_, and the
`release history <https://github.com/iterative/PyDrive2/releases>`_ on GitHub.

.. _table-of-contents:

Guides and reference
--------------------

.. toctree::
   :maxdepth: 2

   quickstart
   oauth
   filemanagement
   filelist
   fsspec
   upgrading
   pydrive2

:ref:`genindex`
