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

Fork and Dependabot PRs run offline tests in the regular CI matrix. To run
live tests before merge, a maintainer can select **Reviewed PR Drive tests**
in Actions, choose **Run workflow** on ``main``, and enter the PR number and
the full head commit SHA.

A reviewer for the ``external`` environment must review the complete PR diff
at that SHA, including dependencies and install steps, before approving the
job. Approval allows that revision to use the test Drive account. The job
checks the current PR head after approval and checks out the approved SHA.
Each new revision needs a new dispatch and approval.

The result appears in Actions under the PR number and SHA. The job runs all
noninteractive CI tests, including live Drive tests, on Ubuntu and Python
3.11. The test step uses the existing ``GDRIVE_USER_CREDENTIALS_DATA``
repository secret, which contains the CI service-account JSON key.
