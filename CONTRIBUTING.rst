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

Each matrix job runs the offline suite, then the live suite. Fork and
Dependabot PRs normally have no repository Drive credential, so the live
step fails. Their offline results are visible in the same jobs.

To land a reviewed external change, a maintainer can open a PR from a
branch in this repository. The required ``Live Drive tests`` check passes
when every matrix job succeeds.
