from django.conf import settings
from storages.backends.sftpstorage import SFTPStorage


class BoxSFTPStorage(SFTPStorage):
    """Backup submission-file store on Box's SFTP service.

    Box constraints baked in here:
      - password auth only, so keys/agent lookups are disabled (they waste round-trips
        and Box rejects them);
      - never set file_mode/dir_mode (Box rejects chmod);
      - host-key pinning via DMOJ_BOX_KNOWN_HOSTS (paramiko still uses AutoAddPolicy for
        hosts absent from the file, but a *changed* key for a pinned host is rejected).
    """

    def __init__(self, **kwargs):
        kwargs.setdefault('host', settings.DMOJ_BOX_SFTP_HOST)
        kwargs.setdefault('root_path', settings.DMOJ_BOX_SFTP_ROOT)
        kwargs.setdefault('known_host_file', settings.DMOJ_BOX_KNOWN_HOSTS or None)
        kwargs.setdefault('params', {
            'port': settings.DMOJ_BOX_SFTP_PORT,
            'username': settings.DMOJ_BOX_SFTP_USER,
            'password': settings.DMOJ_BOX_SFTP_PASSWORD,
            'timeout': 30,
            'banner_timeout': 30,
            'auth_timeout': 30,
            'look_for_keys': False,
            'allow_agent': False,
        })
        super().__init__(**kwargs)

    def open_stream(self, name):
        """Raw paramiko SFTPFile for a read — a true file-like object FileResponse can
        stream in chunks without buffering the whole file in memory."""
        return self.sftp.open(self._remote_path(name), 'rb')
