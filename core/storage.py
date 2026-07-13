# Deprecated: Cloudinary storage support has been completely removed from the backend.
# The server now operates as a 100% pure metadata/text aggregator utilizing raw target site URLs.
# This module is retained only for backward compatibility and is not executed.

import logging

logger = logging.getLogger("uvicorn")

def upload_image_to_cloudinary(*args, **kwargs):
    logger.warning("Call to deprecated upload_image_to_cloudinary function. Bypassing and returning original URL.")
    if args:
        return args[0]
    return ""
