"""Serialize MuPDF calls across local worker threads.

Artwork releases the lock after each page so a newly imported paper can parse
its text without waiting for the previous paper's entire artwork pass.
"""
from functools import wraps
from threading import RLock

PDF_LOCK = RLock()


def synchronized_pdf(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with PDF_LOCK:
            return function(*args, **kwargs)
    return wrapped
