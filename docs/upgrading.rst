Upgrade to PyDrive2 2.0
=======================

PyDrive2 2.0 requires Python 3.11 or newer, authenticates with google-auth,
and uses Google Drive API v3. Drive file and folder IDs remain valid.

Credentials
-----------

Keep your OAuth client ID and secret. Sign in once with PyDrive2 2.0 to
create new user credentials. If you configured a cache, choose a new
``save_credentials_file`` or ``save_credentials_key`` before signing in.
For unattended jobs, enable ``get_refresh_token`` and ``save_credentials``
during sign-in and provision the resulting token JSON. See :doc:`oauth`.

The fsspec backend uses a separate default cache for 2.0. Its first
interactive connection prompts for a login. If you pass an explicit user
cache path, choose a new path. For DVC remotes that use
``GDRIVE_CREDENTIALS_DATA`` for a user account, replace its value with the
newly saved credentials.

Service accounts use their configured JSON or PKCS12 signing key. Keep the
key available to the application. Deployments that relied solely on a
saved service-account token need to provision the signing key.

Filesystem directory lookups
----------------------------

The :doc:`fsspec filesystem <fsspec>` now caches folders returned by
``ls()``. Later calls such as ``exists("root/data")`` can reuse those IDs.
If another client moves or removes a folder, list its parent again or
call ``fs.invalidate_cache("root/data")`` before checking the old path.

Drive API fields and requests
-----------------------------

Update metadata dictionaries, field masks, and search expressions to use
Drive v3 field names.

.. list-table::
   :header-rows: 1
   :widths: 28 72

   * - Drive API v2
     - Drive API v3
   * - ``title``, ``fileSize``, ``alternateLink``
     - ``name``, ``size``, ``webViewLink``
   * - ``labels.trashed``
     - Read ``trashed`` on the file itself.
   * - Parent objects
     - Pass one parent ID as a string, e.g. ``{"parents": [folder_id]}``.
       Assign the new list and call ``Upload()`` to move an existing file.
   * - ``maxResults``, ``items(...)``
     - Use ``pageSize`` and ``files(id,name)``. See :doc:`filelist` for
       pagination and partial responses.
   * - Permission ``value``
     - Use ``emailAddress`` for a user or group. For link sharing, set
       ``allowFileDiscovery=False``. The notification request parameter
       is now ``sendNotificationEmail`` (singular).
   * - Upload option ``convert``
     - Set the destination Workspace MIME type in the file metadata and
       pass the source MIME type as ``media_mime_type`` to ``Upload()``.

File reads and listings request a subset of metadata by default. For
additional file fields, use ``FetchMetadata(fields="owners,capabilities")``
or ``FetchMetadata(fetch_all=True)``. On listings, include ``id`` in a
custom selector when you will operate on the returned files.

See :doc:`filemanagement` for a Workspace import example and Google's
`Drive v2 to v3 reference`_ for the full API mapping.

.. _Drive v2 to v3 reference: https://developers.google.com/workspace/drive/api/guides/v2-to-v3-reference
