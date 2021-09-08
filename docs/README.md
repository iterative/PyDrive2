This document outlines how to rebuild the documentation.

## Setup

- Install Sphinx: `pip install sphinx` or `apt-get install python-sphinx`
- Install theme: `pip install furo`
- Build site: `sphinx-build docs dist/site -b dirhtml -a`

Updating GitHub Pages:

```bash
cd dist/site
git init
git add .
git commit -m "update pages"
git branch -M gh-pages
git push -f git@github.com:iterative/PyDrive2 gh-pages
```

## Contributing

If code files were added, the easiest way to reflect code changes in the
documentation by referencing the file from within `pydrive.rst`.

If a non-code related file was added (it has to have the `.rst` ending),
then add the file name to the list of names under "Table of Contents"
in `index.rst`. Make sure to add the file name excluding the `.rst` file ending.
