# config.py

# Default to False, can be set dynamically
VERBOSE_MODE = False


def set_verbose_mode(value: bool):
    global VERBOSE_MODE
    VERBOSE_MODE = value


def is_verbose_mode():
    return VERBOSE_MODE
