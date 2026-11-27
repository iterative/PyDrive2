Contributing guidelines
=======================

Contributing code
-----------------

Submit changes in a pull request. GitHub's `pull request guide`_ describes
how to propose and review changes.

Install the development tools with
``python -m pip install -e '.[tests,fsspec,typing]'``. Run ``ruff check .`` to
lint Python files and ``ruff format .`` to format them. Run ``python -m mypy``
to type-check the package for Python 3.11.

See the `test guide <pydrive2/test/README.rst>`_ for offline and live suites
and the `documentation guide <docs/README.md>`_ to preview the site.

.. _pull request guide: https://docs.github.com/en/pull-requests

Live Drive tests for external pull requests
-------------------------------------------

Fork and Dependabot PRs run offline tests automatically. A repository
maintainer or admin can comment ``/test-drive`` to request live tests.
An ``external`` reviewer approves the displayed commit after reviewing the
full change. The approved code runs with the existing Drive test credential.
The PR's ``Live Drive tests`` check links to the run and must pass before
merging. A new commit needs a fresh request and approval.
