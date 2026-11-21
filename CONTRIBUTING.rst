Contributing guidelines
=======================

How to become a contributor and submit your own code
----------------------------------------------------

TODO

Contributing code
~~~~~~~~~~~~~~~~~

If you have improvements to PyDrive2, send us your pull requests! For those
just getting started, Github has a `howto`_.

Install the development tools with
``python -m pip install -e '.[tests,fsspec,typing]'``. Run ``ruff check .`` to
lint Python files and ``ruff format .`` to format them. Run ``python -m mypy``
to type-check the package for Python 3.11.

.. _howto: https://help.github.com/articles/using-pull-requests/

Live Drive tests for external pull requests
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Fork and Dependabot PRs run offline tests automatically. A repository
maintainer or admin can comment ``/test-drive`` to request live tests.
An ``external`` reviewer approves the displayed commit after reviewing the
full change. The approved code runs with the existing Drive test credential.
The PR's ``Live Drive tests`` check links to the run and must pass before
merging. A new commit needs a fresh request and approval.
