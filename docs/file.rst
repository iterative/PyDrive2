File referencing made easy
=============================

*PyDrive* can create a single `GoogleDriveFile`_ if you already know
the file's ID.

Specify a file by ID
--------------------

Create a `GoogleDriveFile`_ instance with a ``dict`` specifying the
Google Drive file ID.  Then call the various ``GetContent*()`` methods
(i.e., `GetContentString()`_, `GetContentFile()`_, and
`GetContentIOBuffer()`_) to download contents of that file.

.. code-block:: python

    import mimetypes
    from pydrive2.files import GoogleDriveFile

    # Download a file as a PPTX (e.g., a Google Slides file, or even
    # an actual PPTX file)
    def download_pptx(id, output_file='my-slides.pptx'):
        mtype = mimetypes.guess_type(output_file)[0]
        gfile = GoogleDriveFile(gauth, {'id': id})
        gfile.GetContentFile(filename=output_file, mimetype=mtype)

    # Download a text file as a string
    def download_text_file(id):
        mtype = 'text/plain'
        gfile = GoogleDriveFile(gauth, {'id': id})
        return gfile.GetContentString(mimetype=mtype)

.. _`GoogleDriveFile`: /PyDrive2/pydrive2/#pydrive2.files.GoogleDriveFile
.. _`GetContentFile()`: /PyDrive2/pydrive2/#pydrive2.files.GetContentFile
.. _`GetContentString()`: /PyDrive2/pydrive2/#pydrive2.files.GetContentString
.. _`GetContentIOBuffer()`: /PyDrive2/pydrive2/#pydrive2.files.GetContentIOBuffer
