# Building the PyDrive2 documentation

From the repository root, install the package with fsspec support and the
documentation dependencies. Sphinx imports the installed package to generate
the API reference.

```sh
python -m pip install -e '.[fsspec]' -r docs/requirements.txt
python -m sphinx -W --keep-going -b dirhtml docs dist/site
python -m http.server --directory dist/site 8000
```

Open <http://localhost:8000/> to preview the site. To check external links,
run `python -m sphinx -b linkcheck docs dist/linkcheck`.

Add new guides to the table of contents in `docs/index.rst`. Public modules
and classes are documented in `docs/pydrive2.rst`. The **Build docs**
workflow builds pull requests and publishes successful builds from `main`
to GitHub Pages.
