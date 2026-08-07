import logging


class RequestIdFilter(logging.Filter):
    """Keep structured console logging safe for non-request code paths."""

    def filter(self, record):
        if not hasattr(record, 'request_id'):
            record.request_id = '-'
        return True
