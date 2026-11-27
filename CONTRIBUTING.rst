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

Each matrix job runs the offline suite, then the live suite. Fork and
Dependabot PRs normally have no repository Drive credential, so the live
step fails. Their offline results are visible in the same jobs.

To land a reviewed external change, a maintainer can open a PR from a
branch in this repository. The required ``Live Drive tests`` check passes
when every matrix job succeeds.
