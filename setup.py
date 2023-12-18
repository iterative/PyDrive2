from setuptools import setup

# Extra dependecies to run tests
tests_requirements = [
    "pytest>=4.6.0",
    "timeout-decorator",
    "funcy>=1.14",
    "flake8",
    "flake8-docstrings",
    "pytest-mock",
    "pyinstaller",
    "importlib_resources < 6; python_version < '3.10'",
]

tests_requirements.append("black==23.12.0")

setup(
    name="PyDrive2",
    author="JunYoung Gwak",
    author_email="jgwak@dreamylab.com",
    maintainer="DVC team",
    maintainer_email="support@dvc.org",
    packages=[
        "pydrive2",
        "pydrive2.test",
        "pydrive2.fs",
        "pydrive2.__pyinstaller",
    ],
    url="https://github.com/iterative/PyDrive2",
    project_urls={
        "Documentation": "https://docs.iterative.ai/PyDrive2",
        "Changelog": "https://github.com/iterative/PyDrive2/releases",
    },
    license="Apache License 2.0",
    description="Google Drive API made easy. Maintained fork of PyDrive.",
    long_description=open("README.rst").read(),
    long_description_content_type="text/x-rst",
    install_requires=[
        "google-api-python-client >= 1.12.5",
        "oauth2client >= 4.0.0",
        "PyYAML >= 3.0",
        "pyOpenSSL >= 19.1.0",
    ],
    extras_require={
        "fsspec": [
            "fsspec >= 2021.07.0",
            "tqdm >= 4.0.0",
            "funcy >= 1.14",
            "appdirs >= 1.4.3",
        ],
        "tests": tests_requirements,
    },
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    entry_points={
        "pyinstaller40": [
            "hook-dirs = pydrive2.__pyinstaller:get_hook_dirs",
            "tests = pydrive2.__pyinstaller:get_PyInstaller_tests",
        ]
    },
)
