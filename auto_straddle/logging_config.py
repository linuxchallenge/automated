"""Module providing a function for main function """

# pylint: disable=C0116

import logging
import logging.config
from datetime import datetime

def setup_logging(log_level=logging.INFO):
    log_file_name = f"/tmp/auto_trade_{datetime.now().strftime('%Y-%m-%d')}.log"
    logging_config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'standard': {
                'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
            },
        },
        'handlers': {
            'console': {
                'level': log_level,
                'class': 'logging.StreamHandler',
                'formatter': 'standard',
            },
            'file': {
                'level': log_level,
                'class': 'logging.FileHandler',
                'formatter': 'standard',
                'filename': log_file_name
            },
        },
        'loggers': {
            '': {
                'handlers': ['console', 'file'],
                'level': log_level,
                'propagate': True
            },
        }
    }

    logging.config.dictConfig(logging_config)

setup_logging()
