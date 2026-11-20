Contributing guidelines
=======================

How to become a contributor and submit your own code
----------------------------------------------------

TODO

Contributing code
~~~~~~~~~~~~~~~~~

If you have improvements to PyDrive2, send us your pull requests! For those
just getting started, Github has a `howto <https://help.github.com/articles/using-pull-requests/>`_.

Install the development tools with ``python -m pip install -e '.[tests,fsspec,typing]'``.
Run ``ruff check .`` to lint Python files and ``ruff format .`` to format them.
Run ``python -m mypy`` to type-check the package for Python 3.11.

Live Drive tests for external pull requests
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Fork and Dependabot PRs run offline tests automatically. To request live
Drive tests, comment ``/test-drive`` on the PR. CI looks up the commit, and
an ``external`` reviewer approves it after reviewing the full change. The
approved code runs with the existing Drive test credential. If the PR
changes, comment ``/test-drive`` again for the new revision.
