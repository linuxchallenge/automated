"""Module providing a function for main function """

# pylint: disable=C0116

import logging
import logging.config
from datetime import datetime

class DailyFileHandler(logging.FileHandler):
    """FileHandler that follows the calendar: /tmp/auto_trade_YYYY-MM-DD.log of *today*.

    A plain FileHandler stays on the file named at process start, so long-lived
    processes (csv_data_fetcher ran from Nov 3 to Nov 14) keep appending to their
    first day's log. Same file naming as before; only the switch-over is new.
    """

    def __init__(self, level=logging.NOTSET):
        self._day = datetime.now().strftime('%Y-%m-%d')
        super().__init__(self._path_for(self._day), encoding='utf-8')
        self.setLevel(level)

    @staticmethod
    def _path_for(day):
        return f"/tmp/auto_trade_{day}.log"

    def emit(self, record):
        today = datetime.now().strftime('%Y-%m-%d')
        if today != self._day:
            self.acquire()
            try:
                if today != self._day:
                    self._day = today
                    self.close()                      # FileHandler.emit reopens lazily
                    self.baseFilename = self._path_for(today)
            finally:
                self.release()
        super().emit(record)


def setup_logging(log_level=logging.INFO):
    logging_config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'standard': {
                'format': '%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d): %(message)s'
            },
        },
        'handlers': {
            'file': {
                '()': DailyFileHandler,
                'level': log_level,
                'formatter': 'standard',
            },
        },
        'loggers': {
            '': {
                'handlers': ['file'],
                'level': log_level,
                'propagate': True
            },
        }
    }

    logging.config.dictConfig(logging_config)

    # SmartApi logs the full broker response on a rejected order (status, message,
    # errorcode) through logzero's own logger, which only writes to stderr. Route
    # it to the same file so rejection reasons are visible in auto_trade_*.log.
    sdk_logger = logging.getLogger('logzero_default')
    file_handler = next((h for h in logging.getLogger().handlers
                         if isinstance(h, logging.FileHandler)), None)
    if file_handler is not None:
        # dictConfig builds a new handler per call; replace ours instead of stacking
        for old in [h for h in sdk_logger.handlers if isinstance(h, logging.FileHandler)]:
            sdk_logger.removeHandler(old)
        sdk_logger.addHandler(file_handler)

setup_logging()
