import datetime
import os
import pathlib
import sys
import logging


def create_folder(folder: str | pathlib.Path):
    """
    Create a folder (and any missing parents) if it does not already exist.

    Parameters
    ----------
    folder : str | pathlib.Path
        Path to create.
    """
    if not os.path.exists(folder):
        os.makedirs(folder)


def check_if_folder_exists(path: str) -> bool:
    """
    Check whether a given path exists and is a directory.

    Parameters
    ----------
    path : str

    Returns
    -------
    bool
        True if the path exists and is a directory; False otherwise.
    """
    return os.path.isdir(path)


class Configurations:
    """
    Central runtime configuration for the application.

    Purpose
    -------
    - Holds paths, encoding, logging, and RabbitMQ connection settings used
      throughout the system.
    - Lazily creates **data** and **logs** directories for the current day:
        * `<project_root>/data/data_YYYY_MM_DD`
        * `<project_root>/logs/log_YYYY_MM_DD`
    - Initializes a **root logger** named after the entrypoint script.

    Attributes (selected)
    ---------------------
    encoding : str
        Default text encoding (UTF-8).
    data_directory : pathlib.Path (property)
        Lazily created `data/data_YYYY_MM_DD` folder; can be overridden.
    logging_directory : pathlib.Path (property)
        Lazily created `logs/log_YYYY_MM_DD` folder; can be overridden.
    root_file : pathlib.Path
        Path to the main script (`sys.argv[0]`).
    root_logger_name : str
        Logger base name (stem of root_file).
    logger : logging.Logger
        Root logger instance.
    logging : bool
        If True, logging is enabled (handlers attached in `_setup_logger()`).

    RabbitMQ settings
    -----------------
    rabbit_host : str
    rabbit_port : int
    rabbit_port_http : int
    rabbit_username : str
    rabbit_password : str
    rabbit_exchange : str
    rabbit_queue_timeout : int
        Seconds to wait for queue operations by default.
    rabbit_auth : tuple[str, str]
        (username, password) pair for convenience.
    pickle_protocol : int
        Pickle protocol used for message serialization (if applicable).

    Notes
    -----
    - `data_directory` and `logging_directory` are **created on first access**.
    - By default, the log file is named after the main script, e.g., `logs/log_YYYY_MM_DD/main.log`.
    """

    def __init__(self):
        # -------------------------
        # General
        # -------------------------
        self.encoding = "UTF-8"

        # -------------------------
        # Data
        # -------------------------
        self._data_directory = None
        self.root_file = pathlib.Path(sys.argv[0])  # Entrypoint path (may be absolute or relative)

        # -------------------------
        # Logging
        # -------------------------
        self.logging = True
        self._logging_directory = None
        self.root_file = pathlib.Path(sys.argv[0])  # (re)store to ensure consistency
        self.root_logger_name = self.root_file.stem
        self.logger = logging.getLogger(self.root_logger_name)
        self._setup_logger()

        # -------------------------
        # RabbitMQ
        # -------------------------
        # IMPORTANT: Update these **before** initializing devices that connect to RabbitMQ.
        self.rabbit_host = '127.0.0.1'
        self.rabbit_port = 5672
        self.rabbit_port_http = 15672
        self.rabbit_username = 'guest'
        self.rabbit_password = 'guest'
        self.rabbit_exchange = 'chembot'
        self.rabbit_queue_timeout = 1  # sec
        self.rabbit_auth = (self.rabbit_username, self.rabbit_password)
        self.pickle_protocol = 5

    # -------------------------
    # Data directory (lazy)
    # -------------------------

    @property
    def data_directory(self) -> pathlib.Path:
        """
        Lazy-initialized data directory for today's date.

        Structure
        ---------
        <root>/data/data_YYYY_MM_DD

        Behavior
        --------
        - If not set explicitly, creates `<project_root>/data` and a dated subfolder.
        - Returns a `pathlib.Path` for convenient path operations.
        """
        if self._data_directory is None:
            # create new folder 'data'
            path = self.root_file.parent / pathlib.Path("data")
            create_folder(path)
            # create new folder 'data.date'
            self._data_directory = path / pathlib.Path(datetime.datetime.now().strftime("data_%Y_%m_%d"))
            create_folder(self._data_directory)

        return self._data_directory

    @data_directory.setter
    def data_directory(self, data_directory: str):
        """
        Override the data directory (must already exist).

        Raises
        ------
        ValueError
            If the provided path does not exist or is not a directory.
        """
        if not check_if_folder_exists(data_directory):
            raise ValueError("'data_directory' not found.")
        self._data_directory = data_directory

    # -------------------------
    # Logging directory (lazy)
    # -------------------------

    @property
    def logging_directory(self) -> pathlib.Path:
        """
        Lazy-initialized logging directory for today's date.

        Structure
        ---------
        <root>/logs/log_YYYY_MM_DD

        Behavior
        --------
        - If not set explicitly, creates `<project_root>/logs` and a dated subfolder.
        - Returns a `pathlib.Path` for convenient path operations.
        """
        if self._logging_directory is None:
            # create new folder 'logs'
            path = self.root_file.parent / pathlib.Path("logs")
            create_folder(path)
            # create new folder 'logs.date'
            self._logging_directory = path / pathlib.Path(datetime.datetime.now().strftime("log_%Y_%m_%d"))
            create_folder(self._logging_directory)

        return self._logging_directory

    @logging_directory.setter
    def logging_directory(self, logging_directory: str):
        """
        Override the logging directory (must already exist).

        Raises
        ------
        ValueError
            If the provided path does not exist or is not a directory.
        """
        if not check_if_folder_exists(logging_directory):
            raise ValueError("'logging_directory' not found.")
        self._logging_directory = logging_directory

    # -------------------------
    # Logger setup
    # -------------------------

    def _setup_logger(self):
        """
        Configure the root logger with file + console handlers and a standard format.

        Behavior
        --------
        - Level: DEBUG (captures all levels; handlers can filter further if desired).
        - File handler writes to: `<logging_directory>/<main_script_stem>.log`
        - Console handler prints to stderr.
        - Adds a prominent CRITICAL banner at startup for log separation.
        """
        self.logger.setLevel(logging.DEBUG)

        # file handler
        file_handler = logging.FileHandler(
            self.logging_directory / pathlib.Path(self.root_file.stem + '.log'),
            encoding="UTF-8",
            mode='a'
        )
        # file_handler.setLevel(logging.DEBUG)  # handler-level filtering if needed

        # console logger
        console_handler = logging.StreamHandler()
        # console_handler.setLevel(logging.DEBUG)

        # formatter
        formatter = logging.Formatter(
            '%(asctime)s.%(msecs)03d %(levelname)-8s || %(name)-25s %(message)s\n',
            datefmt='%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        # Attach handlers
        self.logger.addHandler(file_handler)
        self.logger.critical(
            "\n"
            "\n#######################################################################################"
            "\n#######################################################################################"
            "\n"
        )
        self.logger.addHandler(console_handler)

    # -------------------------
    # Helpers
    # -------------------------

    @staticmethod
    def log_formatter(class_, name: str, message: str) -> str:
        """
        Standardize multi-line log messages with a uniform header.

        Parameters
        ----------
        class_ : Any
            The object (or class) composing the message; only its type name is used.
        name : str
            Logical name (e.g., equipment instance name).
        message : str
            The message text; tabs are expanded for readability.

        Returns
        -------
        str
            Formatted log string.
        """
        message = message.replace('\t', '\t\t')
        return f"{type(class_).__name__}: {name}\n\t{message}"

    def error(self):
        """
        Return a formatted string of the most recent exception (type, message, file, line).

        Useful for quick logging/printing without building a traceback manually.
        """
        exc_type, exc_obj, exc_tb = sys.exc_info()
        fname = '\\'.join(os.path.split(exc_tb.tb_frame.f_code.co_filename))
        return f"{exc_type}:{exc_obj.args[0]} \nfile: {fname}, line: {exc_tb.tb_lineno}"


# Singleton-style configuration object for easy imports across the codebase.
config = Configurations()