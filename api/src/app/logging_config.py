import logging

from .config import get_log_level


def configure_logging() -> None:
    logging.basicConfig(
        level=get_log_level(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # basicConfig() sets the *root* logger's level -- any third-party
    # library logger that never sets its own level (most don't) inherits
    # that root level too, not just this app's own `app.*` loggers. The
    # smbprotocol package (fsspec's SMB client, used when
    # LUMINARY_PHOTOS_URI is smb://) logs one very chatty line per SMB2
    # command at INFO (e.g. "receiving SMB2 Create Response" on every file
    # open) -- harmless noise from this app's point of view, drowning out
    # our own INFO lines. Pinned to WARNING regardless of LUMINARY_LOG_LEVEL
    # so raising our own level for debugging doesn't also have to wade
    # through smbprotocol's internal protocol trace.
    logging.getLogger("smbprotocol").setLevel(logging.WARNING)
