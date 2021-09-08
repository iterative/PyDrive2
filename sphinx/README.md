This document outlines how to rebuild the documentation.

## Setup

Install Sphinx: `pip install sphinx` or `apt-get install python-sphinx`.
Then:

```bash
pip install furo  # theme
python setup.py build_sphinx --source-dir=sphinx --build-dir=dist/sphinx -b dirhtml --all-files
rm -rf docs/
mv dist/sphinx/dirhtml docs
```

## Contributing

If code files were added, the easiest way to reflect code changes in the
documentation by referencing the file from within `pydrive.rst`.

If a non-code related file was added (it has to have the `.rst` ending),
then add the file name to the list of names under "Table of Contents"
in `index.rst`. Make sure to add the file name excluding the `.rst` file ending.
